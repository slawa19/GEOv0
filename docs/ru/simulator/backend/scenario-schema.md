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
	- простой токен (string), например `day_10` (runner интерпретирует)
- `behaviorProfiles`/`events` допускаются, но для самого первого MVP runner может работать и без них (только по данным сети + intensity).
- Эквивалент для MVP:
	- предпочтительно `equivalents: ["UAH"]`
	- допускается shorthand `baseEquivalent: "UAH"` (если UI/runner не требует перечисления)

## Что дальше
- Добавить примеры валидных сценариев в `fixtures/simulator/*/scenario.json`.
- Уточнить и зафиксировать enum-ы для `behaviorProfiles.rules[]` и `events[]` после утверждения runner-модели.

## Примеры
- Минимальный валидный сценарий: `fixtures/simulator/minimal/scenario.json`
- Сценарий в стиле примера из `GEO-community-simulator-application.md` (7.2): `fixtures/simulator/golden-7_2-like/scenario.json`
