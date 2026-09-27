# impl-payments: платежи, маршрутизация, линии доверия, участники, баланс, аутентификация
HEAD: 4119ace. Всё прочитано полностью: `app/core/payments/service.py` (2843), `app/core/payments/router.py` (599), `app/core/payments/__init__.py` (0), `app/core/trustlines/service.py` (791), `app/core/participants/service.py` (211), `app/core/balance/service.py` (296), `app/core/auth/{canonical,crypto,service}.py` (101/49/155), `app/api/v1/{payments,trustlines,participants,balance,auth,websocket}.py` (138/87/86/34/110/96), `app/schemas/{payment,trustline,participant,balance,auth}.py` (80/59/64/28/34), `app/utils/{money,validation,security,exceptions,error_codes}.py` (118/632/112/125/32).
Прочитано как контракт (выборочно): `app/core/money_boundary.py` (:55-230, :330-457), `app/core/invariants.py` (:60-200), `app/core/integrity.py` (:19-135), `app/api/deps.py` (проверки статуса), `app/db/session.py`, `app/main.py:376-460`.
Не прочитано: `app/core/ledger/book.py` (архитектура у другого агента), `app/schemas/{admin,simulator,graph,metrics,integrity,equivalent(s),clearing,common}.py`, вызывающие из симулятора.

## Summary
Денежный путь платежа (`execute` → `_bind_payment` → `_apply_payment` → `Book`) логически корректен. Направление `creditor -> debtor` соблюдено везде, где считается ёмкость (`service.py:1845-1858`, `router.py:256-296`, `balance/service.py:160-188`, `invariants.py:108-118`). Идемпотентность `tx_id` отрабатывает все семь исходов из таблицы 019, float на денежном пути нет, и способа двойного эффекта я не нашёл.
Главная новая находка — **три реализации одного расчёта ёмкости расходятся** (IP-01). Ядро (`_segment_capacity`) и баланс считают ёмкостью долг встречного участника и тогда, когда линии нет. Роутер строит рёбра только из активных линий. В итоге участник, которому должны, не может заплатить своему должнику: `/balance` показывает сумму доступной, а `POST /payments` отвечает «нет маршрута». Воспроизведено скриптом на коде роутера.
Вторая — архитектурная и пока выведенная из кода (INFERENCE, не замерена, IP-02). На каждом платеже дважды считается контрольная точка **всего эквивалента** (все долги и все линии, плюс полные `check_trust_limits`/`check_debt_symmetry`), и всё это под SERIALIZABLE. Значит, любые два одновременных платежа одного эквивалента образуют rw-цикл SSI, даже если их пары не пересекаются. Это противоречит посылке 019: «конфликт разрешается SERIALIZABLE по паре».
Остальное — хвосты 019 (мёртвый набор промежуточных состояний, устаревшие якоря и комментарии), рудиментарные параметры, функция `execute` на 475 строк, мелкие несоответствия контракту.
Первым шагом я бы: (1) починил роутер (IP-01, узко, S); (2) поставил стенд на IP-02 (два платежа по непересекающимся парам, счёт `40001`) до следующей программы, которая опирается на пропускную способность SSI.

