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

### 2) Clearing-first policy

Trustline policy defaults:
- `auto_clearing = true` (prefer reducing obligations as soon as cycles appear)
- `can_be_intermediate = true` **only** for `business ↔ business` trustlines (people should not become routing intermediates).

## Equivalents

- `UAH`, `EUR`, `HOUR`

## Generator

- Canonical fixtures pack (in-place): `admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100-v2`
- Optional named pack: `admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100-v2 --pack --activate`

Implementation: [admin-fixtures/tools/generate_seed_greenfield_village_100_v2.py](../../../admin-fixtures/tools/generate_seed_greenfield_village_100_v2.py)
