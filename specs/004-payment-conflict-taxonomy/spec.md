# 004 — Payment conflict and cancellation taxonomy

- **Date:** 2026-08-11
- **Status:** IN PROGRESS — T400 закрыта; T401-T408 авторизованы 2026-08-11
- **Status authority:** метка описательная; завершённость устанавливают success criteria и записанные evidence.
- **Owner surface:** `app/core/payments/service.py`, `app/core/payments/engine.py` (только обработка ошибок, **не** идентичность блокировок), `app/main.py` exception handlers, `app/api/v1/payments.py`, `app/api/v1/simulator.py:1472-1520` (вызов `create_payment_internal` и три его обработчика: `RoutingException` `:1482`, `TimeoutException` `:1506`, `GeoException` `:1513`), `app/core/simulator/real_payments_executor.py:365-374` (staged-вызов)
- **Severity driver:** содержит **A/F1** — P2, не упомянутый ни в одном документе репозитория до этой спеки. Механизм находки проверяем статически по коду и на этом стоит целиком. Runtime-прогон по нему был записан агентом аудита (`_audit/reports/slice_a.md:24`), но производивших его скриптов (`probe_reverse.py` и инлайновый скрипт захвата) в репозитории нет — цифру нельзя перегенерировать, поэтому она цитируется как **историческое evidence, а не как воспроизводимое измерение** (`AGENTS.md:98`: `_audit/` — «историческое evidence, не текущая спецификация»).

## Problem

Платёжный контур умеет ретраить конфликты сериализации, но только там, где он их видит.
На границах — в best-effort блоках, в guard'ах по конкретному типу исключения, в маппинге в HTTP —
конфликт либо проглатывается, либо теряет тип. Результат: штатный `40001`, который должен был
привести к ретраю, превращается в постоянный отказ или в нетипизированную 500.

**Ключевая находка A/F1 подробно.** Блок аудита целостности внутри unit of work обёрнут в
`except Exception: continue` / `except Exception: pass`. Если внутри него PostgreSQL поднимает
`40001`, исключение проглатывается, но транзакция уже отравлена. Следующая операция —
`DELETE FROM prepare_locks` — падает с `25P02` (in_failed_sql_transaction). Кода `25P02` **нет**
в retry-предикате, поэтому whole-UoW retry не срабатывает, и платёж уходит в **терминальный ABORT
навсегда**. Это не деградация производительности: конкурентный платёж просто перестаёт быть
исполнимым.

## Owner surface

Входит: обработка исключений, ретрай-предикат и маппинг ошибок в платёжном контуре и его **трёх**
потребителях. Все три приходят в один и тот же `_create_payment_impl` (`app/core/payments/service.py:258`):

1. `create_payment` (`service.py:160-173`) ← `app/api/v1/payments.py:84` — `POST /api/v1/payments`;
2. `create_payment_internal` (`service.py:175-219`) ← `app/api/v1/simulator.py:1474` — интерактивный путь симулятора;
3. `create_payment_internal_staged` (`service.py:221-256`, зовёт impl на `:245` с `commit=False`)
   ← `app/core/simulator/real_payments_executor.py:369` — staged-путь реального тика симулятора.

Третий потребитель **не пересекает HTTP-границу**: он in-process, внутри `async with
session.begin_nested()` (`real_payments_executor.py:367`). Любое решение, живущее только в
HTTP-слое, его не покрывает.

Не входит: идентичность advisory-ключей и порядок их захвата — это **002 Phase 1**. Пересечение
по файлу `engine.py` разрешается так: 004 трогает только блоки обработки исключений и предикат
`:231-251`; 002 трогает `_segment_lock_key` и `_acquire_*`. Одновременные волны 002 и 004 по
`engine.py` запрещены (AGENTS.md §15).

## Findings

