# impl-clearing-ledger — исполнение клиринга, книга долгов, сверка, целостность, admin/служебные эндпоинты
HEAD: 4119ace. Прочитано целиком: `app/core/clearing/service.py` 1-478, 1468-2296 (исполнение; из обнаружения `:479-1468` — только `_cycle_respects_auto_clearing`/`_policy_flag` `:942-1016`, их зовёт исполнение), `app/core/ledger/book.py` (1051), `app/core/ledger/reconciliation.py` (1238), `app/core/ledger/__init__.py` (9), `app/core/money_boundary.py` (457), `app/core/invariants.py` (288), `app/core/integrity.py` (151), `app/db/journal_tables.py` (359), `app/db/journal_triggers.py` (357), `app/db/reconciliation_tables.py` (171), `app/db/types.py` (182), `app/db/session.py`, `app/db/models/{debt,equivalent,transaction,trustline,audit_log,integrity_checkpoint,participant,config,auth_challenge,__init__}.py`, `app/api/v1/{clearing(55),integrity(319),health(152),admin(2329)}.py`, `app/core/admin/metrics.py` (715), `app/main.py` (897), `app/api/deps.py` (360), `app/utils/{distributed_lock,event_bus,exceptions,background_jobs,observability}.py`; миграции 029 (целиком), 030, 031 (downgrade), шапки 022-028.
Не прочитано: `service.py:520-941, 1018-1467` (детекторы — 023), `:2296-2413` (`auto_clear` — 023), `app/db/models/simulator_storage.py`, миграции 022-028 построчно (только шапки; паритет триггеров 029 с `journal_triggers.py` проверен скриптом `scratchpad/review/cmp_trig.py` — все 6 функций и последовательность идентичны).

## Summary
Денежное ядро в зоне держится: исполнение клиринга — одна SERIALIZABLE-транзакция, строки цикла `FOR UPDATE` под исключительным локом эквивалента, стоп/hold `FOR SHARE` до коммита, сумма = минимум по заблокированным строкам, откат конверта savepoint'ом, разрешение неизвестного коммита отдельно от 40001. Триггер журнала закрывает известные ловушки (`nullif(..., '')`, `set_config(..., true)` внутри savepoint, отложенная проверка `OPEN`). **P1 с репродьюсером не найдено.**

Главный диагноз зоны — **детектор есть, а его вердикт никто не видит**: результат сверки (а)/(б) и удержание эквивалента не выходят ни в один read-эндпоинт, `/integrity/status` говорит `healthy` об эквиваленте под hold, ошибки верификатора по эквиваленту не меняют состояние фонового job'а, а эквивалент, созданный через admin API, живёт без baseline — критерий (а) для него навсегда `UNVERIFIABLE`, и это тоже никому не видно (IMPL-CL-01..03). Это §1 «отсутствующее измерение ≠ нулевое» на самой ценной проверке проекта.

Второй класс — admin API: логические дефекты без денег (удаление эквивалента недостижимо после первого прогона целостности; сводка ликвидности без `equivalent` складывает разные валюты; поиск циклов глотает ошибки в «нет циклов»; 6 из 12 «изменяемых» ключей конфигурации ничего не меняют) и ~150 строк дословно продублированной визуализации.

Третий — читаемость: `_execute_clearing_with_amount` — 528 строк / 65 веток, плюс слой устаревших докстрингов (SQLite, `journal.py`, `PaymentEngine`, «owner lock», «For MVP we just update» над кодом, который как раз блокирует).

Первым делал бы IMPL-CL-01+02+03 одной узкой правкой (поле `reconciliation` в `/integrity/status` + baseline при создании эквивалента + ошибка сверки в job-state), затем IMPL-CL-04/05/06.

