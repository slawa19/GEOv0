# 006 — Verification integrity

- **Date:** 2026-08-11
- **Status:** IN PROGRESS — срез Волны 1 (T601, T607, T608a) закрыт 2026-08-11; остаток авторизован для Волны 5
- **Status authority:** метка описательная; завершённость устанавливают success criteria и evidence.
- **Owner surface:** `tests/`, `pytest.ini`, `tests/conftest.py`, `.github/workflows/quality.yml`, `scripts/verify_local.ps1`, `admin-ui/src/test/`, SSE-ветвление в `app/api/v1/simulator.py`, а также применение patch в `simulator-ui/v2/src/demo/patches.ts` (F-006-1)
- **Почему это отдельная и ранняя программа:** пока гейты сообщают неправду, любое «зелено» из программ 002–005, 007 недоказуемо. AGENTS.md §5 уже запрещает заявлять «CI green»; эта программа делает так, чтобы запрет перестал быть нужен.

**Распределение severity:** 2 × P2, 9 × P3 (всего 11 находок).

## Problem

Часть проверок зелена не потому, что код корректен, а потому, что проверка ничего не проверяет.
Худший случай — production-код симулятора, который ветвится по переменной окружения pytest: путь,
выполняемый в тестах, — не тот, который выполняется в проде, поэтому «зелено» тестов ничего не
говорит о production-ветке.

Отдельно и слабее: замороженное решение Phase 5 «отвергать устаревшие event id до применения
состояния и эффектов» было изменено коммитом `2e9a68e`, а охранявший его тест переписан под новое
поведение. Проверка 2026-08-11 показала, что для дедупликации, счётчиков и FX новое поведение
**корректно** и тест не «кодирует дефект» (подробности в F-006-1). Остаточный дефект узкий и
касается только событий, несущих patch с абсолютными значениями.

## Owner surface

Входит: тестовая инфраструктура, маркеры, CI-джобы, те места production-кода, которые существуют
исключительно ради тестов (SSE-ветвление), а также применение patch в real mode (F-006-1).

Не входит: содержательные исправления находок из 003/004/005 — эта программа делает их
проверяемыми, но не выполняет.

## Findings

| ID | Sev | Находка | Evidence |
|---|---|---|---|
| **F-006-1 (N-B1)** | P3 / **VERIFY FIRST** | События, несущие patch, применяются без какой-либо проверки давности: patch содержит **абсолютные** значения, а единственная защита — `!== undefined` (наличие поля), но не свежесть. Ordering-проверка сейчас невыразима: `NodePatch`/`EdgePatch` не несут ни seq, ни version, ни ts. **Это не demo-путь: `demo/patches.ts` — единственный применитель patch в приложении, он же используется в real mode** | `simulator-ui/v2/src/demo/patches.ts:44-62` (`:46`, `:48`, `:57`, `:58`); типы без порядкового поля — `simulatorTypes.ts:305-306`, нормализаторы `normalizeSimulatorEvent.ts:369-370,438-439,485-486`; исходное решение `001/phase5-simulator-map.md:52` |
| **F-006-2 (B3)** | **P2** | Production SSE-генератор ветвится по `PYTEST_CURRENT_TEST`: под тестом отбрасывает кадры `run_status`, накладывает дедлайн 3.0/6.0 с и фабрикует синтетический `tx.updated`, если настоящего не дождался. Путь, который проверяют тесты, — не тот, который исполняется в проде | `simulator.py:2067` (`is_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))`), `:2170` и `:2293` (`if is_pytest:`); отбрасывание `run_status` — `:2195-2196`; дедлайн — `:2183`; синтетический `tx.updated` — `:2215-2218` |
| **F-006-2b (F3)** | P3 | Публичный query-параметр `stop_after_types` объявлен в API и полностью протянут до генератора, но **все** места его runtime-использования лежат внутри `if is_pytest:` → вне pytest параметр инертен: клиент может его передать и не получить никакого эффекта | объявлен `simulator.py:2355` и `:2590`, разбирается `_parse_stop_after_types` (`:2042`), протянут `:2398`/`:2607` в параметр генератора `:2055`; используется только `:2178, :2183, :2209, :2212, :2218` — все внутри блока `:2170` |
| **F-006-3 (C-cov)** | P3 | Понижено с P2 2026-08-11: это **пробел покрытия, а не дефект** — доказательства корректности 429 нет, но и доказательства поломки нет. Отсутствующее измерение не равно нулевому (AGENTS.md §1). HTTP rate limiter выключен во всём сьюте → 429, окно и fallback не покрыты ни одним тестом | `tests/conftest.py:44` (`settings.RATE_LIMIT_ENABLED = False`); лимитер `app/api/deps.py:42` |
| **F-006-4 (C7)** | P3 | Маркер `e2e` зарегистрирован, но не имеет ни одного носителя → дефолтный deselect `not e2e` — фикция | `pytest.ini:17`; `grep mark.e2e tests/` → 0 |
| **F-006-5 (C6)** | P3 | Отладочный путь `pytest -m postgres` skip-зелёный на SQLite; fail-closed только у канонического раннера | `verify_local.ps1:105-111` |
| **F-006-6 (C8)** | P3 | `container-smoke` только по `workflow_dispatch`, никогда не по расписанию и не на PR | `.github/workflows/quality.yml:169` |
| **F-006-7 (B-1)** | P3 | Admin vitest недетерминирован: реальные rAF-переходы тоста Element Plus текут → 200/200 passed при exit code 1 | `admin-ui/src/test/setup.ts` (только `vi.restoreAllMocks()`, без teardown rAF/тостов); `errorToast.ts:22-32`; `adminConfigFeatureFlags.contract.test.ts` без `vi.mock('element-plus')` |
| **F-006-8 (B1/F1)** | P3 | «OpenAPI semantic parity» реализована как ratchet по зафиксированному дрейфу; исходный пересчёт видел только `GET /health` из 87 операций. После T501 свежий baseline — 2 из 88 (`GET /health`, `GET /admin/health/db`); суть finding подтверждена | `tests/contract/test_openapi_contract.py:29-53,290-305,1004-1186` |
| **F-006-9** | P3 | Ruff/Black — `continue-on-error`. Измеренный долг на **пиннутых** версиях (`ruff==0.1.14`, `black==24.1.1`) в гейтируемой области `app migrations`: **ruff — 24 находки** (19 `F401`, 3 `F841`, 1 `E712`, 1 `E741`), из них **19 чинятся безопасным `--fix`**, ещё **4 (3× `F841`, 1× `E712`) только под `--unsafe-fixes`**, а **1 (`E741`) не автофиксится вовсе**. **Black — 98 файлов под переформатирование, 43 без изменений**; `black --diff` даёт **11248 строк raw diff**, из них **4931 строк с префиксом `+`/`-`** (включая 196 заголовков файлов → 4735 содержательных). По всему репозиторию: ruff 118 находок, black 253 файла. Отдельно: `F821 Undefined name 'NoReturn'` (`scripts/generate_scenario_events.py:77`) — вне гейтируемой области | `.github/workflows/quality.yml:87-93`; `requirements-dev.txt:6-7`; перемеряно 2026-08-11 в песочнице с теми же пиннутыми версиями и тем же скоупом |
| **F-006-10** | P3 | `001_initial_schema.py` — единственная миграция с **незащищённым по диалекту** Postgres-специфичным DDL; во всех последующих миграциях образец guard уже применён (`004:34,44`, `005:27,141`, `006:27,43`, `007:29,41,51,53`, `011`, `012`, `014`, `017`). Из-за этого `alembic upgrade head` не выполняется на SQLite. **Находка во многом мутная:** ни один путь проекта не создаёт SQLite dev-БД через alembic | `migrations/versions/001_initial_schema.py:25` (`op.execute('CREATE EXTENSION IF NOT EXISTS pgcrypto')`); воспроизведено 2026-08-11: `alembic upgrade head` на `sqlite+aiosqlite` → `sqlite3.OperationalError: near "EXTENSION": syntax error` |

