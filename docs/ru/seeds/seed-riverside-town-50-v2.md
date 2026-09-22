# Seed: Riverside Town v2 (50 participants)

This is a **v2 revision** of [seed-riverside-town-50.md](seed-riverside-town-50.md).

**Goal**: keep the compact 50-participant “riverside fishing town” network, but make `UAH` limits / starting debts more realistic for persons and make clearing the default behavior.

## What changed vs v1

### 1) Person-side `UAH` is small by design

For `UAH` trustlines involving `person` participants:
- limits are capped to **≈ 5_000 UAH** (households are typically lower)
- initial `used` is capped by a conservative ratio of the limit

This avoids unrealistic starting states (households or individual workers starting with very large `UAH` debts).

### 2) Clearing-first policy — **только в `UAH`**

v2 переписала политику **исключительно на `UAH`‑линиях**. На них:
- `auto_clearing = true`;
- `can_be_intermediate = true`, если **кредитор** (`from`) — `business`.

Две оговорки, без которых это правило читается неверно:

- Это **не** «только `business ↔ business`»: посредником разрешено быть и на линии `business → person`, и это сделано намеренно — так платёж `person → business` получает бизнес‑посредника.
- Линии в `HOUR` и `EUR` v2 **не трогала вовсе**. Их политика осталась от базовой версии: `auto_clearing` — на чётных, `can_be_intermediate` — на всех, кроме кратных пяти. Здесь это заметно: в Riverside ни одна линия вне `UAH` не имеет `auto_clearing`, а среди `HOUR` есть `person → person` с разрешённым посредничеством.

Фактическое распределение политик — в описании сообщества `seeds/communities/riverside-town-50/community.json`, поле `policy` каждой линии.

## Equivalents

- `UAH`, `EUR`, `HOUR`

## Структура

Ростер и линии v2 живут в описании сообщества: [seeds/communities/riverside-town-50/community.json](../../../seeds/communities/riverside-town-50/community.json) — 50 участников и 316 линий против 222 у базовой версии. Разбивка по эквивалентам и сами эти числа закреплены константами в `tests/unit/test_p017_t1712_community_descriptions.py`.

Реализация, из которой описание извлечено: [admin-fixtures/tools/generate_seed_riverside_town_50_v2.py](../../../admin-fixtures/tools/generate_seed_riverside_town_50_v2.py). Генератор ещё собирает исторический пакет фикстур Admin UI (`admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50-v2`, опционально `--pack --activate`); этот путь выводится из обращения — см. [README](README.md#исторический-путь-пакет-фикстур-admin-ui).
