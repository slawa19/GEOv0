# arch-money-core — денежное ядро

HEAD: 4119ace. Прочитано целиком: `app/core/payments/service.py` (2843), `app/core/payments/router.py` (599), `app/core/clearing/service.py` (2413), `app/core/ledger/book.py` (1051), `app/core/ledger/reconciliation.py` (1238), `app/core/money_boundary.py` (457), `app/core/invariants.py` (288), `app/core/integrity.py` (151), `app/core/balance/service.py` (296), `app/core/trustlines/service.py` (791), `app/core/participants/service.py` (211), `app/core/auth/canonical.py` (101), `app/core/auth/crypto.py` (49), `app/core/auth/service.py` (155), `app/api/v1/payments.py` (138), `app/api/v1/clearing.py` (55), `app/api/v1/trustlines.py` (87), `app/api/v1/participants.py` (86), `app/api/v1/balance.py` (34), `app/api/v1/integrity.py` (319), `app/utils/distributed_lock.py` (78), `app/db/session.py` (34), модели `transaction.py`, `debt.py`, `trustline.py`, `equivalent.py`, `audit_log.py`. Частично: `app/db/journal_triggers.py` (:1-150), `app/api/v1/admin.py` (только :105-121, :1053-1116, три вызова `require_serializable`), `app/api/deps.py` (:124-140), `app/core/simulator/money_replay.py` (:1-60), `real_payments_executor.py` (:365-380, :455-475), `app/api/v1/simulator.py` (:1535-1545, :1640-1696), `inject_executor.py` (:684-692, :824-830, :905-918), `app/config.py` (grep по ключам). Не прочитано: остальной `admin.py`, весь `app/core/simulator/`, `app/utils/validation.py`, `app/utils/money.py`, `app/db/types.py`, `app/db/journal_tables.py`, миграции, тесты (только `git grep`), `api/openapi.yaml` (только `grep`).

Инструменты: `git grep`, `ruff 0.1.14` (`--select F401,F811,F841,ARG,C901,PLR0912,PLR0913,PLR0915` по зоне — 70 находок, из них 2 ARG в ядре: `payments/service.py:1106` `idempotency_key`, `auth/service.py:52` `device_info`). Ничего не запускалось, кроме ruff и grep.

## Summary

1. **После 018 `Book` действительно единственный писатель `debts` в `app/` и `scripts/`**: `git grep` по `Debt(`, `.amount =`, `update(Debt)`, `delete(Debt)`, `UPDATE debts` вне `book.py` даёт ноль (попадания `admin.py:1853,2224` — Pydantic-схема `AdminGraphDebt`). Писатели `trust_lines` вне `TrustLineService` — все в симуляторе, но список 021 неполон (AMC-9), и его гард по построению пропускает `.status =` — то есть анти-вакуум §9 не выполнен.
2. **Ошибок логики класса 1 (деньги/долг) в прочитанном коде не найдено.** Платёж, клиринг, книга и сверка согласованы по направлению `creditor -> debtor`, по алгебре и по атомарности; каждый вход перечислен ниже.
3. **Главный архитектурный дефект — две системы «целостности», из которых старшая работает на денежном пути и никем не проверяется** (AMC-1). Каждый платёж, клиринг и операция с линией выполняют **два полных скана эквивалента** (`debts` + `trust_lines` + два join-инварианта по всему эквиваленту) внутри `SERIALIZABLE`-транзакции, чтобы записать `IntegrityAuditLog` с контрольными суммами «до/после», которые ни один читатель не сравнивает. Настоящий детектор — `reconciliation.py` (018/015). Это §19.2 п. 1 «потеря формулируется через свойства механизма» и одновременно, по выводу (INFERENCE, не замерено), причина ложных `40001` между **любыми** двумя параллельными платежами одного эквивалента — SSI-конфликт по полному чтению. Решение здесь нужно **до** 021 (тик) и 023(b) (исполнение цикла), потому что обе программы будут мерить стоимость операции с этим накладным расходом внутри.
4. Остальное — сопровождаемость: одно правило в пяти местах (ёмкость/лимит доверия, AMC-2; классификация ошибок БД, AMC-3), мёртвые ветки и имена 019 в живом коде (AMC-4, AMC-5, AMC-6), симуляторные швы в `payments/service.py`, за которые 021 не отвечает (AMC-8), и два соглашения о владении транзакцией (AMC-10).
5. **Первым делом**: (а) записать решение §19.4 по чекпойнтам на денежном пути (AMC-1) — сузить, а не доделывать; (б) удалить мёртвые ветки 019 и имена `prepare` (AMC-4) — без контракта, один день; (в) поправить 021: список писателей и гард (AMC-9), поверхность владения для симуляторных швов (AMC-8); (г) освежить якоря 016 — F-016-2/3/5 указывают на удалённый `engine.py` и `recovery.py`.

## Findings

| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| AMC-1 | P2 | architecture | `payments/service.py:1905,2017`; `clearing/service.py:2046,2144`; `trustlines/service.py:182,244,353,377,484,493`; `integrity.py:19-133` | Две системы целостности; старшая (чекпойнт + `IntegrityAuditLog`) сканирует весь эквивалент дважды в каждой денежной транзакции и никем не сверяется; INFERENCE — источник ложных SSI-конфликтов | 016 (F-016-1/2 — только дедуп материализатора, не существование) | M |
| AMC-2 | P2 | duplication | `router.py:292`; `payments/service.py:1845-1858`; `balance/service.py:175,187`; `trustlines/service.py:359-365`; `invariants.py:101-119` | Правило «долг ≤ лимит линии creditor→debtor» и формула ёмкости записаны пятью независимыми копиями с разными фильтрами статуса | none | M |
| AMC-3 | P2 | duplication | `payments/service.py:63-149,264-313,2814-2821`; `clearing/service.py:349-413`; `trustlines/service.py:35-96`; `simulator/money_replay.py:89-144`; `simulator/real_runner_impl.py:65-76` | Три обходчика цепочки исключений и шесть предикатов transient/logical; F-016-3 верна, но её якоря указывают на удалённый `engine.py`; обходчик trust-lines следует `__context__` вопреки правилу 2026-09-12 | 016 (F-016-3, якоря протухли) | S |
| AMC-4 | P2 | dead-code | `payments/service.py:881-897,1365,1707-1725,168,1303,1444-1446,534,981-996,1106`; `config.py:185`; `db/models/transaction.py:25` | Остатки 019 на живом пути: недостижимая ветка in-progress состояний, фаза `prepare` и её метрики/тайм-аут, комментарии на несуществующие функции, шим `commit=`, сквозной мёртвый `idempotency_key` | 016 (F-016-5 — предлагает унифицировать то, что теперь надо удалить) | S |
| AMC-5 | P3 | dead-code | `clearing/service.py:479-486,392-412,548-564,1182,1214-1220,1432-1434,1594-1598,1627-1631,1884-1888,2134` | Остатки 017: `_bind_uuid` no-op, SQLite-обоснования, ветка без interlock, `use_sql` всегда True в проде, мёртвая защита `debt.amount < clear_amount` | 023 (d) удаляет заменённое исполнение — частично; `find_cycles` остаётся | S |
| AMC-6 | P3 | dead-code | `invariants.py:22-85`; `integrity.py:79-90`; `api/v1/integrity.py:36-41` | `check_zero_sum`/`_compute_imbalance` без вызывающих в `app/`; комментарии обещают «015 восстановит настоящую проверку», хотя 015 закрыта решением «тождественно ноль, проверкой быть не может» | none | S |
| AMC-7 | P2 | responsibility | `payments/service.py:1100-1575`; `clearing/service.py:1766-2294` | `execute()` 475 строк / 7 забот, `_execute_clearing_with_amount` 528 строк / 9 забот; естественные швы перечислены в деталях с блоками строк | 023 (b) касается исполнения; для платежа — none | L (рефакторинг не авторизуется без потери, §19.5; записать швы) |
| AMC-8 | P2 | architecture | `payments/service.py:388-438,1022-1098,2047-2206,589-608`; `money_boundary.py:158-179` | ≈350 строк симуляторных швов (staged-путь, staged-локи, `_ADMITTED_REFUSALS`, `PaymentTransactionUnusable`) живут в `payments/service.py`; 021 исключает `app/core/payments/` из owner surface, но делает `acquire_shared_equivalent_locks` мёртвым | 021 (неполно: некому удалить) | S |
| AMC-9 | P2 | architecture | `api/v1/simulator.py:1541`; `simulator/inject_executor.py:688,827,913-915`; `specs/021.../spec.md:15,60` | Список писателей `trust_lines` в 021 неполон, а гард `T2107` не видит `.status =`/`.policy =` — анти-вакуум §9 не выполнен | 021 (неполно) | S |
| AMC-10 | P3 | architecture | `trustlines/service.py:286,436,547`; `participants/service.py:81,188`; `clearing/service.py:1703-1730`; `payments/service.py:2264-2282,2383-2406` | Три соглашения о владении транзакцией (сервис коммитит сам / владелец снаружи / сервис подменяет `self.session`); запись лимита/статуса линии идёт мимо `money_boundary` и `require_serializable` | none | S |
| AMC-11 | P3 | logic-bug | `clearing/service.py:1214-1220,630-641` | `except Exception → []` после сырого SQL на PostgreSQL оставляет транзакцию в `25P02`; следующий запрос DFS падает уже другой ошибкой; в BACKLOG записи нет (комментарий `:1432` говорит «recorded») | none (023 оставляет `find_cycles` как диагностику) | S |
| AMC-12 | P3 | dead-code | `router.py:154-173,302-311,26-49`; `payments/service.py:932-969` | Кэш графа выключен умолчанием (`ROUTING_GRAPH_CACHE_TTL_SECONDS=0`, `.env.example`/docker его не ставят), при этом 5 точек инвалидации, back-compat распаковка 5-кортежа, который никто не пишет, и периметр, рассуждающий о кэше | none | S |
| AMC-13 | P3 | duplication | `payments/service.py:152-192`; `clearing/service.py:188-211,457-477` | Три копии одного примитива «дождаться задачи под повторной отменой» | none | S |
| AMC-14 | P3 | duplication | `reconciliation.py:157-162,729-734` | `_READABLE_ENVELOPES` и `_RULES` — два словаря по одному ключу `(kind, version)`, которые 023(b) обязана править синхронно | 023 (b) | S |
| AMC-15 | P3 | docs-drift | `reconciliation.py:13-16,44-45,935-941,1044-1045`; `book.py:9,330`; `payments/service.py:71-79` | SQLite-эпоха в докстрингах живых модулей («on both dialects», «на SQLite агрегат — float») | none | S |
| AMC-16 | P3 | readability | `payments/service.py:2383-2406`; `api/v1/simulator.py:1689` | `_end_failed_attempt` **коммитит** заимствованную сессию вызывающего после отказа попытки (задокументировано); `action_payment` симулятора получает коммит запроса после отказа маршрутизации | 021 (`action_*` переписываются) | — |

## Детали

### AMC-1 — две системы целостности; старшая на денежном пути и без читателя

**Evidence — кто и сколько считает.** `compute_integrity_checkpoint_for_equivalent` (`integrity.py:19-133`) читает **все** `debts` эквивалента (`:24-30`), **все** `trust_lines` (`:32-47`), затем `check_trust_limits(equivalent_id=...)` — join `debts ⋈ trust_lines` по всему эквиваленту (`invariants.py:101-119`) и `check_debt_symmetry` — self-join `debts` по всему эквиваленту (`:169-193`). Вызывается **дважды на операцию** внутри денежной транзакции:

- платёж: `payments/service.py:1905` (до, в `_apply_payment`) и `:2017` (после, в `_write_integrity_audit`); плюс те же два инварианта ещё раз по парам платежа `:1972-1973` и `check_payment_delta` `:1974`;
- клиринг: `clearing/service.py:2046` и `:2144`, плюс `verify_clearing_neutrality` `:2198` (ещё 2 запроса на участника) и `positions_before` `:2034-2038` (ещё 2 на участника);
- линия доверия: `trustlines/service.py:182/244`, `:353/377`, `:484/493`.