### F-006-1 — переформулировано 2026-08-11

Прежняя формулировка («тест инвертирован и теперь кодирует дефект») **не подтвердилась и отозвана**.
Основания:

- **Дедупликация идёт по `event_id`, а не по порядковому номеру.** `realEventPipeline.ts:88-90`
  проверяет `processedEventIds.has(event.event_id)` и возвращает `duplicate`; вторая половина
  контракта — `commit()` в `:97-112`, который записывает id и подрезает множество по LRU (`:102`);
  множество сбрасывается при смене run (`:84-87`).
- Следовательно, **никогда не виденное** событие с **меньшим** producer sequence — это новые данные,
  применяемые ровно один раз. Отвергать его значило бы **терять данные** — а именно это и починил
  коммит `2e9a68e` («fix(simulator): make replay gaps recoverable»).
- `monotonicCursor` (`:48-53`) при этом **сохранён**, поэтому курсор переподключения по-прежнему
  никогда не откатывается назад. Тест не инвертирован, а **переспецифицирован под реальное
  исправление**. Для дедупликации, счётчиков и FX текущая приёмка **корректна** — эта половина
  прежнего утверждения снимается явно.

**Остаточный дефект — узкий и настоящий: события, несущие patch.** Patch содержит **абсолютные**
значения и применяется без всякой проверки порядка: `demo/patches.ts:44-62`, например `:46`
`target.net_balance_atoms = p.net_balance_atoms`, `:48` `target.net_balance = p.net_balance`,
`:57` `target.used = p.used`, `:58` `target.available = p.available`. Единственный guard — `!== undefined`,
то есть наличие поля, но не его свежесть. `NodePatch`/`EdgePatch` не несут ни seq, ни version, ни ts
(`simulatorTypes.ts:305-306`; нормализаторы `normalizeSimulatorEvent.ts:369-370,438-439,485-486`),
поэтому проверка давности сейчас **невыразима без изменения протокола**.

**Важно: несмотря на путь `demo/`, это REAL MODE.** `demo/patches.ts` — единственный применитель
patch во всём приложении: `useSimulatorApp.ts:28` импортирует `createPatchApplier`, `:1148` строит
`realPatchApplier`, `:1518` передаёт его в `useSimulatorRealMode` (создаётся на `:1505`, гейтится
`isRealMode` = `apiMode.value === 'real'` на `:413`), потребляется в `useSimulatorRealMode.ts:637`
и применяется в `realEventPipeline.ts:234-235, 313-314, 349-350, 414-415`. Отдельного модуля patch
для real mode не существует. Имя каталога — устаревшее расположение, а не признак режима; не
списывать находку как «только demo».

`producerSequence()` по-прежнему существует (`realEventPipeline.ts:36-46`), но его единственный
оставшийся потребитель — `monotonicCursor()`; применение состояния он не гейтит.

### F-006-10 — что проверено и почему T609 не однострочник

Буквальное утверждение **истинно и воспроизведено**. Но практическая значимость мала: **ничто в
проекте не создаёт SQLite dev-БД через alembic**.

- `scripts/init_sqlite_db.py:12-16` и `tests/conftest.py:101` создают схему через
  `Base.metadata.create_all`, а не миграциями.
- `docker/docker-entrypoint.sh:19` жёстко отказывает не-PostgreSQL URL
  (`raise SystemExit(f"Unsupported DATABASE_URL scheme: ...")`) **до** того, как дойдёт до
  `alembic upgrade head` на `:58`.
- CI (`.github/workflows/quality.yml:139`) и `scripts/verify_local.ps1:137` гоняют только
  `scripts/check_alembic_heads.py`.

**T609 — не однострочная правка.** Условный guard вокруг `CREATE EXTENSION` необходим, но
недостаточен: миграция 001 дальше использует `postgresql.JSONB` (проверено —
`CompileError: … can't render element of type JSONB` на диалекте SQLite), `server_default`
`gen_random_uuid()` и семь конструкций `ALTER TABLE … ADD CONSTRAINT`, которых SQLite не
поддерживает. Полная поддержка SQLite в миграции 001 — отдельная работа; её объём должен быть
оценён до авторизации.

### Пробелы покрытия без владельца

`app/core/integrity.py` / `app/core/invariants.py` — вычисление никогда независимо не проверялось.
Паритет ORM↔миграции доказан только для `simulator_runs`/017; девять модулей моделей не сверялись.
Ни один браузер никогда не гонял реальный SSE — все Playwright-сьюты мокают транспорт
(`001/phase5-simulator-map.md:148-149`). Admin real-transport Chromium smoke не является CI-джобой.
Конкурентность auth challenge/refresh помечена `UNVERIFIED / NO FIX` (`001/phase2-owner-map.md:46`).

## Current / Intended / Optimal

**Current.** Зелёный прогон не отличает «проверено» от «пропущено», а в случае SSE-генератора
проверяет не ту ветку кода, которая исполняется в проде.

