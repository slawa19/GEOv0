> **Редакционная правка при переносе в репозиторий, 2026-08-25.** Отчёт приведён дословно, кроме
> одного: ревьюер печатал якоря абсолютными путями внутрь своего замороженного клона
> (`<TEMP>/geov0-t1211-557fcbee1f624047bf57398d0b6a74bb/frozen/<путь>`), а §15 запрещает абсолютные пути в дереве. Префикс до `frozen/`
> снят, поэтому якоря стали repo-relative; **замена механическая и обратимая** — исходный корень
> назван здесь. Ничего кроме префиксов и хвостовых пробелов не менялось: ни формулировок, ни
> вердиктов, ни чисел.

Ревью выполнено read-only на точном `dd1218d2d76452a19ecb56da867f41e7d67e77aa`. `git status --short` пуст; frozen checkout находится в detached HEAD. Реализацию 015 не учитывал.

## Находки

### P2 — Admin UI округляет допустимые деньги до `precision`, хотя `precision` является минимумом

Общий Admin formatter вызывает `formatDecimalFixed(value, precision)` в [useEquivalentPrecision.ts:52](/admin-ui/src/composables/useEquivalentPrecision.ts:52). Этот formatter при `scale > precision` делает `ROUND_HALF_UP` в [decimal.ts:91](/admin-ui/src/utils/decimal.ts:91).

Репродьюсер на production-функции:

```text
node --experimental-strip-types --input-type=module -e "...formatDecimalFixed..."
12.345 1 => 12.3
0.05 1 => 0.1
12.3 2 => 12.30
```

`0.05` при `precision=1` допускается денежной дверью и точно хранится в `Numeric(20,8)`. Правило «минимум знаков» требует оставить `0.05`, но Dashboard, Trustlines, Liquidity и Graph показывают `0.1`. Это изменение величины, а не только написания.

Это искомая четвёртая выборочная слепота:

- RT в [moneyPrecisionByEquivalent.test.ts:35](/admin-ui/src/pages/moneyPrecisionByEquivalent.test.ts:35) выбирает единственную сумму `12.3`: для HOUR/1 она уже ровно в precision, для UAH/2 требуется только padding. Неправильный formatter «precision как максимум» удовлетворяет обе строки.
- Более широкий тест не закрывает дыру, а закрепляет неверный oracle: [graphPageHelpers.test.ts:345](/admin-ui/src/pages/graph/graphPageHelpers.test.ts:345) подаёт `12.345` и требует ровно `precision` знаков. Правильная реализация «minimum, never maximum» этот тест уронит.

Это также пятое ложное утверждение класса «уже верное не изменилось»: на Admin surface верная более точная величина изменяется.

### P2 — слияние детекторов меняет порядок клиринга и итоговое сохранённое состояние

SQL-детекторы намеренно ранжируют циклы по `clear_amount DESC` в [service.py:601](/app/core/clearing/service.py:601) и `:733`. `_deduplicate_cycles` обещает сохранить первый элемент именно ради этих эвристик в [service.py:431](/app/core/clearing/service.py:431).

Но объединение построено как `DFS + SQL` в [service.py:1344](/app/core/clearing/service.py:1344), после чего сортируется только по длине. Поэтому среди циклов одной длины DFS-порядок заменяет `clear_amount DESC`. `auto_clear` исполняет первый успешный цикл и затем пересчитывает граф в [service.py:2062](/app/core/clearing/service.py:2062).

Репродьюсер на двух свежих in-memory SQLite sessions:

```text
Общее ребро A→B = 100
цикл A-B-C-A: остальные рёбра = 10
цикл A-B-D-A: остальные рёбра = 100
низкий цикл вставлен первым

SQL_ORDER    = [100.00, 10.00]
MERGED_ORDER = [10.00, 100.00]

SQL-FIRST FINAL:
cycles=1
debts=[B→C 10.00000000, C→A 10.00000000]

CURRENT MERGED FINAL:
cycles=2
debts=[B→D 10.00000000, D→A 10.00000000]
```

То есть программа изменила число clearing-транзакций и то, между какими участниками остаётся долг. Это третье поведенческое изменение сверх заявленных non-goals, причём затрагивающее сохраняемые деньги.

