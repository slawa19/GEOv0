# Fixture generators (admin-fixtures/tools)

This folder contains **deterministic generators** that write canonical fixture JSON files into:
- `admin-fixtures/v1/datasets/`

These datasets are then copied into the Admin UI public folder via:
- `admin-ui/scripts/sync-fixtures.mjs`

## Conventions (important)

### Determinism

- Do not use randomness without a fixed seed.
- Keep participant ordering stable.
- Keep output JSON stable (sorting / consistent iteration order).

### Trustline semantics

A trustline `from → to` means:
- `from` is the **creditor** (takes risk)
- `to` can become **debtor**

So if a household “buys bread on credit from the bakery”, the trustline is:
- `bakery → household` (in UAH)

### Economic realism rules of thumb

- Retail/bakery: **few suppliers, many buyers**.
- Procurement aggregator: **many supplier-producers, fewer buyers**.
- Hubs (co-op/warehouse): connect clusters and provide limited stabilizing credit.
- Prefer UAH; keep EUR minimal unless testing multi-equivalent.

### Cycles

Include a small number of explicit directed cycles (3–6 nodes) that respect role logic.

## How to run

From repo root:
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe admin-fixtures/tools/generate_seed_greenfield_village_100.py`

Then:
- `cd admin-ui`
- `npm run sync:fixtures`
- `npm run validate:fixtures`