## Findings
| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| IP-01 | P2 | logic-bug | `app/core/payments/router.py:256-300` | Роутер не видит ёмкость «должен мне» без линии в сторону платежа; ядро и `/balance` её видят, и платёж отказывает там, где баланс обещает | none | S |
| IP-02 | P2 | architecture | `app/core/payments/service.py:1901-1918`, `:2011-2035`; `app/core/integrity.py:24-47`, `:92-117` | Контрольная точка всего эквивалента ×2 на платёж под SERIALIZABLE: все одновременные платежи эквивалента конфликтуют (INFERENCE), стоимость O(долгов+линий) ×~6 сканов | none | S замер / M правка |
| IP-03 | P2 | logic-bug (подпись) | `app/core/trustlines/service.py:137-143`, `:339-343` | Подписи линий без свежести: старая подпись `update` снова ставит прежний лимит, подпись `create` снова открывает закрытую линию; T1517 сужена до типа операции и это отбросила | BACKLOG:«Отложено из 015» (T1517, F-015-10), неполно | M (контракт) |
| IP-04 | P3 | docs-drift (контракт) | `app/core/payments/service.py:2458-2463` vs `:899-905` | Допущенный отказ: первый ответ 4xx/504 с конвертом ошибки, повтор того же `tx_id` — 200 с `ABORTED`; протокол, прил. E: «тот же результат» | none | S |
| IP-05 | P3 | dead-code / docs-drift | `app/core/payments/service.py:881-888`, `:2678`, `:168`, `:621`, `:941-948`, `:1303`, `:1443-1446` | Остатки 019: недостижимые (миграция 030) промежуточные состояния, устаревшие якоря и имена (`_create_payment_impl`, журнал) | none (016 F-016-5 берёт неверно) | S |
| IP-06 | P3 | dead-code | `service.py:981,992-996`; `:912,1106,2217`; `:1478`; `app/core/auth/service.py:108` | Рудименты: `commit=` принимает только True, `idempotency_key` протянут через 5 сигнатур и не читается (ruff ARG002), `revoke_refresh_token` без вызывающих | BACKLOG:T1519 частично | S |
| IP-07 | P3 | readability | `app/core/payments/service.py:1100-1575` | `execute()`: 475 строк, C901=45, 175 операторов, 5 флагов поведения; 19 копий `try: import metrics … except: pass` | none | S–M |
| IP-08 | P3 | logic-bug (policy) | `app/utils/validation.py:621-627` vs `app/core/payments/router.py:272-276` | `max_hop_usage: "0.0"` проходит валидацию, но роутер его молча игнорирует; `"NaN"` даёт 500 уже после проверки подписи | none | S |
| IP-09 | P3 | logic-bug (auth) | `app/core/auth/service.py:270-291`, `app/utils/security.py:43-50` | Ротация refresh-токена не атомарна: два одновременных `/auth/refresh` с одним токеном оба успешны; без Redis отзыв живёт в памяти одного процесса | BACKLOG:«Пробелы покрытия без владельца» (auth refresh `UNVERIFIED/NO FIX`) | S |
| IP-10 | P3 | logic-bug (контракт) | `app/utils/exceptions.py:57-59` | `NotFoundException` несёт код `E001` («Маршрут не найден», §9.6) | none | S |
| IP-11 | P3 | readability / dead-code | `app/core/balance/service.py:60-84`; `app/core/payments/router.py:158-161`, `:211-217` | Черновые рассуждения вместо докстринга («Wait, simplified… Actually»); совместимость с 5-кортежем в кэше, который пишется только 6-кортежами | none | S |
| IP-12 | P3 | docs-drift | `docs/ru/09-decisions-and-defaults.md:97`, `:252-253` | Таблица состояний всё ещё пишет «engine выставляет NEW, затем PREPARED», протокол локов — «входят через `PaymentEngine`» | none | S |

## Детали

### IP-01: роутер и ядро по-разному считают ёмкость ребра без линии
Evidence. Роутер добавляет ребро только по активной линии:
```python
# app/core/payments/router.py:256-296
for tl in trustlines:            # only TrustLine.status == 'active' (:197-201)
    creditor_id = tl.from_participant_id
    debtor_id = tl.to_participant_id
    ...
    cap = (limit - debt_debtor_owes_creditor) + debt_creditor_owes_debtor
    if cap > 0:
        self._add_capacity(debtor_pid, creditor_pid, cap)
```
Комментарий `:298-300` («If limit=0 and creditor owes debtor, … still provides positive capacity») верен только при **существующей** линии. Ядро считает ёмкость без линии:
```python
# app/core/payments/service.py:1855-1858
limit = line if line is not None else Decimal("0")
receiver_owes = await self._debt_amount(receiver_id, sender_id, equivalent_id)
sender_owes = await self._debt_amount(sender_id, receiver_id, equivalent_id)
return limit - sender_owes + receiver_owes
```
Баланс делает то же явно: `app/core/balance/service.py:190-202` («Debts without trustlines still contribute positive capacity with Limit=0»).

Репродьюсер. Скрипт `scratchpad/review/impl-payments/repro_router_debt_only.py` исполняет настоящий `PaymentRouter._build_graph_impl` на подставной сессии, БД не нужна. Состояние: A доверяет B (линия A→B, лимит 100), B должен A 50, линии B→A нет. Запрос: A платит B 30.
```
graph: {'A': {}, 'B': {'A': Decimal('50')}}
A->B 30 routes: []
core _segment_capacity A->B would be: 50
```
- Ожидание по собственным правилам ядра: платёж проходит и гасит 30 из долга B (`_segment_capacity` = 50, `Book._apply_payment_flow` сначала уменьшает встречный долг).
- Фактически: `execute` получает `routes_found == []` и отвечает 400 `E002` «No route found with sufficient capacity» (`service.py:1423-1441`). При этом `GET /balance` у A показывает `available_to_spend` ≥ 50.
- Та же дыра у закрытой или замороженной линии, если за ней остался встречный долг.