## Findings
| ID | Sev | Category | path:line | Одной строкой | Covered-by | Effort |
|---|---|---|---|---|---|---|
| IMPL-CL-01 | P2 | logic-bug | app/api/v1/integrity.py:58-133; app/db/models/equivalent.py:14-26 | Вердикт сверки и integrity hold не видны ни в одном read-эндпоинте; `/integrity/status` = `healthy` под hold | none | S |
| IMPL-CL-02 | P2 | logic-bug | app/main.py:170-187, 214-235; reconciliation.py:1199-1208 | Ошибка верификатора по эквиваленту — только лог; job пишет `*_success`, `/health` = ok, метрики нет | none | S |
| IMPL-CL-03 | P2 | logic-bug | app/api/v1/admin.py:1131-1164; reconciliation.py:787-796 | Эквивалент из `POST /admin/equivalents` без baseline → критерий (а) навсегда `UNVERIFIABLE`, молча | none | S |
| IMPL-CL-04 | P2 | logic-bug | app/api/v1/admin.py:1272-1289, 1454-1456; app/core/integrity.py:139-151 | `DELETE /admin/equivalents/{code}` всегда 409 после первого прогона целостности (считаются `integrity_checkpoints`) | none | S |
| IMPL-CL-05 | P2 | logic-bug | app/api/v1/admin.py:776-844 | `/admin/liquidity/summary` без `equivalent` суммирует лимиты и нетто разных валют | none | S |
| IMPL-CL-06 | P2 | logic-bug | app/api/v1/admin.py:2274-2278 | `/admin/clearing/cycles`: `except Exception: raw_cycles = []` — сбой = «циклов нет», отравленная транзакция гасит следующие эквиваленты | none | S |
| IMPL-CL-07 | P2 | dead-code | app/api/v1/admin.py:316-331; app/main.py:254-275 | 6 из 12 «mutable» ключей runtime-конфига без эффекта | частично П4 (только `RECOVERY_*`) | S |
| IMPL-CL-08 | P2 | architecture | app/main.py:378-390, 426-462 | Нет конфигурации логов: request id не попадает в логи; необработанное исключение — plain-text 500 без конверта и `X-Request-ID` | none | S |
| IMPL-CL-09 | P2 | readability | app/core/clearing/service.py:1766-2294, 1543-1764 | `_execute_clearing_with_amount` 528 строк / 65 веток / 172 оператора; `execute_clearing_with_amount` 220 строк | 023 (поверхность, швы не названы) | M |
| IMPL-CL-10 | P2 | architecture | service.py:2039-2062, 2141-2188; app/core/integrity.py:19-128 | Два полных чекпойнта эквивалента внутри клиринга под исключительным локом; `verification_passed` аудита отражает чужие нарушения | 023 F-023-9 — неполно | M |
| IMPL-CL-11 | P2 | duplication | app/api/v1/admin.py:1611-1747 vs 1982-2111; metrics.py:46-52 | Визуализация графа (~150 строк) скопирована в snapshot и ego; атомы нетто округляются HALF_UP здесь и DOWN в metrics | none | S |
| IMPL-CL-12 | P3 | docs-drift | ledger/__init__.py:1-8; journal_tables.py:19-24,346; reconciliation.py:13-15,500; service.py:370-379,396-411,2092-2096; admin.py:1317,1370 | Устаревшие докстринги: живой `journal.py`, SQLite, `PaymentEngine`, owner lock, «For MVP we just update», протухшие якоря | none | S |
| IMPL-CL-13 | P3 | dead-code | invariants.py:22-81; service.py:479-481, 990-1016, 1530-1541 | `check_zero_sum`, `execute_clearing`, `_bind_uuid` живут только ради тестов/скриптов; `_policy_flag` на неизвестной строке даёт согласие | none | S |
| IMPL-CL-14 | P3 | architecture | service.py:1951-1955, 2034-2042, 2163-2167; invariants.py:262-288 | Проверка нейтральности — единственная проверка замкнутости цикла при исполнении, 4 запроса на участника | 023 F-023-9 — риск снять без замены | S |
| IMPL-CL-15 | P3 | docs-drift | service.py:2064-2086; docs/ru/02-protocol-spec.md §7.3, §7.4.1 | Инициатор CLEARING — «первый должник» из неупорядоченного `FOR UPDATE` (протокол: хаб); `payload.cycle` — debt id; `CLEARING_NOTICE` не отправляется | none | S |
| IMPL-CL-16 | P3 | docs-drift | api/openapi.yaml:709-723, 2740+; admin.py:1450-1456; clearing.py:48; service.py:1636,1711 | Канон не объявляет достижимые 409 у `DELETE /admin/equivalents/{code}` и 400/504 у `POST /clearing/auto` | none (канон без владельца, BACKLOG:718) | S |
| IMPL-CL-17 | P3 | duplication | app/main.py:474-535 vs app/api/v1/health.py:40-104, 107-152 | `/health`, `/healthz`, `/health/db` определены дважды, два `_START_TIME`; admin-health отдаёт `str(exc)` | none | S |
| IMPL-CL-18 | P3 | responsibility | app/api/v1/integrity.py:152-270, 273-319 | `POST /integrity/verify` доступен любому участнику: полный скан + запись аудита на вызов; `/audit-log` отдаёт рёбра всех клирингов (INFERENCE) | F-016-1 (только двойной прогон) | S |
| IMPL-CL-19 | P3 | duplication | money_boundary.py:374-410 vs invariants.py:237-259 | Две реализации «нетто-позиции участника» на двух денежных путях | none | S |
| IMPL-CL-20 | P3 | logic-bug | service.py:1590-1622, 1880-1884 | Путь с исключительным локом выбирается по неосвежённому снимку вызывающего; расхождение уводит на путь без лока (живучесть; INFERENCE) | 023 | S |
| IMPL-CL-21 | P3 | architecture | reconciliation.py:325-373, 443-474; integrity.py:139-151 | Сверка каждые 300 с читает весь журнал и все намерения эквивалента; `integrity_checkpoints` без TTL (§12) | none | M |

