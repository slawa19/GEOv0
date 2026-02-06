# Copilot instructions (GEO v0)

## Quick Start — как запустить проект

### Full Stack (Backend + Admin UI + Simulator UI)

```powershell
# Запуск всего стека с одной команды:
.\scripts\run_full_stack.ps1 -Action start

# С пересозданием БД и загрузкой fixtures:
.\scripts\run_full_stack.ps1 -Action start -ResetDb -FixturesCommunity greenfield-village-100

# Статус, остановка, рестарт:
.\scripts\run_full_stack.ps1 -Action status
.\scripts\run_full_stack.ps1 -Action stop
.\scripts\run_full_stack.ps1 -Action restart
```

После запуска:
- Backend API: http://127.0.0.1:18000/docs
- Admin UI: http://localhost:5173/
- Simulator UI: http://localhost:5176/?mode=real

### Тесты

```powershell
# Все unit-тесты
.\.venv\Scripts\python.exe -m pytest tests/unit/ -v

# Конкретная группа тестов
.\.venv\Scripts\python.exe -m pytest tests/unit/ -k "clearing" -v

# С coverage
.\.venv\Scripts\python.exe -m pytest tests/unit/ --cov=app --cov-report=term-missing
```

### Проверка БД

```powershell
.\.venv\Scripts\python.exe scripts/check_sqlite_db.py
```

---

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
- Canonical decisions and data contracts must be recorded in stable docs under `docs/ru/*` (e.g. `docs/ru/09-decisions-and-defaults.md` and the relevant domain docs like `docs/ru/simulator/backend/*`). Do not leave “single source of truth” rules only in `plans/*` or code-review notes.
### Pydantic alias serialization (CRITICAL)

- **Always use `by_alias=True`** when calling `.model_dump(mode="json")` on models with `Field(alias=...)`.
- Without `by_alias=True`, Pydantic uses Python attribute names (`from_`) instead of alias (`from`).
- `serialize_by_alias=True` in `model_config` only affects `model_dump_json()`, NOT `model_dump()`.
- See full explanation: `docs/ru/backend/pydantic-alias-serialization.md`

```python
# ❌ WRONG - will serialize as {"from_": "A"}
evt.model_dump(mode="json")

# ✅ CORRECT - will serialize as {"from": "A"}
evt.model_dump(mode="json", by_alias=True)
```

### SSE Events format

- Simulator events (tx.updated, clearing.plan, clearing.done) use edge refs with `from`/`to` keys.
- Frontend in `normalizeSimulatorEvent.ts` expects `"from"` key, not `"from_"`.
- Always test SSE event serialization when modifying Pydantic event schemas.
## Copilot operational notes (Windows)

- Never paste Python code into PowerShell: run Python snippets via the Python interpreter (or prefer `mcp_pylance_mcp_s_pylanceRunCodeSnippet` / a dedicated script).
- Avoid heredoc-style snippets in PowerShell (`<<EOF` / `<<'PY'`) — they are bash syntax and will fail.
- If a terminal shows a `>>>` prompt, it is a Python REPL; exit it (`exit()`), then run PowerShell commands.
- SQLite gotcha: column name `limit` is a SQL keyword; queries must quote it as `"limit"`.

### Backend restart for UI validation (Copilot must do this)

- If changes affect backend behavior that the UI depends on (routes, schemas, auth, SSE/events, config/env flags, DB/fixtures seeding, dependency changes), Copilot MUST restart the backend (or full stack) before validating in the browser.
- Preferred command: `\.\scripts\run_full_stack.ps1 -Action restart`.
- If only frontend code changed (UI rendering, CSS, client-only state), no restart is required.
- When in doubt, restart first to avoid testing against stale server code.

## Local DB sanity checks

- Preferred: run `scripts/check_sqlite_db.py` using `.venv\Scripts\python.exe`.
- Or use the runner action: `.\scripts\run_local.ps1 check-db`.