Это ещё одна, пятая, выборочная слепота: тест объединения использует явно `disjoint quadrangle` в [test_p012_t1210_detector_union_default_tier.py:96](/tests/unit/test_p012_t1210_detector_union_default_tier.py:96); Postgres reach-тест также строит раздельные triangle и 5-cycle. На непересекающихся циклах порядок не влияет на конечный ledger, поэтому неверное ранжирование невидимо.

### P2 — тот же canon/signature fork существует у `ParticipantProfile`

`F-012-12` подтверждён: дробный JSON number в trustline policy превращается во `float`, а [canonical.py:75](/app/core/auth/canonical.py:75) запрещает float.

Но тот же разрыв существует ещё в create/update participant:

- `ParticipantProfile` допускает любые дополнительные JSON-поля и произвольные `contacts` в [OpenAPI:5042](/api/openapi.yaml:5042).
- Pydantic сохраняет их через `extra="allow"`/`Any` в [participant.py:8](/app/schemas/participant.py:8).
- Profile входит в подписываемый payload в [participants/service.py:45](/app/core/participants/service.py:45) и `:158`.

Фактический вывод:

```text
policy-number REFUSED BadRequestException Float is not allowed in canonical JSON
profile-extra-number REFUSED BadRequestException Float is not allowed in canonical JSON
profile-contact-number REFUSED BadRequestException Float is not allowed in canonical JSON
profile-string SIGNED {"profile":{"score":"100.5"}}
```

Это не пересказ находки 15: та исправила маскировку отказа под `Invalid signature`; допустимое по OpenAPI тело по-прежнему невозможно подписать ни для создания, ни для обновления участника.

### P3 — замер плана запроса невозможно независимо проверить по frozen HEAD

Статически подтверждены две части решения:

- порог не являлся общей защитой от dust, поскольку DFS принимал любое `amount > 0`;
- чтение `precision` действительно означало бы принятие отложенного решения о monetary quantum.

Арифметика заявленного замера сходится: `62 204 − 61 898 = 306`, то есть `0.494%`.

Но в exact tree нет сырого `EXPLAIN (ANALYZE, BUFFERS)`, команды генерации измеренной популяции или результата пяти прогонов. Команда

```text
git grep -n -E '61898|62204|EXPLAIN \(ANALYZE'
```

находит только повторение чисел в комментарии production-кода, spec и docstring теста. `git show --stat 2e9f8c1` также не содержит измерительного артефакта. Поэтому утверждения о `0.5–0.6%`, `77.8/81.0 ms` и независимом повторе проверить нельзя; это сохранённый пересказ, а не открываемое evidence.

## Категории без новых находок

- Дверь: границы совпадают с `Numeric(20,8)` и `is_storable_money`. Проверены обе стороны `±10**12`, `1E-8/1E-9`, максимальное `999999999999.99999999` и trailing zeros. Door срабатывает до crypto verification и записи на payment/trustline create/update; несогласия не нашёл.
- Backend `to_money_str` и Simulator UI: проверил значения с `scale <`, `=`, `>` precision, отрицательные, precision `0/1/2/4/8/18`; более точное значение сохраняется, padding не меняет число.
- `net_balance` против atoms/sign/color: текущий контракт действительно достигнут — одинаковые sign/zero, ошибка меньше одного кванта, точное равенство для представимых значений, обе стороны знака. Остаточный precision-0 coercion уже зарегистрирован в T1210 и повторно не заявляю.
- Изменения подписываемой суммы на payment/trustline входах сверх разрешённого отказа не обнаружил.

## Независимые пересчёты

- Реестр T1210 структурно содержит `13 + 7 = 20` находок.
- На `e45e721` найдено ровно 20 executable `Decimal("0.01")`: `trust_drift_engine` 9; clearing 2; inject 2; real clearing 2; payment planner 2; runtime utils 2; tick orchestrator 1.
- Девять simulator producers сходятся: два edge-builder path, snapshot, viz patch, три clearing-result path и два `topology.changed` path.
- В `app/api/v1/simulator.py`: 16 вызовов `_fmt_decimal_for_api` и 4 вызова `_fmt_num_or_str`; один из четырёх внутренний, итого ровно 19 внешних производящих сайтов.

По программе 015 отдельных замечаний не заявляю.

VERDICT-T1211: FINDINGS
VERDICT-SAMPLE-BIAS: FOUND
VERDICT-READY-TO-CLOSE: NO
VERDICT-DONE