## Детали

### IMPL-CL-01 — вердикт сверки и hold не видны; `/integrity/status` зелёный под hold
Evidence: единственный читатель `debt_reconciliation_results` / `integrity_hold_result_id` в `app/api` и `app/schemas` — эндпоинт снятия hold (`git grep -n "debt_reconciliation_results\|integrity_hold_result_id\|reconciliation" -- app/api app/schemas` → только `admin.py:1299-1396`). Статус строится из двух проверок чекпойнта:
```python
# app/api/v1/integrity.py:84-86, 118-124
        try:
            await checker.check_trust_limits(equivalent_id=eq.id)
            invariants["trust_limits"] = InvariantResult(passed=True, violations=0)
...
        equivalents_status[eq.code] = EquivalentIntegrityStatus(
            status=status,
```
Модель прямо говорит про hold: «Never exposed on a read response» (`app/db/models/equivalent.py:14-26`).
Репродьюсер (из кода, не прогон): FAILED → `react_to_failed` ставит hold → `GET /api/v1/integrity/status` → `status: healthy` при отсутствии нарушений лимитов/симметрии; `/health` → ok. Оператор узнаёт о стопе денег только по 409 `equivalent_integrity_hold` у платежей или по ERROR-строке `debt_reconciliation.integrity_hold_set`.
Почему это важно: сверка (а)/(б) — «гарантируемое утверждение» ядра (`specs/README.md`, решение 2026-09-21 п.3), а её вердикт не наблюдаем через продукт; §12 «фича не завершена, если результат нельзя найти».
Минимальное исправление: в `/integrity/status` и `/verify` добавить на эквивалент поле `reconciliation: {status, checked_at, last_checked_at, held}` из latest-строки и колонки hold; при `held` или `FAILED` — `status: critical`. Не вливать в `passed` чекпойнта (запрет `reconciliation_tables.py:17-21` соблюдается — отдельное поле).
Что может сломаться: `IntegrityStatusResponse` (аддитивно); строгий декодер admin-ui (BACKLOG:655). Covered-by: none. Contract: yes (аддитивное поле OpenAPI).

### IMPL-CL-02 — ошибка верификатора растворяется в «успехе» job'а
Evidence:
```python
# app/main.py:180-187
    try:
        await run_scheduled_reconciliation(session_factory)
    except Exception:  # noqa: BLE001
        logger.exception("integrity.debt_reconciliation_failed reason=%s", reason)
        _emit_integrity_metric(f"{reason}_debt_reconciliation_error")
```
По-эквивалентные ошибки внутри `run_scheduled_reconciliation` не поднимаются (`reconciliation.py:1199-1203`: `counts["error"] += 1; logger.exception(...); continue`), возвращённый `counts` никто не читает, и `_run_integrity_checkpoints_once` затем пишет `f"{reason}_success"` (`main.py:229-235`). `background_health_status` деградирует только на `status == "failed"` (`app/utils/background_jobs.py:14-18`).
Репродьюсер: сбой запроса или `ReconciliationReadError` по одному эквиваленту → каждый прогон: ERROR в логе, строки результата нет, прежняя `PASSED` остаётся latest с застывшим `last_checked_at`, `/health` = 200 ok. Кроме того, сверка стоит внутри того же `try`, что и чекпойнты всех эквивалентов (`main.py:214-217`): сбой слабой проверки выключает сильную. `INTEGRITY_CHECKPOINT_ENABLED=false` выключает сверку и реакцию целиком при `/health` = ok.
Минимальное исправление: `_run_debt_reconciliation_once` возвращает `counts`; при `counts["error"] or counts["hold_errors"]` — `_record_background_job_event(status="failed", event=f"{reason}_debt_reconciliation_error")` и метрика на каждую ошибку. Covered-by: none. Contract: no.