Почему P2, а не P1. Денег это не теряет: платёж отказывается ложно, лишних проводок нет. Протокол §6.3.1 (`docs/ru/02-protocol-spec.md:456-466`) даёт формулу `limit - debt` без слагаемого встречного долга и по направлению двусмыслен. Поэтому этот расчёт утверждает не протокол, а внутреннее расхождение трёх реализаций.

Минимальное исправление: в `_build_graph_impl` после цикла по линиям добавлять для каждого долга (debtor d, creditor c), у которого нет активной линии d→c, ребро c→d ёмкостью `amount`. Это то же правило, что `balance/service.py:190-209`. Флаги политики у такого ребра по умолчанию разрешающие.

Что может сломаться / call-sites: `/capacity`, `/max-flow` (`app/api/v1/payments.py:34-56`), симулятор (`app/api/v1/simulator.py:2308`), `has_topology_path` / `build_topology` (`router.py:66-148`: там тоже только линии, так что различение E001/E002 станет неверным для таких рёбер). Кэш графа не затронут: TTL по умолчанию 0 (`app/config.py:182`).

Тест как индикатор: поиск по `tests/` (`debt-only`, `without trustline`, `reverse debt` × `PaymentRouter`) нашёл ноль тестов на ребро «только долг». Паритет роутер ↔ `_segment_capacity` ↔ баланс не проверяется ничем.

Covered-by: none. F-015-8 (T1510) касается предсказательных маршрутов, а не графа. Contract: no.

### IP-02: контрольная точка всего эквивалента на каждом платеже под SERIALIZABLE (INFERENCE)
Evidence. Внутри операции платежа вызывается
```python
# app/core/payments/service.py:1903-1907 (before) и :2017-2019 (after, per equivalent)
checkpoints_before[eq_id] = await compute_integrity_checkpoint_for_equivalent(session, equivalent_id=eq_id)
```
а `compute_integrity_checkpoint_for_equivalent` читает **все** долги и **все** линии эквивалента (`app/core/integrity.py:24-47`) и прогоняет полные `check_trust_limits` и `check_debt_symmetry` без фильтра по парам (`:92-117`). Движок работает только на SERIALIZABLE (`app/db/session.py:17`, `app/config.py:88,324`).

Почему это важно. Два одновременных платежа T1 и T2 одного эквивалента по непересекающимся парам: каждый читает все строки `debts` эквивалента и каждый пишет свою. Получаются rw-зависимости T1→T2 и T2→T1, это опасная структура SSI, и PostgreSQL отменит одну транзакцию с `40001`. 019 снял owner-, tx- и pair-локи с посылкой «конкурентный платёж по той же паре разрешает SERIALIZABLE» (`service.py:1759-1768`). Контрольная точка делает «той же парой» любую пару эквивалента.
- Бюджет: `COMMIT_RETRY_ATTEMPTS=3`, `PAYMENT_TOTAL_TIMEOUT_SECONDS=10` (`app/config.py:187,190`). Под нагрузкой ожидается исчерпание и повторяемый `409/E008`.
- Стоимость: около шести полных сканов эквивалента на платёж, O(долгов + линий), ради строки аудита `IntegrityAuditLog`. Именно такой вопрос соразмерности ставит §19.
- **Не замерено**: предложен стенд ниже. Я не проверял, какой SIREAD-лок (на отношение или на страницы) берёт план запроса. Цикл возникает в обоих случаях, если транзакции пересекаются во времени.

Минимальное исправление: сначала стенд (S). Два `pay()` по непересекающимся парам одного эквивалента, пересечение по времени через барьер после `_read_payment_prestate`, счёт `40001` и повторов, контроль — те же платежи в разных эквивалентах. Если подтвердится, заменить контрольную точку в платеже на проверку по затронутым парам: проверки по парам уже стоят в `:1971-1978`. Полную контрольную точку оставить плановому циклу целостности. Это правка аудита, а не денежной семантики.