**Intended.** Каждый гейт либо доказывает утверждение, либо явно объявляет себя диагностикой.

**Optimal.** Ни одна ветка production-кода не зависит от факта запуска под тестом; тесты, кодирующие
решения, ссылаются на документ решения; маркеры и джобы, ничего не отбирающие, удаляются, а не
остаются для вида; Ruff переводится в блокирующий после механического среза, Black — только после
отдельного датированного решения.

**Осторожно с F-006-1 (VERIFY FIRST).** Никакого «восстановления отвержения устаревших id» делать
не нужно — по `event_id` дедупликация работает, и общее отвержение по порядковому номеру привело бы
к потере данных. Вопрос ровно один и узкий: **может ли бэкенд при переподключении/replay доставить
ранее не виденное, но более старое событие с patch?** Пока это не доказано на
`app/core/simulator/sse_broadcast.py` (файл переписан тем же коммитом `2e9a68e`), тратиться на
исправление нельзя. По итогам проверки — либо гейтировать применение patch-событий по producer
sequence (что требует протокольного поля), либо зафиксировать датированное решение, объясняющее,
почему устаревшие абсолютные patch приемлемы.

## Non-goals

- Погоня за процентом покрытия.
- Обновление версий Ruff/Black в этой программе: на текущем ruff (0.16.x) тот же скоуп даёт
  **1754** находки против 24 — умножение долга в ~70 раз. Версионный апгрейд — отдельное решение.
- Массовое переформатирование Black (98 файлов): в этой программе Black остаётся диагностикой.
- Исправление самих дефектов из 003/004/005.
- Полная поддержка SQLite в миграции 001 — за пределами T609 (см. разбор F-006-10).

## Verification plan

1. Сначала — доказательство на `app/core/simulator/sse_broadcast.py`: может ли бэкенд доставить
   ранее не виденное **более старое** patch-событие. Если да — гейт применения patch по producer
   sequence с тестом, который **падает** на текущем применителе, и ссылкой на
   `001/phase5-simulator-map.md:52`. Если нет — датированное решение в
   `docs/ru/09-decisions-and-defaults.md` вместо кода.
2. Grep `PYTEST_CURRENT_TEST` по `app/` даёт ноль совпадений; тесты, зависевшие от синтетических
   событий, переписаны на реальный транспорт или помечены как debug-path.
3. `stop_after_types` либо становится работающим публичным параметром вне pytest, либо удаляется
   из OpenAPI и сигнатур.
4. Rate limiter включён минимум в одном наборе тестов, 429/окно/fallback покрыты.
5. `pytest.ini`: маркер без носителей удалён либо носители добавлены.
6. Admin vitest: 20 последовательных прогонов дают стабильный exit code.
7. Ruff на пиннутых версиях: `app migrations` чисты, джоб переведён в блокирующий.
8. Black остаётся неблокирующей диагностикой; перевод в блокирующий возможен только после
   отдельного датированного решения (см. T608b).

## Tasks

| ID | Задача | Статус |
|---|---|---|
| T600 | **VERIFY FIRST.** Доказать на `sse_broadcast.py`, может ли бэкенд доставить не виденное ранее более старое patch-событие. По итогу — либо гейт применения patch по producer sequence (нужно протокольное поле seq/version/ts в `NodePatch`/`EdgePatch`), либо датированное решение о приемлемости устаревших абсолютных patch | `[x]` |
| T601 | Убрать ветвление по `PYTEST_CURRENT_TEST` из production SSE (F-006-2, P2) | `[x]` |
| T602 | Решить судьбу `stop_after_types`: сделать работающим вне pytest или убрать из контракта и сигнатур (F-006-2b, P3) | `[x]` |
| T603 | Включить rate limiter в тестах; покрыть 429/окно/fallback | `[x]` |
| T604 | Привести маркеры `pytest.ini` в соответствие с носителями | `[x]` |
| T605 | Сделать `pytest -m postgres` fail-closed вне канонического раннера | `[x]` |
| T606 | Добавить `container-smoke` в регулярное расписание | `[x]` |
| T607 | Устранить rAF/тост-флак в admin vitest setup | `[x]` |
| T608a | Ruff на пиннутых версиях: `--fix` (19 находок), затем 5 ручных случаев (3× `F841` и 1× `E712` под `--unsafe-fixes`, 1× `E741` вручную); перевод ruff-джоба в блокирующий. Ограниченный объём: одна команда + 5 правок | `[x]` |
| T608b | Black **остаётся неблокирующей диагностикой**: переформатирование 98 файлов / 4931 `+`/`-`-строк уничтожит `git blame`. Перевод в блокирующий — только после отдельного датированного решения | `[x]` |
| T609 | Диалектный guard вокруг `CREATE EXTENSION pgcrypto` в миграции 001 по образцу миграций 004-017. **Не однострочник:** отдельно оценить `postgresql.JSONB`, `gen_random_uuid()` и 7× `ALTER TABLE … ADD CONSTRAINT`, которые SQLite тоже не примет. Приоритет низкий: alembic не используется для создания dev-SQLite | `[x]` |
| T610 | Решение по OpenAPI-ratchet: план сокращения дрейфа либо честное переименование гейта | `[x]` |
| T611 | Независимое внешнее ревью и evidence на точном HEAD (триггер AGENTS.md §15: программа меняет сам механизм проверки, поэтому самопроверка гейта не является доказательством) | `[!]` |

## Changelog

### 2026-08-11 — T601

