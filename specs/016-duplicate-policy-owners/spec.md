# 016 — Единые владельцы повторяющейся политики

- **Date:** 2026-08-25
- **Status:** SPECIFIED — implementation not authorized
- **Status authority:** эта метка описательна; завершённость устанавливают success criteria, красные репродьюсеры и записанные evidence, а не поле `Status`.
- **Owner surface:** перечисленные ниже owner-файлы под `app/`, `admin-ui/src/`, `simulator-ui/v2/src/` и их тесты. `api/openapi.yaml` в этой программе read-only канон; migrations, generated fixtures, legacy/archive и owner surfaces незавершённых 012/013/015 не меняются без датированной передачи владения.
- **Origin / severity driver:** сплошной аудит дублирования 2026-08-25, три внутренних независимых среза, ручная перепроверка оркестратором и внешний Claude Code review. В скоуп вошли только восемь подтверждённых P2, где повторяется политика или failure semantics; P3 не расширяют программу.

## Problem

В восьми активных seams один факт или одна policy поддерживаются несколькими владельцами. На текущем
HEAD большинство копий ещё согласованы, но уже наблюдаются лишние вызовы, разные классификаторы и
разошедшиеся wire/runtime semantics. Стоимость не в количестве похожих строк как таковом: изменение
одного жизненного состояния, формы driver exception, AuditLog item или правила activity требует
синхронной правки нескольких модулей и может оставить одну поверхность правдоподобно зелёной.

Машинный clone scan был только генератором кандидатов. Он не является метрикой качества и не входит в
success criteria: generated JSON, тестовые arrange-блоки и намеренные cross-language контракты дают
много ложных совпадений.

Программа не является одним большим refactor. Каждая находка — отдельный revertable slice с собственным
репродьюсером. Общий framework, service layer или универсальный retry/auth/CRUD слой запрещены.

## Owner surface

Разрешённые owner paths после отдельной авторизации реализации:

- integrity evaluation: `app/core/integrity.py`, `app/api/v1/integrity.py`;
- audit materialization: `app/core/trustlines/service.py`, `app/core/payments/engine.py`,
  `app/core/clearing/service.py` и узкий новый pure helper;
- DB error evidence: `app/core/payments/service.py`, `app/core/payments/engine.py`,
  `app/core/clearing/service.py`, `app/core/trustlines/service.py`, новый `app/utils/db_errors.py`;
- stale payment policy: `app/core/recovery.py`, `app/api/v1/admin.py`,
  `app/core/admin/metrics.py` и доменный owner под `app/core/payments/`;
- trustline list/count filters: `app/core/trustlines/service.py`, `app/api/v1/admin.py` только при
  необходимости сохранить caller contract;
- Admin AuditLog: `admin-ui/src/api/adminContracts.ts`, `admin-ui/src/api/realApi.ts`,
  `admin-ui/src/types/domain.ts`, `admin-ui/src/pages/graph/graphTypes.ts`;
- Simulator error extraction: `simulator-ui/v2/src/utils/errorMessage.ts` и пять активных consumers;
- participant analytics: `app/core/admin/metrics.py`,
  `admin-ui/src/composables/useGraphAnalytics.ts`, `admin-ui/src/api/mockApi.ts`;
- поведенческие, contract и architecture tests только для перечисленных seams.

Interlock:

- 015 сохраняет приоритет владения `integrity.py`, финансовым audit/checkpoint и monetary core;
- 013 сохраняет приоритет владения frontend data honesty;
- 016 не запускает пересекающийся slice параллельно. Перед реализацией overlapping task получает
  датированную передачу владения либо ждёт закрытия текущего владельца.

## Findings

Все якоря перепроверены 2026-08-25. Внешний срез сделан на
`8f35f0861a997883066db12ef8ce203075cf270a`; перед записью спеки оркестратор сравнил его с
`1c2e3aa62280e8889693eec4fb5123f95c2ebe76`. Из owner paths изменены два unrelated участка:
`useSimulatorRealMode.ts:1047+` меняет precision-loader, но не error helper на `:110`;
`app/core/clearing/service.py:545+` исправляет только комментарий/evidence о query plan, не SQLSTATE
extractor на `:230` и не audit materialization на `:2002+`. Остальные owner paths имеют пустой
committed diff от reviewed SHA.