**Evidence — кто читает результат.** `state_checksum_before`/`state_checksum_after` пишутся в `IntegrityAuditLog` (`payments/service.py:2028-2029`, `clearing/service.py:2176-2177`, `trustlines/service.py:258-259` и далее) и читаются только сериализатором `GET /integrity/audit-log` (`api/v1/integrity.py:310-311`). Никто не сравнивает `after` операции N с `before` операции N+1; `git grep state_checksum` по `app/` даёт только писателей и этот сериализатор. `admin-ui` этот эндпоинт не вызывает (`git grep "integrity/" admin-ui/src` → только `/status` и `/verify`). BACKLOG:19 констатирует то же. `verification_passed` строки платежа — результат **общеэквивалентной** проверки, поэтому одно посторонее нарушение лимита где-то в эквиваленте помечает `passed=False` каждый платёж эквивалента, а сам платёж при этом коммитится (`:1972` проверяет только свои пары).

**Evidence — вторая система.** `reconciliation.py` (`verify_journal_equals_change`, `:775-841`) — критерии (а)/(б) из журнала триггера с реакцией `react_to_failed` (`:1113-1154`), fail-closed через hold (`money_boundary.py:287-332`). Обе системы запускает один `_integrity_loop` (`main.py:191`, `:221`) последовательно. Ключ `zero_sum` в чекпойнте — «WITHDRAWN» (`integrity.py:79-90`).

**Почему это важно.**
1. §19.2 п. 1: потеря, которую предотвращает чекпойнт на денежном пути, формулируется только свойствами механизма (никакой читатель не сверяет цепочку сумм). Настоящее обнаружение — сверка 015/018.
2. Стоимость: ≥ 6 полных сканов эквивалента на платёж (2× `debts`, 2× `trust_lines`, 2× join-инварианта), линейно от размера эквивалента, внутри транзакции, держащей `FOR SHARE` на строке эквивалента и разделяемый advisory-лок.
3. **INFERENCE, не замерено:** под `SERIALIZABLE` полное чтение `debts WHERE equivalent_id = X` ставит SIREAD-предикат на весь диапазон; две параллельные транзакции-платежа, каждая читающая весь эквивалент и пишущая хотя бы одну строку `debts`, образуют rw-цикл длины 2, и PostgreSQL прерывает одну `40001` — **независимо от того, пересекаются ли их пары**. То есть «shared holders do not wait for one another» (`money_boundary.py:9-13`) на практике превращается в сериализацию повторами. `T1908` мерил только пересекающиеся расписания (`test_p019_t1908_lock_removal_experiments_postgres.py:207,418,512,608` — payment vs clearing, встречные направления на одной паре, узкое место, изоляция); расписания по **непересекающимся** парам в одном эквиваленте нет. Дешёвый стенд: два платежа A→B и C→D в одном эквиваленте, одновременно, считать `Conflicts.serialization_failures` при включённом и выключенном (`monkeypatch` `compute_integrity_checkpoint_for_equivalent` → `None`) чекпойнте.

**Минимальное исправление (§19.4 — сузить, не доделать).** Решение владельца/консультации, две формы: (а) чекпойнт остаётся **только** у планового цикла (`compute_and_store_integrity_checkpoints`, `main.py:221`) и `POST /integrity/verify`; денежные пути пишут `IntegrityAuditLog` без пересчёта сумм (`""` уже допустимая запись — `trustlines/service.py:250`) и с результатом **своих** парных проверок; (б) оставить как есть, записав в спеку, какую потерю ловит цепочка сумм и кто её читает. Форма (а) — минус ~60 строк в трёх сервисах, без миграции; F-016-2 при этом схлопывается до trust-lines ×3 + verify.

**Что может сломаться / call-sites:** 12 вызовов `compute_integrity_checkpoint_for_equivalent` (список в grep выше); тесты `test_integrity_checkpoints.py`, `test_trustline_audit_fail_closed.py` (fail-closed контракт линий, BACKLOG:219-223 — сохраняется, если аудит-строка остаётся, а меняется только её содержимое); `verification_passed` индекс §11.4.2.

Covered-by: 016 (F-016-1 — `/verify` дважды гоняет suite: подтверждено `api/v1/integrity.py:191-206` + `:231`; F-016-2 — материализация в 5 местах: подтверждено, якоря сдвинулись) — обе решают дублирование, а не существование механизма. Contract: no для формы (а) (таблица и wire `/integrity/audit-log` не меняются; меняется наполнение полей).

### AMC-2 — правило лимита/ёмкости в пяти копиях

Evidence:
- `router.py:292`: `cap = (limit - debt_debtor_owes_creditor) + debt_creditor_owes_debtor`, линии только `status == 'active'` (`:200`);
- `payments/service.py:1845-1858`: `limit - sender_owes + receiver_owes`, линия `TrustLine.status == "active"` (`:1851`), три запроса на сегмент;
- `balance/service.py:175`: `capacity = limit - d_me_peer + d_peer_me`, `:187` зеркально, только `'active'` (`:100,106`);
- `trustlines/service.py:359-364`: `if new_limit < used: raise BadRequestException(...)` — тот же инвариант как предусловие обновления, без учёта встречного долга и статуса `frozen`;
- `invariants.py:101-119`: пост-проверка `Debt.amount > coalesce(tl.limit, 0)` для `status IN ('active','frozen')`;
- `book.py:499-528`: `InjectIncrease.ceiling` — лимит передаёт вызывающий (симулятор), книга правило не знает.

Почему важно: одно правило протокола (§5, «лимит риска кредитора») и одно направление `from -> to = creditor -> debtor` записаны пять раз с тремя разными фильтрами статуса (`active` / `active,frozen` / без фильтра). Сегодня согласованы (frozen — недостижим, BACKLOG:1045), но добавление статуса или политики (`daily_limit`, `max_hop_usage`) потребует пяти синхронных правок, и одна поверхность останется зелёной.