- **До:** свежая проверка `rg -n -C 5 "PYTEST_CURRENT_TEST|is_pytest|stop_after_types|synthetic|tx\.updated" app/api/v1/simulator.py tests` завершилась с exit code 0 и подтвердила все анкоры F-006-2: `app/api/v1/simulator.py:2067` читал `PYTEST_CURRENT_TEST`, ветка начиналась на `:2170`, отбрасывала `run_status` на `:2195-2196`, фабриковала `tx.updated` на `:2215-2218` и останавливала runtime на `:2293-2297`.
- **Изменение:** коммит `e712bfd90f91135842c85c86496349e0317b6255` удалил pytest-зависимую ветку целиком. Обычный production loop теперь следует сразу после bootstrap tail (`app/api/v1/simulator.py:2160`). Публичный `stop_after_types` намеренно оставлен в OpenAPI и сигнатурах (`api/openapi.yaml:1046,1526`; `app/api/v1/simulator.py:2042,2055,2242,2285,2477,2494`) без нового runtime-поведения: решение его судьбы остаётся неавторизованным T602.
- **Тестовый transport:** зависимые ASGI-тесты закрывают поток штатным terminal `run_status` и читают настоящие события через replay; координаторы явно помечены как test/debug path (`tests/integration/test_simulator_sse_fixtures_clearing_animation_pair.py:33`, `test_simulator_sse_trust_drift_decay_topology_patch.py:30-46`, `test_simulator_sse_tx_failed_timeout.py:65`, `test_simulator_super_smoke.py:109`). Атомарный unit-тест завершается настоящим terminal event (`tests/unit/test_simulator_sse_replay_atomic.py:177`).
- **Неудачные попытки сохранены:** `wave1_t601` — exit 1, `2 failed, 14 passed`: trust-drift получил только `{'run_status': 1}`, timeout не увидел `tx.failed`; `wave1_t601_fix` — exit 1, `1 failed, 15 passed`; `wave1_t601_timeout_debug` — exit 1 с точным `actual=['run_status', 'run_status', 'run_status']`. Причина была в harness: replay не запрашивался либо ASGI подписывался до post-commit события; production-код для этих исправлений не менялся.
- **После:** `rg -n "PYTEST_CURRENT_TEST" app` — exit 1, то есть ноль совпадений. `wave1_t601_timeout_fix` — exit 0, `1 passed`; canonical cheap gate `wave1_t601_green` на пяти затронутых файлах — exit 0, `16 passed`; expensive milestone `wave1_t601_super_smoke` с `-IncludeExpensive` — exit 0, `3 passed`. `git diff --check` на шести изменённых файлах — exit 0.
- **Cleanup и sibling remediation:** cleanup `f37fe52` удалил ставший мёртвым import и сдвинул актуальные анкоры `stop_after_types` на `app/api/v1/simulator.py:2041,2054,2241,2284,2476,2493`; normal loop начинается на `:2159`. Внешний review точного старого HEAD обнаружил три пропущенных default-tier ASGI sibling, всё ещё зависевших от конечного pytest-stream. Коммит `5b100d58db2022e35ed859065f4555438c26bcff` перевёл `test_simulator_artifacts_events_ndjson.py:28-52`, `test_simulator_sse_real_smoke.py:27-74` и `test_simulator_sse_smoke.py:24-50` на настоящий runtime event + terminal replay. Точечный `wave1_t601_external_fix` — exit 0, `3 passed`; полный расширенный `wave1_t601_all_siblings` — exit 0, `19 passed`.

### 2026-08-11 — T607

- **До:** свежий anchor scan `rg -n -C 8 "requestAnimationFrame|cancelAnimationFrame|toast|errorToast|feature.?flag|vi\.useFakeTimers|setTimeout|nextTick" admin-ui/src/test/setup.ts admin-ui/src ...` — exit 0. Он подтвердил: `admin-ui/src/test/setup.ts:1-5` делал только `vi.restoreAllMocks()`, реальный toast вызывался через `admin-ui/src/api/errorToast.ts:25-27`, а malformed feature-flag contract не мокал `element-plus`. Один baseline `npm --prefix admin-ui run test` на неизменённом дереве завершился exit 0 (`30 files`, `200 passed`); исторический intermittent exit 1 в этом одиночном запуске не воспроизвёлся.
- **Изменение:** коммит `0b1d625cac0ebdd3092eef73ce123df5d1995185` сделал test setup владельцем детерминированной rAF-очереди и teardown (`admin-ui/src/test/setup.ts:3-33`), очищает DOM между cases (`:31`) и отдельно мокает Element Plus в контракте, который проверяет malformed config/feature flags (`admin-ui/src/api/adminConfigFeatureFlags.contract.test.ts:8`, reset на `:66`). Контрпроверка cancel/live semantics находится в `admin-ui/src/test/setup.test.ts:4`.
- **После:** точечный `npm --prefix admin-ui run test -- src/test/setup.test.ts src/api/adminConfigFeatureFlags.contract.test.ts` — exit 0, `11 passed`; полный `npm --prefix admin-ui run test` — exit 0, `31 files`, `201 passed`; затем 20 последовательных полных прогонов — каждый `RUN=01..20 EXIT=0`, каждый `201 passed`. `npm --prefix admin-ui run build` — exit 0 (fixture validation, `vue-tsc -b`, Vite build); `git diff --check` — exit 0.
- **Финальная серия на post-Ruff HEAD:** `$failed = 0; for ($i = 1; $i -le 20; $i++) { $runOutput = & npm --prefix admin-ui run test 2>&1; $runExit = $LASTEXITCODE; ...; if ($runExit -ne 0) { $failed = 1 } }; if ($failed -ne 0) { exit 1 }` — общий exit 0; `RUN=01..20 EXIT=0`, каждый прогон `201 passed`. Многоточие в ledger означает только сокращённое извлечение summary; выполняемая команда на каждом шаге была неизменно `npm --prefix admin-ui run test`.

### 2026-08-11 — T608a

- **До:** `requirements-dev.txt:6-7` по-прежнему фиксировал `black==24.1.1` и `ruff==0.1.14`; `.venv\Scripts\python.exe -m ruff --version` подтвердил `ruff 0.1.14`. Первый свежий замер после T601 дал exit 1, `25 errors / 20 fixable`: дополнительный `F401` был создан удалением синтетического SSE-кода. Cleanup-коммит `f37fe52` удалил ставший мёртвым `SimulatorTxUpdatedEvent` import; повторный замер восстановил записанный baseline — exit 1, `24 errors / 19 fixable`.
- **Механический срез:** единственная команда `.venv\Scripts\python.exe -m ruff check app migrations --fix --no-cache` завершилась exit 1 с точным `Found 24 errors (19 fixed, 5 remaining)`. Пять оставшихся случаев были исправлены вручную: SQLAlchemy predicate `AuthChallenge.used.is_(False)` (`app/core/auth/service.py:57`), удалены три неиспользуемых вычисления в `metrics_bottlenecks.py`, `snapshot_builder.py` и `viz_patch_helper.py`, переменная ссылки переименована в `link` (`snapshot_builder.py:383`).
- **CI:** коммит `acbcd25c3f15b8225d6aacc58efb3f9bc1d1bd4f` убрал `continue-on-error` только у Ruff (`.github/workflows/quality.yml:87-88`), тогда как Black явно остаётся неблокирующим (`:90-92`). Двусторонний policy guard — `tests/unit/test_static_diagnostics_policy.py:9`.
- **После:** pinned `.venv\Scripts\python.exe -m ruff check app migrations --no-cache` — exit 0; canonical `wave1_t608a` на policy/auth/quantiles/snapshot selectors — exit 0, `5 passed`; `.venv\Scripts\python.exe scripts\check_alembic_heads.py` — exit 0 (`017_add_owner_to_simulator_runs`); `git diff --check` — exit 0.
- **Adversarial remediation:** внутреннее read-only review обнаружило, что safe `--fix` также удалил два намеренных re-export/monkeypatch surface из `app/core/simulator/real_runner.py`, несмотря на защитный комментарий. Коммит `19dab80` вернул `simulator_storage` и `_RealPaymentAction` с локальными `# noqa: F401`; pinned Ruff остался чистым (exit 0), canonical `wave1_t608a_review_fix` на обоих потребителях и CI-policy — exit 0, `3 passed`. Таким образом, исторический вывод команды «19 fixed» сохранён, но итоговый diff намеренно восстанавливает два compatibility import.