### F-016-1 — integrity suite имеет три владельца и запускается дважды в `/verify` (P2)

`app/api/v1/integrity.py:104-141`, `:209-246` и `app/core/integrity.py:78-119` независимо реализуют
`zero_sum`, `trust_limits`, `debt_symmetry` и severity mapping. `POST /verify` выполняет suite inline,
затем `app/api/v1/integrity.py:257-260` вызывает checkpoint computation и повторяет те же три проверки.
На один equivalent приходится шесть invariant scans. Формы результата уже различаются: wire
`InvariantResult`, checkpoint dict и человекочитаемые/machine alerts.

Target: один typed evaluator в `app/core/integrity.py`. `/status`, `/verify` и checkpoint code адаптируют
один результат к wire/storage; `/verify` использует результат один раз и не приобретает новую
обязанность сохранять checkpoint.

### F-016-2 — materialization `IntegrityAuditLog` повторена в пяти production paths (P2)

Trustline create/update/close повторяют checkpoint-after, checksum/status extraction и сборку audit row
в `app/core/trustlines/service.py:249-273`, `:382-430`, `:498-545`. Соседние владельцы повторяют ту же
materialization в `app/core/payments/engine.py:1310-1340` и
`app/core/clearing/service.py:2002-2032`; verify содержит частичную шестую форму в
`app/api/v1/integrity.py:257-283`.

Failure semantics намеренно различаются: trustline path fail-closed, а некоторые payment/clearing
checkpoint paths имеют собственную reconciliation/best-effort policy. Target поэтому только pure
materializer, принимающий уже вычисленные facts/checkpoints и caller-owned `operation_type` /
`affected_participants`. Он не владеет checkpoint execution, exception handling, commit или payload
shape. Create сохраняет `{from,to}`, update/close — `{from,to,trustline_id}`.

### F-016-3 — SQLSTATE и exception chain извлекаются несовместимо (P2)

- `app/core/payments/service.py:47-83` обходит `orig/cause/context` и не принимает wrapper `.code` за
  SQLSTATE;
- `app/core/payments/engine.py:399-405,441-449` читает только непосредственный `orig`, хотя соседний
  constraint extractor обходит цепочку;
- `app/core/clearing/service.py:229-250` глубоко собирает `.sqlstate/.pgcode/.code` без различения
  wrapper code;
- `app/core/trustlines/service.py:50-101` имеет ещё один chain walker и table/detail fallback.

Внутреннее расхождение уже наблюдаемо: `app/core/payments/service.py:471` вызывает приватный shallow
`engine._get_pgcode()` для `55P03`, а `:475` классифицирует ту же ошибку собственным deep extractor.

Target: `app/utils/db_errors.py` владеет только `iter_exception_chain`, множеством SQLSTATE и constraint
name/table evidence. Retry/reconcile policies остаются в доменных сервисах; узкое payment-правило
`23505 + INSERT INTO debts + uq_debts_debtor_creditor_equivalent` не обобщается.

### F-016-4 — `TrustLineService.list_all/count_all` дважды разрешают один filter (P2)

`app/core/trustlines/service.py:628-659` и `:682-716` независимо resolve-ят creditor, debtor,
equivalent и строят одинаковые predicates. Admin caller вызывает методы парой
(`app/api/v1/admin.py:1409-1423`), поэтому один request повторяет lookup queries, а будущий фильтр может
развести `items` и `total`.

Target: private resolved filter spec/predicate builder с явным `no_match`. `list_all` сохраняет ordering
и `[]`, `count_all` — `0`; переход на window count не является условием программы.

### F-016-5 — active/stale payment policy имеет несколько источников истины (P2)

Шесть состояний и `updated_at < cutoff` повторены в `app/core/recovery.py:23-30,163-181`, общем Admin
наборе `app/api/v1/admin.py:113-120` с тремя projections и inline-копии
`app/core/admin/metrics.py:649-659`. `app/core/payments/service.py:261-267` содержит соседнюю пятиэлементную
state-policy для idempotency conflict; она не является stale predicate и не должна автоматически
поглощаться тем же helper.

Target: доменный immutable `ACTIVE_PAYMENT_TX_STATES` и общий stale clause builder. Recovery сохраняет
abort policy, Admin — presentation/query shape. Каждый active state получает positive control, каждый
terminal state — negative control.

