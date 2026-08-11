# 003 — Clearing transaction ownership and durable commit

- **Date:** 2026-08-11
- **Status:** IN PROGRESS — Wave 4 authorized by owner on 2026-08-11; T300 complete
- **Status authority:** эта метка описательная. Завершённость устанавливают только success criteria, записанные evidence и принятые review-артефакты ниже.
- **Owner surface:** `app/core/clearing/`, `app/core/recovery.py`, публикация клиринга в `app/core/simulator/`
- **Origin:** программа 002 явно вынесла эти находки за свой скоуп (`specs/002-payment-integrity-follow-up/tasks.md:78-89`) с формулировкой «requires a separately approved program rather than silent scope expansion». Эта спека — тот самый отдельный дом.
- **Depends on:** 002 Phase 1 (P104) даёт канонический неупорядоченный ключ пары, который вариант A ниже переиспользует. Формально не блокирует, но выбор решения зависит.

## Problem

Клиринг владеет транзакцией и блокировками неявно. Он берёт row locks и коммитит сам, но при этом:
раннее возвращение оставляет блокировки на вызывающем, коммит не различает «зафиксировано» и
«подтверждено», а публикация в симулятор отдаёт не то число, которое реально было заблокировано.
Каждая из четырёх находок ниже — отдельное следствие одной причины: **границы транзакции клиринга
нигде не заданы явно**.

## Owner surface

Входит: `app/core/clearing/service.py`, `app/core/recovery.py:126-148`,
пути публикации клиринга в `app/core/simulator/` и `app/api/v1/simulator.py:1644-1660`.

Не входит: идентичность advisory-ключей платежа и clearing/payment interlock — это 002.
Общий рефакторинг клиринга — прямо запрещён (наследуется от `002/spec.md:194`).

## Findings

| ID | Sev | Находка | Evidence |
|---|---|---|---|
| **F-003-1** | P2 | **Ambiguous durable commit.** `execute_clearing_with_amount` коммитит напрямую; при потере подтверждения/отмене вызывающий не может отличить «зафиксировано» от «не зафиксировано» | `app/core/clearing/service.py:1079`, `:1076-1097`; регистрация как независимого P2 — `002/phase0-evidence-map.md:156-172` |
| **F-003-2** | P2 | **Skip-пути удерживают row locks.** `select(Debt)…with_for_update()` выполняется **до** проверок пропуска; пути возврата `None` не делают ни commit, ни rollback → блокировки живут через цикл `auto_clear` → конкурентный платёж упирается в `lock_timeout` и получает терминальный E007 | FOR UPDATE `clearing/service.py:828-836`; `return None` на `:841`, `:847`, `:876`, `:894`; циклы `service.py:1143-1181`, `app/api/v1/simulator.py:1644-1660`. Runtime-демонстрация во второй сессии аудита |
| **F-003-3** | P3 | **Stale clearing-volume publication.** Симулятор публикует candidate amount вместо реально заблокированной суммы, хотя корректный API уже существует и уже используется на соседнем пути | Кандидат считается по рёбрам цикла **до** исполнения: `app/core/simulator/real_clearing_engine.py:258-266` (`clear_amount = min(amts) if amts else Decimal("0")`). Исполнение идёт через bool-обёртку `:290` (`success = await service.execute_clearing(cycle)`), которая реальную сумму отбрасывает. Дальше публикуется именно кандидат: накопление `:318-319`, разнос по рёбрам `cleared_amount_per_edge` `:334-336`, публикация `:357` и `:581`, эмиссия `:562` и `:607-608`, возврат `:649`. Правильный API уже есть — `app/core/clearing/service.py:790` `async def execute_clearing_with_amount(...) -> Decimal \| None` (докстринг `:791-794`), он считает `clear_amount = min([d.amount for d in debts])` (`:844`) **после** `with_for_update()` (`:831`); `execute_clearing` (`:786-788`) — явно обратно-совместимая bool-обёртка над ним. `app/api/v1/simulator.py:1652` **уже** использует корректный вызов (`clear_amt = await service.execute_clearing_with_amount(cycle)`) |
| **F-003-4** | P3 | **Redundant post-abort lock cleanup.** `engine.abort` на пути `already_committed` удаляет те же `PrepareLock` **дважды** и оба раза коммитит; повторная очистка в recovery — строгое подмножество уже удалённого | `app/core/payments/engine.py:1455-1468` (терминальная проверка до ожидания) и `:1520-1533` (повторная проверка после ожидания) — обе ветки делают `delete(PrepareLock).where(PrepareLock.tx_id == tx_id)` и затем `await self.session.commit()` при `commit=True`. Recovery зовёт `abort(...)` без явного `commit` (`app/core/recovery.py:109-114`), а дефолт сигнатуры — `commit: bool = True` (`engine.py:1414`), поэтому удаление зафиксировано **до** возврата из `abort`. Собственное удаление recovery (`recovery.py:133-135`) — `WHERE id IN (expired_lock_ids)`, то есть строгое подмножество того, что `abort` уже удалил по `tx_id` |