### 2026-08-11 — закрытие среза Волны 1

- Полный verification plan сверён по всем восьми пунктам. В этом срезе закрыты пункты 2, 6 и 7:
  `rg -n "PYTEST_CURRENT_TEST" app` — exit `1`, ноль совпадений; полный расширенный SSE selector
  `wave1_t601_all_siblings` — exit `0`, `19 passed`; 20 последовательных полных Admin Vitest
  прогонов — общий exit `0`, каждый `201 passed`; pinned
  `.venv\Scripts\python.exe -m ruff check app migrations --no-cache` — exit `0`, а Ruff step в
  `.github/workflows/quality.yml:87-88` блокирующий. Пункты 1, 3-5 остаются явной работой
  T600/T602-T605 Волны 5, а не ложным зелёным результатом.
- Пункт 8 подтверждён без изменения политики:
  `.venv\Scripts\python.exe -m black --check app migrations` — exit `1`, `98 files would be
  reformatted, 43 files would be left unchanged`; Black остаётся `continue-on-error` в
  `.github/workflows/quality.yml:90-92` до T608b.
- Независимый внешний `Codex gpt-5.6-sol` review range
  `ea9cde9161c3f7444495ede13871c901abbba811..200a09b` вынес два P2: Ruff удалил два
  monkeypatch/re-export surface, а три default-tier ASGI SSE sibling зависели от удалённого
  pytest-only finite stream. Исправления доставлены коммитами `19dab80` и
  `5b100d58db2022e35ed859065f4555438c26bcff`; targeted gates дали соответственно `3 passed` и
  `3 passed`, расширенный sibling gate — `19 passed`. Повторный независимый review remediation
  delta `200a09b..c19cb5f1b108325c88f4420cd62ce20a313e61ac` завершился `VERDICT-CLEAN` без новых
  P1/P2. История первого провала сохранена; T611 остаётся открытой до финала всей программы 006.
- Canonical milestone на итоговой реализации:
  `$env:DEBUG='false'; .\scripts\verify_local.ps1 -TaskSlug wave1_final_full` — exit `0`; backend
  `945 passed, 3 skipped, 15 deselected`, Alembic head `017`, Admin lint/test/build и Simulator
  lint/typecheck/test/build прошли, Simulator unit — `729 passed`; финальная строка
  `Required local validation passed.`

### 2026-08-11 — T600

- Перед изменением finding сверена по актуальным anchors. **Current:** patch-применитель действительно
  не имеет собственного sequence guard (`simulator-ui/v2/src/demo/patches.ts:44-62`), но production
  transport выделяет id, добавляет buffer и dispatch'ит событие атомарно под одним `RLock`
  (`app/core/simulator/sse_broadcast.py:247-276`); replay snapshot, bootstrap status и регистрация
  подписчика устанавливаются под тем же lock (`:303-384`), live-tail замораживается в порядке
  producer (`:387-399`). Queue overflow закрывает подписку, а reconnect либо восстанавливает полный
  суффикс, либо fail-closed (`:134-170,175-190,199-239`). Runtime-вызовов compatibility
  `broadcast()`/`next_event_id()` в `app/` нет: production emitter выбирает `publish_event`
  (`:431-445`), run-status делает то же в `runtime_impl.py:495`.
- **Intended:** ранее не виденный меньший producer id не должен появляться после большего; отсутствие
  measurement нельзя было подменять UI-фильтром. **Optimal:** сохранить единый ordering-owner в
  backend и не добавлять протокольное поле/клиентский gate для невозможного canonical outcome.
  Датированное решение добавлено в `docs/ru/09-decisions-and-defaults.md` §1.11.1; product code и
  wire schema не менялись.
- Evidence: `rg -n "\.broadcast\(|next_event_id\(" app tests -g "*.py"` — exit `0`, совпадения в
  `app/` только внутри compatibility implementation, все прямые callers — tests; canonical
  `DEBUG=false; ENV=test; .\scripts\verify_local.ps1 -TaskSlug wave5_t600_replay_order
  -BackendOnly -BackendSelector tests/unit/test_simulator_sse_replay_atomic.py
  -Python .\.venv\Scripts\python.exe` — exit `0`, `13 passed`; `npm --prefix simulator-ui/v2 run
  test:unit -- src/composables/realEventPipeline.test.ts` — exit `0`, `13 passed`. Первый selector
  доказывает atomic producer ordering, replay/bootstrap/live-tail и overflow recovery; второй
  сохраняет event-id deduplication и monotonic reconnect cursor. До/после: patch schema и
  применитель неизменны; новый owner record — `docs/ru/09-decisions-and-defaults.md` §1.11.1.

### 2026-08-11 — T602

- Перед правкой finding повторно подтверждена по текущим anchors. **Current:** публичный
  `stop_after_types` оставался в двух OpenAPI operations (`api/openapi.yaml:1042-1050,1517-1525`),
  двух endpoint-сигнатурах и pass-through (`app/api/v1/simulator.py:2259-2305,2494-2514` до
  изменения), однако после T601 генератор только принимал значение и нигде его не использовал.
  **Intended:** история T601 фиксирует параметр как часть удалённого pytest-only finite-stream
  harness, а не как обещанное production-поведение. **Optimal:** удалить инертный параметр из
  wire-контракта и сигнатур вместо введения нового публичного механизма серверного закрытия SSE.
- После: `_run_events_stream` больше не принимает параметр (`app/api/v1/simulator.py:2062`), обе
  endpoint-сигнатуры содержат только рабочие `equivalent`/`Last-Event-ID` параметры
  (`:2251-2253`, `:2483-2486`), а OpenAPI переходит непосредственно от equivalent к
  `Last-Event-ID` (`api/openapi.yaml:1042-1048,1517-1523`).
  `rg -n "stop_after_types" app api docs tests simulator-ui/v2/src` — exit `1`, ноль совпадений.