### IMPL-CL-03 — эквивалент из admin API без baseline
Evidence: `admin_create_equivalent` (`admin.py:1131-1164`) — `db.add(eq)`, аудит, commit. `take_baseline` зовут только `scripts/seed_recipe.py:586` и `scripts/take_reconciliation_baseline.py:49` (`git grep -n take_baseline`). Без baseline:
```python
# reconciliation.py:787-796
    if not await _has_baseline(session, equivalent_id):
        ...
        return ReconciliationOutcome(..., missing_evidence=("baseline",), edges_checked=0, ...)
```
→ `UNVERIFIABLE`; реакция — только на `FAILED` (`reconciliation.py:1208`). Изменение долгов в обход приложения в таком эквиваленте не обнаруживается никогда, и (IMPL-CL-01) это никому не видно.
Минимальное исправление: в транзакции создания вызвать `take_baseline(db, eq.id)` — пустой эквивалент даёт ноль смещений, baseline точен по построению (докстринг `take_baseline`: «run it after seeding and before ordinary money operations»). Covered-by: none. Contract: no.

### IMPL-CL-04 — удаление эквивалента недостижимо
Evidence:
```python
# admin.py:1281-1289, 1454-1456
    integrity_checkpoints = (... select(func.count()).select_from(IntegrityCheckpoint) ...)
    return {"trustlines": ..., "debts": ..., "integrity_checkpoints": int(integrity_checkpoints or 0)}
...
    counts = await _equivalent_usage_counts(db, equivalent_id=eq.id)
    if any(v > 0 for v in counts.values()):
        raise ConflictException("Equivalent is in use", details=counts)
```
`compute_and_store_integrity_checkpoints` пишет чекпойнт **каждому** эквиваленту, активному или нет (`integrity.py:139-147`), на старте и каждые 300 с (`main.py:238-251`). FK чекпойнта — `ondelete='CASCADE'` (`models/integrity_checkpoint.py:10`), и `reconciliation_tables.py:151-153` прямо записал намерение: «RESTRICT here would make every equivalent the scheduled loop has ever looked at undeletable» — API воссоздаёт ровно этот исход.
Репродьюсер: создать эквивалент X, деактивировать, дождаться прогона целостности (или перезапуска) → `DELETE /api/v1/admin/equivalents/X` → 409 `{"integrity_checkpoints": N, ...}` навсегда.
Минимальное исправление: убрать `integrity_checkpoints` из условия отказа (оставить в `/usage`). Настоящие стражи — FK `RESTRICT` долгов/журнала/baseline и перевод `IntegrityError` в 409 (`admin.py:1481-1491`) — уже есть. Covered-by: none. Contract: no.

### IMPL-CL-05 — сводка ликвидности смешивает валюты
Evidence:
```python
# admin.py:782-787 (totals без фильтра эквивалента) и 819-828
            func.coalesce(func.sum(TrustLine.limit), 0).label("total_limit"),
...
        .group_by(Participant.pid, Participant.display_name)
```
Реальный клиент зовёт без `equivalent` (`admin-ui/src/api/realApi.requestJson.test.ts:191`: `/api/v1/admin/liquidity/summary?threshold=${threshold}&limit=10`).
Репродьюсер: A — кредитор B на 100 UAH и должник C на 100 HOUR → `net(A) = 0`, A пропадает из `top_creditors`/`top_debtors`; `total_limit` — сумма UAH+HOUR.
Минимальное исправление: без `equivalent` группировать по `(equivalent, pid)` либо требовать `equivalent`. Covered-by: none. Contract: yes, если меняется форма ответа.