**F-003-3: цена исправления.** Дефект локализован в одной строке вызова:
`real_clearing_engine.py:290` — единственный оставшийся путь, который зовёт lossy-обёртку
`execute_clearing`. Соседний путь (`api/v1/simulator.py:1652`) уже мигрирован. Поэтому severity
**P3, вероятно, занижена**: это не «дорогая правка низкого приоритета», а однострочная замена
вызова плюс замена `clear_amount` на возвращённое значение в точках `:318-319` и `:334-336`.
Постоянная неверная публикация объёма при цене фикса в одну строку вызова — аргумент за пересмотр
severity, а не за откладывание.

**F-003-4: почему находка сохранена вопреки внешнему ревью.** Внешнее ревью потребовало удалить
эту находку как ложную. Независимая проверка показала, что ревьюер **неправ**, и находка оставлена.
Дополнительное evidence второго порядка:

- Инлайн-комментарий `recovery.py:126-128` **фактически ложен**: он утверждает «An already-committed
  transaction returns before that cleanup», тогда как `abort` возвращается **после** удаления
  и коммита. Комментарий — источник ошибки, а не её опровержение.
- Избыточность замаскирована недостоверным тест-даблом: `tests/unit/test_recovery_cleanup.py:355-381`
  монкипатчит `PaymentEngine.abort` фейком, который на `already_committed` возвращает исход
  **без удаления блокировок**. Тест recovery по построению не может увидеть мёртвый код.
- Реальное поведение `abort` при этом уже закреплено тестом:
  `tests/unit/test_payments_2pc.py:76-109` (`test_abort_is_noop_when_already_committed`) утверждает,
  что после `abort` на COMMITTED-транзакции блокировок не остаётся.
- Порядок в git подтверждает: удаление на стороне `abort` появилось в `a334c46` (2026-08-07),
  блок в recovery — в `d2c614a` (2026-08-09), то есть написан уже поверх покрытого случая.

Смежная находка **trust-drift in-memory mutation before commit** (`phase0-evidence-map.md:187`)
принадлежит поверхности trustlines, а не клиринга — вынесена в [`../BACKLOG.md`](../BACKLOG.md).

## Current / Intended / Optimal

**Current.** Клиринг берёт `FOR UPDATE` на строки цикла, считает сумму, затем читает `PrepareLock`
и на части ветвей выходит через `return None`, не завершив транзакцию. Коммит выполняется внутри
сервиса без протокола подтверждения. Публикация берёт число из кандидата, а не из результата.

**Intended.** Контракт `docs/ru/simulator/backend/payment-integration.md:70` требует, чтобы
подготовленные сегменты не клирились под активным платежом. Сегодня это обеспечивается
несинхронизированным чтением, а не владением транзакцией.

**Optimal.** Одна явная граница владения транзакцией на входе в клиринг: либо клиринг владеет
своей транзакцией целиком и все ветви выхода её завершают, либо владение передаётся вызывающему
и клиринг не коммитит вовсе. Смешивать нельзя. Плюс идемпотентный протокол подтверждения, при
котором повторный вызов после потери подтверждения детерминированно распознаёт уже зафиксированный
результат.

