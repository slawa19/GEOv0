# Запуск и границы deployment-поддержки

**Статус:** проверяемые entrypoints текущего репозитория. Этот документ не обещает
production-ready HA, backup/restore, zero-downtime upgrade, TLS termination или
оркестрацию кластера: таких подтверждённых контрактов в репозитории сейчас нет.

## Локальный Windows stack

Основные entrypoints:

```powershell
.\scripts\run_local.ps1 start
.\scripts\run_full_stack.ps1 start
```

Актуальные параметры и команды `status`/`stop` смотрите в `Get-Help` и самих
scripts. Они создают runtime-состояние под `.local-run/`, используют свободные
порты согласно своим параметрам и должны останавливать только запущенные ими
процессы.

Без явного `DATABASE_URL` backend использует
`sqlite+aiosqlite:///./.local-run/geov0.db`. Старый `./geov0.db` не мигрируется и
не удаляется автоматически; осознанный запуск на нём требует явного override.
См. [справочник конфигурации](config-reference.md).

## Docker Compose

Базовый `docker-compose.yml` production-like: backend подключается к Postgres и
Redis и требует безопасные секреты. Для локальных dev-дефолтов подключайте overlay:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

Compose создаёт persistent named volume `postgres_data`. Его удаление уничтожает
локальные данные и не является обычной командой остановки. Порты можно изменить
через `GEO_DB_PORT`, `GEO_REDIS_PORT` и `GEO_API_PORT`.

Перед запуском base Compose вне dev задайте как минимум безопасные значения
`JWT_SECRET`, `ADMIN_TOKEN`, `SIMULATOR_SESSION_SECRET` и допустимый
`SIMULATOR_CSRF_ORIGIN_ALLOWLIST`. Backend fail-fast guard отклоняет dev-заглушки
в `staging`/`prod`.

## Миграции и данные

Alembic управляет схемой. Уже применённые migrations не переписываются; новая
схема доставляется новой migration и проверенным upgrade path. Seed и fixture
команды предназначены для demo/dev, не для восстановления production-данных.

Перед любым reset необходимо отдельно доказать, что URL указывает на disposable
test DB. `GEO_TEST_ALLOW_DB_RESET=1` запрещено использовать с developer или
production DB.

## Проверка перед передачей

Минимальный локальный milestone:

```powershell
.\scripts\verify_local.ps1 -TaskSlug premerge_slice
```

Он не включает Postgres concurrency, Playwright E2E, `slow` и другие дорогие
tiers. Их запускают отдельно, когда изменён соответствующий контракт. Наличие
workflow-файла не означает, что опубликованный CI завершился успешно.

## Непокрытые production-обязанности

Владелец deployment обязан отдельно выбрать и проверить:

- secret management и rotation;
- TLS/reverse proxy и trusted network boundaries;
- Postgres backup, restore drill, retention и disaster recovery;
- monitoring/alerting и log retention;
- rollout/rollback, capacity и multi-replica semantics;
- эксплуатацию Redis и persistent volumes.

До появления исполнимых scripts, инфраструктурного кода и runtime evidence эти
пункты остаются целевыми требованиями, а не реализованными возможностями.