| ID | Sev | Находка | Evidence |
|---|---|---|---|
| **F-004-1 (A/F1)** | **P2** | Best-effort audit-блок проглатывает `40001` → транзакция отравлена → следующий DELETE даёт `25P02`, которого нет в retry-предикате → терминальный ABORT `tx_id` | Статические якоря, на которых находка стоит целиком: `engine.py:1149-1152` (`except Exception: continue` / `except Exception: pass`), DELETE `:1155-1156`, предикат `:240-251` (`return sqlstate in {"40P01", "40001"} …` — `25P02` отсутствует). Runtime-наблюдение зафиксировано в `_audit/reports/slice_a.md:24`; harness отсутствует в репозитории, поэтому цитируется как историческое evidence, а не как измерение (см. Severity driver выше) |
| **F-004-2 (A1-narrow)** | P3 | Вставка tx-строки защищена только `except IntegrityError`; внешний `try` (`:461`) ловит лишь `asyncio.TimeoutError` (`:811`) → `40001` на `session.commit()` уходит голой Starlette 500. Три потребителя. Отказ **временный**: ничего не сохраняется, ретрай с тем же `tx_id` чист (разбор ниже) | `service.py:543-553`, commit `:545`; потребитель 2 — `simulator.py:1472-1520` (только `RoutingException` `:1482`, `TimeoutException` `:1506`, `GeoException` `:1513`; `:1472-1481` — сам `try`-блок с вызовом); потребитель 3 — `real_payments_executor.py:369` через `create_payment_internal_staged`, ветка `commit=False` (`service.py:551-552`). Семантика описана в `002/spec.md:145`, но исполняемой задачи в `002/tasks.md` **нет** |
| **F-004-3 (A1-mapping)** | P3 | Понижено с P2 2026-08-11 по тому же основанию, что и F-004-2: конфликт сериализации не оставляет долговечного следа, поэтому потеря типа — дефект наблюдаемости и DX, а не целостности. Любой `DBAPIError`, включая `40001`, схлопывается в общий `GeoException`/`E010`/500 — нет типизированного кода retryable-конфликта | Схлопывание в `E010` происходит в `app/core/payments/service.py:600,618,668` / `:683,748,810` (строка `E010` в `app/main.py` не встречается вовсе). **Аргумент от отсутствия:** `app/main.py` регистрирует ровно два обработчика — `GeoException` (`:490`) и `RequestValidationError` (`:495`); обработчика для общего `Exception` **нет**, поэтому всё, что не `GeoException`, уходит голой Starlette-500, и типизировать конфликт на уровне приложения негде |
| **F-004-4 (N-A1)** | P3 | `CancelledError` — потомок `BaseException`, обходит `except Exception` на `:600`/`:683` и единственный `except asyncio.TimeoutError` на `:811`; `finally` нет → долговечные `NEW`/`PREPARED` без abort | `service.py:461,569,600,683,811` |
| **F-004-5 (A3b)** | P3 | Staged (`commit=False`) timeout-abort — `except Exception: pass`, без лога и без re-raise, тогда как соседняя ветка логирует `payment.timeout_abort_failed` | `service.py:871-873` против `:855-861` |
| **F-004-6 (A3)** | P3 | Голый `asyncio.shield` в timeout-abort; паттерн drain-to-terminal из renovation не перенесён на соседа | `service.py:847-861` |
| **F-004-7 (A4)** | P3 | Ретрая сериализации нет нигде за пределами платёжного движка; маппинг ошибок отсутствует у двух сервисов из трёх | у клиринга маппинг есть (`clearing/service.py:30-41` ← `:1096`); `trustlines/service.py:140` — голый commit вне `try`; `app/api/v1/integrity.py:66-69` — `except BaseException: rollback; raise` |