### IMPL-CL-06 — поиск циклов в admin глотает ошибки
Evidence:
```python
# admin.py:2274-2278
    for code in codes:
        try:
            raw_cycles = await service.find_cycles(code, max_depth=max_depth)
        except Exception:
            raw_cycles = []
```
Ошибка детектора неотличима от «циклов нет» (§1); на PostgreSQL ошибка запроса отравляет транзакцию сессии, и `find_cycles` следующих эквивалентов тоже падает → тоже `[]`. Не входит в BACKLOG «Проглатывание исключений» (`specs/BACKLOG.md:200-282`).
Минимальное исправление: убрать `try/except` (500 с корреляцией) или откатывать сессию и помечать эквивалент как `error` в ответе. Covered-by: none. Contract: no (или аддитивно).

### IMPL-CL-07 — «изменяемые» ключи конфига без эффекта
Evidence: `admin.py:316-331` объявляет mutable `LOG_LEVEL`, `INTEGRITY_CHECKPOINT_ENABLED`, `INTEGRITY_CHECKPOINT_INTERVAL_SECONDS`, `RECOVERY_ENABLED`, `RECOVERY_INTERVAL_SECONDS`, `PAYMENT_TX_STUCK_TIMEOUT_SECONDS`. `git grep` вне `config.py`/`admin.py`: `LOG_LEVEL` — нигде в `app/` (только docker-compose); `RECOVERY_*`, `PAYMENT_TX_STUCK_*` — нигде; `INTEGRITY_CHECKPOINT_ENABLED` читается один раз при старте (`main.py:273`), интервал цикла — один раз до `while` (`main.py:256-258`). Остальные 6 ключей читаются в рантайме (проверено `git grep -l`).
PATCH отвечает 200 и пишет аудит «изменено» без эффекта. Минимальное исправление: `mutable=False` для этих ключей (для `RECOVERY_*` — до П4); интервал читать в каждой итерации. Covered-by: П4 (`main.py:269-271`) только `RECOVERY_*`. Contract: no.

### IMPL-CL-08 — корреляция ошибки с логом не работает
Evidence: в `app/` и `scripts/*.py` нет `basicConfig`/`dictConfig`/`addFilter` (`git grep` пусто); `request_id_var` читает только `log_duration` (уровень DEBUG, `app/utils/observability.py:21-23`). Поэтому `logger.exception("event=clearing.failed")` (`service.py:103`) пишется без request id, хотя клиент получил `X-Request-ID`. Обработчиков два — `GeoException` и `RequestValidationError` (`main.py:426-462`); иное исключение проходит сквозь `request_id_middleware` (`main.py:385-390`: заголовок ставится только после успешного `call_next`) → plain-text 500 без конверта и `X-Request-ID`.
Минимальное исправление: один `logging.Filter` с `request_id` из контекстной переменной при старте; `@app.exception_handler(Exception)` → конверт E010 + заголовок. Covered-by: none (§12). Contract: no.

### IMPL-CL-09 — монолит исполнения клиринга
Evidence: ruff на `service.py:1766` — `C901 52`, `PLR0912 65`, `PLR0915 172`; `execute_clearing_with_amount` (`:1543`) — `C901 32`, 43 ветки, 114 операторов. Швы: (1) идентичность и реплей `:1797-1823`; (2) SERIALIZABLE + стоп/hold `:1825-1845`; (3) чтение `FOR UPDATE`, периметр `:1847-1908`; (4) сумма и согласие `:1910-1946`; (5) обогащение payload, чекпойнт «до» `:1948-2062`; (6) конверт, применение, аудит, нейтральность `:2064-2188`; (7) разрешение исхода коммита `:2189-2275` (≈90 строк, самостоятельный протокол).
Covered-by: 023 владеет `:1766-2294`, но швы не называет; предложение — выделить (5) и (7) в том же срезе исполнения без смены поведения. Contract: no.

### IMPL-CL-10 — полный чекпойнт эквивалента дважды внутри клиринга
Evidence: `service.py:2039-2043` и `:2143-2146` зовут `compute_integrity_checkpoint_for_equivalent` — чтение всех `debts` и `trust_lines` эквивалента, `check_trust_limits` (outer join всех долгов) и `check_debt_symmetry` (self-join) (`integrity.py:23-126`). Всё под исключительным сессионным локом (`service.py:1705-1709`), которого ждут все платежи эквивалента с `lock_timeout = min(PAYMENT_TOTAL_TIMEOUT, COMMIT_TIMEOUT)` (`money_boundary.py:102-110`). Результат нужен только строке `IntegrityAuditLog` (`:2159-2187`), где `verification_passed` = «нет нарушений лимитов/симметрии **во всём эквиваленте**»: клиринг, ничего не нарушивший, пишется `verification_passed=false`, если где-то есть превышение лимита (штатно для `frozen`-линии, `T1551`).
Почему это важно (INFERENCE, не замерено): удержание исключительного лока растёт с размером эквивалента, а не цикла → таймауты платежей под клирингом. F-023-9 называет это «бюджетом»; живучесть и ложная семантика аудита там не названы.
Минимальное исправление: в клиринге — только хеш, либо проверки, суженные до пар цикла (`participant_pairs` уже есть в `invariants.py:92-95,152-165`). Covered-by: 023 (F-023-9) — неполно. Contract: no.