Что может сломаться: читатели `state_checksum_before/after` у `IntegrityAuditLog` типа `PAYMENT` (админ-экран аудита), BACKLOG T1554 (подмена `checksum_after`).

Тест как индикатор: тесты 019 T1908 (`test_p019_t1908_lock_removal_experiments_postgres.py`) мерили lost-update на одной паре. Тест на непересекающиеся пары мне не встретился (по имени не нашёл, файлы не читал).

Covered-by: none. Contract: no.

### IP-03: подписи линий доверия можно переиграть (свежесть выпала из T1517)
Evidence.
```python
# app/core/trustlines/service.py:137-143 (create)
signed_payload = {"to": data.to, "equivalent": data.equivalent, "limit": data.limit}
# :339-343 (update)
signed_payload = {"id": str(trustline_id)};  signed_payload["limit"] = data.limit
```
В подписи нет ни `tx_id`/`msg_id`, ни типа операции, ни времени, хотя протокол §4.1 (`docs/ru/02-protocol-spec.md:249-261`) подписывает конверт с `msg_id`, `msg_type` и `tx_id`.

Сценарий. Владелец поднял лимит до 1000 (подпись S1), позже снизил до 100. Держатель S1 и bearer-токена владельца (или сам хаб, против которого подпись и служит доказательством согласия) снова шлёт `PATCH {limit:"1000", signature:S1}`, и лимит, а с ним риск кредитора, возвращается к 1000. Подпись `create` так же переоткрывает закрытую линию.

Почему «неполно покрыто». F-015-10 называла и тип операции, и свежесть («отсутствие operation domain и freshness допускает поздний replay»). Решение по T1517 от 2026-09-13 оставило «узко: связывание типа операции» (`specs/015-financial-core-verification/spec.md:3736`). Переигрывание в пределах одной операции не закрыто ничем.

Минимальное исправление: «документировать, не патчить» до спеки. Нужен `tx_id` (или nonce) в подписываемом payload линий и хранение использованных значений, а это смена контракта (`api/openapi.yaml`, клиенты).

Тест как индикатор: нет.

Covered-by: BACKLOG «Отложено из 015» T1517 (неполно). Contract: yes.

### IP-04: повтор допущенного отказа отвечается другим HTTP-статусом
Evidence. Первый раз `_settle_failed_attempt` записывает `ABORTED` и поднимает исходную ошибку:
```python
# app/core/payments/service.py:2458-2463
refusal = _definitive_refusal(attempt, exc, admission)
if refusal is not None:
    stored = await self._record_refusal(sessions, refusal, deadline=deadline)
    ...
raise exc
```
При повторе `_resolve_existing_payment` возвращает `_tx_to_payment_result` (`:905`), то есть 200 и `{"status":"ABORTED","error":{…}}`. Тест закрепляет именно это: `tests/integration/test_p015_t1523_replay_after_a_hold_or_an_abort.py:288` (`504`) и `:315` (`200`, `ABORTED`). Протокол, прил. E (`docs/ru/02-protocol-spec.md:2219-2221`): «сервер возвращает тот же результат». OpenAPI (`api/openapi.yaml:2874`) объявляет 200 «committed or aborted».

Почему это важно. Клиент по-разному обрабатывает первый ответ и повтор. Staged-путь для того же случая уже умеет отдавать публичную ошибку сохранённого отказа (`_public_error_of_stored`, `:441-464`), а API-путь нет.

Минимальное исправление: решить, что верно. Либо дописать в прил. E, что повтор отдаёт сохранённый исход как 200 `PaymentResult`. Либо в `pay()` поднимать `_public_error_of_stored(stored)` для сохранённого `ABORTED` (одна строка). Covered-by: none. Contract: yes.

### IP-05: остатки 019 в `service.py`
Evidence.
- `:881-888`: `if existing_tx.state in {"NEW","ROUTED","PREPARE_IN_PROGRESS","PREPARED","PROPOSED","WAITING"}` после проверки `type == "PAYMENT"` (`:783`). Для `PAYMENT` это недостижимо: миграция 030, ограничение `chk_transaction_payment_terminal` (`docs/ru/09-decisions-and-defaults.md:89`).
- `:2678`: отображение «любое не-терминальное → `ABORTED`» ради того же.
- `:168`: комментарий «leave a durable NEW/PREPARED row».
- `:1303`: «forbidding a new PREPARED state».
- `:1443-1446`: ссылка на несуществующий `_create_payment_impl` (`:448-451`).
- `:941-948`: якоря `router.py:167-173`, `:342-351`, `:369-373`; фактически `:166-172`, `:302-311`, `:329-333`.
- `:621`: «the journal's own quantization predicate refuses»: `journal.py` удалён в 018. Предикат теперь `money_storability_violation` в `Book` и `MoneyNumeric` (`app/utils/validation.py:339-345`), смысл сохранён, имя устарело.