**F-004-2: почему P3, а не P2.** Когда `session.commit()` на `service.py:545` поднимает `40001`,
`self.session.add(new_tx)` на `:542` — **первая и единственная запись** в этом unit of work.
`PrepareLock`-и создаются позже, в `engine.prepare(...)` / `engine.prepare_routes(...)`
(`service.py:572-593`), строго после `tx_persisted = True` на `:569`; мутации `Debt` — ещё позже,
внутри `engine.commit` (`service.py:670-676`). Коммит атомарен, поэтому при `40001` не оседает
ничего. Выход из `async with AsyncSessionLocal() as session` (`app/db/session.py:85-87`) закрывает
сессию и откатывает незавершённую транзакцию. Ретрай с тем же `tx_id` при этом **чист**:
pre-insert lookup (`service.py:401-405`) ничего не находит (`.scalar_one_or_none()` → `None` на
`:405`), ветка `_resolve_existing_payment` (`:406-411`, определение `:116`) не срабатывает, и путь
идёт на свежую вставку — ни duplicate key, ни осиротевшего `PrepareLock`. Отказ временный,
клиент видит некрасивую 500, но состояние не портится → **P3**. Уродливость ответа остаётся
дефектом контракта (F-004-3), но не severity-драйвером.

**F-004-2: нюанс staged-пути (потребитель 3).** В ветке `commit=False` вставка идёт через
`async with self.session.begin_nested()` + `flush()` (`service.py:551-552`). Комментарий `:547-550`
обосновывает SAVEPOINT тем, что он «не даст `IntegrityError` отравить/закрыть внешний контекст» —
и для `IntegrityError` это верно. Но serialization failure отменяет **всю** PostgreSQL-транзакцию,
а не только savepoint: теряется внешняя транзакция тика симулятора
(`real_payments_executor.py:367`), то есть весь staged-батч тика, а не один платёж. Это **не**
возвращает P2 — по-прежнему ничего не зафиксировано и отказ временный, — но это корректная
формулировка радиуса поражения, и она обязана попасть в T403.

## Родственные инстансы паттерна (вне скоупа 004)

Паттерн F-004-1 («best-effort блок проглатывает исключение внутри живой транзакции») встречается
и за пределами платёжного движка. Здесь он зафиксирован **как реестр, а не как скоуп**: 004 их
не трогает. Внешнее ревью перечислило шесть инстансов; проверка список уточнила.

**Тот же паттерн, подтверждено:**

| Место | Что делает | Почему опасно |
|---|---|---|
| `app/core/clearing/service.py:964-969` | `except Exception:` + `logger.warning` + `checkpoint_before = None` | Логирует, но отравленная сессия дальше получает `self.session.add(new_tx)` (`:996`) и `flush()` (`:1014`) |
| `app/core/clearing/service.py:1022-1027` | то же для `checkpoint_after` | **Худший в наборе:** стоит **после** `flush()` на `:1014`, то есть после того, как декременты и удаления долгов (`:1004-1012`) уже сброшены в БД |
| `app/core/trustlines/service.py:137-138` | голый `except Exception: pass` | **Пропущен внешним ревью, но самый сильный в trustlines:** обёртка накрывает и запрос чекпойнта (`:112-115`), и вставку `IntegrityAuditLog` (`:121-136`), а сразу за ней идёт `await self.session.commit()` (`:140`) |
| `app/core/trustlines/service.py:81-82` | `except Exception: checkpoint_before = None`, без лога | Тихое проглатывание перед мутацией trustline |
| `app/core/trustlines/service.py:242-243` | `except Exception: pass` | То же на пути update |
| `app/core/trustlines/service.py:335-336` | `except Exception: pass` | То же на пути close |
| `app/api/v1/integrity.py:260-261` | `except Exception:` → подстановка старого `checksum` | Проглоченная ошибка БД из `compute_integrity_checkpoint_for_equivalent` (`:258`) отравляет request-сессию до `await db.commit()` на `:287` |

**Уточнение к внешнему списку.** Ревью назвало `app/api/v1/integrity.py:256` — это строка
комментария; реальный инстанс на `:260-261`. И **`integrity.py:284-285` паттерном не является**:
этот `try` накрывает `db.add(IntegrityAuditLog(...))` и `model_dump()` (`:263-284`) — чисто
in-memory операции, IO они не выполняют, поэтому отравить транзакцию не могут. (Под следствие (b)
ниже `:284-285` при этом всё равно попадает: запись аудита теряется молча.)