- Canonical targeted gate:
  `$env:DEBUG='false'; $env:ENV='test'; $selectors=@('tests/contract/test_openapi_contract.py',
  'tests/unit/test_simulator_sse_replay_atomic.py','tests/integration/test_simulator_sse_smoke.py',
  'tests/integration/test_simulator_sse_real_smoke.py'); .\scripts\verify_local.ps1 -TaskSlug
  wave5_t602_remove_stop_after_final -BackendOnly -BackendSelector $selectors -Python
  .\.venv\Scripts\python.exe` — exit `0`, `38 passed`. Pinned Ruff `0.1.14` на
  `app/api/v1/simulator.py` — exit `0`; `git diff --check` на product/contract-файлах — exit `0`.
- Неудачная инфраструктурная попытка сохранена: запуск с внешним лимитом управляющего вызова был
  оборван через 3.6 s (`exit 124`), после чего pytest получил закрытый stdout (`OSError: [Errno 22]
  Invalid argument`). Это не product/test failure; та же canonical-команда выше завершилась штатно.
- Независимый read-only preflight на exact `27e0024` подтвердил `REMOVE`: runtime/callers/tests/UI
  отсутствуют, stable SSE docs обещают terminal event/client disconnect, а история `ee355dbe` и
  `e712bfd` связывает параметр только с удалённым pytest harness. Stop-level anchor drift и P1/P2
  не обнаружены.

### 2026-08-11 — T603

- Перед правкой anchor `tests/conftest.py:44` подтвердился: глобальный fixture по-прежнему выключает
  limiter для основного suite. При этом **Current** уточнился после Program005: прямые unit-тесты
  уже включали in-memory fallback и проверяли bound/429 (`tests/unit/test_rate_limit_memory_bound.py:
  18-74`), поэтому исходное «ни одним тестом» стало частично историческим; HTTP envelope, переход
  окна и Redis path всё ещё не измерялись. **Intended:** основной suite остаётся изолированным от
  best-effort throttling, но отдельный набор явно включает production dependency. **Optimal:** два
  узких HTTP-теста без изменения runtime/config defaults.
- Новый `tests/integration/test_http_rate_limit.py:26-91` локально включает limiter. Первый тест
  оставляет `REDIS_ENABLED=True`, но убирает client, тем самым проходит реальный in-memory fallback:
  два `200`, затем `429/E009` с точными `window_seconds`/`limit`, после перехода bucket снова `200`
  (`:42-67`). Второй ставит минимальный async Redis double и доказывает production Redis branch:
  `INCR`, единственный `EXPIRE` на `window+1`, затем HTTP 429; in-memory counters остаются пустыми
  (`:70-91`).
- Canonical targeted gate:
  `$env:DEBUG='false'; $env:ENV='test'; $selectors=@('tests/integration/test_http_rate_limit.py',
  'tests/unit/test_rate_limit_memory_bound.py'); .\scripts\verify_local.ps1 -TaskSlug
  wave5_t603_rate_limit -BackendOnly -BackendSelector $selectors -Python
  .\.venv\Scripts\python.exe` — exit `0`, `5 passed`. Pinned Ruff `0.1.14` на новом файле — exit
  `0`; `git diff --check` — exit `0`.

### 2026-08-11 — T604

- Перед правкой finding подтверждена на текущем дереве. **Current:** `pytest.ini:17` регистрировал
  backend-marker `e2e`, `rg`/AST scan не нашли ни одного носителя, а debug-only
  `pytest --collect-only -q -m e2e` собирал `0/1030` и завершался exit `5`; canonical default
  всё равно исключал фиктивный tier через `scripts/verify_local.ps1:127`. **Intended:** backend
  expensive tier принадлежит реальному marker `slow`, PostgreSQL — `postgres`, Playwright E2E
  запускается отдельными package jobs. **Optimal:** удалить пустую регистрацию и `not e2e`, не
  маркировать in-process super-smoke выдуманным external-service контрактом.
- После: `pytest.ini:14-17` регистрирует `scenario`, `slow`, `postgres`; canonical default стал
  `not slow and not postgres` (`scripts/verify_local.ps1:127`). Operational descriptions приведены
  к той же семантике в `README.md:314`, `docs/ru/10-testing-framework.md:14` и AGENTS.md marker/full
  gate sections. Policy guard `tests/unit/test_backend_marker_policy.py:7-29` AST-проверкой не даёт
  вернуть пустой `pytest.mark.e2e` tier или фиктивный default exclusion.
- Canonical targeted gate:
  `$env:DEBUG='false'; $env:ENV='test'; $selectors=@('tests/unit/test_backend_marker_policy.py',
  'tests/unit/test_postgres_test_taxonomy.py'); .\scripts\verify_local.ps1 -TaskSlug
  wave5_t604_marker_policy_fix -BackendOnly -BackendSelector $selectors -Python
  .\.venv\Scripts\python.exe` — exit `0`, `4 passed`. Debug-only full collect с новым выражением —
  exit `0`, `987/1031 collected`, `44 deselected`; `pytest --markers` — exit `0`, e2e registrations
  `0`. Pinned Ruff `0.1.14` и `git diff --check` на срезе — exit `0`.
- Неудачная попытка policy guard сохранена: первый canonical `wave5_t604_marker_policy` завершился
  exit `1` (`1 failed, 3 passed`), потому что строковый scan счёл собственный literal
  `"pytest.mark.e2e"` носителем. Guard исправлен на AST-role check; production/tooling target не
  менялся между попытками.

**2026-08-12 / T611 correction:** внешнее ревью обнаружило второй пустой marker `scenario`,
оставленный зарегистрированным без единого носителя. Он удалён из `pytest.ini`; policy guard теперь
AST-проверяет отсутствие `pytest.mark.scenario`, а активные EN/PL testing guides больше не рекомендуют
несуществующий marker. Canonical `wave5_extrem_verification_policy` — exit `0`, `26 passed` вместе с
Ruff-policy и OpenAPI contract selectors; pinned Ruff и diff-check — exit `0`.

### 2026-08-11 — T605

