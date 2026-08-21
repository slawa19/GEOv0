# 008 — Аудит плана перед возобновлением

- **Date:** 2026-08-12
- **Target:** текущий HEAD на момент аудита `4d08a7c02e27d4234f3ebbb1a442bea9c369365b`
- **Reviewed baseline:** замороженный HEAD программы `ea9cde9161c3f7444495ede13871c901abbba811`
- **Status:** AUDIT COMPLETE — A1–A8 закрыты; runtime-ограничения перечислены отдельно
- **Status authority:** A1–A7 записаны с evidence, A8 выполнен отдельной fresh-context сессией,
  существенные возражения перепроверены и внесены. Этот документ не меняет статус и авторизацию
  задач программы.
- **Scope:** критическая перепроверка `spec.md`, `plan.md`, `tasks.md`, структуры текущего дерева,
  истории после baseline и актуальности P2 волны 1.

Этот файл — версионируемая передача следующему агенту. Он не заменяет `tasks.md`: источник статуса
задач остаётся там. Полные локальные реестры из `plans/` не являются source of truth.

## Граница завершённого аудита

Аудит завершён без исправления `spec.md`, `plan.md`, `tasks.md` или реализации программы.
Следующий шаг требует отдельного решения владельца о правке проверяемого плана.

Не переносить старую находку в работу только потому, что она отмечена `CONFIRMED` на `ea9cde9`.
Для каждой нужен текущий статус `LIVE`, `FIXED_EXTERNALLY`, `CHANGED`, `REFUTED` или
`UNVERIFIED`.

## Этапы аудита

Все этапы ниже закрыты в этом файле. Они не разрешают исправлять проверяемый план или продуктовый
код.

### A1 — Re-baseline всех P2 волны 1 — CLOSED

Admin-пара `C-C1-001`/`C-C1-002` и backend-находки `C-A1b-001`, `C-A1a-001`,
`B-A1b-003`, `B-A1b-002`, `C-A1a-003` перепроверены на текущем HEAD.

Для каждой находки нужны текущий статус, current/intended/optimal, актуальные `path:line`, material
call-sites, изменившие поверхность коммиты, команды с exit codes и unverified runtime paths.
Результат субагента необходимо вручную перепроверить и записать только в этот audit-файл.

### A2 — Evidence закрытой волны 1 — CLOSED

Локальные inventory/findings A1a, A1b и C1, на которые ссылаются `tasks.md:5-7,19-23` и
`spec.md:62-70,199-200`, сверены по наличию, counts, verdict totals, finding IDs, пофайловой
полноте, self-refutation и разделам «что НЕ проверял».

### A3 — Карта покрытия owner slices — CLOSED

Tracked-файлы Phase-I surface машинно сопоставлены с A1–A4/B1–B4/C1–C4 на `ea9cde9` и текущем
HEAD; исходный partition defect отделён от последующего drift. Границы слайсов не исправлялись.

### A4 — Test strategy — CLOSED

Нужно окончательно разделить: какие релевантные тесты читаются вместе с source-слайсом и что
остаётся уникальной задачей suite-wide волны 5. Проверить покрытие conftest, guards, markers,
slow/Postgres tests, UI setup, Playwright configs/suites и адекватность behavior/tier matrix.
Отдельно оценить, достаточно ли package scripts и CI matrix для targeted/milestone gates фазы II.

### A5 — Границы completeness — CLOSED

Для migrations, scripts/CI/pytest infrastructure, Docker/config/security, generators/fixtures/seeds,
Playwright и docs/OpenAPI назначить одну классификацию: `IN SCOPE`,
`INTENTIONALLY EXCLUDED WITH OWNER`, `EXCLUDED BUT HANDOFF MISSING` или `ACCIDENTALLY OMITTED`.
Закрытые 005/006 — историческое evidence, но не вечное доказательство текущего состояния.

### A6 — Anti-Enterprise — CLOSED

Для source inventory, test inventory, P3/T808, T809, thresholds, предварительных rewrite/delete
verdicts, baseline tag, пяти проходов и двух параллельных треков дать статус `KEEP`, `SIMPLIFY` или
`REMOVE` с прямой ценностью. Не добавлять тяжёлый performance/security/infrastructure-аудит без
фактического сигнала.

### A7 — Итоговая оценка по пяти критериям — CLOSED

Добавить итоговую матрицу: корректность и адекватность; детальность; последовательность и логика;
полнота; Anti-Enterprise. Для каждого критерия нужны оценка, сильные стороны, подтверждённые пробелы
и минимальная рекомендация. Матрица должна ссылаться на findings, а не вводить новые гипотезы.

### A8 — Adversarial и редакционная проверка — CLOSED

Отдельный read-only reviewer с чистым контекстом проверяет отчёт на ложные findings, severity,
отсутствующее evidence, устаревшие ссылки, противоречия, смешение рекомендаций с решениями и
runtime-заявления без runtime evidence. Оркестратор перепроверяет существенные возражения. После
последней правки выполняются `git diff --check` и проверка ссылок; только затем статус меняется на
`AUDIT COMPLETE` с честным списком residual risks.

