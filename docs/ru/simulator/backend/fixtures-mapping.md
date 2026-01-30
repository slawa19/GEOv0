# Mapping admin-fixtures → simulator scenario (MVP)

**Статус:** done (2026-01-28)

Цель: зафиксировать правила, по которым canonical fixtures из `admin-fixtures/v1/datasets/*.json` превращаются в валидный `fixtures/simulator/*/scenario.json` (по схеме `fixtures/simulator/scenario.schema.json`).

Документ специально написан так, чтобы по нему можно было реализовать/поддерживать генератор seed-сценариев `scripts/generate_simulator_seed_scenarios.py`.

---

## 0) Источники

Входные данные (canonical fixtures, source of truth):
- `admin-fixtures/v1/datasets/participants.json`
- `admin-fixtures/v1/datasets/trustlines.json`
- `admin-fixtures/v1/datasets/equivalents.json`

Контракт выхода:
- `fixtures/simulator/scenario.schema.json`

Seed-логика и «семантика ролей» (для групп/профилей):
- `docs/ru/seeds/seed-greenfield-village-100.md`
- `admin-fixtures/tools/generate_seed_greenfield_village_100.py`
- `admin-fixtures/tools/generate_seed_riverside_town_50.py`

---

## 1) Общие принципы

### 1.1 Guardrail: направление trustline
TrustLine direction фиксирована:
- `from → to` = **creditor → debtor** (лимит риска), не наоборот.

Конвертер обязан копировать направление из fixtures 1:1.

### 1.2 Детерминизм
Конвертация должна быть **детерминированной**:
- одинаковые входные fixtures + одинаковый `scenario_id` → одинаковый `scenario.json` (байт-в-байт после нормализации сортировки).

Минимальные требования:
- стабильная сортировка `participants[]` и `trustlines[]`
- стабильная генерация `groups[]` и `behaviorProfiles[]` (если добавляются)

### 1.3 Нормализация чисел
- `trustlines[].limit` в fixtures — строка с десятичной точкой (например `"1000.00"`).
- В `scenario.json` для MVP оставляем `limit` **строкой** (без потери точности).

---

## 2) Mapping таблица (fixtures → scenario)

### 2.1 participants
Источник: `admin-fixtures/v1/datasets/participants.json`

| Fixtures поле | Scenario поле | Правило |
|---|---|---|
| `pid` | `participants[].id` | Копировать 1:1 |
| `display_name` | `participants[].name` | Копировать 1:1 |
| `type` | `participants[].type` | Копировать 1:1 (`person|business|hub`) |
| `status` | `participants[].status` | Копировать 1:1 (`active|suspended|left|deleted|frozen`) |

Примечание: в fixtures встречается `status="frozen"`, а в schema participant.status это допустимо.

### 2.2 equivalents
Источник: `admin-fixtures/v1/datasets/equivalents.json`

В `scenario.json` используем один из вариантов:
- **обязательный для текущего runtime**: `equivalents: string[]`
- `baseEquivalent: string` допускается оставить как метаданные/shorthand схемы, но не использовать как единственный источник правды

Правило для `equivalents`:
1) собрать все `equivalent` из `admin-fixtures/v1/datasets/trustlines.json` (уникально)
2) оставить только те, которые присутствуют в `admin-fixtures/v1/datasets/equivalents.json` и `is_active=true`
3) отсортировать лексикографически

Если итоговый список пустой — это ошибка конвертации.

### 2.3 trustlines
Источник: `admin-fixtures/v1/datasets/trustlines.json`

| Fixtures поле | Scenario поле | Правило |
|---|---|---|
| `from` | `trustlines[].from` | Копировать 1:1 |
| `to` | `trustlines[].to` | Копировать 1:1 |
| `equivalent` | `trustlines[].equivalent` | Копировать 1:1 |
| `limit` | `trustlines[].limit` | Копировать 1:1 (строкой) |
| `policy` | `trustlines[].policy` | Копировать 1:1 (как JSON object) |
| `status` | `trustlines[].policy.status` | Опционально: сохранить как часть `policy` |

Поля `used`/`available` не входят в `scenario.json` (это состояние, не конфигурация).

Фильтрация (MVP):
- по умолчанию включаем trustlines со статусами `active` и `frozen` (если нужны для реализма)
- если runner в MVP не умеет `frozen`, то конвертер может:
  - либо исключить `status != active`
  - либо проставить `policy.status` и оставить на runner

---

## 3) Группы (groups) и поведение (behaviorProfiles)

### 3.1 Когда добавляем groups/behaviorProfiles
- Для самого минимального сценария можно их не добавлять.
- Для «seed-сценариев» (greenfield-village-100, riverside-town-50) **рекомендуется** добавлять: это фиксирует роли и делает поведение runner воспроизводимым.

### 3.2 groupId для seed GreenField Village (100)
Опираемся на seed-спеку и детерминированную нумерацию PID в генераторе.

Как получить индекс `idx` из PID:
- PID имеет формат `PID_U<NNNN>_<hash>`
- `idx = int(NNNN)`

Группы по диапазонам (1-based):
- `anchors`: 1..10
- `producers`: 11..35
- `retail`: 36..45
- `services`: 46..60
- `households`: 61..95
- `agents`: 96..100

Правило:
- `participants[].groupId = <group>` по диапазону `idx`.
- `groups[]` содержит эти id + label.

### 3.3 groupId для seed Riverside Town (50)
Диапазоны (1-based):
- `anchors`: 1..5
- `producers`: 6..15
- `retail`: 16..23
- `services`: 24..33
- `households`: 34..48
- `agents`: 49..50

### 3.4 behaviorProfileId (MVP-минимум)
Рекомендуемый набор profile id (3–6 штук), привязка по groupId:
- `anchor_hub` (anchors)
- `producer` (producers)
- `retail` (retail)
- `service` (services)
- `household` (households)
- `agent` (agents)

Правило:
- `participants[].behaviorProfileId = <profile>` по groupId.

`behaviorProfiles[]` (MVP) можно хранить как «пустые» профили с `props`/`rules` и доопределять в runner:
- конвертер создаёт profile ids
- runner может интерпретировать их как пресеты (в текущей реализации planner их не использует)

---

## 4) Рекомендуемая сортировка (для стабильности diff)

### 4.1 participants
Сортировать по `id` (лексикографически).

### 4.2 trustlines
Сортировать по ключу:
`(equivalent, from, to)`.

---

## 5) Структура выходного scenario.json (seed)

Минимальный состав для seed-сценария:
- `schema_version: "scenario/1"`
- `scenario_id: <id>`
- `equivalents: [...]`
- `participants: [...]` (+ groupId + behaviorProfileId)
- `groups: [...]`
- `behaviorProfiles: [...]`
- `trustlines: [...]`

`events[]` в seed-конвертации по умолчанию пустой (добавляется позже, если нужен stress-профиль).

---

## 6) Проверки конвертера (Definition of Done)

Конвертер считается реализуемым/корректным, если:
- выходной `scenario.json` валидируется `fixtures/simulator/scenario.schema.json`
- все `trustlines[].from/to` существуют в `participants[].id`
- `equivalents` содержит все используемые `trustlines[].equivalent`
- порядок элементов стабилен (повторная генерация не меняет diff)