## Non-goals

- Изменение доменной семантики клиринга, состава циклов или алгоритма выбора суммы.
- Расширение на payment lock identity (это 002 Phase 1).
- Рефакторинг клиринга сверх минимального, необходимого для границ транзакции.

## Verification plan

Все проверки — на реальном PostgreSQL; SQLite не воспроизводит семантику блокировок.

1. Детерминированный тест: клиринг уходит в skip-ветку → конкурентный платёж на тех же строках
   завершается успешно, а не через `lock_timeout`/E007. Должен падать на текущем коде.
2. Тест на каждую из четырёх веток `return None` (`:841`, `:847`, `:876`, `:894`) — проверка, что
   транзакция завершена и блокировки отпущены.
3. Тест потери подтверждения: прерывание после коммита → повторный вызов не создаёт второй эффект
   и корректно сообщает исход.
4. Проверка публикации: опубликованный объём равен реально заблокированной сумме, а не кандидату.
5. Существующие селекторы клиринга и `tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py`
   без регрессий.

Запрещено: синтетическая инъекция DBAPI-ошибок вместо реального расписания (наследуется из 002 P102).

## Tasks

| ID | Задача | Статус |
|---|---|---|
| T300 | Детерминированный PG-репродьюсер удержания блокировок на skip-пути | `[x]` |
| T301 | Инвентаризация всех веток выхода `clearing/service.py`, явное решение по владению транзакцией | `[x]` |
| T302 | Реализация выбранной границы владения; все ветви выхода завершают транзакцию | `[x]` |
| T303 | Идемпотентный протокол подтверждения коммита клиринга | `[x]` |
| T304 | Публикация реально заблокированной суммы вместо кандидата: перевести `real_clearing_engine.py:290` с bool-обёртки `execute_clearing` на `execute_clearing_with_amount` и провести возвращённую сумму в `:318-319` и `:334-336` (F-003-3) | `[x]` |
| T305 | Устранение повторной очистки lock в recovery (`recovery.py:131-136`) и исправление ложного комментария `recovery.py:126-128`; тест-дабл `tests/unit/test_recovery_cleanup.py:355-381` привести в соответствие с реальным поведением `abort` (F-003-4) | `[x]` |
| T306 | Синхронизация `docs/ru/simulator/backend/payment-integration.md` и решений в `docs/ru/09-decisions-and-defaults.md` | `[x]` |
| T307 | Независимое ревью и публикация evidence на точном HEAD | `[!]` |

Легенда: `[x]` выполнено, `[ ]` в работе, `[!]` заблокировано/не авторизовано.

## Changelog

### 2026-08-11 — T300

- RED-test commit: `eb4cac2`. Перед добавлением теста актуальные анкоры подтвердились без
  смыслового расхождения: `FOR UPDATE` находится в `app/core/clearing/service.py:831`, а
  policy-skip возвращает `None` на `:894`, не завершая открытую транзакцию.
- После: реальное PostgreSQL-расписание находится в
  `tests/integration/test_clearing_skip_releases_locks_postgres.py:18-229`. Оно оставляет
  policy-skip session открытой, запускает полный `PaymentService` на общей Debt-строке и требует
  реальный результат `COMMITTED`/изменение Debt `100 -> 105`; DBAPI-ошибка не инъецируется.
- Canonical RED:
  `$env:DEBUG='false'; $env:ENV='test'; $env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@localhost:55433/geov0_test_wave4_003'; $env:GEO_TEST_ALLOW_DB_RESET='1'; .\scripts\verify_local.ps1 -TaskSlug wave4_003_t300_red4 -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_clearing_skip_releases_locks_postgres.py`
  — exit `1`, `1 failed`; точный actual:
  `clearing policy skip retained Debt row locks: actual=TimeoutException code=E007 status=504`.
  Cache собрал один ожидаемый nodeid. Pinned Ruff для нового файла и `git diff --check` — exit `0`.

