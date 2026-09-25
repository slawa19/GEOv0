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
порты согласно своим параметрам. `run_full_stack.ps1` и `run_local.ps1`
сохраняют для каждого непосредственно запущенного сервиса versioned ownership
metadata: repository identity, PID, UTC start fingerprint, имя сервиса и порт.
Процесс останавливается только когда metadata всё ещё описывает тот же экземпляр,
а его PID является listener-ом сохранённого порта. Чужой, повторно использованный,
неподтверждённый или изменившийся PID/listener не останавливается. Обычная команда
`run_full_stack stop`, как и cleanup перед start, сначала строит коллективный
read-only plan по всем сервисам; конфликт любого сервиса отменяет всю остановку
до первого `Stop-Process`. `run_local status` только читает metadata, legacy
evidence и listener state: stale-файлы и каталоги эта команда не очищает и не
создаёт.

Mutating lifecycle `run_full_stack.ps1`, `run_local.ps1` и
`run_real_simulator.ps1` взаимно исключается общим repository-scoped OS mutex.
Он удерживается от ownership preflight до завершения stop/start/reset и
автоматически освобождается ОС при crash launcher-а; конкурентная mutating
команда завершается понятным отказом. Переменная окружения не может считаться
доказательством владения mutex; `run_local restart` выполняет stop/start внутри
одного процесса и одной критической секции. `run_full_stack.ps1` хранит versioned JSON
под `.local-run/full-stack/*.owner.json`, `run_local.ps1` — под
`.local-run/run-local/*.owner.json`, а Simulator UI, запущенный
`run_real_simulator.ps1`, — под
`.local-run/run-real-simulator/simulator-ui.owner.json`; эти пространства не
пересекаются. Исторические bare `.local-run/backend.pid` и
`.local-run/admin-ui.pid` больше не являются доказательством владения: launcher
считает их read-only конфликтом, не принимает sibling uvicorn/Vite по сходной
command line и требует явного разбора legacy evidence. При active exact или
нечитаемом full-stack owner команды
`run_local start/stop/restart/restart-backend/reset-db` и недиагностический
`cleanup-simulator` завершаются отказом и не пытаются эвристически принять,
остановить либо изменить данные процесса. В обратную сторону full-stack launcher
считает listener без своей exact metadata конфликтом и также ничего не
останавливает. `run_real_simulator start` запускает свой Vite как непосредственно
владеемый background-процесс и освобождает lifecycle mutex после startup;
отдельный `run_real_simulator stop` сверяет его JSON, listener PID и start
fingerprint, затем останавливает только этот UI и именованные Compose-сервисы
`app`, `redis`, `db`. Он не останавливает процессы `run_local`; ошибка Compose
stop передаётся вызывающему коду и не сопровождается ложным сообщением об успехе.
При `run_real_simulator start` ownership-конфликт и зависимости UI проверяются до
Compose mutation, а прежний exact-owned UI на том же порту останавливается только
после успешных Compose readiness и seed, непосредственно перед заменой. Ошибка до
этого момента сохраняет прежний UI. После начала same-port замены zero-downtime не
обещается: при ошибке Vite новый exact-owned UI и только Compose-сервисы, которых
не было в исходном running baseline, откатываются. Уже работавшие Compose-сервисы
не останавливаются. Однако `docker compose up --build` может пересоздать уже
работавший baseline-контейнер; launcher не восстанавливает его прежний image/config
и при поздней ошибке оставляет этот контейнер в текущем наблюдаемом Compose-состоянии.
Изменения данных от seed также не являются транзакцией launcher-а.
Для handoff сначала
остановите stack его собственным launcher-ом, проверьте `status`/порты, затем
запускайте другой entrypoint. `run_local` может
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
в диагностике сохраняются обе причины. Если более поздний этап full-stack startup
завершается ошибкой, launcher в обратном порядке откатывает только сервисы,
которые успел запустить в этой попытке, и только после повторного exact identity
proof; primary и rollback failure сохраняются раздельно. Тот же контракт
действует для `run_local start` и `restart-backend`: после успешной фиксации
ownership любой поздний health/config/UI failure откатывает только процессы
текущей попытки в обратном порядке, сохраняя отдельно primary и rollback evidence.

