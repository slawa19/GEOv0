# Acceptance Criteria — Simulator Backend + Simulator UI (Real Mode)

Дата: **2026-01-28**

Цель: зафиксировать **проверяемое** определение «готово» для Real Mode (backend ↔ simulator-ui) и исключить дрейф контрактов.

Обозначения:
- **SB-*** — критерии Simulator Backend
- **SB-NF-*** — нефункциональные критерии Simulator Backend
- **SUI-*** — критерии Simulator UI (Real Mode)
- **INT-*** — интеграционные критерии (backend+UI+fixtures)

Ссылки (source of truth):
- `api/openapi.yaml` (Simulator endpoints + `SimulatorEvent` union)
- `docs/ru/simulator/backend/ws-protocol.md` (SSE framing/keepalive/ошибки)
- `fixtures/simulator/scenario.schema.json` (входной `scenario.json`)
- `docs/ru/simulator/frontend/docs/api.md` (UI ожидания по stream/snapshot)

---

## SB — функциональные критерии (backend)

| ID | Критерий | Приоритет | Метод проверки |
|---:|---|:---:|---|
| SB-01 | `POST /api/v1/simulator/scenarios` принимает `scenario.json` как JSON body и валидирует по `fixtures/simulator/scenario.schema.json` с понятными ошибками валидации | MUST | unit + contract |
| SB-02 | `POST /api/v1/simulator/runs` создаёт прогон и возвращает `run_id` | MUST | unit |
| SB-03 | `GET /api/v1/simulator/runs/{run_id}` возвращает актуальный `state` из: `idle|running|paused|stopping|stopped|error` и `last_error?` | MUST | unit |
| SB-04 | `POST /api/v1/simulator/runs/{run_id}/pause` **идемпотентно** приостанавливает прогон; backend перестаёт генерировать доменные события (кроме `run_status`) | MUST | integration |
| SB-05 | `POST /api/v1/simulator/runs/{run_id}/resume` **идемпотентно** возобновляет прогон; генерация событий продолжается | MUST | integration |
| SB-06 | `POST /api/v1/simulator/runs/{run_id}/stop` **идемпотентно** останавливает прогон и освобождает ресурсы; stream корректно завершается | MUST | integration |
| SB-07 | `GET /api/v1/simulator/runs/{run_id}/events` отдаёт SSE stream с событиями `SimulatorEvent` (включая `run_status`) согласно `ws-protocol.md` | MUST | integration |
| SB-08 | Один шаг симуляции (tick) генерирует **0..N** доменных событий на основе отношений (`trustlines[]`, `equivalents[]`, `participants[]`) с детерминизмом по `seed`; `tick` как событие допускается только в debug-режиме. `behaviorProfiles.props` интерпретируются в real mode planner (минимум: `tx_rate`, `recipient_group_weights`, `equivalent_weights`, `amount_model`). | MUST | unit (детерминизм с seed) |
| SB-09 | При моделировании платежей backend вызывает **реальный** PaymentEngine / GEO Core API (не mock) и эмитит события `tx.*` по факту вызовов/ответов | MUST | integration |
| SB-10 | При ошибке/отказе PaymentEngine backend эмитит `tx.failed` с причиной (`last_error.code`/payload), чтобы UI мог показать понятную диагностику | MUST | unit |

Дополнение к SB-07 (обязательные свойства stream):
- `run_status` эмитится: (а) на каждом переходе состояния, (б) периодически во время `running` (например каждые 1–2 секунды).
- keep-alive/heartbeat соответствует `ws-protocol.md`.

---

## SB-NF — нефункциональные критерии (backend)

| ID | Критерий | Приоритет | Метод проверки |
|---:|---|:---:|---|
| SB-NF-01 | На сценарии уровня **100 участников / 300 trustlines** средняя латентность «tick шага» < 100ms (в профиле интенсивности MVP) | SHOULD | perf |
| SB-NF-02 | SSE stream выдерживает 10 одновременных клиентов без деградации протокола (валидные события, keep-alive, корректный stop) | SHOULD | load |
| SB-NF-03 | Память процесса не растёт неконтролируемо при 1000+ шагах (нет утечки из-за event buffer / tasks) | SHOULD | soak (≥10 минут) |
| SB-NF-04 | Воспроизводимость: одинаковый `seed` + сценарий → одинаковая последовательность доменных событий (в пределах допускаемой недетерминированности времени) | MUST | unit |

---

## SUI — критерии Simulator UI (Real Mode)

| ID | Критерий | Приоритет | Метод проверки |
|---:|---|:---:|---|
| SUI-01 | В simulator-ui есть переключение `apiMode: mock | real`; `mock` остаётся работоспособным как сейчас | MUST | manual + e2e |
| SUI-02 | В `real`-режиме UI умеет: выбрать сценарий (из списка) / загрузить сценарий → запустить run → отображать `run_status` и ключевые события | MUST | e2e |
| SUI-03 | UI устойчив к reconnect SSE (потеря сети/обновление страницы): переподключается с backoff; состояние восстанавливает через `GET /runs/{run_id}` | SHOULD | e2e/manual |
| SUI-04 | UI **не вычисляет** `viz_*` семантику сам; использует значения, присланные backend (например из snapshot/metrics/stream) | MUST | code review + e2e |
| SUI-05 | Команды управления прогоном (`pause/resume/stop`, `intensity`) вызываются из UI и отражаются в UI через `run_status` | MUST | e2e |
| SUI-06 | UI показывает диагностическую информацию при ошибках (`last_error`, коды), и не «ломает» визуализацию при неизвестном событии | SHOULD | e2e/manual |

---

## INT — интеграционные критерии (backend+UI+fixtures)

| ID | Критерий | Приоритет | Метод проверки |
|---:|---|:---:|---|
| INT-01 | Прогон эталонного сценария `fixtures/simulator/greenfield-village-100/scenario.json`: старт → `run_status: running` → поток доменных событий → UI визуализирует без падений | MUST | e2e |
| INT-01a | Прогон сценария `fixtures/simulator/greenfield-village-100-realistic-v2/scenario.json` в real mode при `SIMULATOR_REAL_AMOUNT_CAP>=500`: суммы в `tx.updated` перестают быть ограничены 1–3, регулярно видны `clearing.done` (ориентир 2–5/min), и появляется заметный P2P (households↔households) | SHOULD | manual + integration |
| INT-02 | Pause/Resume в UI на живом run: во время `paused` нет доменных событий (кроме `run_status/keep-alive`), после resume поток продолжается | MUST | e2e |
| INT-03 | Stop в UI: сервер завершает run, UI получает `run_status: stopped` (или `error` при ошибке) и корректно завершает сессию | MUST | e2e |
| INT-04 | `bottlenecks` и `metrics` endpoints (если включены в UI) согласованы с OpenAPI и отображаются в соответствующих панелях Real Mode | SHOULD | contract + e2e |
| INT-05 | `artifacts` endpoints: UI видит список артефактов run и может скачать файл; формат соответствует OpenAPI | SHOULD | integration |

---

## Evidence: что считаем доказательством выполнения

- Ссылки на тесты (pytest/playwright) с зелёным прогоном.
- Для SB-01: набор позитивных/негативных примеров, подтверждающих валидацию схемы.
- Для INT-01..03: воспроизводимый сценарий запуска (команды/скрипт) + запись успешного прогона теста.