- Перед правкой finding воспроизведена на текущем коде. **Current:** прямой
  `$env:TEST_DATABASE_URL='sqlite+aiosqlite:///./.local-run/test-runs/wave5_t605_red/test.db';
  .\.venv\Scripts\python.exe -m pytest -q -m postgres
  tests/integration/test_payment_engine_uow_retry_postgres.py` завершался exit `0`, `2 skipped`;
  независимый полный collect/run видел `40 skipped, 991 deselected`. Canonical runner уже
  fail-closed через `--require-backend postgresql` (`scripts/verify_local.ps1:105-111`).
  **Intended:** выбранный postgres-marked test никогда не считается зелёным на другом backend.
  **Optimal:** collection hook после builtin marker deselection, а не разбор literal `-m`.
- `tests/conftest.py:49-63` добавляет `pytest_collection_finish`: только если среди окончательно
  собранных после deselection items есть marker `postgres`, non-PostgreSQL URL даёт `pytest.UsageError` с
  инструкцией про dedicated `geov0_test_*` и reset opt-in. Поэтому canonical default
  `not slow and not postgres` не false-fail, а прямой selector postgres-файла без `-m` тоже защищён.
  Subprocess guard `tests/unit/test_postgres_marker_fail_closed.py:41-68` доказывает обе стороны:
  SQLite → exit `4`/точное сообщение; безопасно именованный PostgreSQL URL → collection exit `0`,
  `2 tests collected`, без сетевого подключения.
- После: тот же прямой SQLite selector завершился exit `4`, `no tests ran`, с
  `ERROR: PostgreSQL-marked tests were selected, but TEST_DATABASE_URL does not use PostgreSQL`.
  Canonical non-PG gate `wave5_t605_fail_closed` на новом guard, taxonomy и DB-safety tests — exit
  `0`, `24 passed, 1 skipped`. Pinned Ruff `0.1.14` на двух изменённых Python-файлах и
  `git diff --check` — exit `0`.
- Реальный anti-vacuum milestone: заранее проверена отсутствующая disposable DB
  `geov0_test_wave5_t605_663bd2f`; canonical
  `.\scripts\verify_local.ps1 -TaskSlug wave5_t605_postgres -BackendOnly -BackendMarker postgres
  -BackendSelector tests/integration/test_payment_engine_uow_retry_postgres.py -Python
  .\.venv\Scripts\python.exe` — exit `0`, `2 passed`. Перед удалением active connections `0`,
  после `DROP DATABASE` запись отсутствует. README, `docs/ru/10-testing-framework.md` и текущий
  AGENTS marker contract обновлены: direct wrong-backend run больше не описывается как допустимый
  skip/debug result.
- Независимая проверка hook timing на pytest `7.4.4` подтвердила, что `trylast=True` видит один
  выбранный postgres item при `-m postgres` и ноль при `-m 'not postgres'`; результаты — exit `4`
  и exit `0` соответственно. По review subprocess guard дополнительно изолирован от унаследованного
  `GEO_TEST_USE_MIGRATED_SCHEMA`, а SQLite probe переведён на `:memory:`; это исключает ложный ранний
  отказ до проверяемого hook. Повторный canonical `wave5_t605_fail_closed_isolated` — exit `0`,
  `24 passed, 1 skipped`.
- Финальное упрощение по review: guard перенесён из order-зависимого
  `pytest_collection_modifyitems(..., trylast=True)` в `pytest_collection_finish(session)`, который
  получает уже окончательный `session.items`. Повторный canonical
  `wave5_t605_collection_finish` — exit `0`, `24 passed, 1 skipped`; прямой SQLite postgres selector
  сохранил exit `4`, а safe PostgreSQL collect — exit `0`.

### 2026-08-11 — T606

- Перед правкой finding и anchors подтверждены. **Current:** workflow уже имел weekly trigger
  `cron: "17 3 * * 1"` (`.github/workflows/quality.yml:9-10`), но `container-smoke` назывался
  manual и имел единственное условие `github.event_name == 'workflow_dispatch'` (`:228-230` до
  изменения). **Intended:** тот же production-like image/schema smoke выполняется на существующем
  weekly cadence и остаётся доступен вручную. **Optimal:** переиспользовать один cron, не добавлять
  PR-cost и второй scheduler.
- После: job называется `Production-like container and schema smoke (scheduled/manual)` и допускает
  ровно `schedule || workflow_dispatch` (`.github/workflows/quality.yml:228-230`). Permissions
  остаются `contents: read`; job генерирует/маскирует ephemeral secrets, использует локальный image и
  task-local containers/network, cleanup остаётся под `always()`. README weekly-job inventory
  синхронизирован (`README.md:341-344`), текущая AGENTS CI table больше не называет job manual-only.
- Policy guard `tests/unit/test_quality_workflow_schedule.py:9-30` проверяет не только job-level
  условие, но и anti-vacuum: top-level `on.schedule` существует как непустой список с непустым cron.
  Первая версия guard без проверки top-level trigger прошла (`wave5_t606_schedule`, exit `0`,
  `5 passed`), но могла false-green при удалённом cron; история сохранена. После remediation
  canonical `wave5_t606_schedule_final` на schedule/static/Postgres workflow policies — exit `0`,
  `5 passed`; независимый повтор policy-selector — exit `0`, `1 passed`. Pinned Ruff `0.1.14` и
  `git diff --check` — exit `0`.
- Ограничение evidence: конфигурация и route policy доказаны локально; фактическое плановое
  выполнение может подтвердить только будущий GitHub `schedule` run на default branch. Это не
  подменяется manual dispatch и не заявляется как уже состоявшийся scheduled run.

### 2026-08-11 — T608b

- Перед решением проверены pinned owner surfaces: `requirements-dev.txt:6` фиксирует
  `black==24.1.1`, CI `Black diagnostics` остаётся `continue-on-error: true`
  (`.github/workflows/quality.yml:90-92`), а policy guard отдельно требует blocking Ruff и
  non-blocking Black (`tests/unit/test_static_diagnostics_policy.py:9-27`). `git status --short --
  app migrations` — exit `0`, пустой вывод: замер сделан на чистой product-области, несмотря на
  несвязанные пользовательские docs changes в общем worktree.
- Свежий pinned `.\.venv\Scripts\python.exe -m black --check app migrations` — exit `1`, точный
  итог `99 files would be reformatted, 42 files would be left unchanged`. Дополнительный
  `.\.venv\Scripts\python.exe -m black --diff app migrations` — exit `0`; 11529 raw output lines,
  4991 строк с `+`/`-`, включая 198 file headers, то есть 4793 содержательные diff-строки. Это
  актуализирует исторический baseline `98/4931`, не переписывая его.