## Как проводился аудит

Проверены:

- актуальные `spec.md`, `plan.md`, `tasks.md`;
- `AGENTS.md`, прежде всего §§1, 2, 5, 7–9, 11, 13, 15, 16;
- `specs/README.md` и owner surfaces закрытых программ 005/006;
- текущие tracked-файлы трёх application surfaces и тестов;
- package scripts обоих UI и `.github/workflows/quality.yml`;
- история `ea9cde9..4d08a7c` по затронутым поверхностям;
- актуальный код и контракты Admin-находок `C-C1-001`/`C-C1-002`;
- несущие факты независимого read-only слайса перепроверены основным оркестратором по коду.

Команды измерения:

```powershell
git merge-base --is-ancestor ea9cde9 HEAD
git rev-list --count ea9cde9..HEAD
git diff --shortstat ea9cde9..HEAD -- app admin-ui/src simulator-ui/v2/src
git diff --shortstat ea9cde9..HEAD -- tests
git diff --shortstat ea9cde9..HEAD -- scripts .github pytest.ini
git ls-files <surface>
```

Фактический результат на `4d08a7c`:

- baseline является предком HEAD (`merge-base --is-ancestor`, exit `0`);
- между baseline и HEAD — **212 коммитов**;
- application surfaces: 59 изменённых файлов, `+3219/-1012`;
- `tests/`: 61 изменённый файл, `+9815/-180`;
- `scripts/`, `.github/`, `pytest.ini`: 8 изменённых файлов, `+71/-599`;
- текущие tracked application files: `app=123`, `simulator-ui/v2/src=266`,
  `admin-ui/src=114`, всего **503**;
- текущие backend test modules `test_*.py`: **179**; всего tracked Python-файлов под `tests/` —
  **184**, а всех tracked entries `tests/**` — **185**;
- UI unit/component tests: Simulator **98**, Admin **33**;
- Playwright specs: Simulator **7**, Admin **4**. Под `simulator-ui/v2/e2e/**` всего 14 entries,
  но семь из них — snapshots/config/tsconfig, не executable specs.

Исторические числа в changelog переписывать нельзя. Но их нельзя использовать как критерий
приёмки ещё не начатых волн.

## Итоговая оценка

Каркас программы сильный и сохраняется:

- машинные сигналы отделены от находок;
- каждый application-файл должен отличаться как прочитанный или непрочитанный;
- находка требует `path:line`, достижимого эффекта и способа проверки;
- B/C требуют падающего репродьюсера до исправления;
- delete требует reference scan и `git log`, а не пустого grep;
- default verdict — оставить как есть;
- новые слои, framework и абстракции «на будущее» запрещены;
- отдельное внешнее ревью оправдано правилами §15 и не заменяет внутренний adversarial review.

План нельзя продолжать без коррекции, потому что его исполняемое разбиение не покрывает объявленный
scope, статусы противоречат друг другу, test strategy слишком поздно подключает evidence конкретных
путей, а текущий HEAD существенно ушёл от baseline.

## Подтверждённые дефекты плана

### F-PA-1 — Неактуальная база для будущих волн

**Severity:** P2 governance; обязательный precondition перед Wave 2.

`plan.md:19-20` правильно требует перепроверять старые находки после движения HEAD, но в
`tasks.md` нет исполняемой задачи re-baseline. Движение не косметическое: 212 коммитов затронули
59 application-файлов и 61 test-файл. Среди них есть owner surfaces волн 1–3.

**Рекомендация:** сохранить `ea9cde9` как исторический baseline закрытой волны 1; назначить новый frozen
HEAD для непройденных волн 2–6. P2 волны 1 перепроверить отдельно на новом HEAD.

### F-PA-2 — Owner slices не образуют полное разбиение объявленного scope

**Severity:** BLOCKER критерия полноты.

`spec.md:43-46` объявляет read-only покрытие всего `app/**`, `admin-ui/src/**`,
`simulator-ui/v2/src/**`, `tests/**`. Перечень в `plan.md:91-119` не назначает часть текущих файлов.

Подтверждённые примеры backend:

- `app/main.py`, `app/config.py`;
- `app/db/**`;
- `app/utils/**`;
- часть `app/core/simulator/`, не совпадающая с перечисленными семействами A2a/A2b.

Подтверждённые примеры Admin UI:

- `admin-ui/src/App.vue`, `main.ts`, `style.css`;
- `admin-ui/src/composables/useLatestRequest.ts`;
- `admin-ui/src/composables/useRouteHydrationGuard.ts`;
- `admin-ui/src/test/setup.ts`.

Подтверждённые примеры Simulator UI:

- `simulator-ui/v2/src/App.vue`, `main.ts`;
- `src/config/**`, `src/types/**`;
- top-level fixture/scene/mapping и CSS-файлы.

**Рекомендация:** не раздувать prose списком из 503 файлов. Генерировать manifest через `git ls-files`,
где каждый файл назначен ровно одному слайсу. Проверка должна падать и при пропуске, и при двойном
назначении.

### F-PA-3 — Статусы расходятся