Минимальное исправление: одна чистая функция ёмкости `(limit, forward_debt, reverse_debt) -> Decimal` и одна константа «статусы линии, дающие ёмкость», используемые роутером, `_segment_capacity` и балансом; `trustlines.update` и инвариант ссылаются на ту же константу. Не сервис, не слой — модуль с двумя именами.

Call-sites: перечисленные выше плюс `_hydrate_trustline` (`trustlines/service.py:750`, `available = limit - used` — это wire-определение из `api/openapi.yaml:5248`, не ёмкость; не трогать). Covered-by: none (016 закрыла только внутридвижковый дубль, BACKLOG:84). Contract: no.

### AMC-3 — классификация ошибок БД: три обходчика, шесть предикатов

Evidence:
- `payments/service.py:63-96` `_iter_exception_chain` (`orig`/`__cause__`, **не** `__context__`), `:99-115` `_payment_db_sqlstate` (пропускает `.code` у `DBAPIError`-обёртки), `:129-149` `_classify_payment_db_error` ({40001, 40P01} + `DebtVersionConflict` + `is_debt_pair_collision`), `:277-284` `_is_tx_id_collision`, `:247` классы для COMMIT, `:1094` и `:2817` — `55P03` отдельно, `:2814-2821` inline-лестница в `record_definitive_refusal`;
- `clearing/service.py:349-388` `_postgres_error_codes` — тот же обход, но собирает `.code` **и с обёртки** (`:379`), `:390-413` `_is_retryable_concurrency_error` ({40001, 40P01}), `:1711` — `55P03` отдельно;
- `trustlines/service.py:54-67`: `node = getattr(node, "__cause__", None) or getattr(node, "__context__", None)` — следует `__context__`, который два соседних модуля 2026-09-12 намеренно исключили (`payments/service.py:71-79`, `clearing/service.py:360-365`);
- симулятор: `money_replay.py:89,102-144` `money_conflict_name`, `real_runner_impl.py:65-76` `_is_transient_inject_db_error` ({40001, 40P01, 55P03} + pair collision).

Почему важно: F-016-3 описывала ровно это, но её якоря — `engine.py:399-405,441-449` (удалён 019), `clearing/service.py:229-250` (теперь `:349-413`), `trustlines/service.py:50-101` (теперь `:35-96`). Расхождение по `__context__` в trust-lines — риск невелик (предикат отвечает только на «какой констрейнт», не на «повторять ли»), но это тот самый класс маскировки, который в платежах уже стоил дефекта.

Минимальное исправление: как в F-016-3 — один `iter_exception_chain` + `sqlstate_of` + `constraint_name_of` в `app/utils/db_errors.py`; политики повторов остаются у владельцев. Covered-by: 016 (F-016-3) — освежить якоря и добавить симуляторные предикаты в перечень. Contract: no.

### AMC-4 — остатки 019 на живом пути платежа

Evidence:
- `payments/service.py:881-897`: `if existing_tx.state in {"NEW","ROUTED","PREPARE_IN_PROGRESS","PREPARED","PROPOSED","WAITING"}: ... raise ConflictException("Payment with same tx_id is in progress")` — недостижимо для `type == "PAYMENT"` после миграции 030: `db/models/transaction.py:31` `CheckConstraint("type <> 'PAYMENT' OR state IN ('COMMITTED', 'ABORTED')")`; проверка типа стоит выше на `:783`. Метрика `conflict_in_progress` (`:890-894`) считает то, чего не бывает;
- `:1365` `prepare_timeout_s = settings.PREPARE_TIMEOUT_SECONDS`, `:1707-1710` `wait_for(self._bind_payment(...), timeout=prepare_timeout_s)`, `:1714-1725` `event=payment.prepare_failed`, `PAYMENT_EVENTS_TOTAL{event="prepare"}`, префикс `prepare_nested_abort`; `config.py:185`. Фазы `prepare` нет — это фаза связывания (`_bind_payment`), докстринг `:1655` так и говорит «log/metric phase name `prepare`»;
- `:168` «leave a durable NEW/PREPARED row», `:1303` «forbidding a new PREPARED state», `:1444-1446` «`_create_payment_impl` resolves … (`:448-451`)» — функции нет;
- `:534` `include_engine_success_metrics` — движка нет;
- `:981-996` `create_payment_internal(..., commit: bool = True)` только `raise ValueError` при `False`; единственный вызывающий передаёт `commit=True` (`api/v1/simulator.py:1694`);
- `:1106` `execute(..., idempotency_key=...)` — ruff `ARG002`, внутри не используется; пробрасывается через `pay()` → `_pay_attempt()` → `execute()`; в API `payments.py:91` «Legacy header is accepted but ignored»;
- `db/models/transaction.py:25` `chk_transaction_state` перечисляет 9 состояний, из которых для PAYMENT достижимы 2, для CLEARING — `NEW` в памяти (`clearing/service.py:2088`) и `COMMITTED` (`:2205`) до коммита.

Минимальное исправление: удалить ветку `:881-897` с метрикой, переименовать `prepare_*` → `bind_*` (метрика/лог — наблюдаемый контракт: сверить с дашбордами и тестами `tests/integration/test_payment_prepare_error_taxonomy.py`, `test_payment_timeouts`), убрать `commit=`, убрать `idempotency_key` из `execute`/`_pay_attempt` (оставить только в `pay()` как принятый и игнорируемый заголовок), поправить три комментария. `chk_transaction_state` — **документировать, не патчить** (миграция, §8).

Covered-by: 016 F-016-5 предлагала «доменный `ACTIVE_PAYMENT_TX_STATES`» — после 019 правильное действие обратное: удалить. `recovery.py` из F-016-5 удалён; admin-читатели «stuck payment» — сознательная поверхность совместимости (`admin.py:116-121`, Q2). Contract: no для правок кода; yes для констрейнта.

### AMC-5 — остатки 017 в клиринге