### 2026-08-11 — T301

- Повторная проверка на текущем HEAD:
  `rg -n "execute_clearing\\(|execute_clearing_with_amount\\(|auto_clear\\(" app --glob '*.py'`
  и
  `rg -n "return None|with_for_update|await self\\.session\\.(commit|rollback)" app/core/clearing/service.py`
  — обе команды exit `0`. Production-callers исчерпываются REST `app/api/v1/clearing.py:31-44`,
  interactive simulator `app/api/v1/simulator.py:1651-1659`, real background clearing
  `app/core/simulator/real_clearing_engine.py:113-126,290` и compatibility wrapper/loop
  `app/core/clearing/service.py:786-788,1099-1181`.
- Полный inventory выходов `execute_clearing_with_amount`: pre-SQL `None` на
  `app/core/clearing/service.py:801,825`; `FOR UPDATE` на `:831`; post-lock `None` на
  `:841,847,876,894`; success-коммит и durable return на `:1079,1094`; ordinary exception идёт
  через rollback helper `:27-40,1096-1097`; `CancelledError` локально не терминализируется.
- **Current:** сервис уже владеет успешной транзакцией и ordinary-failure rollback, а все три
  caller surface трактуют возвращённый amount/bool как durable; незавершёнными остаются `None` и
  cancellation/commit-ack paths. **Intended:** каждая попытка клиринга имеет одну явную границу,
  освобождает блокировки на любом исходе и не публикует неподтверждённый результат. **Optimal:**
  сохранить service-owned UoW без `commit=False`: success означает durable commit, каждый skip
  rollback'ит attempt, ambiguous commit разрешается внутри сервиса. Caller-owned вариант отвергнут
  как более широкий: он потребовал бы менять все три owners и превратил бы существующий amount в
  provisional result без продуктовой выгоды.
- Product code до/после T301 не менялся. Решение закреплено этой записью в
  `specs/003-clearing-transaction-ownership/spec.md:132-157`; отдельный caller-risk для T302:
  rollback истекает ORM state, поэтому interactive path должен кэшировать `eq.code` до первого
  вызова сервиса. `git diff --check -- specs/003-clearing-transaction-ownership/spec.md` — exit `0`.

### 2026-08-11 — T302

- Implementation commit: `f3857ff`. До изменения `FOR UPDATE` был на
  `app/core/clearing/service.py:831`, а шесть `None`-выходов на `:801,825,841,847,876,894` не
  завершали service-owned attempt. После изменения единый rollback boundary находится на
  `app/core/clearing/service.py:43-50`; все выходы вызывают его на
  `:809,834,851,858,888,907` перед `return None`.
- Interactive caller больше не читает expired ORM после rollback: строковый code кэшируется на
  `app/api/v1/simulator.py:1620` и используется во всех дальнейших find/publish/log/response paths.
- Real-PG coverage находится в
  `tests/integration/test_clearing_skip_releases_locks_postgres.py:23-163,167-378`. Параметры
  `empty/malformed/missing/nonpositive/locked/policy` доказывают отсутствие активной транзакции;
  `nonpositive` использует реальную production-конфигурацию `autoflush=False` и dirty identity-map,
  не нарушая PostgreSQL `CHECK amount > 0`. Отдельное расписание доказывает реальный concurrent
  payment effect `Debt 100 -> 105`, поэтому rollback-проверка не проходит вхолостую.
- Первый GREEN-attempt с hostname `localhost` (`wave4_003_t302_t300_green`) сохранён как exit `1`:
  reconnect в `PaymentEngine.commit` был отменён внутри `asyncpg.connect` по total timeout, и тест
  снова наблюдал E007. Это не было принято как доказательство исправления. Тот же canonical selector
  с явным `127.0.0.1` (`wave4_003_t302_t300_green_ipv4`) — exit `0`, `1 passed`.