### F-016-6 — один AuditLog wire item проверяется двумя расходящимися Zod schemas (P2)

`admin-ui/src/api/adminContracts.ts:85-103` требует UUID/date-time и `.strict()`, а
`admin-ui/src/api/realApi.ts:156-172` принимает произвольные строки и `.passthrough()`. Канон
`api/openapi.yaml:4743-4791` требует UUID/date-time, nullable object state и не задаёт
`additionalProperties: false`. Дополнительно `admin-ui/src/pages/graph/graphTypes.ts:50-62` делает
`object_type/object_id` обязательными non-null и теряет `user_agent`, расходясь с
`admin-ui/src/types/domain.ts:49-63`.

Target: одна exported OpenAPI-aligned runtime schema и выведенный из неё тип для real, mock и graph.
Нельзя подгонять OpenAPI под permissive real decoder или переносить текущий `.strict()` без отдельного
контрактного решения.

### F-016-7 — Simulator error extraction имеет общий owner и пять обходов (P2)

`simulator-ui/v2/src/utils/errorMessage.ts:4-13` уже обрабатывает `Error`, string и object `message`.
Пять активных вариантов в `useSimulatorApp.ts:76-80`, `useSimulatorRealMode.ts:110-114`,
`useCookieSessionBootstrap.ts:19-22`, `useSceneState.ts:83-86` и `useInteractActions.ts:104` теряют
часть этой семантики. Например, rejection `{message: "reason"}` превращается в `[object Object]` на
трёх paths.

Target: все пять consumers используют `extractErrorMessage`; fallback policy передаётся явным
параметром только там, где она действительно отличается.

### F-016-8 — participant activity считается тремя расходящимися алгоритмами (P2)

Backend `app/core/admin/metrics.py:569-735`, fallback
`admin-ui/src/composables/useGraphAnalytics.ts:548-680` и
`admin-ui/src/api/mockApi.ts:726-806` расходятся по временной точке, моменту закрытия trustline,
источнику participant operations и определению участия в payment/clearing. Backend использует
`updated_at` закрытой линии и audit actions; fallback использует `created_at` и только
`admin.participants.*`; mock использует `Date.now()`, считает transaction как participant operation и
видит только initiator.

`mockApi.participantMetrics` мёртв на product path: единственный production caller выполняется только
при `isRealMode`, когда transport уже `realApi`. Target: удалить мёртвый endpoint; письменно объявить
семантику server contract и привести живой fallback к ней. Backend-код не считается правильным только
по факту существования: anchor/close-time/operation vocabulary сначала фиксируются conformance cases.

### Disposition внешнего среза D01–D20

| Verdict | IDs | Диспозиция |
|---|---|---|
| `CONFIRMED P2` | D01, D02, D03, D04, D06, D08, D09, D13 | F-016-1…F-016-8 и задачи ниже |
| `CONFIRMED P3` | D05, D07, D10, D14, D16, D17, D18, D19, D20 | не расширяют 016; отдельный backlog/cleanup при своём owner и своём gate |
| `CONFIRMED P3`, уже имеет owner | D15 launcher helpers | `specs/BACKLOG.md`, строка `Launcher runtime helpers`; не переоткрывать |
| `OVERSTATED P3` | D12 simulator enum catalogue | допустимо свести только TS union и runtime list через `as const`; OpenAPI, runtime Set, UI order и total title Record выполняют разные роли |
| `REFUTED` | D11 generic paginated loader | `useLatestRequest` уже является общей primitive; остаток — разная route/filter policy, новый composable был бы ложной абстракцией |

## Current / Intended / Optimal

| Состояние | Описание |
|---|---|
| **Current** | Восемь P2 seams имеют от двух до шести владельцев. Часть копий согласована тестами, но D01 уже делает двойную работу; D03, D04, D06 и D13 имеют наблюдаемый semantic drift. |
| **Intended** | OpenAPI остаётся каноном wire shape; доменные transition/retry/fail-closed policies остаются у owning services; fallback и mock не создают параллельную бизнес-семантику; один факт имеет один проверяемый owner. |
| **Optimal** | Восемь узких reusable boundaries, каждая доказана красным репродьюсером и negative controls. Никакой общей «архитектуры переиспользования» поверх несвязанных доменов. |