Evidence: `clearing/service.py:479-481` `_bind_uuid` возвращает аргумент («format supported by the current DBAPI» — DBAPI один); `:483-486` надгробие `_bind_decimal`; `:392-412` абзац о `SQLITE_BUSY_SNAPSHOT` с пометкой «HISTORY»; `:548-564` `_debt_id_key` обосновывается расхождением спеллинга на SQLite (на asyncpg оба пути отдают `uuid.UUID`, `:555`); `:791-795`, `:1432-1434`, `:1884-1888` — то же; `:1182` `use_sql = isinstance(self.session, AsyncSession)` — в проде всегда True, DFS-only путь живёт только для тестов с подменённой сессией; `:1592-1598` и `:1617-1631` — пути в `_run_attempts` **без** `interlocked_equivalent_id` (нет исключительного лока): при несовпадении числа строк preflight и `FOR UPDATE`-перечитывание идут в одном снимке той же сессии, поэтому второй раз даёт то же несовпадение и `None` (`:1859-1875`) — ветка дорогая и по построению пустая; `:2134` `if debt.amount < clear_amount` — `clear_amount = min(...)` тех же строк на `:1915`, условие ложно всегда.

Минимальное исправление: удалить `_bind_uuid` (12 вызовов в модуле), сжать четыре SQLite-абзаца до одной строки «история: 017», объединить два fallback-пути в один явный отказ, убрать мёртвую защиту `:2134`. Covered-by: 023 (d) «удаление лестницы и заменённого пути исполнения» — накроет `:1594-1631` и `:2134`, если исполнение перепишется; `find_cycles` (`:1182`, `:1214`) остаётся диагностикой и в 023 не входит. Contract: no.

### AMC-6 — `check_zero_sum` без вызывающих и обещание «015 восстановит»

Evidence: `git grep check_zero_sum -- app` → только `app/schemas/integrity.py:27` (докстринг) и определение `invariants.py:22`; вызывающие — только тесты (`test_invariants.py`, `test_p014_t1402_*`, `test_integrity_checkpoints.py`). `integrity.py:88`: «Building a real zero-sum check is programme 015. The key stays»; `api/v1/integrity.py:39`: «would keep saying "not verified" after 015 restores a real check». 015 закрыта 2026-09-21; `specs/README.md` п. 3: «сумма нетто-позиций равна нулю тождественно … проверкой она быть не может (снята `T1402`)».

Минимальное исправление: удалить `check_zero_sum`/`_compute_imbalance` (перенести три теста в «гард: проверка не существует» или удалить с записью); заменить два комментария ссылкой на решение README п. 3. Ключ `zero_sum: WITHDRAWN` на wire — защищённая форма (`InvariantWithdrawn`), не трогать. Covered-by: none. Contract: no.

### AMC-7 — ответственность и естественные швы (ответ на вопрос 1)

**`payments/service.py`** (2843):

| Блок | Строки | Забота |
|---|---|---|
| A | 54-315 | классификация ошибок БД, SQLSTATE/constraint, `55P03`, `RefusalNotRecorded` — чистые функции без состояния |
| B | 316-610 | dataclasses попытки/допуска/отказа, `PaymentPostCommitEffects` (кэш + метрики + `event_bus`) |
| C | 611-740 | намерение конверта: `DeclaredFlow`, `PaymentDeclaration`, `_read_payment_prestate` |
| D | 774-905 | идемпотентность по `tx_id` (`_resolve_existing_payment`: тип, инициатор, отпечаток, периметр, состояние) |
| E | 907-1098 | три входа (`create_payment`, `create_payment_internal`, `create_payment_internal_staged`), `_confine_router_to_perimeter`, staged-локи |
| F | 1100-1575 | `execute()`: дверь суммы 1146-1166; валидация участников/эквивалента 1177-1242; подпись 1244-1272; идемпотентность 1274-1297; стоп/hold pre-check 1299-1322; лимиты маршрутизации 1324-1369; маршрутизация + периметр 1371-1467; допуск + операция 1469-1523; сборка ответа 1525-1575 |
| G | 1577-1751 | savepoint операции, вставка `Transaction`, бинд + деньги с двумя `wait_for` и метриками |
| H | 1753-1872 | связывание: лок, ёмкость сегментов |
| I | 1874-2044 | деньги: чекпойнт, стоп `FOR SHARE`, престейт, конверт `Book`, проверки, аудит-строка |
| J | 2045-2206 | staged-урегулирование отказа, запись `ABORTED` в чужую транзакцию, резолвер идентичности |
| K | 2208-2624 | `pay()`: владелец повторов, урегулирование отказа попытки и COMMIT, чтение победителя, запись отказа |
| L | 2625-2762 | чтение: `get_payment*`, `_tx_to_payment_result`, `list_payments` |
| M | 2765-2843 | `record_definitive_refusal` — короткая транзакция отказа |

Естественные швы по коду (не по ощущению): **A** ни от чего не зависит и используется симулятором (`money_replay.py`, `real_runner_impl.py`) — кандидат в `app/utils/db_errors.py` (совпадает с F-016-3); **L** не зависит ни от чего, кроме `Transaction` и схем — читающая сторона; **E+J** и `acquire_shared_equivalent_locks` существуют только ради симулятора (AMC-8); **F** — единственный метод, где валидация подписи (1244-1272), маршрутизация (1371-1467) и оркестрация (1469-1523) склеены общими локальными переменными; расклеить можно только через явный объект «проверенный запрос» (sender/receiver/equivalent/fingerprint), что и есть первая половина `_PaymentAttempt`.