**Severity:** P2 governance.

- `spec.md:4` говорит «волна 1 в работе»; `spec.md:66` и `tasks.md:22-23` говорят, что она закрыта.
- `spec.md:172-173` оставляет T801/T802 открытыми; `tasks.md:19-22` отмечает их выполненными.
- `spec.md:171` закрывает агрегат T800; `tasks.md:15` оставляет T800-3 открытой.
- T800-3 утверждает, что `specs/README.md` untracked; сейчас файл tracked, но 008 в реестре нет.
- `spec.md:12,119` называет 002–006 неавторизованными, тогда как текущий
  `specs/README.md:25-29,122-127` фиксирует их завершение.
- `[!]` одновременно означает «не начато» и «заблокировано», хотя это разные состояния.

**Правка:** синхронизировать три документа и использовать отдельные значения `NOT STARTED`,
`PAUSED`, `BLOCKED`. Исторические утверждения привязать к дате, а не выдавать за текущий статус.

### F-PA-4 — Тесты впервые читаются слишком поздно

**Severity:** P2 метода.

Отдельная волна 5 нужна: она ловит vacuous guards, marker traps, duplicated fixtures и thin-wiring
tests. Но источник нельзя оценивать как dead/merge/rewrite и нельзя утверждать, что регрессия
поймается, не прочитав релевантные тесты в исходном слайсе.

**Рекомендация:**

- в каждой source-wave читать тесты, относящиеся к её call-paths, и записывать, какой эффект они
  доказывают или не доказывают;
- волну 5 сохранить как suite-wide проверку marker taxonomy, isolation, guards, fixture ownership,
  дублей и пробелов;
- не переносить всю волну 5 раньше и не удалять её.

### F-PA-5 — Source-style inventory чрезмерен для каждого test-файла

**Severity:** P3 эффективности.

Сейчас это 179 backend `test_*.py` modules, 99 Simulator test files и 33 Admin test files. Таблица
`keep/clean/merge/rewrite/delete` на каждый тест хуже отвечает на вопрос «какое поведение доказано»,
чем матрица поведения.

**Правка:** для тестов использовать таблицу:

| Группа | Наблюдаемое поведение | Уровень/tier | Owner source | Покрытые failure paths | Риск |
|---|---|---|---|---|---|

Пофайловый verdict обязателен только для delete/merge, больших файлов, policy guards,
marker-bearing slow/Postgres tests и подозрений на no-op/thin-wiring.

### F-PA-6 — Playwright не включён и не исключён

**Severity:** P2 полноты test surface.

Волна 5 перечисляет только `src/**/*.test.ts`; она не включает 7 Simulator и 4 Admin Playwright
specs.
Package scripts подтверждают, что это отдельные исполняемые поверхности.

**Рекомендация:** добавить их как два небольших read-only слайса волны 5 либо явно объявить non-goal с
ссылкой на владельца. Запускать browser suite ради инвентаризации не требуется; запуск нужен только
для подтверждения соответствующей runtime-находки или milestone.

### F-PA-7 — Machine-local `plans/` используется как условие формального завершения

**Severity:** P2 воспроизводимости.

`tasks.md:5-7` считает слайс выполненным только при наличии двух ignored файлов. При этом
`AGENTS.md:22,99,353` запрещает считать `plans/` source of truth. Потеря локального каталога делает
формальную программу невосстановимой; 33 P3 волны 1 остаются только локально.

**Рекомендация:** тяжёлые реестры можно оставить untracked, но в git нужен компактный evidence index:
slice, SHA, manifest count, verdict totals, finding IDs/severity/disposition, unverified paths и
воспроизводимая команда/контрольная сумма локального артефакта.

### F-PA-8 — Два внутренних adversarial-шага частично дублируются

**Severity:** P3 стоимости.

P3 в `plan.md:70-78` требует отдельного clean-context агента на опровержение, включая поиск
пропущенных sibling bugs. T808 повторяет тот же поиск по сведённым реестрам. При этом ручная
проверка оркестратора и внешнее T809 имеют другие функции и удаляться не должны.

**Рекомендация:** slice agent делает self-refutation; оркестратор проверяет все P1/P2 и существенную
выборку P3; T808 остаётся единым fresh-context внутренним adversarial-проходом; T809 остаётся
внешним review другой системой.

### F-PA-9 — Предназначенные verdict'ы создают confirmation bias

**Severity:** P3 метода.

`plan.md:107-109` заранее называет B2 кандидатом на rewrite/merge, а `dev/`, `demo/`,
`legacyReference/` — прямыми кандидатами на delete. Это противоречит default verdict «оставить».
Особенно опасен `demo/`: программа 006 уже доказала использование `src/demo/patches.ts` в real mode.

**Рекомендация:** заменить verdict'ы вопросами: «проверить источник истины», «проверить runtime imports»,
«проверить ownership». Verdict появляется только после evidence.

### F-PA-10 — Числовые thresholds выданы за правила

**Severity:** P3 Anti-Enterprise.