**Два разных следствия, которые нельзя смешивать.** Их обязательно разделять при любом будущем
разборе:

- **(a) Отравление транзакции.** Проглоченный `40001`/`25P02` ломает живой unit of work — это то,
  что делает F-004-1 дефектом P2. Лечится транзакционным guard'ом.
- **(b) Потеря аудита.** Проглоченная ошибка роняет запись `IntegrityAuditLog`, **а бизнес-изменение
  всё равно коммитится** — тихий пробел в аудите. Транзакционный guard этого **не чинит**:
  нужен либо fail-closed на записи аудита, либо явный учёт пропусков.
  Наиболее выражено в `trustlines/service.py:137-138` → `:140` и `integrity.py:284-285` → `:287` —
  последний случай даёт **только** (b), без (a), и потому наглядно показывает, что следствия
  независимы.

**Владение.** Клиринговые инстансы (`clearing/service.py:964-969`, `:1022-1027`) лежат на owner
surface **003** и должны разбираться там. Инстансы в `trustlines/service.py` и `api/v1/integrity.py`
на текущем HEAD **не имеют владельца**: они не входят ни в одну спеку и **не зарегистрированы**
в [`../BACKLOG.md`](../BACKLOG.md) — их туда нужно внести (владелец `BACKLOG.md` — не 004).
Формулировка «этим займётся другая программа» здесь была бы неправдой: такой программы нет.

## Current / Intended / Optimal

**Current.** Ретрай работает ровно в границах whole-UoW движка и только для кодов из предиката
`:240-251`. Всё, что случается в best-effort блоках, на границе сервиса и в HTTP-маппинге, теряет
тип конфликта и превращается либо в постоянный ABORT (F-004-1), либо в 500 (F-004-2/3).

**Intended.** Конфликт сериализации — штатное событие под SERIALIZABLE. Клиент должен получать
типизированный retryable-код, а движок — не терять возможность ретрая из-за диагностического кода.

**Optimal.** Три правила: (1) best-effort диагностика не выполняется внутри транзакции, состояние
которой она может испортить, — либо выносится за границу UoW, либо её исключения классифицируются,
а не глотаются; (2) единый классификатор ошибок БД на границе сервиса, дающий стабильный код
retryable-конфликта вместо `E010`; (3) `finally`-гарантия терминального состояния для отмены и
таймаута, одинаковая для staged и non-staged веток.

## Non-goals

- Изменение идентичности или порядка захвата advisory-блокировок (002).
- Увеличение числа ретраев как способ «починки» — прямо запрещено (наследуется из `002/spec.md:195`).
- Введение новых бизнес-кодов ошибок в OpenAPI без отдельного решения владельца.

## Verification plan

1. Детерминированный PG-репродьюсер F-004-1: `40001` внутри audit-блока → сегодня терминальный
   ABORT, после фикса — успешный ретрай. Тест обязан падать на текущем коде.
2. Тест: `40001` на `session.commit()` во **всех трёх** потребителях даёт типизированный ответ,
   а не голую 500: (a) `POST /api/v1/payments`; (b) интерактивный путь симулятора
   (`simulator.py:1474`) — ответ обязан отличаться от `PAYMENT_REJECTED`; (c) staged-путь
   (`real_payments_executor.py:369`) — in-process, HTTP-границы не пересекает, поэтому проверяется
   на уровне сервиса, включая судьбу внешней транзакции тика.
3. Тест отмены: `CancelledError` в каждой из фаз → нет долговечных `NEW`/`PREPARED`.
4. Тест staged timeout-abort: отказ логируется так же, как в non-staged ветке.
5. Регресс: существующие селекторы same-direction, same-tx, idempotency, timeout, cancellation, recovery.

