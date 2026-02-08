# Backend-first: кумулятивные счётчики транзакций

## Контракт

Бэкенд является **единственным авторитетным источником** для кумулятивных счётчиков:

| Поле              | Описание                                          |
|-------------------|---------------------------------------------------|
| `attempts_total`  | Общее число попыток платежей (committed + rejected + errors) |
| `committed_total` | Успешно проведённые транзакции                     |
| `rejected_total`  | Отклонённые (capacity / routing / trustline)       |
| `errors_total`    | Ошибки (timeout, internal error)                   |
| `timeouts_total`  | Подмножество errors_total: таймауты               |

## Где хранятся

- **RunRecord** (`app/core/simulator/models.py`) — in-memory dataclass, накапливает в `_emit_if_ready()`.
- **run_status SSE** (`runtime_impl.py: publish_run_status`) — отправляет каждую секунду.
- **REST /runs/{id}** (`runtime_utils.py: run_to_status`) — тот же набор полей.
- **Frontend `RunStatus`** (`simulatorTypes.ts`) — типизация фронта.

## Классификация error vs rejection

Единственный источник — бэкенд (`real_runner.py: _emit_if_ready`):

| err_code            | Классификация | Что считает                         |
|---------------------|---------------|-------------------------------------|
| `PAYMENT_TIMEOUT`   | **error**     | errors_total + timeouts_total       |
| `INTERNAL_ERROR`    | **error**     | errors_total                        |
| `None` (status=REJECTED) | **rejection** | rejected_total (через map_rejection_code) |

Фронтенд (`isUserFacingRunError`) повторяет эту логику для оптимистичного отображения между run_status:
- `PAYMENT_TIMEOUT` → error
- `INTERNAL_ERROR` → error
- Всё остальное → rejection

## Синхронизация фронтенда

1. На каждое `tx.updated` / `tx.failed` фронт оптимистично инкрементирует локальные counters.
2. На каждое `run_status` (~1 сек) фронт **перезаписывает** локальные counters значениями из бэкенда.
3. При SSE-реконнекте счётчики не теряются — бэкенд продолжает хранить кумулятивные значения.

## Что было до этого (и почему это проблема)

До этой доработки фронтенд считал `attempts`, `committed`, `rejected`, `errors`, `timeouts`
исключительно локально, инкрементируя на каждое SSE-событие. Проблемы:

- При SSE-реконнекте `resetRunStats()` обнулял все счётчики → потеря истории.
- Классификация расходилась: `SENDER_NOT_FOUND` считался ошибкой на фронте, но rejection на бэкенде.
- `INTERNAL_ERROR` считался rejection на фронте, но ошибкой на бэкенде.