## Non-goals

- Не объединять Payment/Clearing/Trustline retry policies; общий только extractor evidence.
- Не менять OpenAPI, применённые migrations, ORM или wire aliases ради удобства helper.
- Не объединять root/versioned health routes, auth priority chains, REST/SSE/fixture decoders,
  participant list/search alias или typed simulator endpoints.
- Не сводить backend/Admin/Simulator money rendering в одну файловую зависимость: три реализации
  намеренно связаны `api/money-rendering-conformance.json`.
- Не рефакторить generated fixture JSON и не обобщать v1 community generators.
- Не включать P3 cleanup в P2 implementation wave. Dead Graph tabs и `httpText` удаляются отдельным
  cleanup slice после полного reference scan.
- Не считать число clone detector findings success metric.

## Verification plan

### 1. Репродьюсеры, обязанные падать на текущем коде

- `R016-1`: spy на `InvariantChecker` — `/integrity/verify` обязан вызвать каждый invariant ровно один
  раз на equivalent; current actual — два.
- `R016-2`: nested SQLAlchemy/driver exception shapes — один `40001/40P01/55P03` даёт одинаковое
  extracted evidence во всех consumers; current engine shallow path расходится.
- `R016-3`: один Admin request `list + count` resolve-ит каждый PID/equivalent filter один раз; current
  actual — дважды.
- `R016-4`: active/terminal state matrix проходит recovery и все Admin projections через один owner;
  current source не имеет такого owner и содержит inline-копию в metrics.
- `R016-5`: real AuditLog decoder отвергает non-UUID/non-date-time и принимает nullable object state с
  разрешёнными extra keys; current real schema пропускает первые два.
- `R016-6`: `{message: "reason"}` возвращает `reason` на пяти Simulator consumers; current три paths
  возвращают `[object Object]`.
- `R016-7`: один table-driven activity fixture даёт одинаковые windows для backend contract и живого
  TS fallback, включая recipient-only payment, clearing edge, close `updated_at` и оба action prefixes;
  current результаты расходятся.
- `R016-8`: audit materializer conformance table строит одинаковые checksum/status/error fields для
  trustline/payment/clearing facts, сохраняя caller-owned operation/payload и exception policy.

### 2. Инварианты и anti-vacuum

- Integrity violations остаются critical/critical/warning и fail-closed там, где fail-closed владеет UoW;
  positive control обязан содержать реальное нарушение каждого класса.
- SQLSTATE controls включают retryable `40001/40P01`, lock `55P03`, узкий допустимый payment `23505` и
  non-retryable FK/CHECK/другой UNIQUE. Ни один classifier не становится broad retry.
- State matrix явно включает все шесть active states и минимум `COMMITTED/ABORTED/FAILED` как terminal
  negative controls; отсутствие измерения не считается пустым набором.
- Audit materializer не ловит исключения и не коммитит; create/update/close payload shapes проверяются
  раздельно.
- AuditLog schema проверяется и на обязательные/nullable поля, и на дополнительное разрешённое поле.
- Activity conformance содержит both initiator and non-initiator participant paths; mock endpoint после
  удаления имеет anti-vacuum reference scan по package scripts, tests и dynamic imports.

### 3. Существующие selectors, обязанные остаться зелёными

```powershell
$env:DEBUG = "false"
powershell -ExecutionPolicy Bypass -File .\scripts\verify_local.ps1 `
  -TaskSlug p016_backend `
  -BackendOnly `
  -BackendSelector `
    tests/integration/test_integrity_endpoints.py `
    tests/unit/test_integrity_checkpoints.py `
    tests/unit/test_payment_db_error_classifier.py `
    tests/unit/test_recovery_cleanup.py `
    tests/unit/test_trustline_audit_fail_closed.py `
    tests/unit/test_admin_trustlines_list.py `
    tests/unit/test_admin_participant_metrics.py

npm.cmd --prefix admin-ui run test -- `
  src/composables/useGraphAnalytics.test.ts `
  src/api/mockApi.participantMetrics.test.ts `
  src/api/realApi.listContracts.test.ts
npm.cmd --prefix admin-ui run build