Инъекция синтетических DBAPI-ошибок допустима только как дополнение к реальному расписанию,
не как замена (`002` P102, а также прецеденты false-green:
`tests/integration/test_payment_engine_uow_retry_postgres.py:14-181`,
`tests/unit/test_payment_engine_retry_savepoint_nocommit.py`).

## Tasks

| ID | Задача | Статус |
|---|---|---|
| T400 | PG-репродьюсер F-004-1 (`25P02` после проглоченного `40001`) | `[x]` |
| T401 | Вынести/классифицировать best-effort audit-блок; убрать `except Exception: continue/pass` | `[x]` |
| T402 | Единый классификатор ошибок БД на границе сервиса + типизированный retryable-код | `[x]` |
| T403 | Расширить guard вставки tx-строки за пределы `IntegrityError` — **одним guard'ом внутри `PaymentService`** вокруг `service.py:543-568` в `_create_payment_impl` (`:258`), который наследуют все **три** потребителя. Подробности и критерии приёмки — ниже | `[x]` |
| T404 | `finally`-гарантия терминального состояния для отмены и таймаута (staged и non-staged) | `[x]` |
| T405 | Симметричное логирование timeout-abort в обеих ветках | `[x]` |
| T406 | Решение по ретраю/маппингу для trustlines и integrity сервисов | `[x]` |
| T407 | Синхронизация `docs/ru/09-decisions-and-defaults.md` и платёжной RU-документации | `[!]` |
| T408 | Независимое ревью и evidence на точном HEAD | `[!]` |

### T403 — подход и критерии приёмки

**Наивное решение отвергнуто.** Глобальный `@app.exception_handler(Exception)` в `app/main.py`
(там сейчас ровно два обработчика — `GeoException` `:490` и `RequestValidationError` `:495`)
чинить F-004-2 **не годится**. Причина сильнее, чем «меняет семантику по всему репозиторию»:
третий потребитель, `app/core/simulator/real_payments_executor.py:369`, работает **in-process**
и HTTP-границу не пересекает вовсе — глобальный HTTP-обработчик его не покрывает ни при каких
условиях.

**Минимально корректное решение.** Один guard внутри `PaymentService` вокруг `service.py:543-568`
в `_create_payment_impl` (`:258`). Это единственная точка, через которую проходят все три
потребителя (`create_payment` `:160-173`, `create_payment_internal` `:175-219`,
`create_payment_internal_staged` `:221-256`), поэтому покрытие наследуется автоматически, без
дублирования логики на трёх границах.

**Критерии приёмки:**

1. Guard классифицирует `40001`/`40P01` на `session.commit()` (`:545`) и на `flush()` в
   savepoint-ветке (`:551-552`) как retryable-конфликт, а не как внутреннюю ошибку.
2. **Guard, поднимающий обычный `GeoException`, — недостаточен.** `app/api/v1/simulator.py:1513-1520`
   отображает любой `GeoException` в `_action_error(status_code=exc.status_code,
   code="PAYMENT_REJECTED")`, а «rejected» для retryable-конфликта семантически неверно: это не
   терминальный отказ. Допустимы ровно два варианта: (a) guard поднимает **отдельный тип
   исключения**, который `simulator.py` маппит своей веткой; либо (b) в `simulator.py` ветка
   `except GeoException` получает различающую подветку. Тест обязан фиксировать, что интерактивный
   путь **не** отвечает `PAYMENT_REJECTED`.
3. Для staged-потребителя зафиксировано поведение внешней транзакции тика
   (`real_payments_executor.py:367`): serialization failure отменяет всю PG-транзакцию, а не
   savepoint, — тик обязан либо корректно завершить батч, либо явно его переиграть, но не
   продолжать в отравленной сессии.
4. Ни один из трёх путей не отдаёт голую Starlette-500 на `40001`.

## Changelog

### 2026-08-11 — T400

- Перед правкой анкоры сверены на exact HEAD `5631c02bc8a7eaf9ffeb7b8812cf66a2a276ec0a`:
  retry predicate остался на `app/core/payments/engine.py:231-251`, audit swallow — на
  `:1149-1152`, следующий `DELETE` — на `:1155-1156`; F-004-1 подтверждена без расхождения.
