# Simulator fixtures

Каноничные `scenario.json` и [JSON Schema](scenario.schema.json) симулятора.

- [Сценарии и движок (RU)](../../docs/ru/simulator/scenarios-and-engine.md) — запуск и lifecycle.
- [Правила модели realistic-v2 (RU)](../../docs/ru/simulator/realistic-scenarios.md) — роли, назначения, opening, валидация и измерение.

## Активные сценарии

| Сценарий | Участников | Назначение |
|---|---|---|
| `greenfield-village-100-realistic-v2` | 100 | Ролевая экономика UAH: назначенные контрагенты, профили «роль × степень», стартовые платежи, stress, warmup и trust drift. |
| `riverside-town-50-realistic-v2` | 50 | Та же модель на компактном составе сообщества. |
| `clearing-demo-10` | 10 | Небольшая сеть для демонстрации платежей и клиринга; публичный сценарий посетителя по умолчанию. Владелец подтвердил сохранение clearing-demo-10 2026-10-27; T5007 демонстрирует результат без смены default. |
| `community-story-10` | 10 (+1 по `inject`) | Сюжетная история с эпизодами и паузами. Вне списка по умолчанию; нужны `SIMULATOR_SCENARIO_ALLOWLIST=community-story-10`, `SIMULATOR_REAL_ENABLE_INJECT=1`, интенсивность 0 и чистое начальное состояние. См. [§2.13 руководства](../../docs/ru/simulator/scenarios-and-engine.md). |
| `minimal` | 2 | Минимальный валидный вход для проверок схемы и валидатора. |
| `negative/` | — | Невалидные входы для тестов валидатора. |

Сервер использует Real Mode. Статические снимки для клиентского Sandbox находятся отдельно в `simulator-ui/v2/public/simulator-fixtures/`. Режим `INTERACT` удалён; его прежний автоматический paused run не описывает нынешний запуск.

## Файловая структура

```text
fixtures/simulator/
├── scenario.schema.json
├── README.md
├── greenfield-village-100-realistic-v2/scenario.json
├── riverside-town-50-realistic-v2/scenario.json
├── clearing-demo-10/scenario.json
├── community-story-10/scenario.json
├── minimal/scenario.json
├── negative/
└── _archive/
```

## Типы участников

Для текущих сценариев используются `person` (человек, домохозяйство) и `business` (организация, магазин, мастерская, кооператив или центральный узел). Тип не заменяет экономическую роль: в realistic-v2 её задаёт `groupId`, а текстовое описание хранится в `metadata.role`.

У `hub` нет отдельного поведения в v0-симуляторе; семантический хаб в этих наборах обозначается `business`. Разрешение транзита для generated realistic-v2 определяется типом кредитора: `business` допускает посредничество, `person` — нет. Каждое звено ролевого кольца является отдельным прямым платежом, поэтому не требует транзита всем кругом.

## Источник realistic-v2 и регенерация

[generate_simulator_seed_scenarios.py](../../scripts/generate_simulator_seed_scenarios.py) берёт из `seeds/communities/<id>/community.json` **состав, группы, имена, типы, статусы и описательные роли**. Линии community не копируются: общая модель генератора выводит назначения контрагентов, обратные creditor→debtor trustlines, профили и стартовые платежи. `community.json` и `recipe.json` продолжают задавать отдельный административный мир; это принятый долг 050, решение 9 ([подробности](../../docs/ru/simulator/realistic-scenarios.md#1-два-мира-одного-сообщества)).

Для просмотра результата вне tracked fixtures, из корня репозитория:

```powershell
.\.venv\Scripts\python.exe scripts/generate_simulator_seed_scenarios.py --out-root .local-run/scenario-preview
```

Для согласованной регенерации каноничных двух наборов:

```powershell
.\.venv\Scripts\python.exe scripts/generate_simulator_seed_scenarios.py
```

Оба пути выполняют JSON Schema и проверку экономики до публикации каждого файла. Валидация использует настоящую `plan_payments`: seeds 42/1729/20261027, по 2000 тактов при 100 %, максимум 20 действий/такт. Входящие и исходящие суммы сначала складываются по cohort, затем проверяются дисбалансы участников и ролей; параметры и ограничения — [в правилах модели](../../docs/ru/simulator/realistic-scenarios.md#6-генерация-и-проверка-модели). Генератор сам изолирует ENV/state validation subprocess; офлайн-проверка не соединяется с БД.

`events` пишет тот же генератор:

- Opening — обычные `payment` на тактах 1–20, не более пяти за такт, по назначенным прямым парам. Номинальный вклад на пару ограничен половиной исходного лимита. Opening исполняется и при темпе 0; `/restart` повторяет его поверх сохранённых долгов, отказы допустимы.
- Stress начинается на тактах 120/180/240/300. Он включён в офлайн-характеризацию 2000 тактов, но не в первые 100 тактов номинальной живой приёмки.
- Trust drift сохраняет рост `0.05`, затухание `0` (решение по измерению 2026-10-27).

Глобальный `SIMULATOR_REAL_AMOUNT_CAP` по умолчанию `None`; положительное значение — opt-in. Для этих наборов cap 500 не требуется. Измерять результаты нужно отдельно по планам и живым денежным событиям: [CLI, replay и границы метрик](../../docs/ru/simulator/realistic-scenarios.md#7-что-измерять).

## Как загружаются сценарии

- Backend загружает preset-сценарии из `fixtures/simulator/*/scenario.json`.
- Uploaded-сценарии хранятся в `.local-run/simulator/scenarios/<scenario_id>/scenario.json` по умолчанию; корень меняется через `SIMULATOR_STATE_DIR`.
- `SIMULATOR_SCENARIO_ALLOWLIST` фильтрует список и допуск сценариев для не-администратора.
- Runtime игнорирует `_archive/`: там нет `scenario.json` на уровне загружаемого preset.
- При создании прогона runtime привязывает идентичности участников к run и сам засевает сценарий. Рецепт Admin UI не является обязательным шагом этого пути; `/restart` сохраняет идентичности и долги.