- Финальный PG milestone:
  `$env:DEBUG='false'; $env:ENV='test'; $env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@127.0.0.1:55433/geov0_test_wave4_003'; $env:GEO_TEST_ALLOW_DB_RESET='1'; $selectors=@('tests/integration/test_clearing_skip_releases_locks_postgres.py','tests/integration/test_concurrent_clearing_payment_lost_update_postgres.py'); .\scripts\verify_local.ps1 -TaskSlug wave4_003_t302_pg_exact -BackendOnly -BackendMarker postgres -BackendSelector $selectors`
  — exit `0`, `8 passed`, восемь ожидаемых nodeids.
- Caller/unit matrix `wave4_003_t302_units` (восемь clearing/interact/real-engine selectors) —
  exit `0`, `64 passed`. Pinned Ruff на трёх изменённых файлах и `git diff --check` — exit `0`.

### 2026-08-11 — T303

- Implementation commit: `94fe24f`. До изменения каждая попытка создавала случайный clearing
  `tx_id`, напрямую ожидала `session.commit()` и возвращала amount только после подтверждения
  (`app/core/clearing/service.py` до коммита: прежние `:1076-1094`); после потери подтверждения
  тот же цикл видел уже удалённую Debt и возвращал `None`.
- После: unordered set Debt UUID получает стабильный UUIDv5 execution identity
  (`app/core/clearing/service.py:64-66`), а durable `Transaction.payload.amount` разрешается до
  повторного эффекта и после ожидания row locks (`:68-87,891-899,917-925`). `Transaction.tx_id` и
  `idempotency_key` используют эту identity (`:1064-1071`), поэтому audit и денежный эффект имеют
  одну occurrence-запись.
- Commit дренируется отдельно от caller cancellation (`service.py:89-105,1168`); если commit уже
  durable, вызывающий получает типизированный `ClearingCommittedAfterCancellation` с `tx_id` и
  фактическим `cleared_amount` (`:29-36,1183-1187`). Это сохраняет cancellation-сигнал и даёт T304
  точный результат для публикации, не угадывая его по candidate.
- Real-PG test `tests/integration/test_clearing_commit_replay_postgres.py:18-214` сначала выполняет
  настоящий commit, задерживает только возврат подтверждения, отменяет caller и повторяет тот же
  Debt-ID-set в обратном порядке. RED `wave4_003_t303_red` — exit `1`, `1 failed`, exact
  `assert None == Decimal('30.00000000')`; одновременно pinned Ruff честно нашёл и затем был
  исправлен один `F401` в новом тесте.
- GREEN `wave4_003_t303_green1` — exit `0`, `1 passed`: replay возвращает `30`, создаёт ровно одну
  первую Transaction/audit/effect; новый cycle с новым Debt UUID возвращает `5`, поэтому
  idempotency не поглощает новую occurrence (`test:152-178,206-214`).
- Финальный PostgreSQL milestone `wave4_003_t303_pg_exact` по commit-replay, всем skip branches и
  clearing/payment contention — exit `0`, `9 passed`; unit/caller matrix
  `wave4_003_t303_units` — exit `0`, `64 passed`. Pinned Ruff и `git diff --check` — exit `0`.

### 2026-08-11 — T304

- Implementation commit: `c759558`. Перед изменением находка снова подтверждена: candidate
  вычислялся из входных edge на `app/core/simulator/real_clearing_engine.py:258-278`, lossy
  `execute_clearing` вызывался на прежней `:290`, candidate попадал в aggregate/per-edge на
  прежних `:318-319,334-336` и затем в `clearing.done`.
- После: входное число честно называется только `candidate_amount` и используется для diagnostic
  log (`real_clearing_engine.py:269-290`). Durable `actual_amount` приходит из
  `execute_clearing_with_amount` (`:294-301`) и только оно попадает в aggregate и per-edge
  (`:327-349`), следовательно также во все последующие return/trust-growth/SSE surfaces.