- Добавлен Postgres-only репродьюсер
  `tests/integration/test_payment_engine_audit_conflict_postgres.py:21-198`. Он запускает реальную
  SERIALIZABLE-транзакцию: audit checkpoint читает `Equivalent`, отдельная сессия коммитит
  конкурирующий update, после чего update платёжной транзакции получает настоящий PostgreSQL
  `40001`. Никакой synthetic `DBAPIError` в основной проверке не создаётся.
- Red canonical command на disposable PostgreSQL 16:
  `$env:TEST_DATABASE_URL='postgresql+asyncpg://geo:geo@localhost:55432/geov0_test_wave2'; $env:GEO_TEST_ALLOW_DB_RESET='1'; .\scripts\verify_local.ps1 -TaskSlug wave2_t400_red -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_payment_engine_audit_conflict_postgres.py`
  — exit `1`, `1 failed`. Фактическая цепочка: audit-`40001` был проглочен, затем
  `DELETE FROM prepare_locks` завершился
  `asyncpg.exceptions.InFailedSQLTransactionError: current transaction is aborted` (`25P02`),
  что тест проверяет на `:166-171`.
- До T401 репродьюсер помечен `xfail(strict=True)` (`:16-19`): улучшение немедленно даст XPASS и
  потребует снять маркер. Та же canonical команда с `-TaskSlug wave2_t400` — exit `0`,
  `1 xfailed`; pinned Ruff для файла и `git diff --check` — exit `0`.

### 2026-08-11 — T401

- Implementation commit: `c10a61a`. До изменения оба audit checkpoint блока проглатывали любые
  исключения (`app/core/payments/engine.py:985-988,1149-1152`), поэтому исходный PostgreSQL
  `40001` терялся до следующего statement.
- После изменения DBAPI-ошибки немедленно пробрасываются в существующий whole-UoW classifier
  (`app/core/payments/engine.py:985-998,1168-1184`); non-DB ошибки диагностики остаются явно
  best-effort, но теперь логируются с `tx_id` и `error_type`. Добавлять симптом `25P02` в retry-set
  не потребовалось, и lock-identity surface 002 не менялась.
- Anti-vacuum countercheck на `tests/integration/test_payment_engine_audit_conflict_postgres.py:140-143`
  бросает `ValueError` уже после настоящего database retry и доказывает, что безопасная non-DB
  ошибка аудита не отменяет платёж. `xfail(strict=True)` снят.
- Canonical PostgreSQL gate на реальном репродьюсере и существующем whole-UoW test
  (`-TaskSlug wave2_t401_pg_counter -BackendOnly -BackendMarker postgres`) — exit `0`, `2 passed`.
  Canonical SQLite regression selector
  (`-TaskSlug wave2_t401_unit`, savepoint retry + payments 2PC + prepare taxonomy) — exit `0`,
  `32 passed`. Pinned Ruff на изменённых файлах и `git diff --check` — exit `0`.

### 2026-08-11 — T402

- Implementation commit: `c5589d2`. Анкоры перед изменением подтвердились: generic prepare error
  схлопывался в `GeoException/E010` на `app/core/payments/service.py:600-668`, generic commit error —
  на `:683-810`; в `app/main.py:490,495` по-прежнему только GeoException и validation handlers.
- Решение по границе записано явно. **Current:** PostgreSQL `40001/40P01` теряли тип и становились
  500/E010. **Intended:** клиент и оба simulator-пути должны отличать transient conflict от
  терминального rejection. **Optimal в разрешённом скоупе:** переиспользовать объявленный wire
  contract HTTP `409` + `E008`, добавить только sanitized details
  `retryable=true, conflict_kind=database_concurrency`; не вводить отсутствующие в OpenAPI 503 или
  новый business code. Interactive/staged mapping этого типа принадлежит следующей T403.