`plan.md:37-38,161-164` использует ≥20 строк и ≥3 места. Это допустимые discovery heuristics, но не
условия истинности. Собственный пример программы — опасный дубль ровно между двумя реализациями.

**Рекомендация:** назвать числа сигналами ранжирования. Рассматривать минимальный локальный extraction и для
двух копий, если доказано расхождение контракта или снижение числа синхронных касаний.

### F-PA-11 — Мелкие исполняемые дефекты

**Severity:** P3.

- `plan.md:196`: `\.scripts\verify_local.ps1` вместо `\.\scripts\verify_local.ps1`;
- имена проходов P0–P4 конфликтуют с severity P1/P2/P3;
- T803 одновременно выглядит «не начатой», «заблокированной» и ожидающей решения владельца;
- `NEEDS-RUNTIME` не порождает отдельную задачу с владельцем и exact gate.

**Рекомендация:** исправить путь, переименовать проходы в R0–R4, разделить статусы, для каждого
`NEEDS-RUNTIME` создавать задачу с selector, prerequisite и ожидаемым доказательством.

## Что в предыдущей критике было слишком категоричным

Следующий агент не должен механически применять эти предложения:

1. **Не переносить всю волну 5 раньше.** Верно только требование читать релевантные тесты вместе с
   source. Suite-wide аудит тестов логично оставить после понимания продуктовых путей.
2. **Не отменять per-file inventory application source.** Для source это полезный доказуемый
   критерий полноты; сокращать следует только низкоинформативные строки по тестам.
3. **Не добавлять формальный performance/load audit.** Класс P уже требует измеримой стоимости;
   программа 001 и AGENTS.md запрещают benchmarking без воспроизведённой потребности.
4. **Не утверждать, что security отсутствует.** Auth/deps/API входят в source review, а 005 закрыла
   конкретный security/delivery scope. Нужна явная граница: это code review security-релевантного
   кода, не threat model, pentest, dependency или crypto audit.
5. **Не считать migrations/CI случайно забытыми без оговорки.** Они исторически принадлежат
   закрытым 005/006 и applied migrations защищены. Проблема — отсутствие явной excluded-surface
   таблицы и handoff, а не обязательность нового полного infrastructure-аудита.
6. **Не удалять T809 и ручную проверку оркестратора.** Они решают другие trust-задачи. Упростить
   нужно только перекрытие per-finding P3 и итогового T808.

## Актуальность P2 волны 1

### Завершённая перепроверка Admin UI/API

#### `C-C1-001` — LIVE

Текущий `admin-ui/src/api/realApi.ts:905-915` строит `/admin/graph/snapshot` только с
`equivalent`. `graphEgo` в `:918-925` также не передаёт `include`.

Backend требует opt-in:

- endpoint parameter: `app/api/v1/admin.py:1395-1403`;
- extras остаются пустыми без include: `app/api/v1/admin.py:1685-1704`;
- канон: `api/openapi.yaml:896-917`, ego `:927-969`.

UI реально использует массивы в `useGraphAnalytics.ts:559-665`: incidents, audit operations,
committed payment/clearing activity и `hasTransactions`. Следовательно, real mode молча показывает
нули/отсутствие данных при существующих backend rows.

**Новый sibling contract risk:** простое добавление
`include=incidents,audit_log,transactions` пока небезопасно. Backend transaction helper
`app/api/v1/admin.py:206-232` не отдаёт `payload`, а frontend `TransactionSchema` требует его в
`realApi.ts:174-188`; аналитика читает `payload` в `useGraphAnalytics.ts:621-644`. Этот sibling
дефект требует отдельной записи и репродьюсера, а не скрытой правки внутри C-C1-001.

#### `C-C1-002` — FIXED_EXTERNALLY

Коммит `84ca3965f9825ac85d2ad351310504472e19d473` (`fix(admin): accept nullable audit fields`)
изменил `actor_id` и sibling nullable fields на `.nullable().optional()` и добавил контрпроверки
malformed non-null values.

Текущий контракт согласован:

- frontend schema: `admin-ui/src/api/realApi.ts:156-172`;
- frontend type: `admin-ui/src/types/domain.ts:49-63`;
- backend schema/OpenAPI допускают null.

Исходная обязательная сцепка C-C1-001 + C-C1-002 больше не существует. C-C1-002 нельзя оставлять
как `fix now`; статус должен стать `fixed externally at 84ca396`.

### Завершённая ручная перепроверка backend P2

Независимые сессии не дали evidence: четыре read-only запуска Claude Code завершились до чтения
репозитория сообщением `You've hit your weekly limit · resets Aug 14, 2am (Europe/Kiev)`. Поэтому
результаты ниже получены и перепроверены оркестратором, но не засчитываются как A8.

#### `C-A1b-001` — LIVE

**Current behavior.** `app/schemas/simulator.py:90-110` по-прежнему задаёт
`serialize_by_alias=True` двум edge-моделям. На закреплённом Pydantic ключ отсутствует в
`ConfigDict.__annotations__`; оба `model_dump()` возвращают `{'from_': 'A', 'to': 'B'}`.
Материальные продюсеры остаются в `app/core/simulator/sse_broadcast.py` и сохраняют wire только
явным `model_dump(mode="json", by_alias=True)`; sibling `SimulatorActionEdgeRef` использует
отдельный serializer (`app/schemas/simulator.py:628-638`). После baseline файл схем не менялся.

