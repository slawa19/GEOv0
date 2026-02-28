# Simulator fixtures

Каноничные `scenario.json` сценарии и JSON Schema для симулятора.

Документация (RU): [`docs/ru/simulator/scenarios-and-engine.md`](../../docs/ru/simulator/scenarios-and-engine.md)
Правила realistic-v2 (RU, единая точка истины): [`docs/ru/simulator/realistic-scenarios.md`](../../docs/ru/simulator/realistic-scenarios.md)

---

## Активные сценарии

| Сценарий | Участников | Описание |
|----------|-----------|----------|
| **`greenfield-village-100-realistic-v2`** | 100 | Основной рабочий сценарий. Realistic behaviorProfiles (10 подтипов), seasonal events, settings (warmup, trust_drift, flow). UI default. |
| **`riverside-town-50-realistic-v2`** | 50 | Компактная альтернатива. Те же фичи, быстрее для тестов и демо. |
| **`clearing-demo-10`** | 10 | Минимальный сценарий для интерактивной демонстрации клиринга. **Дефолтный датасет Interact Mode.** 4 бизнеса + 6 людей, множество потенциальных циклов. Запускать с `intensity_percent=0`. |
| **`minimal`** | 2 | Минимальный валидный сценарий для unit-тестов валидатора/схемы. |
| **`negative/`** | — | 2 невалидных JSON для тестов валидатора schema. |

## Файловая структура

```
fixtures/simulator/
├── scenario.schema.json                                ← JSON Schema (MVP)
├── README.md                                           ← этот файл
├── greenfield-village-100-realistic-v2/scenario.json   ← основной (100)
├── riverside-town-50-realistic-v2/scenario.json        ← компактный (50)
├── clearing-demo-10/scenario.json                      ← демо клиринга (10)
├── minimal/scenario.json                               ← unit-тесты
├── negative/                                           ← тесты валидатора
└── _archive/                                           ← устаревшие сценарии (не загружаются runtime)
```

## Типы участников (`participant.type`)

В сценариях v0 допустимы два типа:

| Тип | Визуализация | Когда использовать |
|---|---|---|
| `person` | Круг (синий) | Физическое лицо, домохозяйство |
| `business` | Квадрат (зелёный) | Организация, магазин, мастерская, кооператив — и **любой центральный узел-«хаб»** в демо-сценарии |

> **Важно.** Тип `hub` существует в протокольной спецификации GEO (§8 «Межхабовое взаимодействие»),
> но в **v0-симуляторе не реализован как отдельный тип**: UI не имеет для него визуального стиля
> (рендерится как `person`), бэкенд не имеет специальной логики.
> Во всех сценариях и фикстурах **использовать `"type": "business"`**, даже если узел
> семантически является хабом.

## Interact Mode — дефолтный датасет

При запуске Simulator UI в режиме `INTERACT` (реальный бэкенд, кнопка «INTERACT» в шапке)
автоматически создаётся paused run по сценарию **`clearing-demo-10`**.

Участники датасета (для справки при разборе проблем):

| id | name | type |
|---|---|---|
| `shop` | Магазин | business |
| `bakery` | Пекарня | business |
| `farmer` | Фермер | business |
| `workshop` | Майстерня | business |
| `alice` | Алиса | person |
| `bob` | Боб | person |
| `olena` | Олена | person |
| `dmytro` | Дмитро | person |
| `natalya` | Наталя | person |
| `vasyl` | Василь | person |

Источник: `fixtures/simulator/clearing-demo-10/scenario.json`

## Регенерация

Regenerate realistic-v2 seed scenarios:
```bash
python scripts/generate_simulator_seed_scenarios.py
```

Patch events для realistic-v2:
```bash
python scripts/generate_scenario_events.py
```

## Как загружаются сценарии

- Backend загружает preset сценарии из `fixtures/simulator/*/scenario.json`
- Загруженные (uploaded) сценарии хранятся в `.local-run/simulator/scenarios/<scenario_id>/scenario.json`
- Список сценариев в UI может фильтроваться через `SIMULATOR_SCENARIO_ALLOWLIST`
- Директория `_archive/` **не содержит** валидных scenario.json на верхнем уровне — runtime её игнорирует

## Планы

- Аудит сценариев и план демо клиринга: [`plans/scenario-audit-and-interactive-clearing-demo.md`](../../plans/scenario-audit-and-interactive-clearing-demo.md)
- Интерактивная демонстрация в Simulator UI: [`plans/interactive-clearing-demo-in-simulator-ui.md`](../../plans/interactive-clearing-demo-in-simulator-ui.md)