- Единый classifier расположен в `app/core/payments/service.py:44-95`: он обходит `orig`,
  `__cause__`, `__context__`, понимает driver attributes `sqlstate`/`pgcode`/`code` и не принимает
  SQLAlchemy wrapper `.code='dbapi'` за SQLSTATE. Типизированный подкласс существующего E008 —
  `app/utils/exceptions.py:72-84`. Prepare/commit boundaries используют classifier на
  `service.py:672-674,804`, а durable abort сохраняет тот же безопасный code/details на
  `:840-857`.
- Unit matrix `tests/unit/test_payment_db_error_classifier.py:25-68` покрывает `40001`, `40P01`,
  три driver attribute, cause chain и контрпримеры `25P02/23505/08006`; integration cases prepare
  и commit находятся в `tests/integration/test_payment_prepare_error_taxonomy.py:152-210`.
  Первая реализация classifier дала canonical exit `1`, `7 failed, 31 passed`: wrapper
  `.code='dbapi'` ошибочно перекрывал driver SQLSTATE; история сохранена, обход исправлен.
- После исправления canonical selector `wave2_t402_boundary` — exit `0`, `40 passed`; OpenAPI
  selector `wave2_t402_contract` — exit `0`, `23 passed`; pinned Ruff и `git diff --check` — exit `0`.

### 2026-08-11 — T403

- Implementation commit: `21753fd`. Перед изменением общий insertion guard подтвердился на
  `app/core/payments/service.py:543-568`: он ловил только `IntegrityError`; три entrypoint оставались
  `create_payment` `:160-173`, `create_payment_internal` `:175-219` и staged `:221-256`.
- Один общий DBAPI guard теперь находится в `app/core/payments/service.py:623-646`. Для commit-path
  он rollback'ит отравленную сессию и поднимает sanitized `RetryablePaymentConflictException`; для
  staged-path намеренно не делает локальный rollback, а передаёт владение внешней транзакцией тика.
  Nonretryable DBAPI ошибки не переклассифицируются.
- REST использует уже объявленный 409/E008. Interactive simulator имеет отдельную ветку
  `app/api/v1/simulator.py:1482-1489` с code `CONFLICT`, а не `PAYMENT_REJECTED`. Staged executor
  пробрасывает subtype до tick boundary (`app/core/simulator/real_payments_executor.py:414-419`),
  поэтому clearing/trust-drift/persistence на отравленной сессии не продолжаются; существующий
  tick rollback подтверждён тестом.
- Acceptance cases: public/internal/staged insertion guard и HTTP response —
  `tests/integration/test_payment_prepare_error_taxonomy.py:212-330`; interactive mapping —
  `tests/unit/test_interact_actions_backend_p1.py:784`; executor propagation —
  `tests/unit/test_real_payments_ordered_journal.py:184`; outer tick rollback —
  `tests/unit/test_real_tick_orchestrator_rollback_resolution.py:241`.
- Первый targeted run `wave2_t403` завершился exit `1`, `1 failed, 73 passed`: тест ошибочно ожидал
  поле `ok` в существующем `SimulatorActionError`; production response был корректен, ожидание
  приведено к фактической schema. Повтор `wave2_t403_fix` — exit `0`, `74 passed`; real-PG
  regression `wave2_t403_pg` — exit `0`, `2 passed`; OpenAPI selector `wave2_t403_contract` —
  exit `0`, `23 passed`; pinned Ruff и `git diff --check` — exit `0`.

### 2026-08-11 — T404

- Implementation commit: `f914ddd`. До изменения outer boundary ловил только timeout
  (`app/core/payments/service.py:811`), `CancelledError` обходил cleanup, а timeout использовал
  недренируемый bare `asyncio.shield` на `:847-873`. Существующий тест прямо фиксировал старый
  дефект как «abort не вызывается».