**`clearing/service.py`** (2413): 97-477 — жизненный цикл попытки, cleanup соединения, резолвер коммита, retry-классификатор; 479-641 — SQL-помощники; 643-1079 — детекторы и консент (SQL-консент `:529-544` и Python-консент `:942-1016` — два правила, 023 это видит, п. 6 Problem); 1081-1468 — `find_cycles` (102 оператора, C901=39); 1470-1528 — владелец повторов; 1530-1764 — пиннинг соединения и исключительный лок; **1766-2294** — исполнение: реплей 1812-1823, изоляция 1828-1836, стоп/hold 1838-1842, `FOR UPDATE` 1844-1857, периметр 1903-1912, сумма 1914-1919, консент 1927-1946, позиции 2033-2042, чекпойнт 2044-2062, `Transaction` 2064-2090, конверт и уменьшения 2109-2140, чекпойнт-после и аудит 2142-2195, нейтральность 2197-2202, коммит и двухступенчатое разрешение 2207-2269, кэш/метрики 2271-2289; 2296-2413 — лестница `auto_clear`. Граница «обнаружение / исполнение / сверка» **чистая по данным**: детекторы отдают `debt_id` строками, исполнение перечитывает по id под `FOR UPDATE` и заново проверяет консент (`:1929`), сверка читает только `intent` конверта (`reconciliation.py:573-604`). Связь проходит через идентичность `uuid5(debt ids)` (`:283-285`) — это F-023-1, 023 её меняет.

Минимальное исправление: не рефакторить без потери (§19.5); зафиксировать таблицу швов в спеке-приёмнике и выполнить только два дешёвых выноса (A и L) — при первом же изменении этих блоков. Covered-by: 023 (b)/(d) для исполнения клиринга; none для платежа. Contract: no.

### AMC-8 — симуляторные швы внутри `payments/service.py` без владельца

Evidence: `create_payment_internal_staged` (`:1022-1058`) — единственный вызывающий `real_payments_executor.py:465`; `acquire_shared_equivalent_locks` (`:1060-1098`) — вызывающие только симулятор (`real_payments_executor.py:373`, `real_runner_impl.py:627`, `real_tick_orchestrator.py:304`), реализация `money_boundary.py:158-179` с докстрингом «A caller-owned staged batch's (the tick's money phase, an inject)»; `_ADMITTED_REFUSALS`/`collect_admitted_refusals` (`:388-410`) — только `money_replay.py:76,545`; `PaymentTransactionUnusable` (`:413-438`) — `money_replay.py`, `real_payments_executor.py`; `StagedPaymentResult.written_here` (`:599-603`) — «LANDING EVIDENCE» для фазы тика (`real_payments_executor.py:474-476`); `_settle_staged_failure`/`_record_refusal_in_transaction`/`_resolve_identity` (`:2047-2206`).

Почему важно: 021 объявляет «лок один, разделяемый, берётся `PaymentService.execute`» (021 `spec.md:112`) и «`tick.py` на `PaymentService.execute` … savepoint на операцию» (`:34`, `T2105`), но owner surface 021 — только `app/core/simulator/`, `storage.py`, `crypto.py`, `simulator.py:972-2322` (`:6`). После 021 `acquire_shared_equivalent_locks` (сервис + `MoneyBoundary`) остаётся без вызывающих, а staged-урегулирование — с единственным. Удалять их некому, и следующий читатель `payments/service.py` снова разбирает 350 строк «для кого это».

Минимальное исправление: расширить owner surface 021 (или задачу `T2105`) явным пунктом «удаление симуляторных швов из `payments/service.py` и `money_boundary.py`, ставших мёртвыми», с перечнем символов выше. Covered-by: 021 (неполно). Contract: no.

### AMC-9 — 021: список писателей линий неполон, гард пропускает `.status`

Evidence. 021 `spec.md:15` перечисляет: `inject_executor.py:531,555` (долги — уже ушли в `Book`), `real_scenario_seeder.py:226`, `trust_drift_engine.py:310,488`, `simulator.py:1117,1359`. По HEAD `git grep` даёт ещё: `api/v1/simulator.py:1541` `tl.status = "closed"`; `inject_executor.py:688` и `:827` `TrustLine(...)` в инжект-событиях; `inject_executor.py:913-915` `p_row.status = "suspended"` / `frozen_tl.status = "frozen"`. Гард `T2107` (`spec.md:60`): «ноль конструкций `Debt(` / `TrustLine(`, ноль присваиваний `.amount` / `.limit`, ноль `update(Debt|TrustLine)`» — присваивание `.status` и `.policy` не входит, значит `:1541` и `:915` пройдут гард, хотя это запись в `trust_lines` мимо `TrustLineService.close` (который требует нулевой долг в обе стороны, `trustlines/service.py:479-482`, и подпись).

Почему важно: §9 anti-vacuum — гард, объявляющий «линии пишет только `TrustLineService`», обязан краснеть на подложенном `tl.status = ...`; сегодняшняя формулировка этого не даёт. Заморозка линии инжектом (`:915`) — единственный продуктовый путь к статусу `frozen` (BACKLOG:1045 говорит «недостижим продуктом» — из хаба; из симулятора достижим).

Минимальное исправление: в 021 — добавить четыре якоря в Problem п. 1 и расширить гард на `.status =`, `.policy =` (и `update(Participant)` не нужно — `participants` не денежная таблица). Covered-by: 021 (неполно). Contract: no.

### AMC-10 — три соглашения о владении транзакцией; линии — мимо `money_boundary`

Evidence: `TrustLineService.create/update/close` коммитят сами на сессии запроса (`trustlines/service.py:286`, `:436`, `:547`), `ParticipantService` тоже (`:81`, `:188`); `PaymentService.execute` не коммитит никогда (`:1113`), владелец — `pay()` (`:2325-2326`) или staged-вызывающий; `ClearingService` подменяет `self.session` на пиннутую рабочую сессию (`:1703`, `:1719`, `:1730`) и коммитит сам (`:2207`); admin-роуты коммитят в роуте после `require_serializable` (`admin.py:1185,1330,1436`). `require_serializable` вызывают платёж, клиринг, инжект, дрейф, admin — но не `TrustLineService.update`, который делает read-then-write инвариантно значимого поля (`:359-365`: `used = ...; if new_limit < used: raise; trustline.limit = new_limit`). Держит только фенс движка (`config.py:85-107`) — докстринг `money_boundary.py:237-238` прямо говорит, что `require_serializable` «guards a session HANDED IN by a caller at another level».

Почему важно: не потеря денег (движок зафенсен), а несогласованность контракта: «каждый писатель, участвующий в инварианте, обязан быть `SERIALIZABLE`» (`money_boundary.py:233-238`) записан для долгов и не распространён на лимит, от которого долг зависит. И два стиля владения транзакцией — источник ошибок вроде AMC-16.

