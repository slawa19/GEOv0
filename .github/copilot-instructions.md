# Copilot instructions (GEO v0)

## Where to look first (project semantics)

- Protocol and semantics:
  - `docs/ru/concept/Проект GEO — 7. GEO v0.1 — базовый протокол кредитной сети для локальных сообществ и кластеров.md`
  - `docs/ru/concept/Проект GEO — 6. Архитектура, ревизия v0.1.md`

## Seed communities and fixtures

- Seed authoring guide (principles + workflow):
  - `docs/ru/seeds/README.md`

- Current demo community seed (with role audit table):
  - `docs/ru/seeds/seed-greenfield-village-100.md`

- Deterministic fixture generators:
  - `admin-fixtures/tools/README.md`
  - `admin-fixtures/tools/generate_seed_greenfield_village_100.py`

- Canonical fixtures (source of truth):
  - `admin-fixtures/v1/datasets/*.json`

- Admin UI runtime fixtures (synced copy):
  - `admin-ui/public/admin-fixtures/v1/datasets/*.json`
  - sync script: `admin-ui/scripts/sync-fixtures.mjs`
  - validation: `admin-ui/scripts/validate-fixtures.mjs`

## Guardrails

- TrustLine direction is `from → to` = creditor → debtor (risk limit), *not* the reverse.
- Keep changes deterministic and validate fixtures (`npm run validate:fixtures`) after regeneration.