`DATABASE_URL` обязателен и принимается только как `postgresql+asyncpg://...`
(программа 017): без него backend не стартует; лаунчеры выставляют URL своей базы
`geov0_dev_<DbSlug>`. Прежние SQLite-файлы (`./geov0.db`, `.local-run/geov0.db`) не
читаются, не мигрируются и не удаляются. См. [справочник конфигурации](config-reference.md).

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

`ENV` — канонический ключ окружения. Поддерживаемый legacy `ENVIRONMENT`
участвует в проверке конфликта; неподдерживаемое значение legacy игнорируется при
явном `ENV`, но без `ENV` приводит к точной startup-ошибке конфигурации. Это
правило одинаково для process environment, `.env` и constructor input Pydantic.

Base Compose собирает канонический production-like образ из `docker/Dockerfile`:
он копирует приложение по явному allowlist. Dev overlay переключается на корневой
`Dockerfile` с `COPY . .` для reload; его context ограничивает `.dockerignore`, в
том числе для `.local-run`, `.venv`, любых `node_modules` и `*.db`. Blocking CI job
`dev-image-content` перед сборкой создаёт sentinel-файлы этих классов и проверяет
содержимое `/app` внутри готового образа. Зелёный source-policy тест без фактической
сборки не заменяет этот job.

### Health и perimeter transport

- `/healthz` и `/api/v1/healthz` — liveness: HTTP 200, пока процесс отвечает.
- `/health` и `/api/v1/health` — readiness: `status: degraded` сопровождается
  HTTP 503. Оба Docker-образа используют этот путь в `HEALTHCHECK`; Docker
  помечает контейнер `unhealthy`, но сам по себе не обещает restart.
- `/health/db` и `/api/v1/health/db` публично сообщают только reachable/latency и
  не возвращают exception text или dialect. Полная диагностика доступна как
  `GET /api/v1/admin/health/db` только с `X-Admin-Token`.
- WebSocket `/api/v1/ws` принимает access token через
  `Sec-WebSocket-Protocol: bearer, <access_token>`. Legacy `?token=` отклоняется,
  потому что uvicorn/proxy могут журналировать request path. Для браузера:
  `new WebSocket(url, ["bearer", accessToken])`.

## Миграции и данные

Alembic управляет схемой. Уже применённые migrations не переписываются; новая
схема доставляется новой migration и проверенным upgrade path. Seed и fixture
команды предназначены для demo/dev, не для восстановления production-данных.

Перед любым reset необходимо отдельно доказать, что URL указывает на disposable
test DB. `GEO_TEST_ALLOW_DB_RESET=1` запрещено использовать с developer или
production DB.

### Предусловие на свежей базе: ширина `alembic_version.version_num`

Alembic создаёт `alembic_version.version_num` как `VARCHAR(32)`, а идентификаторы ревизий
здесь длиннее — до 46 символов.

**С 2026-09-21 (`T1701`) предусловие выполняет сам вход миграций.** `migrations/env.py` создаёт
или расширяет колонку до `VARCHAR(128)` и коммитит это до прогона ревизий, поэтому голая
`alembic -c migrations/alembic.ini upgrade head` на пустой PostgreSQL-базе доходит до головы, и
делать руками ничего не нужно — ни в контейнере, ни вне его. Отказ предусловия прерывает прогон:
миграции не начинаются, и сообщение называет инструкцию и ошибку драйвера.

**История, а не текущее поведение.** До 2026-09-21 то же DDL стояло тремя копиями
(`docker/docker-entrypoint.sh`, `tests/migrated_schema.py` и фикстура `container-smoke` в
`.github/workflows/quality.yml`), а вход миграций его не имел — и голая команда падала на переходе
010 → 011 с `StringDataRightTruncationError: value too long for type character varying(32)`
(воспроизведено 2026-09-20). Если вы накатываете ревизии инструментом, который **обходит**
`migrations/env.py`, предусловие придётся выполнить самому:

```sql
CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(128) NOT NULL PRIMARY KEY);
ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(128);
```

Опции Alembic это не решает: ширину колонки версии он настраивать не умеет — параметры
`EnvironmentContext.configure` ограничены `version_table`, `version_table_pk` и
`version_table_schema`, а тип захардкожен.

### Переход на миграцию `029` — только с остановкой писателей

Миграция `029_debt_journal_by_the_database` (программа 018, стадия B) передаёт запись журнала долгов
базе: триггеры на `debts` и трёх журнальных таблицах, `ordinal` вместо `flush_ordinal`, без
`flush_count`, новые конверты операций — `schema_version = 2`. Старый код пишет `flush_count` и не
выставляет контекст операции, поэтому смешанной работы старого и нового кода нет, и онлайн-переход
не обещается:

1. остановить всех писателей: backend (`run_local.ps1 stop` / `run_full_stack.ps1 stop` или
   контейнер), симулятор, любые сидеры (`scripts/seed_db.py`);
2. применить миграцию: `alembic -c migrations/alembic.ini upgrade head`. Миграция **отказывает**,
   если в базе есть конверт в состоянии `OPEN` (операция, прерванная остановкой, — её надо разобрать
   до перехода, а не обойти);
3. запустить новый код.

**Откат (`downgrade` ниже `029`) отказывает, пока в базе есть хотя бы один конверт
`schema_version = 2`** — то есть после первой же операции нового кода: историю, записанную
триггером, в старую схему без потерь не вернуть. Без таких конвертов откат восстанавливает старую
форму. Откат после работы нового кода — восстановление из резервной копии, снятой до шага 2, а
резервное копирование этим репозиторием не поставляется (см. «Непокрытые production-обязанности»).

### Переход на миграцию `030` — осушение старой версией, затем ограда

Миграция `030_payment_rows_are_terminal` (программа 019, стадия 4, 2026-09-25) сопровождает код, который
исполняет платёж одной транзакцией и не сохраняет промежуточных состояний: строка `PAYMENT` бывает
только `COMMITTED` или `ABORTED`, фонового восстановления застрявших платежей больше нет. Миграция
ставит немедленный CHECK `chk_transaction_payment_terminal`
(`type <> 'PAYMENT' OR state IN ('COMMITTED','ABORTED')`) — **ограду устаревшей записи платежа**:
старый бинарник упадёт уже на своей первой вставке `NEW`. Клиринг (свой `NEW`) и прочие типы
транзакций вне ограды. Смешанная работа версий не поддерживается, поэтому переход — с остановкой:

1. остановить приём (API, симулятор, сидеры) и дождаться завершения или отката текущих писателей —
   платежей, клиринга, тиков, admin abort;
2. под **старым** бинарником дать восстановлению (`RECOVERY_ENABLED`, цикл `app/core/recovery.py`
   старой версии) довести **все** платежи до `COMMITTED`/`ABORTED` и опустошить `prepare_locks`.
   Проверка:
   `SELECT state, count(*) FROM transactions WHERE type = 'PAYMENT' AND state NOT IN ('COMMITTED','ABORTED') GROUP BY state;`
   — пусто, `SELECT count(*) FROM prepare_locks;` — `0`;
3. остановить старый процесс и убедиться, что старых писателей не осталось;
4. применить миграцию (`alembic -c migrations/alembic.ini upgrade head`) и развернуть новый код.
   Миграция **отказывает**, пока хоть один `PAYMENT` не терминален или есть строка `prepare_locks`,
   и называет их число по состояниям: решать судьбу незавершённого платежа — дело восстановления
   старой версии, а не миграции схемы, поэтому сама она ничего не терминализует. Таблицы
   блокируются от записи на время проверки и постановки ограничения;
5. возобновить работу одной совместимой версии.

**Откат (`downgrade` ниже `030`) разрешён** под той же остановкой: он снимает только ограду и
возвращает схему к `029` для реализации до программы 019. Таблица `prepare_locks` этой миграцией не
трогается (её удаляет и на откате восстанавливает `031` стадии 5).

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