Минимальное исправление: одна строка `await MoneyBoundary.require_serializable(self.session, writer="trustline.update")` перед `:359` (и в `close` перед `:479`); задокументировать в `money_boundary.py` таблицу «кто владеет коммитом». Covered-by: none. Contract: no.

### AMC-11 — проглатывание после сырого SQL оставляет транзакцию в `25P02`

Evidence: `clearing/service.py:1214-1220`:
```python
            except Exception:
                logger.warning("event=clearing.find_cycles_sql_failed equivalent=%s", equivalent_code, exc_info=True)
                cycles = []
```
далее `:1286-1287` `all_debts = (await self.session.execute(stmt))` — на PostgreSQL после ошибки в `find_triangles_sql` транзакция прервана, и этот `execute` падает `InFailedSqlTransaction` (25P02), которую `_auto_clear_find` (`:2361-2373`) заворачивает в `E010` — исходная причина остаётся в warning-логе, наружу идёт вторичная. Комментарий `:1432-1434` называет это «pre-existing, recorded not fixed», но `grep -n "T1210-bis\|find_cycles_sql_failed\|25P02" specs/BACKLOG.md` → пусто. Тот же класс: `_equivalent_precision` `:630-637` `except Exception: return 2` (вызывается только прямыми вызывающими без `precision`).

Минимальное исправление: не ловить (пусть `find_cycles` падает с настоящей ошибкой) либо обернуть два SQL-детектора в `begin_nested()` — тогда fallback на DFS честный. Covered-by: none (023 Non-goals: диагностика `find_cycles` не меняется; ограничения 1–3, 6, 7 — другие). Contract: no.

### AMC-12 — кэш графа выключен, обвязка живёт

Evidence: `router.py:154` `ttl = settings.ROUTING_GRAPH_CACHE_TTL_SECONDS` умолчание `0` (`config.py:182`); `grep` по `.env.example`, `docker/` — пусто; при `ttl == 0` ветки `:155-173` и `:303-311` не выполняются никогда. При этом: `invalidate_cache` вызывают `trustlines/service.py:298,437,548`, `clearing/service.py:2275`, `payments/service.py:547`; `:158-162` «Backward-compatible cache unpacking … `if len(cached) == 5`» — запись всегда 6-кортеж (`:304-311`), тесты пишут 6-кортежи (`test_simulator_network_growth.py:452`); `_confine_router_to_perimeter` (`payments/service.py:932-969`) — 20 строк докстринга о том, как не задеть кэш; параметр `use_shared_routing_cache` (`:1109`, `:2308`).

Минимальное исправление: удалить ветку 5-кортежа; решение владельца — кэш нужен (тогда включить по умолчанию и мерить) или нет (удалить кэш и пять инвалидаций). Covered-by: none. Contract: no.

### AMC-13 — три «дождаться под отменой»

Evidence: `payments/service.py:152-178` `_drain_payment_cleanup`, `:181-192` `_drain_call`; `clearing/service.py:188-211` `_drain_task`, `:457-477` `_commit_to_terminal` — один и тот же цикл `while not task.done(): try: await shield(task) except CancelledError: remember`. Минимальное исправление: одна функция в `app/utils/` с двумя вызывающими; не абстракция, а дедуп. Covered-by: none. Contract: no.

### AMC-14 — два словаря сверки по одному ключу

Evidence: `reconciliation.py:157-162` `_READABLE_ENVELOPES = {("CLEARING",1): FULL_RECOMPUTATION, ...}` и `:729-734` `_RULES = {("CLEARING",1): _clearing, ...}`; `_criterion_b` читает первый для `level` (`:750`) и второй для функции (`:769`). 023 решение 5 добавляет `("CLEARING", 2)` — в оба. Минимальное исправление: один словарь `(kind, version) -> (level, rule)`. Covered-by: 023 (b). Contract: no.

### AMC-15, AMC-16 — см. таблицу

AMC-16 evidence: `payments/service.py:2403` `failure = await _drain_payment_cleanup(self.session.commit)` в `_end_failed_attempt` — вызывается для каждой неудавшейся попытки, включая отказ **до допуска** (маршрут не найден), и для заимствованной сессии (`_borrowed_session`, `:226-233`) это коммит транзакции вызывающего; докстринг `:2388-2390` это признаёт. Вызывающий `action_payment` (`api/v1/simulator.py:1689`) до этого только читает, так что сегодня безвредно; 021 переписывает `action_*`.

## Ответы на вопросы, не ставшие находками

**Вопрос 2 (один ли писатель).** Да для `debts`: `git grep -nE "Debt\(|\.amount\s*=[^=]|update\(Debt\)|delete\(Debt\)|UPDATE debts|DELETE FROM debts|INSERT INTO debts" -- app scripts` вне `book.py` → ноль настоящих попаданий (`scripts/seed_db.py:483`, `measure_*.py` — через `NewDebt`/`Book`). Триггер `geo_debts_journal` (`journal_triggers.py:78-94`) отказывает `GE001` любому DML без `OPEN`-конверта — второй барьер. Для `trust_lines` — AMC-9.

**Вопрос 3 (дублирование инвариантов).** Направление `creditor -> debtor`: `router.py:254-259`, `payments/service.py:1845-1852`, `invariants.py:112-114`, `clearing/service.py:749-751,952-953`, `book.py:347-348`, `balance/service.py:160-164`, `reconciliation.py:496-518` — семь мест, все согласованы; последнее намеренно независимо (`:497-502`). Знак долга: `db/models/debt.py:68-71` CHECK `chk_debt_amount_positive` + `book.py:204-223` предикат + `db/types.py` (не читал). Идемпотентность `tx_id`: одно место (`_resolve_existing_payment`, D) с тремя вызывающими (`:1287`, `:2184`, `:2570`, `:2836`) — хорошо. Подпись: `verify_signature` вызывают платёж `:1262`, линии `:156,349,474`, участник `:55,173`, auth `:82` — каждый строит свой `signed_payload`; это протокольные payload'ы (§5, §6), дублирование формы, не правила; общая функция только скрыла бы, что подписывается. Лимит/ёмкость — AMC-2.