Почему это важно: набор «in progress» выглядит живой политикой и уже попал в 016 как дубль (F-016-5), который якобы надо унифицировать.

Минимальное исправление: удалить ветку `:881-897` и сузить `:2678`. Комментарии поправить при касании. Covered-by: none. 016 F-016-5 предлагает общий `ACTIVE_PAYMENT_TX_STATES`: это ошибочное направление, ветку надо удалить. Contract: no.

### IP-06: рудиментарные параметры и символы
Evidence.
- `service.py:981,992-996`: `commit: bool = True`, а `False` сразу поднимает `ValueError`. Единственный продовый вызов передаёт `commit=True` (`app/api/v1/simulator.py:1695`).
- `idempotency_key` принимают `create_payment` (`:912`), `create_payment_internal`, `pay` (`:2217`), `_pay_attempt` и `execute` (`:1106`), и нигде он не читается (ruff `ARG002 Unused method argument: idempotency_key` на `:1106`). Колонка `Transaction.idempotency_key` всегда пишется `None` (`:1478`).
- `AuthService.revoke_refresh_token` (`app/core/auth/service.py:108`): `git grep revoke_refresh_token` показывает только определение, вызовов нет ни в `app/`, ни в `tests/`, ни в `scripts/`.

Минимальное исправление: удалить. Для `commit` сначала поправить вызывающий в `simulator.py:1695`. Covered-by: BACKLOG T1519 (частично: там `validate_idempotency_key`, `get_payment`, `PaymentDetail`). Contract: no.

### IP-07: `execute()` смешивает семь ответственностей
Evidence: `app/core/payments/service.py:1100-1575`, ruff C901=45, PLR0915=175. Швы:
- валидация и подпись с девятью блоками метрик — `:1146-1273`;
- идемпотентность — `:1274-1297`;
- предпроверка стоп/hold — `:1299-1322`;
- эффективные ограничения маршрута — `:1324-1367`;
- маршрут и периметр — `:1371-1467`;
- допуск и операция — `:1469-1523`;
- сборка результата — `:1525-1575`.

`record_refusal` переключает весь режим урегулирования отказа (staged или `pay()`, `:1514-1523`); кроме него есть `use_shared_routing_cache`, `emit_start`, `require_signature` и `deadline`. `PAYMENT_EVENTS_TOTAL.labels(...)` встречается 19 раз, почти каждый раз внутри `try: from app.utils.metrics import …; …inc() except Exception: pass`. Защищать тут нечего: `router.py:19` импортирует метрики на уровне модуля.

Минимальное исправление: хелпер `_count(event, result)` (S). Затем вынести `_validate_and_authenticate` (`:1146-1297`) и `_route` (`:1324-1467`) без изменения поведения. Covered-by: none. Contract: no.

### IP-08: политика `max_hop_usage` валидируется и читается по разным правилам
Evidence: валидатор (`app/utils/validation.py:621-627`) принимает любое `Decimal(str(value)) >= 0`, роутер делает `int(tl.policy.get('max_hop_usage', 1)) == 0` внутри `except Exception: pass` (`router.py:272-276`). Исполнено:
```
0.0 accepted / router int raises ValueError      -> ребро остаётся промежуточным
Infinity accepted / router int raises ValueError
NaN InvalidOperation                               -> 500 вместо 400, после verify_signature
```
Минимальное исправление: в валидаторе требовать неотрицательное целое (строку цифр или `int`); в роутере `Decimal(...) == 0`. Covered-by: none. Протокол `max_hop_usage` не объявляет вовсе (§3.3, `:183-191`). Contract: no.

### IP-09: гонка ротации refresh-токена
Evidence: `refresh_tokens` делает `decode_token` (с проверкой отзыва), потом `await self.db.execute(...)`, потом `revoke_jti` (`app/core/auth/service.py:270-291`). Проверка и отзыв не атомарны. С Redis это `exists` плюс `set` без `NX` (`security.py:46,58`). Без Redis (по умолчанию, `app/config.py:140`) отзыв хранится в словаре процесса (`security.py:49-50`), и в другом воркере отозванный токен действителен до `exp` (7 дней).

