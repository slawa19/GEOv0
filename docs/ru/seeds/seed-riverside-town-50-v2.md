# Seed: Riverside Town v2 (50 participants)

This is a **v2 revision** of [seed-riverside-town-50.md](seed-riverside-town-50.md).

**Goal**: keep the compact 50-participant “riverside fishing town” network, but make `UAH` limits / starting debts more realistic for persons and make clearing the default behavior.

## What changed vs v1

### 1) Person-side `UAH` is small by design

For `UAH` trustlines involving `person` participants:
- limits are capped to **≈ 5_000 UAH** (households are typically lower)
- initial `used` is capped by a conservative ratio of the limit

This avoids unrealistic starting states (households or individual workers starting with very large `UAH` debts).

### 2) Clearing-first policy

Trustline policy defaults:
- `auto_clearing = true`
- `can_be_intermediate = true` only for `business ↔ business`

## Equivalents

- `UAH`, `EUR`, `HOUR`

## Generator

- Canonical fixtures pack (in-place): `admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50-v2`
- Optional named pack: `admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50-v2 --pack --activate`

Implementation: [admin-fixtures/tools/generate_seed_riverside_town_50_v2.py](../../../admin-fixtures/tools/generate_seed_riverside_town_50_v2.py)