- **Current:** Black красный, но честно диагностический; Ruff остаётся blocking. **Intended:** не
  смешивать массовое форматирование с функциональными волнами. **Optimal:** сохранить
  non-blocking policy и потребовать отдельное датированное решение/атомарный formatting slice для
  будущего перехода. Решение записано в `docs/ru/09-decisions-and-defaults.md:365-382`; product code,
  formatter version и CI step не менялись.
- Canonical policy gate `.\scripts\verify_local.ps1 -TaskSlug wave5_t608b_black_policy
  -BackendOnly -BackendSelector tests/unit/test_static_diagnostics_policy.py -Python
  .\.venv\Scripts\python.exe` — exit `0`, `1 passed`; `git diff --check` на decision/spec — exit `0`.

### 2026-08-11 — T609

- Finding воспроизведена: с `DATABASE_URL=sqlite+aiosqlite:///:memory:` прямой
  `.\.venv\Scripts\python.exe -m alembic -c migrations/alembic.ini upgrade head` завершался exit
  `1`, exact error `sqlite3.OperationalError: near "EXTENSION": syntax error`, SQL
  `CREATE EXTENSION IF NOT EXISTS pgcrypto` (`migrations/versions/001_initial_schema.py:25`).
- Первоначальная literal-попытка поставить dialect guard в уже применённую 001 прошла локальный
  unit guard (`wave5_t609_pgcrypto_guard`, exit `0`, `4 passed`), но была **не закоммичена и
  отозвана** после независимого review. Причины: applied revisions защищены AGENTS.md, а следующий
  реальный SQLite blocker — не записанный в спеке `postgresql.UUID` (`001:29`), поэтому guard был
  бы vacuous. Рабочее изменение 001 и его тест удалены; migration history остаётся неизменной.
- Исправленный inventory: в 001 есть 19 ссылок `postgresql.UUID`, 11 `postgresql.JSONB`, 9 defaults
  `gen_random_uuid()`, defaults `NOW()`, семь literal `ALTER TABLE ... ADD CONSTRAINT` и три
  `op.create_unique_constraint`; 002/003/004/010 также содержат незащищённые PostgreSQL-only
  операции. Изолированная SQLite-проверка: UUID/JSONB не компилируются; DDL с
  `DEFAULT (gen_random_uuid())` принимается, но INSERT даёт `unknown function`; raw ADD CONSTRAINT
  даёт syntax error. Таким образом, исходное «001 — единственная» и перечисление следующих
  blockers были неполными.
- **Current:** Alembic chain фактически PostgreSQL-only; SQLite создаётся поддержанным
  `scripts/init_sqlite_db.py`/`Base.metadata.create_all`. **Intended:** applied revisions неизменны,
  production migrations работают на PostgreSQL. **Optimal:** fail-closed до исполнения любого
  revision вместо имитации частичной SQLite-совместимости. `migrations/env.py:26-32,34-47,66-74`
  теперь проверяет URL и для online, и для offline/`--sql`; другой backend получает credential-safe
  `Alembic migrations support PostgreSQL only` с указанием SQLite initializer.
- Black-box anti-vacuum `tests/unit/test_alembic_postgres_only.py:29-75` доказывает: SQLite online и
  offline завершаются до `Running upgrade`/revision SQL, а `scripts/init_sqlite_db.py` создаёт
  representative SQLite tables. Canonical `wave5_t609_postgres_only` — exit `0`, `5 passed`;
  single Alembic head check — exit `0`, head `017_add_owner_to_simulator_runs`.
- PostgreSQL milestone на заранее отсутствующей disposable DB с каноническим entrypoint-preflight
  (`alembic_version VARCHAR(128)`) завершился exit `0`: revision
  `017_add_owner_to_simulator_runs`, `PGCRYPTO=True`, три representative constraints найдены;
  active connections перед удалением `0`, DB после drop отсутствует. Более ранняя прямая попытка
  без обязательного preflight честно сохранена: exit `1`,
  `StringDataRightTruncationError ... character varying(32)` на длинном revision 011; её DB также
  удалена при `0` connections. Pinned Ruff `0.1.14` и `git diff --check` на env/test/spec — exit `0`.

### 2026-08-11 — T610

- Перед правкой анкоры сверены заново. **Current:** шесть category ratchets в
  `tests/contract/test_openapi_contract.py:29-55,290-305` уже были двусторонними
  count+digest храповиками, но имя первого широкого теста обещало `match_app`.
  Свежий read-only пересчёт дал `operations=88 clean=2 dirty=86`; чисты только
  `GET /health` и `GET /admin/health/db`. Тем самым исходные 1/87 устарели после
  T501, но суть F-006-8 подтверждена.
- **Intended / Optimal:** gate честно называет то, что доказывает. Выбран узкий
  rename: module docstring теперь говорит `Selected exact contracts and drift ratchets`, а test node id явно
  разделяет exact paths/methods/identities/requiredness и ratcheted schemas
  (`tests/contract/test_openapi_contract.py:1,995`). Сами baselines, OpenAPI и product code не
  менялись. Датированное решение и правило постепенного сокращения долга
  записаны в `docs/ru/09-decisions-and-defaults.md:383-403`; активная REN-009 completion
  phrase уточнена append-only в `specs/001-codebase-renovation/tasks.md:586-591` без изменения
  её датированной истории.
- Первая verification-попытка передала pytest и file selector, и node selector одновременно;
  pytest-asyncio рекурсивно собрал один модуль дважды: `wave5_t610_after` — exit `1`,
  `RecursionError` на collection. Это harness error, не product failure; история не скрыта.
- После разделения selectors: `wave5_t610_node` — exit `0`, `1 passed` по точному
  новому node id; `wave5_t610_full` — exit `0`, `23 passed`. Pinned Ruff `0.1.14` на
  contract test — exit `0`; `git diff --check` на test/decision/spec — exit `0`. После финального
  wording correction канонический `wave5_t610_final` повторён: exit `0`, `23 passed`;
  pinned Ruff и diff-check повторно exit `0`.

**2026-08-12 / T611 correction:** фраза `identities/requiredness` выше была шире фактического
гейта. Exact проверяются business parameter identities и request-body presence/requiredness;
auth transport header identity/requiredness входят в count+digest ratchet. Node id уточнён до
`business_parameter_identities`, а датированное решение синхронизировано без изменения baselines.
Canonical contract selector в `wave5_extrem_verification_policy` — exit `0`; полный contract file
собрал `23 passed` в предшествующем финальном verification.