### IMPL-CL-11 — дублированная визуализация графа
Evidence: `_attach_net_viz` (`admin.py:1611-1747`) и `_attach_net_viz_ego` (`:1982-2111`) — одинаковые запросы, `_percentile`, `_debt_bin`, `DEBT_BINS = 9`, `max_scale = 1.90`, `gamma = 0.75`, размеры 26×22/16×16; `_eq_precision`/`_eq_precision_ego` — копии. Проекция trust line + used/available — трижды (`:648-745`, `:1753-1831`, `:2116-2196`). «Атомы нетто» в `metrics.py:46-52` — `ROUND_DOWN` («Mirror UI»), в графе — `ROUND_HALF_UP` (`admin.py:1665-1667`): нетто 0.005 при precision 2 → граф `1`, метрики `0`.
Минимальное исправление: одна функция `_net_viz(db, participants, eq)`; одно правило округления. Covered-by: none. Contract: no.

### IMPL-CL-12 — устаревшие докстринги на живом пути
Evidence (дословно):
- `app/core/ledger/__init__.py:3-4`: «`journal` is the only module here today, and since step 4 slice C (2026-09-12) it is LIVE: importing it arms the journal» — `journal.py` удалён 018.
- `journal_tables.py:19-24` (NaN на SQLite); `:346` «the flush hook must already have refused» — хука нет.
- `reconciliation.py:13-15` «on SQLite it is not ... done here on both dialects»; `:500` «Importing `PaymentEngine._apply_flow`».
- `service.py:2092-2096`: «We must lock rows? Or just update. ... For MVP, we just update.» — над кодом, читающим строки `FOR UPDATE` (`:1849-1851`).
- `service.py:370-379` — якоря `:1717, :1743, :2121, :1611` (фактически `55P03` — `:1711`); `:396-411` — 16 строк про SQLite-busy.
- `admin.py:1317-1318` «UNDER THE OWNER LOCK THROUGH COMMIT» и `:1370-1371` «the owner lock ... end HERE» — лока нет с 019/5 (код `:1336-1337` говорит обратное).
- Словарь «interlock» (`_rollback_before_interlock`, `_release_interlock_session`, `interlocked_equivalent_id`, события `clearing.interlock_*`), хотя 019 «убирает interlock»; теперь это исключительный лок эквивалента.
Covered-by: none. Contract: no.

### IMPL-CL-13 — код только ради тестов и fail-open в разборе политики
Evidence: `check_zero_sum`/`_compute_imbalance` (`invariants.py:22-81`) — прод-вызовов нет, есть гард `test_no_production_path_calls_check_zero_sum` (`tests/integration/test_p014_t1402_zero_sum_is_not_published_as_a_check.py:140`); докстринг всё ещё обещает «smoke-test for inconsistency». `execute_clearing` (`service.py:1530-1541`) — только `scripts/measure_p020_detector_cost.py` и два теста. `_bind_uuid` (`:479-481`) — тождество. `_policy_flag` (`:990-1016`): API пропускает только bool (`app/utils/validation.py:570-571`), а неизвестная строка → `default=True` — согласие на клиринг.
Минимальное исправление: удалить `check_zero_sum`/`_compute_imbalance` (гард переписать на отсутствие символа); в `_policy_flag` не-bool → `False`. Covered-by: none. Contract: no.

### IMPL-CL-14 — нейтральность как единственная проверка замкнутости
Evidence: исполнение не проверяет, что рёбра `cycle` образуют цикл: принимается любой набор debt id одного эквивалента (`service.py:1797-1861`). Незамкнутый набор отвергает только `verify_clearing_neutrality` (`:2163-2167`) ценой 2 SUM-запросов на участника до (`:2034-2038`) и 2 после (`invariants.py:245-259`). В сверке то же — `Counter(debtors) == Counter(creditors)` в памяти (`reconciliation.py:593-596`).
Почему это важно: F-023-9 считает нейтральность затратой; снятая без замены, она откроет исполнение незамкнутых наборов (сверка (б) поймает постфактум, деньги уже двинуты). Минимальное исправление: in-memory проверка замкнутости по заблокированным строкам до записи. Covered-by: 023 (F-023-9) — предупредить. Contract: no.

