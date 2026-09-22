# Seed: GreenField Village Community v2 (100 participants)

This is a **v2 revision** of [seed-greenfield-village-100.md](seed-greenfield-village-100.md).

**Goal**: keep the same “village / hromada” story and participant roster, but make initial credit limits and starting debts more realistic:
- persons should not start with tens/hundreds of thousands in `UAH`;
- big balances and large credit limits are mostly a **business ↔ business** phenomenon;
- the network should support **fast clearing** by default.

## What changed vs v1

### 1) Person-side `UAH` guardrails

For `UAH` trustlines where at least one side is `person`:
- credit limits are capped to **≈ 5_000 UAH** (households are typically lower);
- the initial `used` (starting debt) is capped by a conservative ratio of the limit.

This keeps starting debts for households / producers / services in the “normal local economy” range.

### 2) Clearing-first policy — **только в `UAH`**

v2 переписала политику **исключительно на `UAH`‑линиях**. На них:
- `auto_clearing = true` (сокращать обязательства, как только появляются циклы);
- `can_be_intermediate = true`, если **кредитор** (`from`) — `business`.

Две оговорки, без которых это правило читается неверно:

- Это **не** «только `business ↔ business`». Посредником разрешено быть и на линии `business → person`: маршрутизатор применяет политику кредиторской линии (получатель → отправитель) к ребру потока платежа, поэтому платёж `person → business` маршрутизируется через бизнес‑кредитора именно благодаря этому.
- Линии в `HOUR` и `EUR` v2 **не трогала вовсе**. Их политика осталась такой, какой её оставила базовая версия: `auto_clearing` — на чётных, `can_be_intermediate` — на всех, кроме кратных пяти. То есть среди них есть и `person → person` с разрешённым посредничеством.

Фактическое распределение политик — в описании сообщества `seeds/communities/greenfield-village-100/community.json`, поле `policy` каждой линии; пересказывать его числом здесь нельзя, оно устареет.

## Equivalents

- `UAH`, `EUR`, `HOUR`

## Структура

Ростер и линии v2 живут в описании сообщества: [seeds/communities/greenfield-village-100/community.json](../../../seeds/communities/greenfield-village-100/community.json) — 100 участников и 523 линии против 439 у базовой версии. Разбивка по эквивалентам и сами эти числа закреплены константами в `tests/unit/test_p017_t1712_community_descriptions.py`, который краснеет и при расхождении, и при незаписанном изменении.

Реализация, из которой описание извлечено: [admin-fixtures/tools/generate_seed_greenfield_village_100_v2.py](../../../admin-fixtures/tools/generate_seed_greenfield_village_100_v2.py). Генератор ещё собирает исторический пакет фикстур Admin UI (`admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100-v2`, опционально `--pack --activate`); этот путь выводится из обращения — см. [README](README.md#исторический-путь-пакет-фикстур-admin-ui).