- `_drain_payment_cleanup` (`app/core/payments/service.py:97-123`) теперь доводит session-owned
  rollback/abort до terminal result: первая отмена сохраняется, повторная отменяет child, но child
  всё равно дренируется до завершения. Флаг возможной записи tx выставляется до commit/flush
  (`:498,625`), поэтому покрыта неоднозначная отмена самой вставки, а не только поздние фазы.
- Cancellation boundary (`service.py:916-954`) сохраняет исходный `CancelledError`, но сначала
  rollback/read-before-abort для commit-path либо staged abort для caller-owned outer transaction.
  Уже `COMMITTED` состояние не регрессирует. Timeout boundary использует тот же terminal drain
  (`:957-1019`) вместо detached shield.
- Behavioral coverage: insert/prepare/commit cancellation и durable ABORTED —
  `tests/integration/test_payment_prepare_error_taxonomy.py:1017-1113`; staged prepare abort до
  outer rollback — `:1116-1167`; одинарная/повторная cancellation дренирования —
  `tests/unit/test_payment_cleanup_cancellation.py:11-61`. Старое ложное ожидание намеренно
  переписано под инвариант.
- Первый canonical selector `wave2_t404_initial` подтвердил смену поведения: exit `1`,
  `1 failed, 32 passed`, только старое ожидание `abort_called is False`. После обновления tests
  `wave2_t404` и финальный `wave2_t404_final` — exit `0`, каждый `38 passed`; pinned Ruff и
  `git diff --check` — exit `0`.

### 2026-08-11 — T405

- Implementation commit: `99cf45b`. Перед правкой non-staged timeout-abort логировал
  `payment.timeout_abort_failed`, а staged branch после terminal drain возвращал timeout без записи
  причины abort failure.
- Обе ветки теперь используют один event name и одинаковый sanitized payload `tx_id/error_type`
  (`app/core/payments/service.py:1002-1007,1020-1025`); raw exception text и `exc_info` наружу не
  попадают. Staged timeout сохраняет исходный `TimeoutException`, а caller-owned savepoint
  откатывает незавершённый tx.
- Countercheck `tests/integration/test_payment_prepare_error_taxonomy.py:1384-1437` заставляет
  staged abort упасть с sentinel и проверяет наличие `error_type=RuntimeError`, отсутствие sentinel
  и отсутствие tx после outer rollback.
- Первый `wave2_t405` — exit `1`, `1 failed, 34 passed`: read-after-timeout получил stale identity-map
  `NEW` после реально закоммиченного состояния и попытался abort. Оба ambiguity reads переведены на
  `populate_existing=True` (`service.py:928,977`); targeted `wave2_t405_timeout_fix` — exit `0`,
  `2 passed`, полный `wave2_t405_final` — exit `0`, `39 passed`. Pinned Ruff и
  `git diff --check` — exit `0`.

### 2026-08-11 — T406

- Fresh owner scan
  `rg -n "except Exception|await .*commit|compute_integrity_checkpoint" app/core/trustlines/service.py app/api/v1/integrity.py app/core/clearing/service.py`
  — exit `0`; подтвердил trustlines `:81-82,137-140,242-250,335-343`, integrity
  `:260-261 -> :287` и clearing siblings `:964-969,1022-1027`. F-004-7 остаётся подтверждённой.
- **Current:** clearing уже имеет собственный mapper и принадлежит 003; trustlines/integrity не
  имеют owner-программы и смешивают два разных риска — poisoned transaction и потерю audit row.
  **Intended:** не расширять payment Wave 2 на sibling services. **Optimal:** будущий owner-slice
  отдельно проектирует transaction guard и политику полноты аудита; один savepoint не выдаётся за
  решение обоих рисков.
- Регистрация уже присутствовала в пользовательском `specs/BACKLOG.md`, раздел
  «Проглатывание исключений: экземпляры без владельца», с пятью подтверждёнными инстансами и явным
  разделением классов (а)/(б). Поэтому BACKLOG повторно не переписывался, код trustlines,
  integrity и clearing не менялся. T406 закрыта как решение/маршрутизация, не как фиктивный fix.
