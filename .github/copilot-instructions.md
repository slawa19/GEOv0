# Copilot instructions (GEO v0)

## Where to look first (project semantics)

- Protocol and semantics:
  - `docs/ru/concept/Проект GEO — 7. GEO v0.1 — базовый протокол кредитной сети для локальных сообществ и кластеров.md`
  - `docs/ru/concept/Проект GEO — 6. Архитектура, ревизия v0.1.md`

## Admin UI conventions

- Typography & text styles (must-follow):
  - `docs/ru/admin-ui/typography.md`

## Seed communities and fixtures

- Seed authoring guide (principles + workflow):
  - `docs/ru/seeds/README.md`

- Current demo community seed (with role audit table):
  - `docs/ru/seeds/seed-greenfield-village-100.md`

- Deterministic fixture generators:
  - `admin-fixtures/tools/README.md`
  - `admin-fixtures/tools/generate_fixtures.py`

- Canonical fixtures (source of truth):
  - `admin-fixtures/v1/datasets/*.json`

- Admin UI runtime fixtures (synced copy):
  - `admin-ui/public/admin-fixtures/v1/datasets/*.json`
  - sync script: `admin-ui/scripts/sync-fixtures.mjs`
  - validation: `admin-ui/scripts/validate-fixtures.mjs`

## Guardrails

- TrustLine direction is `from → to` = creditor → debtor (risk limit), *not* the reverse.
- Keep changes deterministic and validate fixtures (`npm run validate:fixtures`) after regeneration.

## Copilot operational notes (Windows)

- Never paste Python code into PowerShell: run Python snippets via the Python interpreter (or prefer `mcp_pylance_mcp_s_pylanceRunCodeSnippet` / a dedicated script).
- Avoid heredoc-style snippets in PowerShell (`<<EOF` / `<<'PY'`) — they are bash syntax and will fail.
- If a terminal shows a `>>>` prompt, it is a Python REPL; exit it (`exit()`), then run PowerShell commands.
- SQLite gotcha: column name `limit` is a SQL keyword; queries must quote it as `"limit"`.

## Local DB sanity checks

- Preferred: run [scripts/check_sqlite_db.py](../scripts/check_sqlite_db.py) using `.venv\Scripts\python.exe`.
- Or use the runner action: `.\scripts\run_local.ps1 check-db`.