Минимальное исправление: `SET NX` на `jwt:jti:revoked:<jti>` как акт отзыва; успех только у того, кто поставил ключ. Covered-by: BACKLOG «Пробелы покрытия без владельца» (конкурентность auth refresh `UNVERIFIED / NO FIX`). Contract: no.

### IP-10: 404 с кодом маршрутизации
Evidence: `app/utils/exceptions.py:57-59`, `super().__init__(message or "Not Found", code=ErrorCode.E001, …, status_code=404)`, а `E001` означает «Routing: Route not found» (`error_codes.py:9`, протокол §9.6 `:1623`). Отсюда «Sender not found», «Trustline not found» и прочие приходят с кодом маршрутизации. Covered-by: none. Contract: yes (коды на проводе).

### IP-11: черновые комментарии и мёртвая совместимость
Evidence:
- `app/core/balance/service.py:60-84`: докстринг с «Wait, simplified… Actually… Wait, if I owe N».
- `app/core/payments/router.py:211-217`: рассуждения «Let's use a separate query».
- `router.py:158-161`: распаковка 5-кортежа («Backward-compatible cache unpacking»), хотя кэш только в памяти процесса и пишется 6-кортежем (`:304-311`).

Covered-by: none. Contract: no.

### IP-12: `docs/ru/09` описывает удалённый движок
Evidence: `docs/ru/09-decisions-and-defaults.md:97` («Payment engine фактически выставляет `NEW`, затем `PREPARED`») и `:252-253` («Service, Admin abort и recovery входят в этот протокол через `PaymentEngine`»). Двумя строками ниже 019 уже зачеркнул соседние фразы, эти остались. Covered-by: none. Contract: no.

## Что не проверено
- Ни один тест с БД не запускался (бриф). IP-02 выведен из семантики SSI PostgreSQL и кода, не замерен; план запроса и гранулярность SIREAD-локов не смотрел.
- `app/core/ledger/book.py` читался только как контракт: как `_apply_payment_flow` сальдирует встречную пару, я принял по докстрингам `service.py:684-689` и `money_boundary.py`.
- Путь симулятора (`real_payments_executor`, `money_replay`) и приём ими `StagedPaymentResult` / `PaymentTransactionUnusable` — зона 021.
- Транзит через замороженного участника и платёж замороженному получателю не заявлял. Получатель — родственный случай записи BACKLOG 2026-09-22: `execute` не читает `receiver.status`, `service.py:1200-1212`.
- `/max-flow` без дедлайна синхронно в event loop на больших графах (`router.py:545-585`): не мерил. Родственно F-015-8 / T1510.
- Кэши (`_graph_cache`, `_summary_cache`) с TTL > 0 и инвалидация между воркерами: по умолчанию выключены (`app/config.py:182,201`), не разбирал.
- WebSocket (`app/api/v1/websocket.py`) не перепроверяет статус участника и срок токена после `accept`: заметил, серьёзность не оценивал.

## Оценка направления текущего плана
- **016 F-016-5** устарела: `recovery.py` удалён, а набор промежуточных состояний в `payments/service.py:881-888` недостижим после миграции 030. Правильный шаг — удалить ветку, а не заводить общий `ACTIVE_PAYMENT_TX_STATES`.
- **016 F-016-3**: якоря устарели (`engine.py` удалён), но расхождение живое. `payments/service.py:63-96` намеренно не ходит по `__context__`, а `trustlines/service.py:67` ходит (`__cause__ or __context__`). Это ровно тот путь маскировки, от которого предостерегает докстринг платежа.
- **T1517** (BACKLOG): сужение до типа операции отбросило свежесть подписи (IP-03). Стоит вернуть это явно при заявке, иначе закрытие T1517 будет выглядеть как закрытие F-015-10 целиком.
- **021/023**: из моей зоны их направление ничто не опровергает. Если какая-то будущая программа будет опираться на пропускную способность «локов нет, есть SSI» (023 планирует периодический раннер клиринга рядом с платежами), IP-02 стоит замерить до неё: полная контрольная точка в каждом платеже делает SSI конфликтным на уровне эквивалента.
