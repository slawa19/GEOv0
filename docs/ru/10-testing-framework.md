# Проверки и тестовые уровни GEOv0

**Статус:** текущий operational guide. Discovery и markers принадлежат
[`pytest.ini`](../../pytest.ini), обязательная локальная точка входа —
[`scripts/verify_local.ps1`](../../scripts/verify_local.ps1), а CI-конфигурация —
[`.github/workflows/quality.yml`](../../.github/workflows/quality.yml).

## Канонический локальный gate

```powershell
.\scripts\verify_local.ps1 -TaskSlug phase6_example
```

Команда запускает backend tier на PostgreSQL без `slow`, проверку одного
Alembic head, а затем lint/unit/build для Admin UI и lint/typecheck/unit/build для
Simulator UI v2. Успешный локальный запуск не доказывает статус опубликованного CI.

Каждый параллельный процесс получает уникальный `TaskSlug`. Его pytest
basetemp/cache и failure artifacts находятся в
`.local-run/test-runs/<TaskSlug>/`, а база — `geov0_test_<TaskSlug>` на
`127.0.0.1:5432` (раннер выводит URL сам, если `TEST_DATABASE_URL` не задан, и тир
создаёт базу); общий task-less output запрещён. Нет PostgreSQL на машине —
[`docs/ru/backend/postgres-local-portable.md`](backend/postgres-local-portable.md).

## Узкие backend-проверки

```powershell
$taskSlug = 'agent_contract_review'
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly `
  -BackendSelector tests/contract/test_openapi_contract.py
```

Selector сначала проходит safety guard. Прямой вызов `python -m pytest` допустим
для диагностики, но не заменяет canonical path; его cache изолирован под
`.local-run/test-runs/direct-pytest/`, а базу умолчанием он не получает: `TEST_DATABASE_URL`
(PostgreSQL, `geov0_test_*`) и `GEO_TEST_ALLOW_DB_RESET=1` задаются явно.

Уровни evidence различаются:

- pure unit — алгоритм без DB/API;
- component/service integration — взаимодействие слоёв в процессе;
- DB integration — наблюдаемый эффект в БД;
- Postgres concurrency — locks/isolation/concurrent writers;
- E2E — пользовательский путь через реальный UI/backend.

С 2026-09-23 (017, стадия 2c) SQLite-тира и marker `postgres` нет: каждый
backend-тест идёт на PostgreSQL. Если база выбрана вами, а не выведена раннером,
проверьте её имя и лишь затем разрешайте reset:

```powershell
$taskSlug = 'agent_payments_review'
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_$taskSlug"
$env:GEO_TEST_ALLOW_DB_RESET = '1'
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly
```

Fail-closed действует и для диагностического прямого pytest: SQLite-URL или
отсутствующий `TEST_DATABASE_URL` завершают прогон UsageError/exit `4` ещё до сбора
тестов, а не зелёным skip.

## UI и дорогие проверки

Для узкого цикла используйте scripts соответствующего package:

```powershell
npm --prefix admin-ui run test
npm --prefix admin-ui run build
npm --prefix simulator-ui/v2 run typecheck
npm --prefix simulator-ui/v2 run test:unit
npm --prefix simulator-ui/v2 run build
```

Playwright, full-stack, Postgres и simulator super-smoke — milestone, а не debug
loop. Для super-smoke канонический вызов:

```powershell
.\scripts\verify_local.ps1 -TaskSlug simulator_super_smoke -BackendOnly `
  -BackendSelector tests/integration/test_simulator_super_smoke.py -IncludeExpensive
```

Playwright-процессы обязаны иметь уникальные порты и output roots. Baseline
screenshots обновляются только после ручного подтверждения намеренного визуального
изменения.

## Что считается доказательством

Отчёт содержит точную команду, exit code, число тестов, затронутую surface и
непройденные пути. Тест проверяет наблюдаемое поведение — state/DB effect,
API/event payload, DOM/a11y или visual contract — а не только существование строки
в source. Assertion-free тест допустим лишь как явно именованная граница
«операция не падает»; no-op steps удаляются.

Пиннутый Ruff для `app migrations` является блокирующим CI-гейтом; Black остаётся
non-blocking diagnostic с известным repository-wide debt, mypy не настроен как gate.
Локальный `verify_local.ps1 -StaticDiagnostics` только выводит результаты обоих
инструментов и не меняет exit code; это не отменяет блокирующую семантику Ruff в CI.

## Владение контрактами

- REST и wire schema: [`api/openapi.yaml`](../../api/openapi.yaml);
- test discovery/markers: [`pytest.ini`](../../pytest.ini);
- required local orchestration: [`scripts/verify_local.ps1`](../../scripts/verify_local.ps1);
- fixtures: канонические generators/sources, а не public/generated copies;
- доменные инварианты: реализация + behavioral tests + актуальная RU-документация.
