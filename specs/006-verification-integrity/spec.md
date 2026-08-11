# 006 — Verification integrity

- **Date:** 2026-08-11
- **Status:** IN PROGRESS — только T601, T607 и T608a авторизованы 2026-08-11
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
| **F-006-8 (B1/F1)** | P3 | «OpenAPI semantic parity» реализована как ratchet по зафиксированному дрейфу; независимый пересчёт: полностью чист от дрейфа только `GET /health` из 87 операций | `tests/contract/test_openapi_contract.py:29-53` |
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
| T600 | **VERIFY FIRST.** Доказать на `sse_broadcast.py`, может ли бэкенд доставить не виденное ранее более старое patch-событие. По итогу — либо гейт применения patch по producer sequence (нужно протокольное поле seq/version/ts в `NodePatch`/`EdgePatch`), либо датированное решение о приемлемости устаревших абсолютных patch | `[!]` не авторизовано |
| T601 | Убрать ветвление по `PYTEST_CURRENT_TEST` из production SSE (F-006-2, P2) | `[x]` |
| T602 | Решить судьбу `stop_after_types`: сделать работающим вне pytest или убрать из контракта и сигнатур (F-006-2b, P3) | `[!]` |
| T603 | Включить rate limiter в тестах; покрыть 429/окно/fallback | `[!]` |
| T604 | Привести маркеры `pytest.ini` в соответствие с носителями | `[!]` |
| T605 | Сделать `pytest -m postgres` fail-closed вне канонического раннера | `[!]` |
| T606 | Добавить `container-smoke` в регулярное расписание | `[!]` |
| T607 | Устранить rAF/тост-флак в admin vitest setup | `[x]` |
| T608a | Ruff на пиннутых версиях: `--fix` (19 находок), затем 5 ручных случаев (3× `F841` и 1× `E712` под `--unsafe-fixes`, 1× `E741` вручную); перевод ruff-джоба в блокирующий. Ограниченный объём: одна команда + 5 правок | `[!]` |
| T608b | Black **остаётся неблокирующей диагностикой**: переформатирование 98 файлов / 4931 `+`/`-`-строк уничтожит `git blame`. Перевод в блокирующий — только после отдельного датированного решения | `[!]` |
| T609 | Диалектный guard вокруг `CREATE EXTENSION pgcrypto` в миграции 001 по образцу миграций 004-017. **Не однострочник:** отдельно оценить `postgresql.JSONB`, `gen_random_uuid()` и 7× `ALTER TABLE … ADD CONSTRAINT`, которые SQLite тоже не примет. Приоритет низкий: alembic не используется для создания dev-SQLite | `[!]` |
| T610 | Решение по OpenAPI-ratchet: план сокращения дрейфа либо честное переименование гейта | `[!]` |
| T611 | Независимое внешнее ревью и evidence на точном HEAD (триггер AGENTS.md §15: программа меняет сам механизм проверки, поэтому самопроверка гейта не является доказательством) | `[!]` |

## Implementation evidence

### 2026-08-11 — T601

- **До:** свежая проверка `rg -n -C 5 "PYTEST_CURRENT_TEST|is_pytest|stop_after_types|synthetic|tx\.updated" app/api/v1/simulator.py tests` завершилась с exit code 0 и подтвердила все анкоры F-006-2: `app/api/v1/simulator.py:2067` читал `PYTEST_CURRENT_TEST`, ветка начиналась на `:2170`, отбрасывала `run_status` на `:2195-2196`, фабриковала `tx.updated` на `:2215-2218` и останавливала runtime на `:2293-2297`.
- **Изменение:** коммит `e712bfd90f91135842c85c86496349e0317b6255` удалил pytest-зависимую ветку целиком. Обычный production loop теперь следует сразу после bootstrap tail (`app/api/v1/simulator.py:2160`). Публичный `stop_after_types` намеренно оставлен в OpenAPI и сигнатурах (`api/openapi.yaml:1046,1526`; `app/api/v1/simulator.py:2042,2055,2242,2285,2477,2494`) без нового runtime-поведения: решение его судьбы остаётся неавторизованным T602.
- **Тестовый transport:** зависимые ASGI-тесты закрывают поток штатным terminal `run_status` и читают настоящие события через replay; координаторы явно помечены как test/debug path (`tests/integration/test_simulator_sse_fixtures_clearing_animation_pair.py:33`, `test_simulator_sse_trust_drift_decay_topology_patch.py:30-46`, `test_simulator_sse_tx_failed_timeout.py:65`, `test_simulator_super_smoke.py:109`). Атомарный unit-тест завершается настоящим terminal event (`tests/unit/test_simulator_sse_replay_atomic.py:177`).
- **Неудачные попытки сохранены:** `wave1_t601` — exit 1, `2 failed, 14 passed`: trust-drift получил только `{'run_status': 1}`, timeout не увидел `tx.failed`; `wave1_t601_fix` — exit 1, `1 failed, 15 passed`; `wave1_t601_timeout_debug` — exit 1 с точным `actual=['run_status', 'run_status', 'run_status']`. Причина была в harness: replay не запрашивался либо ASGI подписывался до post-commit события; production-код для этих исправлений не менялся.
- **После:** `rg -n "PYTEST_CURRENT_TEST" app` — exit 1, то есть ноль совпадений. `wave1_t601_timeout_fix` — exit 0, `1 passed`; canonical cheap gate `wave1_t601_green` на пяти затронутых файлах — exit 0, `16 passed`; expensive milestone `wave1_t601_super_smoke` с `-IncludeExpensive` — exit 0, `3 passed`. `git diff --check` на шести изменённых файлах — exit 0.

### 2026-08-11 — T607

- **До:** свежий anchor scan `rg -n -C 8 "requestAnimationFrame|cancelAnimationFrame|toast|errorToast|feature.?flag|vi\.useFakeTimers|setTimeout|nextTick" admin-ui/src/test/setup.ts admin-ui/src ...` — exit 0. Он подтвердил: `admin-ui/src/test/setup.ts:1-5` делал только `vi.restoreAllMocks()`, реальный toast вызывался через `admin-ui/src/api/errorToast.ts:25-27`, а malformed feature-flag contract не мокал `element-plus`. Один baseline `npm --prefix admin-ui run test` на неизменённом дереве завершился exit 0 (`30 files`, `200 passed`); исторический intermittent exit 1 в этом одиночном запуске не воспроизвёлся.
- **Изменение:** коммит `0b1d625cac0ebdd3092eef73ce123df5d1995185` сделал test setup владельцем детерминированной rAF-очереди и teardown (`admin-ui/src/test/setup.ts:3-33`), очищает DOM между cases (`:31`) и отдельно мокает Element Plus в контракте, который проверяет malformed config/feature flags (`admin-ui/src/api/adminConfigFeatureFlags.contract.test.ts:8`, reset на `:66`). Контрпроверка cancel/live semantics находится в `admin-ui/src/test/setup.test.ts:4`.
- **После:** точечный `npm --prefix admin-ui run test -- src/test/setup.test.ts src/api/adminConfigFeatureFlags.contract.test.ts` — exit 0, `11 passed`; полный `npm --prefix admin-ui run test` — exit 0, `31 files`, `201 passed`; затем 20 последовательных полных прогонов — каждый `RUN=01..20 EXIT=0`, каждый `201 passed`. `npm --prefix admin-ui run build` — exit 0 (fixture validation, `vue-tsc -b`, Vite build); `git diff --check` — exit 0.
