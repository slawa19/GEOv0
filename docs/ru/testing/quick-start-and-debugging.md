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

Прямой `python -m pytest` — debug path: он не заменяет verifier, держит cache под
`.local-run/test-runs/direct-pytest/` и требует явного PostgreSQL `TEST_DATABASE_URL` —
умолчания базы у него нет (017, стадия 2c).

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

## Локальная dev-база

С программы 017 SQLite не поддерживается: локальная база — PostgreSQL
`geov0_dev_<DbSlug>` лаунчера (см. `README.md`). Проверка её готовности (схема на
head, популяция рецепта, baseline):

```powershell
$env:ENV = 'dev'
.\scripts\run_local.ps1 check-db
```

Прежние `.local-run/geov0.db` и `./geov0.db` — данные пользователя: tooling их не
читает, не переносит и не удаляет.

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