**Вопрос 5 (транзакционная модель).** Явных `begin()` нет; `begin_nested()` — три места (`book.py:943`, `payments/service.py:1587`, `:2133`) плюс симулятор. Advisory-локи — только `money_boundary.py` (`pg_advisory_xact_lock_shared` `:151`, `pg_advisory_lock` `:193`, `pg_advisory_unlock` `:207`); `git grep pg_advisory -- app` других не даёт. `redis_distributed_lock` — три вызывающих (`payments.py:79` per-participant-per-equivalent, `clearing.py:53`, `main.py:214`), no-op без Redis (`distributed_lock.py:36-38`) — F-023-6 и BACKLOG:113 это уже держат. Денег мимо `money_boundary`: нет; лимитов мимо — AMC-10.

**Вопрос 7 (сверка и integrity).** Две системы, связь — только очередь в `_integrity_loop` (`main.py:191,221`) и колонка `integrity_hold_result_id` (`db/models/equivalent.py:36-44`), которую ставит сверка (`reconciliation.py:1051-1058`) и читает граница (`money_boundary.py:321-332`). `integrity.py` — протокол §11.3 (контрольная сумма), `reconciliation.py` — §11.2.1/§11.6 (сравнение с историей). Первая — без читателя (AMC-1).

**Вопрос 8 (§19).** Банковского класса без обоснования: (1) цепочка контрольных сумм на денежном пути (AMC-1); (2) `IntegrityAuditLog` как второй журнал рядом с триггерным (`debt_journal_entries`) — читателя нет; (3) `check_payment_delta` (`money_boundary.py:375-457`) — самопроверка книги в той же транзакции: дёшево (2 запроса на эквивалент), но по §19.2 п. 3 это «обнаружение случайных дефектов» и так должно называться; (4) кэш графа с обвязкой при выключенном кэше (AMC-12); (5) три копии cancel-drain (AMC-13). Недостающее для протокола в этой зоне: ничего денежного; периодический клиринг — 023; `frozen` — BACKLOG:1045.

## Что не проверено

- Никакой runtime: pytest, Postgres, Playwright не запускались (бриф). INFERENCE в AMC-1 о SSI-конфликтах непересекающихся платежей **не измерен**; стенд описан.
- `app/core/simulator/` целиком, `app/api/v1/admin.py` целиком, `app/api/v1/simulator.py` целиком — только `grep` и точечные срезы; выводы AMC-8/9/16 опираются на них.
- `app/utils/validation.py` (`money_storability_violation`, `parse_money_amount`), `app/utils/money.py` (`to_money_str`), `app/db/types.py` (`MoneyNumeric`) — не читал; дверь денег принята как заявлено.
- `app/db/journal_tables.py` — только grep FK; `journal_triggers.py` — первые 150 строк (сам триггер прочитан, гарды таблиц — нет).
- Миграции 029–031 и соответствие модели `Transaction` миграции 030 — по комментарию модели, не по файлу.
- Тесты — только по именам/grep; какие из них держат `prepare`-метрики и мёртвую ветку in-progress (12 файлов в grep) — не открывал; перед AMC-4 их надо пройти.
- `api/openapi.yaml` — только `available` (`:5248`) и `/clearing/auto`; wire-формы не сверял.
- Корректность `find_flow_routes` (BFS с `amount=0` на `:479`, вестигиальный параметр `amount` в `_bfs_single_path`) и `calculate_max_flow` — прочитано, но не анализировалось на оптимальность; это маршрутизация, не деньги.

## Оценка направления текущего плана

**023 режет верно** — граница обнаружение/исполнение/сверка проходит по данным (id долгов → `FOR UPDATE` → intent конверта), и 023 меняет ровно идентичность (F-023-1) и правило сверки (решение 5), не трогая книгу и границу 019. Два замечания: (1) 023(b) «исполнение цикла с объявленной суммой через границу 019» унаследует `_execute_clearing_with_amount` с двумя чекпойнтами на цикл (F-023-9 их считает как данность) — решение по AMC-1 стоит принять **до** 023(b), иначе замер стоимости исполнения MTCS будет включать накладной расход, который потом уберут, и пороги придётся переутверждать; (2) 023 оставляет `find_cycles` диагностикой — AMC-11 и `:1182` тогда надо записать в BACKLOG как ограничения диагностики рядом с 1–3, 6, 7, иначе они потеряются.

**021 режет верно** (настоящие ключи вместо дыр в подписи; инжект и сид — операции `Book`; один тик на `execute`), но неполно в двух местах: список писателей и гард (AMC-9) и владение симуляторными швами в ядре (AMC-8). Третье: 021 `T2103` переводит инжект на `Book.post(INJECT)`, после чего `Book.current` (`book.py:1000-1007`, единственный вызывающий `inject_executor.py:518`) остаётся только для тестов — записать.

**Что стоило бы сделать до них:** AMC-1 (решение, не код — §19.4), AMC-4 (день, без контракта), правки спек 021 по AMC-8/9, освежение якорей 016.

**016 в ядре после 018/019:** F-016-1 — актуальна (`api/v1/integrity.py:191-206` + `:231`, шесть сканов на эквивалент подтверждены); F-016-2 — актуальна, якоря сдвинулись (`payments/service.py:2011-2035`, `clearing/service.py:2157-2189`, trust-lines без изменений), и её объём зависит от решения AMC-1; F-016-3 — актуальна, якоря протухли (AMC-3); F-016-4 — актуальна без изменений (`trustlines/service.py:613-713`); F-016-5 — **пересмотреть**: `recovery.py` удалён, ветка `payments/service.py:881-897` мёртва — не унифицировать, а удалить (AMC-4); admin-читатели — сознательная совместимость Q2. 016 остаётся неавторизованной по §19.5, и это правильно: ни одна её находка не отвечает на вопрос 1; но её якоря — evidence, и они должны быть верны на HEAD, иначе следующий ревьюер переоткроет их как новые.
