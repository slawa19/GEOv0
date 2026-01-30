# JSON Schema сценария симулятора (MVP)

**Статус:** done (2026-01-28)

Этот документ описывает входной формат `scenario.json` и ссылается на формальную схему:
- `fixtures/simulator/scenario.schema.json`

## Термины
- `schema_version`: версия входного формата сценария (не путать с `api_version` control-plane).
- `scenario_id`: идентификатор сценария.
- `participant`: узел сети (person/business/hub).
- `trustline`: направленный лимит `from → to` (кредитор → должник).

## MVP договорённости
- Время в `events[].time` — одно из:
	- **миллисекунды от старта прогона** (integer)
	- простой токен (string), например `day_10` (для будущей интерпретации runner)
- `behaviorProfiles`/`events` допускаются схемой, но **текущая реализация runner (fixtures + real)** их не интерпретирует.
  - На практике сейчас runner использует только `participants[]`, `trustlines[]`, `equivalents[]` и `intensity_percent`.
- Эквиваленты (важно для совместимости с текущим runtime):
	- **обязательно** указывать `equivalents: ["UAH", ...]` (даже если он один)
	- `baseEquivalent` можно оставить как метаданные, но не использовать как единственный источник правды
	- у каждого trustline **обязательно** должен быть `trustlines[].equivalent` (иначе ребро не попадёт в граф и события)

## Практические замечания по текущему коду

1) `baseEquivalent` в schema является shorthand, но runtime строит граф/события по явным `equivalents[]` и `trustlines[].equivalent`.
2) `trustlines[].status` не является полем schema; если нужно переносить статус из fixtures, его можно сохранять внутри `trustlines[].policy` (например `policy.status`).

## Что дальше
- Добавить примеры валидных сценариев в `fixtures/simulator/*/scenario.json`.
- Уточнить и зафиксировать enum-ы для `behaviorProfiles.rules[]` и `events[]` после утверждения runner-модели.

## Примеры
- Минимальный валидный сценарий: `fixtures/simulator/minimal/scenario.json`
- Сценарий в стиле примера из `GEO-community-simulator-application.md` (7.2): `fixtures/simulator/golden-7_2-like/scenario.json`
