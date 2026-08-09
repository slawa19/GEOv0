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
порты согласно своим параметрам. `run_full_stack.ps1` сохраняет для каждого
сервиса ownership metadata: PID, UTC-время старта процесса, имя сервиса и порт;
он останавливает процесс только когда metadata всё ещё описывает тот же экземпляр
и его PID является listener-ом настроенного порта. Чужой, повторно
использованный, неподтверждённый или изменившийся PID/listener не
останавливается. `run_local.ps1` сохраняет legacy PID-файлы и дополнительно
проверяет executable command line перед остановкой своего процесса.

Два launcher-а взаимно исключаются. `run_full_stack.ps1` хранит versioned JSON
под `.local-run/full-stack/*.owner.json`, а legacy lifecycle `run_local.ps1`
сохраняет свои PID-файлы непосредственно под `.local-run/`; эти пространства не
пересекаются. При active exact или нечитаемом full-stack owner команды
`run_local start/stop/restart/restart-backend` завершаются отказом и не пытаются
эвристически принять либо остановить процесс. В обратную сторону full-stack
launcher считает listener без своей exact metadata конфликтом и также ничего не
останавливает. Для handoff сначала остановите stack его собственным launcher-ом,
проверьте `status`/порты, затем запускайте другой entrypoint. `run_local` может
удалить full-stack metadata только после повторного доказательства, что процесс
отсутствует или fingerprint отличается и ожидаемый порт свободен; invalid или
unreadable metadata сохраняется для явного разбора.

`start`/`restart` дважды проверяет владение всеми сервисами, причём последняя
проверка завершается до первого `Stop-Process`. Это предотвращает частичную
остановку при конфликте, появившемся во время preflight. Между последней проверкой
и системным вызовом остановки остаётся неизбежное короткое race-окно; поэтому
перед каждым stop дополнительно сверяется fingerprint экземпляра. Если stop
завершился ошибкой, ошибка передаётся вызывающему коду, а ownership-файлы не
удаляются. Живой PID без listener-а не убивается: metadata очищается как stale
только когда процесс доказанно отсутствует или fingerprint не совпадает (PID был
переиспользован); при совпадении либо невозможности прочитать identity это
конфликт, требующий явного разбора процесса.

Во время startup metadata записывается только после проверки, что listener PID
совпадает с PID непосредственно запущенного Python/Node-процесса. Если до записи
metadata возникает timeout, чужой listener, исключение или прерывание `Ctrl-C`,
launcher в `finally` повторно
сверяет start fingerprint и best-effort останавливает только свой точный процесс.
Чужой или уже переиспользованный PID не останавливается; уже завершившийся child
не считается ошибкой cleanup. Ошибка cleanup не скрывает исходную startup-ошибку:
в диагностике сохраняются обе причины.

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
