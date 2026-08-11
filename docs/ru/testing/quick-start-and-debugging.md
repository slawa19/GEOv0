# Быстрый запуск тестов и диагностика

## Required path

Из корня репозитория:

```powershell
.\scripts\verify_local.ps1 -TaskSlug local_check
```

`TaskSlug` должен быть уникальным для параллельного процесса. Backend DB,
basetemp, cache и failure artifacts окажутся в
`.local-run/test-runs/local_check/`. Полный состав gate и границы evidence описаны
в [`../10-testing-framework.md`](../10-testing-framework.md).

## Быстрый backend selector

```powershell
.\scripts\verify_local.ps1 -TaskSlug payment_debug -BackendOnly `
  -BackendSelector tests/unit/test_payments_2pc.py
```

При изменении REST schema/serialization отдельно запускайте контракт:

```powershell
.\scripts\verify_local.ps1 -TaskSlug api_contract -BackendOnly `
  -BackendSelector tests/contract/test_openapi_contract.py
```

Прямой `python -m pytest` — debug path: он не заменяет verifier и использует
fallback state под `.local-run/test-runs/direct-pytest/`.

## Диагностика окружения

Перед выводом «окружение сломано» проверьте реальные executables:

```powershell
Get-Command python
python --version
Get-Command npm
npm --version
```

Если PowerShell блокирует activation, передайте интерпретатор явно:

```powershell
.\scripts\verify_local.ps1 -Python .\.venv\Scripts\python.exe `
  -TaskSlug explicit_python -BackendOnly `
  -BackendSelector tests/unit/test_invariants.py
```

## SQLite

Локальный default — `.local-run/geov0.db`. Проверка текущей DB:

```powershell
$env:ENV = 'dev'
.\.venv\Scripts\python.exe scripts/check_sqlite_db.py
```

Существующий `./geov0.db` считается legacy/user data: tooling не переносит и не
удаляет его автоматически. Для диагностики этого файла задайте явный
`DATABASE_URL`.

## PostgreSQL

Locking, isolation и concurrent writers проверяются только на отдельной disposable
Postgres DB. До `GEO_TEST_ALLOW_DB_RESET=1` вручную убедитесь, что имя URL относится
к тестовой базе. Не используйте developer/prod DB.

## UI

```powershell
npm --prefix admin-ui run test
npm --prefix admin-ui run build
npm --prefix simulator-ui/v2 run typecheck
npm --prefix simulator-ui/v2 run test:unit
npm --prefix simulator-ui/v2 run build
```

Playwright требует уникального порта. Его default output находится под
`.local-run/playwright/`; package-local `test-results` и `playwright-report` не
являются каноническими новыми outputs.

## Как сообщать результат

Запишите точную команду, exit code, число тестов и пропущенные tiers. Не называйте
локальный прогон «CI green». Локальный `-StaticDiagnostics` только печатает оба
результата; в CI pinned Ruff для `app migrations` блокирует, Black остаётся
non-blocking diagnostic, mypy не является gate.