### IMPL-CL-15 — транзакция CLEARING расходится с протоколом
Evidence: `initiator_id = debts[0].debtor_id` (`service.py:2064-2066`), `debts` — `select ... where id in (...) FOR UPDATE` без `ORDER BY` (`:1849-1851`) → инициатор произвольный; протокол §7.3: `"initiator": "HUB_PID"`. `payload.cycle` — debt id (`:2077`), в протоколе — PID. §7.4.1 «Отправляется `CLEARING_NOTICE`» — публикации клиринга участникам нет (`git grep CLEARING_NOTICE` пусто; `event_bus.publish` зовёт только платёж). Инициатор используется в метриках активности (`metrics.py:659-660`). Минимальное исправление: `ORDER BY id` или документировать отклонение §7.3; notice — пометить нереализованным. Covered-by: none. Contract: yes.

### IMPL-CL-16 — канон не объявляет достижимые статусы
Evidence: генерированная схема сверена с `api/openapi.yaml` скриптом `scratchpad/review/oa.py` по 10 операциям (`clearing/auto`, `integrity/status|verify`, `admin/equivalents/{code}` DELETE, `.../integrity-hold/clear`, `admin/config` PATCH, `admin/clearing/cycles`, `admin/liquidity/summary`, `admin/participants`, `/health`). `DELETE /admin/equivalents/{code}`: главный отказ 409 (`admin.py:1451,1456,1488`) не объявлен ни в каноне (`openapi.yaml:709-723`), ни в генерации. `POST /clearing/auto`: 400 (`clearing.py:48`, `validate_equivalent_code`) и 504 (`service.py:1636,1711`) не объявлены. Остальные 8 совпадают по параметрам и множествам статусов с точностью до известных артефактов (422 заголовка, 400 у `admin/config`). Covered-by: none (канон без владельца, BACKLOG:718). Contract: yes.

### IMPL-CL-17 — health определён дважды
Evidence: `main.py:474-535` и `health.py:40-104` — `/health`, `/healthz`, `/health/db`; две копии `_START_TIME` и `_best_effort_version`; `/api/v1/health` несёт `environment`, корневой — нет. `/admin/health/db` отдаёт `"details": str(exc)` (`health.py:148`) — текст ошибки драйвера. Минимальное исправление: корневые маршруты — алиасы функций `health.py`. Covered-by: none.

### IMPL-CL-18 — тяжёлые integrity-эндпоинты открыты любому участнику
Evidence: `require_participant_or_admin` на всех четырёх (`integrity.py:61,138,155,277`); `/verify` на каждый вызов сканирует эквиваленты (дважды — F-016-1) и коммитит `IntegrityAuditLog` на эквивалент (`:238-265`); `/audit-log` отдаёт `affected_participants.edges` всех клирингов (`:296-315`). INFERENCE: рычаг нагрузки и раскрытие графа долгов; протокол §11.7.1 доступ не оговаривает. Covered-by: F-016-1 (только двойной прогон). Contract: yes при смене доступа.

### IMPL-CL-19 — две «нетто-позиции»
Evidence: `MoneyBoundary._snapshot_net_positions` (`money_boundary.py:374-410`, GROUP BY, платёж) и `InvariantChecker._calculate_net_position` (`invariants.py:237-259`, 2 запроса на участника, клиринг). Одна формула, две реализации. Covered-by: none (не в F-016-1…8).

### IMPL-CL-20 — путь без исключительного лока
Evidence: предпроверка читает строки на сессии вызывающего без обновления снимка (`service.py:1590-1600`); при несовпадении числа строк — `_reconcile_committed_execution` (откатывает сессию) и `_run_attempts` **без** `interlocked_equivalent_id` (`:1604-1618`). Если снимок вызывающего старше строк цикла, свежая попытка увидит все строки и исполнит клиринг без исключительного лока (ветка `:1880-1884`, иначе мёртвая). Корректность держит SERIALIZABLE; теряется гарантия живучести (`money_boundary.py:21-24`). INFERENCE. Covered-by: 023.

