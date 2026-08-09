# Конфигурация GEOv0

**Статус:** справочник текущей реализации. Актуальность проверяется по
[`app/config.py`](../../app/config.py); переменные Compose дополнительно задаются в
[`docker-compose.yml`](../../docker-compose.yml) и
[`docker-compose.dev.yml`](../../docker-compose.dev.yml). В проекте нет активного
YAML-файла конфигурации приложения.

## Правила загрузки

- Backend читает переменные окружения и корневой `.env` через Pydantic Settings.
- Имена чувствительны к регистру; неизвестные ключи игнорируются.
- `ENV` обязателен и нормализуется в `dev`, `test`, `staging` или `prod`.
  `ENVIRONMENT` — только совместимый legacy-alias. Конфликт двух ключей останавливает
  запуск.
- Небезопасные значения `JWT_SECRET`, `ADMIN_TOKEN` и
  `SIMULATOR_SESSION_SECRET` допустимы только в `dev`/`test`. В остальных средах
  startup guard завершает процесс ошибкой.
- Пустой `SIMULATOR_CSRF_ORIGIN_ALLOWLIST` допускается только в безопасных средах;
  элементы списка должны быть точными HTTP(S) origins.

## База данных и локальное состояние

Без явного `DATABASE_URL` локальный backend использует
`sqlite+aiosqlite:///./.local-run/geov0.db`. Каталог создаётся при инициализации
engine; `.local-run/` — ignored runtime root, а не fixture и не часть репозитория.

Существующий legacy-файл `./geov0.db` не переносится и не удаляется автоматически.
Для осознанного временного запуска с ним задайте:

```powershell
$env:DATABASE_URL = 'sqlite+aiosqlite:///./geov0.db'
```

Команды `reset-db`/`-ResetDb` при таком override завершаются ошибкой: runner
разрешает удаление только нового default-файла под `.local-run/`.

В Compose приложение использует Postgres URL из `docker-compose.yml`. Настройки
пула `DB_POOL_PRE_PING`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`,
`DB_POOL_TIMEOUT_SECONDS`, `DB_POOL_RECYCLE_SECONDS` и
`DB_POSTGRES_ISOLATION_LEVEL` применимы к client/server БД; SQLite работает с
`NullPool`.

## Группы backend-параметров

Точные типы и дефолты находятся рядом с использованием в `Settings`:

- инфраструктура: `DATABASE_URL`, `REDIS_URL`, `REDIS_ENABLED`, `LOG_LEVEL`,
  `DEBUG`;
- auth: `JWT_*`, `AUTH_CHALLENGE_EXPIRE_SECONDS`, `ADMIN_TOKEN`,
  `ADMIN_DEV_MODE`, `ADMIN_DEV_ALLOWLIST`;
- payments/recovery: `PREPARE_LOCK_TTL_SECONDS`, `RECOVERY_*`,
  `PAYMENT_TX_STUCK_TIMEOUT_SECONDS`, `PREPARE_TIMEOUT_SECONDS`,
  `COMMIT_TIMEOUT_SECONDS`, `PAYMENT_TOTAL_TIMEOUT_SECONDS`, `COMMIT_RETRY_*`;
- routing/balance: `ROUTING_*`, `MAX_FLOW_MAX_HOPS`,
  `BALANCE_SUMMARY_CACHE_TTL_SECONDS`;
- service controls: `RATE_LIMIT_*`, `METRICS_ENABLED`, `CLEARING_ENABLED`,
  `FEATURE_FLAGS_*`, `INTEGRITY_CHECKPOINT_*`;
- simulator: `SIMULATOR_DB_ENABLED`, `SIMULATOR_VIZ_QUANTILE_REFRESH_TICKS`,
  `SIMULATOR_SESSION_*`, `SIMULATOR_MAX_ACTIVE_RUNS_PER_OWNER`,
  `SIMULATOR_CSRF_ORIGIN_ALLOWLIST`;
- Admin graph include limits: `ADMIN_GRAPH_INCLUDE_MAX_*`.

Не переносите дефолты из этого документа в новый параллельный конфиг: изменение
контракта выполняется согласованно в `app/config.py`, Compose/env-примере, тестах и
этом справочнике.

## Runtime-настройки Admin

Часть операционных параметров изменяется через Admin API и хранится в БД. Их
канонический перечень и валидация находятся в backend schemas/routes, а REST wire
shape — в [`api/openapi.yaml`](../../api/openapi.yaml). Эти значения не следует
дублировать в `.env` как второй источник состояния.

## Тестовые overrides и артефакты

[`scripts/verify_local.ps1`](../../scripts/verify_local.ps1) по умолчанию назначает
уникальные для `TaskSlug`:

- `TEST_DATABASE_URL=.local-run/test-runs/<TaskSlug>/test.db`;
- pytest basetemp и cache;
- `GEO_TEST_ARTIFACT_ROOT=.local-run/test-runs/<TaskSlug>/artifacts`.

Для Postgres-тестов задавайте отдельную disposable DB и только после проверки URL
включайте `GEO_TEST_ALLOW_DB_RESET=1`. Прямой pytest — debug path; его fallback
также находится под `.local-run/test-runs/direct-pytest/`.

## UI build-time параметры

Admin UI использует `VITE_API_MODE` и `VITE_API_BASE_URL`; локальный runner пишет
`admin-ui/.env.local`. Playwright output можно изолировать через
`GEO_ADMIN_PLAYWRIGHT_OUTPUT_DIR`, `GEO_ADMIN_PLAYWRIGHT_REPORT_DIR`,
`GEO_SIMULATOR_PLAYWRIGHT_OUTPUT_DIR` и отдельный
`GEO_SIMULATOR_HUD_QA_OUTPUT_DIR`; дефолты находятся под `.local-run/`.

Секреты, `.env`, локальные БД, логи, PID, NDJSON и test output не являются
каноническими данными и не коммитятся.
