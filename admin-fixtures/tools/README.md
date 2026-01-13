# Fixture generators (admin-fixtures/tools)

This folder contains **deterministic generators**.

Canonical seed generators write fixture JSON files into:
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

For fixtures-first prototyping of clearing and balance analytics, generators should also produce:
- `admin-fixtures/v1/datasets/debts.json` (derived from trustline `used` values)
- `admin-fixtures/v1/datasets/clearing-cycles.json` (a small sample of short debt cycles)

And keep metadata in sync:
- `admin-fixtures/v1/_meta.json`
	- includes `seed_id` (guardrail: Admin UI validation only allows known seeds by default)

## How to run

From repo root:

### Seed generators

These write the canonical seed datasets into `admin-fixtures/v1/datasets/`.

Important:
- Seed generators **overwrite** the canonical datasets (there is a single `v1` folder).
- Shared logic lives in `admin-fixtures/tools/seedlib.py`.

Run one of:
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100`
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50`

### Participant types

Participant `type` is intentionally kept consistent across the repo:
- `person`
- `business`
- `hub`

### Bottlenecks (Dashboard)

Dashboard “Trustline bottlenecks” filters active trustlines by the ratio:

$$\frac{available}{limit} < 0.10$$

Generators should include a handful of such trustlines (with `limit > 0`) so the widget has data.

Then:
- `cd admin-ui`
- `npm run sync:fixtures`
- `npm run validate:fixtures`