### IMPL-CL-21 — неограниченный рост
Evidence: `_journal_sums` читает все записи журнала эквивалента (`reconciliation.py:325-340`), `_operations` — все конверты с JSON-намерениями (`:443-474`), каждые 300 с (+ повтор на FAILED). `integrity_checkpoints` — строка на эквивалент за прогон без чистки (`integrity.py:139-151`; §12 требует TTL). Для масштаба сообщества терпимо годами; записать долгом. Covered-by: none.

## Ответы по перечисленным формам (что проверено и чисто)
1. **Исполнение клиринга:** одна транзакция; все рёбра `FOR UPDATE` до записи (`:1849`); `c` = минимум заблокированных строк, конкурентный платёж исключён исключительным локом + SERIALIZABLE; `amount < c` недостижимо (`:2098-2099`, `book.py:407-411`); частичный отказ — откат savepoint и отказ попытки; идемпотентность — uuid5 от множества debt id (F-023-1 известна); событий клиринг не публикует вовсе (IMPL-CL-15).
2. **`Book`:** алгебра `_apply_payment_flow` совпадает с независимым правилом сверки (`reconciliation.py:494-517`); нулевые строки удаляются во всех путях; версия — `version_id_col` + `DebtVersionConflict`; `amount <= 0` → `BookError`. Триггер: `nullif(current_setting(..., true), '')` (пустая строка на пуле), `set_config(.., true)` откатывается с savepoint, `NEW.amount = OLD.amount` → без записи, смена ключа → `GE002`. SQL 029 и модуля идентичны.
3. **Сверка:** неизвестный вид/версия → `b_version_unsupported` → FAILED (fail-closed); без baseline — `UNVERIFIABLE`, (б) всё равно считается; один снимок RR READ ONLY; реакция fail-closed. Проблема — видимость (01-03) и зависимость от успеха чекпойнтов (02).
4. **`money_boundary`:** предикат клиринга один — `{40001, 40P01}` (`service.py:412`); 23505 для клиринга недостижим (вставляется только уникальный по построению `tx_id` под исключительным локом). Shared у платежа/тика/инжекта, exclusive только у клиринга (`git grep acquire_*`). Redis: `REDIS_ENABLED=false` → лок no-op (fail-open), деньги от него не зависят; ошибка Redis в работе → необработанное исключение → plain-text 500 на `/clearing/auto` и на **каждом** запросе через `rate_limit` (`deps.py:69`).
6. **`main.py`:** цикл целостности не умирает от исключения, отмена при shutdown — `cancel()+gather` до закрытия Redis/engine, перекрытия итераций в процессе нет.
7. **Health:** см. 01, 02, 17.

## Что не проверено
- Детекторы и `auto_clear` (023), `app/core/payments/*`, симулятор, trust-line сервис — вне зоны.
- Ни одного pytest/Postgres-прогона: всё — чтение кода; репродьюсеры IMPL-CL-04/05 выведены из кода, не исполнены; IMPL-CL-10/20 — INFERENCE без замера.
- OpenAPI сверен по 10 операциям (параметры и множества статусов), схемы тел не сравнивались.
- Миграции 022-028 построчно не читались.

## Оценка направления текущего плана
- **023 и нейтральность (IMPL-CL-14).** F-023-9 ставит нейтральность в «бюджет», но это единственная проверка замкнутости цикла при исполнении. 023 должна заменить её in-memory проверкой замкнутости до записи, а не просто снять.
- **023 и чекпойнты в клиринге (IMPL-CL-10).** Проблема не в числе запросов, а во времени удержания исключительного лока и в ложном `verification_passed`. «Убрать из клиринга» дешевле любой оптимизации: аудиту клиринга полный хеш эквивалента не нужен.
- **023 и монолит (IMPL-CL-09).** Раз 023 меняет идентичность и сумму в `:1766-2294`, выделить разрешение исхода коммита и обогащение payload стоит в том же срезе, иначе MTCS-исполнение нарастит те же 528 строк.
- **Незанятая полоса вне 016-023:** наблюдаемость сверки (01-03) и admin-логика (04-07, 11). 01-03 — пробел **обнаружения** на денежном пути (отсутствующее измерение выглядит как нулевое), не рефакторинг; 04-07 — узкие правки без спеки. Предлагаю узкую правку для 01-03 и строки BACKLOG «узкие правки» для 04-07.