npm.cmd --prefix simulator-ui/v2 run typecheck
npm.cmd --prefix simulator-ui/v2 run test:unit -- `
  src/composables/useInteractActions.test.ts `
  src/composables/useSceneState.test.ts `
  src/composables/useSimulatorRealMode.test.ts
npm.cmd --prefix simulator-ui/v2 run build
```

При изменении transaction/retry seam обязательны отдельные Postgres selectors с уникальной disposable
DB и `-BackendMarker postgres`; SQLite не является evidence driver wrapping/concurrency.

### 4. Запрещённые способы проверки

- Не считать зелёный full-stack прогон доказательством конкретного owner seam.
- Не доказывать D03 только synthetic exception с `sqlstate` на первом `orig`; нужен nested driver shape
  и negative wrapper `.code="dbapi"`.
- Не доказывать D01 числом `200 OK`; нужен call-count и согласованность status/checkpoint adapters.
- Не обновлять OpenAPI или fixture expectation, чтобы ослабленный frontend decoder стал зелёным.
- Не удалять mock/dead code по одному `rg`: проверить package scripts, dynamic imports, docs и history.
- Не регенерировать public/generated fixtures как побочный эффект frontend build без осознанного diff.

Milestone после отдельных cheap gates:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify_local.ps1 -TaskSlug p016_premerge
```

Он исключает `slow` и `postgres`, что указывается в evidence явно.

## Tasks

Легенда: `[x]` выполнено · `[ ]` в работе · `[!]` заблокировано или не авторизовано.

| ID | Задача | Статус |
|---|---|---|
| T1600 | Построить `R016-1…R016-8`, записать actual/expected и anti-vacuum controls до изменения поведения | `[!]` implementation not authorized |
| T1601 | F-016-1: единый typed integrity evaluator, один suite run в `/verify` | `[!]` implementation not authorized; interlock 015 |
| T1602 | F-016-2: pure audit materializer без владения commit/failure policy | `[!]` implementation not authorized; после T1601, interlock 015 |
| T1603 | F-016-3: общий DB error evidence extractor, доменные policies раздельны | `[!]` implementation not authorized |
| T1604 | F-016-4: resolved trustline filter spec для list/count | `[!]` implementation not authorized |
| T1605 | F-016-5: доменный owner active states и stale predicate | `[!]` implementation not authorized |
| T1606 | F-016-6: единая OpenAPI-aligned Admin AuditLog schema/type | `[!]` implementation not authorized; interlock 013 |
| T1607 | F-016-7: все Simulator consumers используют shared error extractor | `[!]` implementation not authorized; interlock 013 |
| T1608 | F-016-8: зафиксировать activity semantics, удалить dead mock endpoint, выровнять fallback | `[!]` implementation not authorized; interlock 013 |
| T1609 | Независимый adversarial scan: пропущенные copies/callers, false abstractions, bypass paths | `[!]` implementation not authorized |
| T1610 | Cheap gates каждого slice, затем full local milestone и нужные Postgres selectors | `[!]` implementation not authorized |
| T1611 | Обязательное внешнее Claude Code review final implementation range на exact HEAD и публикация evidence ledger | `[!]` implementation not authorized |

## Changelog

- **2026-08-25 — программа заведена.** Внутренний аудит: три read-only owner slices; оркестратор
  независимо перепроверил несущие факты. Внешний review выполнен Claude Code CLI `2.1.241` командой
  с `--model opus --effort high --permission-mode plan --disallowedTools Edit,Write,NotebookEdit
  --output-format json` в standalone clone на
  `8f35f0861a997883066db12ef8ce203075cf270a`. Exit `0`, JSON `44166` bytes, `is_error=false`,
  `subtype=success`, resolved model `claude-opus-5`, marker `VERDICT-DUP-AUDIT: FINDINGS`.
  Диспозиция: 8 `CONFIRMED P2`, 10 `CONFIRMED P3`, 1 `OVERSTATED P3`, 1 `REFUTED`, 0 P1.
  Review files сохранены вне репозитория и не являются source of truth. Перед интеграцией спеки
  committed owner paths перепроверены на `1c2e3aa`: изменены только unrelated precision-loader в
  `useSimulatorRealMode.ts:1047+` и evidence-комментарий в `app/core/clearing/service.py:545+`;
  подтверждённые seams на `useSimulatorRealMode.ts:110`, `clearing/service.py:230` и `:2002+`
  остались прежними.