**Intended behavior.** Защищённый wire-контракт требует `from`, а комментарий модели обещает это
по умолчанию. **Optimal next action:** оставить finding живым; до правки нужен падающий serialization
test без явного `by_alias=True`, затем минимально убрать ложный config/comment либо ввести реально
работающий serializer. Реальный SSE/browser path не запускался.

#### `C-A1a-001` — LIVE

Восемь маршрутов всё ещё используют import-time
`include_in_schema=_actions_enabled()` (`app/api/v1/simulator.py:891-1960`) и отсутствуют в
`api/openapi.yaml`. Материальные callers — Simulator API client для шести action/list операций и
payment-targets; material guard — `tests/contract/test_openapi_contract.py:1007`.

**Current behavior:** канонический selector без флага дал `23 passed`, exit `0`; тот же selector с
`SIMULATOR_ACTIONS_ENABLE=1` дал `1 failed, 22 passed`, exit `1`, с extra generated paths. Это
исполняемо подтверждает env-sensitive false green. **Intended:** канон и сгенерированные business
paths совпадают независимо от режима запуска. **Optimal next action:** сохранить P2 и потребовать
репродьюсер в обоих env-состояниях до согласованной правки OpenAPI/route exposure. HTTP/browser и
full-stack не запускались. После baseline `simulator.py` меняли `133e668`, `c759558`; этот механизм
и восемь декораторов не исправлены.

#### `B-A1b-003` — LIVE

`app/api/v1/admin.py:2125-2133` всё ещё превращает любой `Exception` от
`ClearingService.find_cycles` в `raw_cycles=[]` без лога/degraded marker. Материальные sibling
call-sites: `app/api/v1/clearing.py:26` (ошибку не глушит),
`app/api/v1/simulator.py:1659`, `app/core/simulator/real_clearing_engine.py:147,237` и
`app/core/clearing/service.py:1614`; они подтверждают, что blanket fallback не является общей
семантикой сервиса. **Intended:** отказ поиска отличим от честного пустого результата.
**Optimal next action:** падающий route-level reproducer с `find_cycles -> RuntimeError`, затем
явная failure/degraded семантика и централизованный лог. Admin HTTP path не запускался. Коммиты
`02feee7`, `98bc9cc` касались `admin.py`, но не этого блока.

#### `B-A1b-002` — LIVE, Postgres-следствие UNVERIFIED

`app/api/v1/auth.py:66-88` по-прежнему пишет сырой
`http_request.headers.get("X-Request-ID")`; material caller один — успешный `POST /auth/login`.
Middleware в `app/main.py` владеет валидированным correlation ID, а sibling admin audit writer
использует централизованный контекст. После baseline `auth.py` не менялся.

**Current behavior:** статически подтверждена возможность расхождения response/log correlation ID;
rollback аудита остаётся best-effort без отдельной диагностики. Переполнение `String(64)` и точный
класс PG-ошибки не проверены. **Intended:** одна валидированная correlation identity на response,
log и audit row. **Optimal next action:** сначала HTTP/SQLite reproducer на различие IDs; отдельно
disposable Postgres gate для длины/rollback, не смешивая его с уже живым correlation defect.

#### `C-A1a-003` — UNVERIFIED

Кодовая асимметрия сохранилась: `_resolve_participant_or_error` выбирает глобальный
`Participant` (`app/api/v1/simulator.py:611-629`) и вызывается мутирующими trustline/payment paths
(`:929-932`, `:1125-1128`, `:1282-1285`, `:1438-1441`), тогда как list paths сначала получают
run/scenario scope. Это подтверждает current mechanism, но не заявленный cross-run effect.

**Intended behavior** не установлен: real mode может сознательно разделять общую DB, а read scope
может быть только presentation boundary. **Optimal next action:** не чинить и не повышать severity;
нужны два рана на разных сценариях, foreign PID и наблюдение DB/read-model эффекта плюс решение
владельца о run boundary. После baseline `simulator.py` менялся, но resolver/call-sites остались.

### A2 — сверка ignored evidence волны 1

Каталог фактически существует и содержит ровно шесть ожидаемых файлов:
`backend/A1a-{inventory,findings}.md`, `backend/A1b-{inventory,findings}.md`,
`admin-ui/C1-{inventory,findings}.md`; `ledger.md` из шаблона `plan.md:189` отсутствует.

- A1a: 3 inventory rows, `clean=2`, `keep=1`; 14 findings, 2 P2.
- A1b: 26 rows, `keep=15`, `clean=11`; 15 findings, 3 P2.
- C1: 17 product rows, `keep=10`, `clean=6`, `delete=1`; 11 findings, 2 P2.
- Сумма совпадает с tracked summary: 46 файлов, 40 findings, 7 P2, verdict totals
  `keep=26`, `clean=19`, `delete=1`, `merge=0`, `rewrite=0`.