- Post-commit cancellation carrier из T303 теперь потребляется обоими publishers до повторного
  raise: real engine записывает amount/edges и выпускает fallback `clearing.done`
  (`real_clearing_engine.py:294-364,590-636`), interactive action —
  `app/api/v1/simulator.py:1665-1698`. Cancellation остаётся cancellation, durable progress не
  теряется.
- RED `wave4_003_t304_red` — exit `1`, `5 failed`: при candidate `11` и фактическом service-result
  `5` actual был `{'USD': 11.0}` и SSE `cleared_amount='11.00'`; initial committed-cancellation
  вообще не доходила до accounting. Это прямые actual/expected, не fixture под старую строку.
- Behavioral tests `tests/unit/test_real_clearing_engine_partial_failure.py:77-212` проверяют
  aggregate `5`, per-edge `{('bob','alice'): 5.0}`, SSE `5.00`, partial failure и cancellation;
  interactive carrier покрыт на `tests/unit/test_interact_actions_backend_p1.py:1174-1329`.
  Оба service doubles переведены на amount API.
- Targeted GREEN `wave4_003_t304_green2` — exit `0`, `34 passed`; отдельный amount-surface selector
  `wave4_003_t304_amount_surfaces` — exit `0`, `5 passed`; финальная восьмифайловая caller matrix
  `wave4_003_t304_units_exact` — exit `0`, `66 passed`. Pinned Ruff и `git diff --check` — exit `0`.

### 2026-08-11 — T305

- Implementation commit: `356a09e`. Перед изменением текущие Phase-1 anchors проверены заново:
  обе `already_committed` ветви `PaymentEngine.abort` удаляют все `PrepareLock` по `tx_id` и при
  `commit=True` коммитят до возврата (`app/core/payments/engine.py:1668-1685,1736-1753`). Recovery
  после этого повторно удалял observed subset и ещё раз коммитил на прежних
  `app/core/recovery.py:126-148`.
- После: recovery делегирует terminal lock ownership engine один раз и только учитывает заранее
  observed IDs (`app/core/recovery.py:109-131`). Ложный комментарий заменён точным контрактом на
  `:126-129`; лишние DELETE/commit и ставший мёртвым import удалены. PaymentEngine не менялся.
- Test double теперь воспроизводит реальный `abort`: и `success`, и `already_committed` удаляют все
  locks и durable-коммитят (`tests/unit/test_recovery_cleanup.py:384-391`). Счётчик SQL DELETE
  (`:372-402`) — anti-vacuum: ровно два engine-owned вызова для двух tx, не ноль.
- RED `wave4_003_t305_red` — exit `1`, `1 failed`, exact `assert 3 == 2`: третий DELETE принадлежал
  recovery. GREEN selector по полному recovery-файлу и реальному payment terminal countercheck
  `wave4_003_t305_green` — exit `0`, `9 passed`. Pinned Ruff и `git diff --check` — exit `0`.

### 2026-08-11 — T306

- Documentation commit: `8dfc6a9`. До изменения профильный документ описывал только endpoints,
  PrepareLock snapshot guard и общий `clearing.done` (`payment-integration.md:68-77,235-247`), а
  decision registry не фиксировал owner/replay/amount contracts.
- После: service-owned UoW и UUIDv5 replay identity записаны в
  `docs/ru/simulator/backend/payment-integration.md:73-82`; actual amount и post-commit cancellation
  publication — на `:246-257`. Каноническое решение находится в
  `docs/ru/09-decisions-and-defaults.md:250-267` и также фиксирует единственного owner очистки
  terminal PrepareLock — `PaymentEngine`.
- Reference scan
  `rg -n "владеет одной попыткой|UUIDv5|ClearingCommittedAfterCancellation|actual amount|1\\.12\\.1|recovery не повторяет" docs/ru/simulator/backend/payment-integration.md docs/ru/09-decisions-and-defaults.md`
  — exit `0`, все шесть контрактов найдены. `git diff --check` для обоих документов — exit `0`.
  Пользовательский metadata-hunk в начале `09-decisions-and-defaults.md` намеренно не вошёл в
  commit и остаётся в рабочем дереве.