- Все три findings-файла имеют отдельные self-refutation и «Что я НЕ проверял»; они прямо говорят,
  что tests/runtime не запускались. A1a отдельно оставляет C-A1a-003 runtime-unverified, A1b —
  Postgres-ветку B-A1b-002, C1 — весь UI runtime.

Это подтверждает внутреннюю арифметику исторического evidence на `ea9cde9`, но не делает `plans/`
воспроизводимым source of truth. Отсутствие tracked manifest/checksum/ledger сохраняет F-PA-7.

## Рекомендации по исправлению плана

## A3 — исчерпывающая file-to-slice карта

Независимый read-only агент `ses_00b1113baffeR2eAOrmPILPHwS` построил карту из
`git ls-tree -r --name-only <REV>` и буквальных правил `plan.md:91-118`. Оркестратор сверил total
с `git ls-files` и контрольные Wave-1 counts: A1a=3, A1b=26, C1 без тестов=17.

| Revision | Declared files | Ровно один slice | Без slice | Несколько slices |
|---|---:|---:|---:|---:|
| `ea9cde9` | 666 | 452 | 129 | 85 |
| `4d08a7c` | 688 | 472 | 129 | 87 |
| Drift | +22 | +20 | 0 | +2 |

Все 129 zero-assignment существовали уже на baseline: это исходный partition defect, не drift.
Полный machine-list сгруппирован следующими исчерпывающими классами:

- backend: `app/main.py`, `app/config.py`, package markers, весь `app/db/**`, весь `app/utils/**` и
  17 файлов `app/core/simulator/**`, не совпавших с whitelist A2a/A2b;
- Admin: root `App.vue`, `main.ts`, `style.css`, `env.d.ts`, `src/test/setup.ts` и четыре общих
  composable/test-support файла;
- Simulator: root/config/types/fixture/scene/mapping/CSS и общие composables, не перечисленные в
  B1/B2/B3. Именно эти literal paths составляют остальные zero rows; новых zero после baseline нет.

Все baseline 85 multiple-assignment — UI `.test.ts`, одновременно попадающие в функциональный
slice и B4/C4: C1∩C4=15, C2∩C4=4, C3∩C4=7; B1∩B4=5, B2∩B4=6, B3∩B4=48. На HEAD добавились
`admin-ui/src/api/realApi.listContracts.test.ts` (C1/C4) и
`admin-ui/src/utils/decimal.test.ts` (C3/C4), итого 87. Никакого recorded precedence «B4/C4
забирает тесты» в плане нет, хотя закрытая C1 фактически применяла именно его: literal C1=32,
tracked inventory C1=17.

Неоднозначности, которые нельзя угадывать в manifest: «остальные роуты» A1b; примерные семейства
A2a/A2b; неназначенные общие composables; «остальные pages» C3; test precedence. Следовательно,
F-PA-2 подтверждён исчерпывающе: `plan.md:88-89` не выполняется ни на baseline, ни на HEAD.

## A4 — итог test strategy

Результат независимого агента `ses_00b0e1eb0ffe4sLmPVjm2tEh5E` перепроверен по владельцам
конфигурации и package scripts.

**Предлагаемое правило для source slice:** читать вместе с кодом material success/rejection/failure/retry/recovery/
cancellation tests; contract/serialization tests для API/SSE; component/composable tests реального
DOM/state effect; Playwright spec/config при утверждении пользовательского эффекта или safe delete;
policy guard, непосредственно читающий изменяемый source/tooling файл.

**Уникальная роль wave 5:** `tests/conftest.py` и shared state/lifecycle; marker taxonomy и
anti-vacuum; slow/Postgres tier ownership; guards и false-green counter-checks; fixture ownership и
isolation; UI setup/config; Playwright discovery/ports/artifacts/mock-real modes; suite-wide
behavior/tier matrix. Формат: `group / observable behavior / tier / owner source / failure paths /
risk`; per-file verdict остаётся только для delete/merge, крупных файлов, policy guards,
marker-bearing tests и подозрений на no-op/thin-wiring.

Fresh inventory: 179 backend test modules, Simulator unit/component 99, Admin 33; marker scan —
2 slow и 13 Postgres файлов. Admin setup назначен через `admin-ui/vitest.config.ts:6-10` и
`admin-ui/src/test/setup.ts:3-34`; Simulator setup file отсутствует. Playwright живёт вне B4/C4:
`admin-ui/{e2e,e2e-real}` и `simulator-ui/v2/e2e`, с отдельными configs и package scripts.

Gate matrix:

| Gate | Что доказывает | Чего не доказывает |
|---|---|---|
| `verify_local.ps1` targeted/default | selector safety, SQLite default tier `not slow and not postgres` | slow, Postgres, browser |
| full local milestone | default backend, Alembic head, lint/unit/build обоих UI | Postgres и Playwright |
| PR `required-quality` + `ui-smoke` | canonical default и Chromium smoke обоих UI | full/real/visual E2E, Postgres |
| scheduled/manual jobs | Postgres, container, Admin E2E, Simulator super/visual | не являются PR evidence |

Итог: F-PA-4/F-PA-5/F-PA-6 подтверждены. Package scripts достаточны как entrypoints, но план не
связывает exact tier с типом finding/change и потому не задаёт достаточную Phase-II acceptance
matrix.

## A5 — соседние поверхности

| Surface | Классификация | Evidence/owner |
|---|---|---|
| Applied migrations | `INTENTIONALLY EXCLUDED WITH OWNER` | `spec.md:55-57`; policy/PG owner 006, Alembic-head canonical gate |
| `tests/conftest.py`, marker semantics `pytest.ini` | `IN SCOPE` | A4=`tests/**`; marker traps не читаются без `pytest.ini:13-22` |
| `scripts/`, `.github/workflows/` | `EXCLUDED BUT HANDOFF MISSING` | material tier owners; historical 006 не заменяет current handoff |
| Docker | `EXCLUDED BUT HANDOFF MISSING` | historical owner 005; current container/policy CI active |
| `app/config.py`, `app/main.py`, security-relevant app code | `ACCIDENTALLY OMITTED` | declared `app/**`, но нет A-slice assignment |
| Threat model/pentest/dependency/crypto audit | `INTENTIONALLY EXCLUDED WITH OWNER` | 005/non-goals; без сигнала scope не расширяется |
| Canonical fixtures/generators/seeds | `EXCLUDED BUT HANDOFF MISSING` | исключены только generated copies; sync/validation entrypoints активны |
| Generated fixture copies | `INTENTIONALLY EXCLUDED WITH OWNER` | `spec.md:55-57`, canonical sync scripts |
| Playwright configs/suites | `EXCLUDED BUT HANDOFF MISSING` | owner decision «тесты входят» неоднозначен, а declared paths не включают UI `e2e/` |
| OpenAPI | `IN SCOPE` | P2 contract evidence и D1 triangulation |
| Product docs | `INTENTIONALLY EXCLUDED WITH OWNER` | intended-behavior evidence, не inventory surface |

## A6 — Anti-Enterprise

| Механизм | Verdict | Минимальная ценность/изменение |
|---|---|---|
| Application per-file inventory | `KEEP` | единственное machine evidence «прочитано»; генерировать one-file/one-slice manifest |
| Test inventory | `SIMPLIFY` | behavior/tier matrix вместо source-style verdict на каждый тест |
| Per-finding P3 + T808 | `SIMPLIFY` | self-refutation в slice, ручная P1/P2 проверка, один fresh-context T808 |
| T809 | `KEEP` | другая система/модель и отдельная trust boundary после T808 |
| Thresholds | `SIMPLIFY` | только discovery ranking, не условие истинности/допустимости extraction |
| Preliminary rewrite/delete verdicts | `REMOVE` | заменить нейтральными вопросами ownership/references/runtime |
| Baseline tag | `REMOVE` как обязательный tag; `KEEP` frozen SHA | full SHA + revertable commits дают нужное evidence; на `ea9cde9` tag отсутствует |
| Five passes | `SIMPLIFY` | сохранить функции как R0-R4, разделить self-refutation/T808/T809/triage |
| Two parallel tracks | `KEEP` как максимум | bounded orchestration capacity, не обязательная загрузка и не performance claim |

## A7 — матрица критериев владельца

| Критерий | Оценка | Сильная сторона | Подтверждённый пробел | Минимальная рекомендация |
|---|---|---|---|---|
| Корректность и адекватность | `PARTIAL` | effect/path:line/reproducer/default keep | stale baseline, status drift, biased verdicts/thresholds | re-baseline и нейтральные discovery rules |
| Детальность | `PARTIAL` | owner/findings/gates формализованы | test per-file prose и local-only completion evidence | behavior matrix + tracked compact evidence index |
| Последовательность и логика | `PARTIAL` | data planes → internals → synthesis | tests подключены после source verdict; P3/T808 overlap | material tests в source slice, suite-wide wave 5 сохранить |
| Полнота | `FAIL` | declared scope и machine criterion ясны | 129 zero, 87 multiple на HEAD; Playwright/adjacent handoff gaps | exact manifest и A5 boundary table до продолжения |
| Anti-Enterprise | `PARTIAL` | запрет новых слоёв и default keep | лишний test inventory, hard thresholds, preliminary verdicts | применить A6 без нового tooling/framework |

Эта матрица не авторизует правку проверяемого плана; она завершает только его аудит.

Это рекомендации владельцу программы, а не разрешение следующему агенту менять план:

- [x] Записан итог backend recheck пяти P2 с текущими `path:line` и unverified paths.
- [x] Все семь P2 имеют текущий статус; `C-C1-002` отмечена external fix `84ca396`.
- [ ] Для нового transaction shape mismatch создан отдельный finding/triage item.
- [ ] Для волн 2–6 назначен новый frozen HEAD; исторический `ea9cde9` сохранён как baseline волны 1.
- [x] Построена audit-карта one-file/one-slice на baseline и HEAD; исправлять manifest запрещено scope этого аудита.
- [ ] Все неназначенные и дважды назначенные файлы устранены.
- [x] Сформулировано audit-требование читать material tests и уникальная suite-wide роль wave 5.
- [x] Playwright классифицирован как `EXCLUDED BUT HANDOFF MISSING`.
- [x] Определена behavior/tier matrix с выборочным per-file verdict.
- [ ] Статусы `spec.md`, `tasks.md`, `specs/README.md` синхронизированы.
- [ ] Создан компактный tracked evidence index; ignored `plans/` больше не единственное доказательство.
- [ ] Удалены предварительные verdict'ы rewrite/delete; thresholds отмечены как heuristics.
- [ ] P3/T808 дедуплицированы без удаления orchestrator check и T809.
- [ ] Добавлена excluded-surface таблица: migrations, scripts/CI, docker/config, fixtures/generators,
      security boundary, performance boundary, E2E.
- [ ] Исправлены `\.\scripts`, названия проходов и различение NOT STARTED/PAUSED/BLOCKED.
- [ ] Для каждого `NEEDS-RUNTIME` существует задача с exact selector/prerequisite.
- [ ] После правок выполнены `git diff --check` и reference scan по ссылкам программы 008.

## Возможный минимальный target после решения владельца

Если владелец отдельно авторизует исправление плана, достаточная правка:

1. обновить front matter/status и aggregate task table в `spec.md`;
2. добавить явные статусы в `tasks.md`;
3. исправить sequence/slices/test strategy в `plan.md`;
4. добавить компактный tracked manifest/evidence index;
5. зарегистрировать 008 в `specs/README.md`;
6. записать новый frozen HEAD и продолжить с волны 2.

Никаких новых framework, service layer, generated API client или тяжёлого audit tooling для этого
не нужно. Manifest может быть простой tracked-таблицей, построенной `git ls-files`.

## Непроверенное

- A1/A2 перепроверены оркестратором; отдельные Kilo Task-сессии закрыли A3–A8.
- Карта A3 построена статически на обоих SHA; неоднозначные prose-границы применены буквально и
  перечислены, runtime ownership ими не доказывается.
- A4-A7 завершены read-only агентами и вручную сверены по несущим owner/config фактам.
- Отдельный adversarial review audit-report выполнен в A8; browser/runtime evidence он не заменяет.
- Browser/E2E и full-stack не запускались.
- PostgreSQL-гипотеза `B-A1b-002` не проверялась в этом аудите.
- Новый transaction shape mismatch подтверждён статически, но ещё не имеет падающего executable
  reproducer; до него это finding для triage, а не закрытый runtime defect.
- A3 machine-классификация исчерпывает tracked paths и counts, но 688-row manifest не сохранён как
  отдельный tracked artifact; полная row-by-row репродукция требует повторить classifier.
- Audit-файл остаётся untracked и не имеет входящей Markdown-ссылки из tracked документов.
- Inline path targets проверены выборочно; anchors и repository-wide Markdown links не проверены.

## A8 — итог adversarial review

Первоначальные внешние CLI-сессии не дали evidence из-за weekly limit. Затем штатные Kilo Code
Task-сессии успешно закрыли A3 (`ses_00b1113baffeR2eAOrmPILPHwS`), A4-A5
(`ses_00b0e1eb0ffe4sLmPVjm2tEh5E`) и A6-A7 (`ses_00b0e1eafffeqvAK4kjZtJ4pvK`). Отдельная
fresh-context A8-сессия `ses_00b09c3cefferEu4hFHSyWGKge` прочитала сведённый audit целиком.

Оркестратор подтвердил и исправил её существенные замечания: stale front matter/handoff;
противоречивый residual A3; denominators 179/184/185; 7 Simulator specs против 14 directory
entries; categorical recommendations; severity F-PA-1; Playwright classification; границы link
check. Browser/full-stack/Postgres не запускались согласно scope и остаются residual runtime risks,
а не blocker завершения read-only аудита.

Команды, выполненные оркестратором после re-baseline:

```text
.\.venv\Scripts\python.exe -c "...ConfigDict...model_dump..." -> exit 0;
serialize_key False; {'from_': 'A', 'to': 'B'} дважды
.\scripts\verify_local.ps1 -TaskSlug audit008_openapi_default -BackendOnly
  -BackendSelector tests/contract/test_openapi_contract.py -> exit 0; 23 passed
$env:SIMULATOR_ACTIONS_ENABLE='1'; .\scripts\verify_local.ps1
  -TaskSlug audit008_openapi_actions -BackendOnly
  -BackendSelector tests/contract/test_openapi_contract.py -> exit 1; 1 failed, 22 passed
git diff --check -- specs/008-surface-code-review/plan-audit-2026-08-12.md -> exit 0
Test-Path для spec.md, plan.md, tasks.md, external-review-runbook.md, BACKLOG.md и openapi.yaml
  -> exit 0; отсутствующих файлов нет
```

Специализированного `scripts/check_docs_links.py` в репозитории нет: попытка запуска завершилась
exit `2` (`can't open file`). Поэтому ссылки проверены только на существование targets; якоря и
repository-wide Markdown links не заявлены проверенными.
