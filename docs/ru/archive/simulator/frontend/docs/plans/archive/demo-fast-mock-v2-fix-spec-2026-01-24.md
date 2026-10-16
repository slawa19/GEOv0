# Demo Fast Mock v2 — Code Review + Spec for Fixes (2026-01-24)

Контекст: изменения в визуале/анимации очень чувствительны. Цель этой спеки — **исправлять ошибки и риски**, не меняя «утверждённый» вид эффектов и клиринга без отдельного решения.

Документы/код, на которых основано ревью:
- Спека: `docs/ru/simulator/frontend/docs/specs/GEO-visual-demo-fast-mock.md`
- UI: `simulator-ui/v2/src/App.vue`
- FX: `simulator-ui/v2/src/render/fxRenderer.ts`
- Base graph: `simulator-ui/v2/src/render/baseGraph.ts`
- Fixtures validation: `simulator-ui/v2/src/fixtures.ts`, `simulator-ui/v2/src/types.ts`
- Генератор: `admin-fixtures/tools/generate_simulator_demo_snapshots.py`
- E2E: `simulator-ui/v2/e2e/scenes.spec.ts`, `simulator-ui/v2/playwright.config.ts`
- Плейлисты: `simulator-ui/v2/public/simulator-fixtures/v1/UAH/events/demo-tx.json`, `demo-clearing.json`

---

## 1) Что осталось по TODO (demo-fast-mock v2)

## Статус на 2026-01-25 (что уже сделано)

Сделано (по коду/фикстурам/тестам):
- Fail-fast: валидация edge-ов плейлистов относительно `snapshot.links` (Scene D/E).
- Таймеры: централизованная отмена `setTimeout` при `loadScene()` и перед новыми прогонами Tx/Clearing.
- Clearing contract: UI проигрывает и `highlight_edges` (pulse), и `particles_edges` (beam sparks).
- Рельсовые плейлисты: `demo-tx.json` расширен (включая multi-hop), `demo-clearing.json` расширен до нескольких шагов.
- Floating labels: добавлен мягкий лимит, чтобы DOM не раздувался при спаме.
- DemoControls MVP (Scene D/E): `Step`, `Play/Pause`, `Reset`, `Labels LOD`, `Quality (dprClamp)`.
- Player: кэширование демо-событий на `loadScene()` (не перезагружаем fixtures на каждый клик).
- README/UX мелочи: приведены в соответствие пути и allow-list layoutMode (см. PR-7 ниже).

Дополнительно сделано (refactor-only, без изменений визуала/таймингов):
- Декомпозиция App.vue на DI-composables (PR-9…PR-14) + дополнительные модули 7.7 (Scene State / Edge Tooltip / Node Card / Render Loop).
- Устранена часть дублирования утилит в render-слое (rounded-rect path вынесен в общий helper).

Дополнительно сделано (2026-01-25):
- `?eq=` deep-link (allow-list; в test-mode игнорируется).
- Scene B e2e реально показывает focus через `?focus=`.
- `viz_width_key`/`viz_alpha_key` для snapshot.links заполнены генератором (отдельный visual-change; golden обновлены).
- Drag + pin/unpin реализованы (выключено в test-mode; pin-кнопки скрыты в WebDriver).

Сверка со спекой (разделы 6.1, 6.4, Scene D/E):

### TODO-A: Панель демо-контролов «как продукт» (спека 6.1)
Реализовано:
- Play/Pause + Step для сцен D/E (плейлист событий и шагов).
- Reset (сброс selection + overlays + остановка проигрывания).
- Labels LOD: `off / selection / neighbors`.
- Quality: `low/med/high` → dprClamp (1 / 1.5 / 2), в test-mode DPR фиксируется.

Не реализовано (следующий шаг):
- См. раздел ниже «Что осталось (по важности)».

### TODO-B: «Рельсовые» плейлисты событий под утверждение визуала
- Scene D (Tx burst): в спеке требуется 10–30 событий + обязательно multi-hop.
  - Сейчас `demo-tx.json` содержит серию событий и включает multi-hop.
- Scene E (Clearing plan): требуется 3–8 шагов клиринга, где **каждый шаг — отдельный клиринговый цикл долгов**.
  - По протоколу циклы клиринга — это циклы долгов длиной **3–6** участников (чаще 3–4).
  - Для демо-читабельности: в плейлисте **не повторяем одни и те же направленные рёбра `from→to`** между шагами.
  - Сейчас `demo-clearing.json` содержит несколько шагов (несколько циклов).

Что именно не так с текущими плейлистами (почему это риск “испортить анимацию” при следующих правках):
- Они не “нагружают” FX: один single-edge tx и один clearing-step не показывают поведение частиц/оверлеев на серии событий.
- Не проверяют важные случаи из спеки: multi-hop Tx и цикл 4–6 узлов для clearing.
- Любая правка FX/таймингов на таких плейлистах легко “кажется ок”, но потом разваливается на реальных сценариях.

Как расширять плейлисты безопасно (без изменения внешнего вида анимации):
- Меняем **только данные** (events JSON и/или генератор), не трогаем `fxRenderer.ts` и не вводим новые “магические” тайминги в UI.
- Используем канонические маршруты из datasets:
  - `transactions.json` для Tx;
  - `clearing-cycles.json` (циклы **долгов**) для clearing.
  Важно: `clearing-cycles` хранит ребро долга как `debtor→creditor`, а в демо-графе направление trustline — `creditor→debtor`.
  Поэтому для визуализации клиринга подсвечиваем/анимируем **инвертированное ребро** `creditor→debtor`.
- Не усложняем эффект: лучше больше событий с тем же эффектом, чем новый эффект.
- Для всех ребер гарантируем: edge существует в snapshot (fail-fast).

### TODO-C: Устойчивые визуальные тесты (детерминизм)
E2E у вас уже есть (Scene A–E screenshot). Данные для Scene D/E расширены до серии событий/шагов (это повышает полезность регрессий).

## Что осталось (по важности)

На 2026-01-25 все пункты из списка «по важности» закрыты, кроме архитектурных предложений (см. п.5 ниже).

P2 — **опционально / архитектура на будущее**:
1) Дальнейшая декомпозиция (раздел 4: eventPlayer/txPlayer/clearingPlayer/graphRenderer/DemoControls.vue)
  - Почему важно: снижает риск визуального дрейфа при будущих изменениях.
  - Почему можно позже: текущая DI-композиция уже даёт основной выигрыш; углубление имеет смысл при новых фичах/изменениях поведения.

### TODO-D (опционально): Динамика узлов — перетаскивание (drag) + pin/unpin (без Shift)

Статус: **реализовано** (2026-01-25).

Поведение:
- `pointerdown` по узлу → select + кандидат на drag
- если курсор сдвинулся > порога → drag, узел становится pinned (позиция фиксируется)
- `Pin/Unpin` доступны в node-card (скрыты в WebDriver; drag выключен в `VITE_TEST_MODE=1`)

Зачем может понадобиться:
- интерактивная «песочница» для исследования читаемости связей/кластеров без переключения layout режима;
- точечная проверка UX вокруг фокуса/лейблов.

UX-заметка:
- Drag реализован как «UX поверх раскладки»: мы двигаем только runtime позиции (`__x/__y`), а pin сохраняется поверх последней раскладки.

Когда делать безопаснее всего:
- после внедрения камеры (PR-5), потому что drag должен работать в координатах world↔screen (иначе будет ломать pan/zoom).

Guardrails (чтобы не ломать детерминизм/визуальные тесты):
- В `VITE_TEST_MODE=1` drag должен быть отключён.
- Drag должен быть «UX поверх раскладки»: мы двигаем только runtime позиции (не fixtures), плюс вводим pin/unpin.
- Нужна команда сброса (чтобы не копить “ручные” состояния):
  - либо отдельная `Reset layout` (снимает pin и пересчитывает layout)
  - либо “Reset view” расширяется до camera+pins.

Приемка:
- Узел перетаскивается, рёбра/FX следуют.
- Pin сохраняется до явного unpin или смены сцены.
- Скриншотные тесты не меняются, т.к. drag выключен в `VITE_TEST_MODE=1`.

---

## 2) Код-ревью: замечания/риски (с приоритетами)

### P0 (может ломать визуал/демо при регене фикстур)

#### P0.1 Несовпадение генератора и рантайма по clearing (highlight_edges vs particles_edges)
Статус: **реализовано** (см. “Статус на 2026-01-25”).

Контракт выровнен:
- Генератор пишет в steps и `highlight_edges`, и `particles_edges`.
- UI проигрывает оба массива (подсветка + частицы), а в test-mode фиксируется детерминированный шаг для e2e.

#### P0.3 Нет fail-fast проверки «ребро из event существует в snapshot»
Статус: **реализовано**.

В `loadScene()` добавлена fail-fast проверка: любые рёбра из плейлистов должны существовать в `snapshot.links`.

#### P0.2 Таймеры в runClearingOnce()/runTxOnce() не отменяются при смене сцены/повторном запуске
Статус: **реализовано**.

Таймеры централизованы через реестр, и чистятся при `loadScene()` и перед новыми прогонами Tx/Clearing.

### P1 (важно для утверждения визуала и будущих изменений маппинга)

#### P1.1 В base graph есть «семантические» оверрайды стилей рёбер на focus/active
Статус: **реализовано** (semantic base pass + overlay pass).

Факт:
- `simulator-ui/v2/src/render/baseGraph.ts` рисует рёбра в 2 прохода:
  1) базовый — строго по `viz_*`
  2) overlay — усиление для focus/active (без перезаписи базового слоя)

Почему это риск:
- Визуал по спецификации должен следовать `viz_*` и таблице маппинга.
- Сейчас часть поведения “прошита” и не выражена как явный overlay-слой.
- При смене `VIZ_MAPPING` можно случайно сломать уже “утверждённую” картинку, потому что фактически применяется другой набор правил.

Рекомендация (минимальный риск):
- Разделить “базовый стиль” и “UX overlay” явно:
  1) Базовый проход рисует рёбра **строго по viz_*** (alpha/width/color).
  2) Второй проход поверх — “focus/active overlay”, который:
     - **мультипликативно** усиливает alpha (например `alpha = clamp(alpha * k, 0, 1)`)
     - **не уменьшает** ширину ниже базовой (только `max(baseWidth, overlayWidth)`)
     - цвет задаётся как отдельная политика overlay (например cyan) — но это явно «UX слой», не семантика.

Критерии приемки:
- При изменении `VIZ_MAPPING.link.alpha/width_px` базовый рисунок меняется предсказуемо, а overlay только усиливает, не “переписывает”.

#### P1.2 `viz_width_key`/`viz_alpha_key` для рёбер snapshot (visual-change)
Статус: **реализовано** (2026-01-25).

Что сделано:
- Генератор `admin-fixtures/tools/generate_simulator_demo_snapshots.py` теперь заполняет `viz_width_key`/`viz_alpha_key` для `snapshot.links` детерминированно.
- Golden-скриншоты сцен A–E переутверждены через `npm run test:e2e:update`.

Зачем:
- Семантика толщины/альфы теперь управляется данными (не хардкодом в UI).

#### P1.3 Scene B (Focus) в e2e должен реально демонстрировать фокус
Статус: **реализовано** (2026-01-25).

Что сделано:
- Добавлен safe deep-link `?focus=<nodeId>` (фокус применяется только если nodeId существует в snapshot).
- E2E для Scene B теперь запускается с `?focus=...` и снимает настоящий focus-кадр.

### P2 (качество продукта/UX и поддерживаемость)

#### P2.1 README в simulator-ui/v2 содержит неверный текст (legacy и cd)
Факт:
- `simulator-ui/v2/README.md` говорит: legacy был заархивирован в `simulator-ui/v2/` (это само-ссылка).
- Инструкция “Or manually” предлагает `cd simulator-ui`, но dev-сервер/пакеты v2 живут в `simulator-ui/v2`.

Рекомендация:
- Поправить на `simulator-ui/v1` и `cd simulator-ui/v2`.

#### P2.2 Восстановление layoutMode из localStorage не поддерживает все режимы
Факт:
- В `simulator-ui/v2/src/App.vue` localStorage restore допускает только `admin-force | community-clusters | balance-split`.
- В UI реально есть ещё `type-split` и `status-split`.

Рекомендация:
- Расширить allow-list.

#### P2.3 Дублирование утилит `withAlpha()` / `roundedRectPath()` в render-слое
Факт:
- `withAlpha()` определён отдельно в `nodePainter.ts` и `fxRenderer.ts`.
- Логика rounded-rect path тоже продублирована (в `fxRenderer.ts` как функция и в `nodePainter.ts` как локальная функция).

Риск:
- При будущих правках легко получить рассинхрон (один модуль изменили, второй забыли).

Рекомендация:
- Вынести в общий модуль (например `src/render/renderUtils.ts`) и использовать из обоих мест (PR можно делать `no-visual-change`).

#### P2.4 `floatingLabels` потенциально раздувают DOM при спаме
Факт:
- Очистка идёт в `renderFrame`, но нет `maxFloatingLabels` лимита.

Рекомендация:
- Добавить мягкий лимит (например 50): при превышении удалять самые старые или не добавлять новые.

#### P2.5 URL-параметр `eq` (deep-link)
Статус: **реализовано** (2026-01-25).

Что сделано:
- Поддержан `?eq=` с allow-list (`UAH|HOUR|EUR`).
- В `VITE_TEST_MODE=1` `?eq=` игнорируется, чтобы не трогать детерминизм e2e.

---

## 3) Спецификация исправлений (что делаем, почему, и как принимать)

Ниже — предложенные изменения, **в порядке внедрения**. Принцип: маленькие PR, каждый с независимой приемкой.

### PR-1 (P0): Свести clearing-contract между генератором и UI
Изменения:
1) В UI-проигрывателе clearing (сейчас внутри `runClearingOnce()`):
   - поддержать `step.highlight_edges` в non-test режиме:
     - вариант А: `spawnEdgePulses()` по `highlight_edges`
     - вариант Б: отдельный overlay “подсветка рёбер” (вне baseGraph)
   - оставить текущий `particles_edges` как “micro-tx” (sparks beam)
2) В генераторе:
   - добавить в clearing steps опциональный `particles_edges`, чтобы UI мог показывать микротранзакции без ручного дописывания.
   - либо (если решите, что particles_edges — только UI-специфичная штука) — то наоборот: в UI сделать красивую анимацию только по `highlight_edges`.

Почему так:
- Убираем риск «пустого» клиринга после регена.

Приемка:
- После запуска генератора (без ручных JSON правок) Scene E показывает: подсветку маршрута и/или читаемый ритм шагов.

### PR-2 (P0): Централизованная отмена таймеров (Tx/Clearing)
Изменения:
- Добавить реестр timeout ids и очистку в `resetOverlays()` и начале `loadScene()`.
- Протянуть эту практику на будущий playlist player.

Приемка:
- Быстрое переключение сцен/кнопок не оставляет "хвостов"; повторные прогоны ведут себя одинаково.

### PR-2b (P0): Fail-fast валидация рёбер в плейлистах относительно snapshot
Изменения:
- После `loadSnapshot()` и `loadEvents()` в `loadScene()` добавить проверку: каждое `from/to` из событий должно существовать в `snapshot.links`.

Почему так:
- Это дешёвая страховка от «пустых» анимаций при регене/ручных правках данных.

Приемка:
- Если плейлист содержит несуществующее ребро — сцена падает сразу с понятной ошибкой.

### PR-3 (P1): Явный слой overlay для focus/active ребер
Изменения:
- В `drawBaseGraph`:
  - базовый проход рисует viz-стили без “магии”
  - overlay проход усиливает видимость incident/active без полной подмены стиля

Приемка:
- При любом `VIZ_MAPPING` базовый слой всегда соответствует viz.
- Overlay не ломает утверждение визуала при изменении маппинга.

### PR-4 (TODO-A): Демо-панель «как продукт» (минимальный MVP)
Изменения:
- Добавить playlist player для сцен D/E:
  - `Play/Pause` запускает таймер применения событий
  - `Step` применяет ровно 1 событие
  - `Reset view` (см. PR-5) или хотя бы сброс selection+overlays
- Labels LOD: `off / selection / neighbors`
  - важно: должны быть детерминированы и не создавать DOM-переполнение
- Quality: `low/med/high` → dprClamp (1 / 1.5 / 2)

Приемка:
- Элементы управления совпадают со спекой 6.1.

### PR-5 (TODO-A): Камера (pan/zoom) + Reset view
Изменения:
- Ввести camera state (panX/panY/zoom)
- Применять transform при отрисовке base+fx (в одном месте)
- UI: drag pan + wheel zoom
- `Reset view` сбрасывает camera state

Приемка:
- В test-mode камера фиксируется и совпадает со скриншотами.

### PR-6 (TODO-B): Рельсовые плейлисты (Scene D/E)
Изменения в данных:
- Scene D: 10–30 событий tx.updated (за 10–20s) + минимум 1–2 multi-hop.
- Scene E: 3–8 шагов клиринга по циклам долгов (3–6 участников), без повторов направленных рёбер `from→to` между шагами.

Где лучше делать:
- В генераторе, чтобы это обновлялось детерминированно.

Приемка:
- Данные соответствуют разделу 6.4 спеки.

### PR-7 (качество): README + localStorage allow-list
Изменения:
- `simulator-ui/v2/README.md`: исправить legacy path + `cd simulator-ui/v2`.
- `App.vue`: allow-list layoutMode restore = все текущие режимы.

---

## 4) Предложения по декомпозиции (чтобы правки были безопаснее и «не ломали анимацию»)

Проблема сейчас: `App.vue` содержит одновременно:
- загрузку fixtures
- layout engine
- render loop
- input (click)
- scene switching
- проигрывание событий
- FX choreography

Это делает любые правки “слишком широкими” и повышает шанс визуального дрейфа.

### Рекомендуемая декомпозиция (с минимальными рисками)

1) `src/demo/sceneState.ts`
- хранит `eq/scene/layoutMode`
- знает как loadScene + reset
- не рисует и не запускает FX

2) `src/demo/eventPlayer.ts`
- API: `load(playlist)`, `play()`, `pause()`, `step()`, `stop()`
- внутри: единый таймер-реестр, runId, детерминизм

3) `src/demo/clearingPlayer.ts` и `src/demo/txPlayer.ts`
- чистые функции: «применить event → какие overlays/FX спавнить/какие patches применить»
- без прямого доступа к Vue state; возвращают команды (commands pattern)

4) `src/render/graphRenderer.ts`
- тонкая прослойка, которая знает как нарисовать base + fx при заданном состоянии

5) `src/ui/DemoControls.vue`
- только UI, никаких таймеров/рендера

### Тактика внедрения без ломания визуала
- Сначала выделять **типовые утилиты без изменения поведения** (например timer registry), в отдельный PR.
- Затем выносить код «как есть» в модули, сопровождая snapshot-тестами или минимальными контрактными тестами.
- Каждая итерация должна менять максимум 1 ось:
  - либо структура кода
  - либо визуал
  - либо данные

### Минимальные «страховки» от дрейфа
- Зафиксировать визуал сценами A–E через Playwright screenshot (уже есть).
- Добавить лёгкий unit-test на clearing/tx “command output” (например количество спавнов, какие поля читаются: highlight_edges/particles_edges).

---

## 5) Checklist серии PR (как внедрять безопасно и не ломать анимацию)

Цель чеклиста: чтобы каждая правка была маленькой, проверяемой и не меняла «утверждённый» визуал без отдельного решения.

### 5.1 Общие guardrails для всех PR

1) **Одна ось изменений**
- PR либо про контракт данных, либо про таймеры/стейт‑машину, либо про рендер‑оверлеи, либо про UI‑контролы, либо про данные.
- Нельзя смешивать «рефакторинг структуры» и «изменение визуала/таймингов».

2) **Политика “No Visual Change” по умолчанию**
- Любой PR по умолчанию считается `no-visual-change`.
- Если визуал меняется намеренно: нужен отдельный PR/коммит с пометкой `visual-change` + обновление golden‑скриншотов + короткое объяснение, что именно утверждаем заново.

3) **Размер PR**
- Предпочтение: 1–2 файла, до ~150–250 LOC diff (исключения: DemoControls/камера).
- Если выходит больше — разбиваем.

4) **Набор обязательных проверок перед merge**
- E2E/visual: скриншотные тесты Scene A–E должны проходить.
- Поведение test-mode не меняем без причины (VITE_TEST_MODE=1 должен давать стабильный кадр).
- Быстрый ручной smoke (2–3 минуты): Scene D/E, кнопки, переключение Scene/Layout, без ошибок в консоли.

5) **Правило обратимости**
- В каждом PR должно быть понятно, как откатить изменение без каскадных конфликтов.

### 5.2 Регламент “визуал не изменился”

Минимальный протокол проверки для `no-visual-change` PR:
1) Прогнать Playwright screenshots (Scene A–E) и убедиться, что нет diffs.
2) Открыть демо локально и сделать 2 клика:
  - Scene D → `Single Tx`
  - Scene E → `Run Clearing`
  Убедиться, что эффекты запускаются и снимаются (без «пустых» прогонов).

Если Playwright выдаёт diffs:
- это считается **визуальным изменением** (даже если «вроде мелочь»), и PR блокируется до решения: либо фиксим дрейф, либо осознанно принимаем новый baseline.

### 5.3 Checklist по PR (пошагово)

Ниже — “как реально мержить”: что делаем, что не трогаем, и как принимаем.

#### PR-1 (P0): Clearing contract — UI понимает highlight/particles
Scope:
- Правим только проигрывание `clearing.plan` (желательно вынести в маленькую функцию внутри App.vue перед дальнейшей декомпозицией).
- Не меняем внешний вид FX: используем уже существующие примитивы `spawnEdgePulses`, `spawnSparks`.

Checklist:
- [x] UI обрабатывает `steps[].highlight_edges` в non-test режиме (как минимум как pulse‑подсветку маршрута)
- [x] `particles_edges` продолжает работать как раньше
- [x] После регена генератором (без ручных JSON правок) Scene E не становится пустой

Acceptance:
- Playwright screenshots: без diffs.
- Scene E в non-test: видно маршрут и/или микротранзакции.

#### PR-2 (P0): Отмена таймеров (Tx/Clearing) через единый реестр
Scope:
- Только управление таймерами (никаких изменений формул, цветов, длительностей).

Checklist:
- [x] Все `setTimeout` в Tx/Clearing регистрируются
- [x] `resetOverlays()` и/или `loadScene()` очищают таймеры
- [x] Повторный запуск не оставляет хвостов

Acceptance:
- Playwright screenshots: без diffs.
- Ручной smoke: быстрое переключение Scene D↔E и повторные клики не приводят к «призракам»

#### PR-2b (P0): Fail-fast валидация рёбер в плейлистах
Scope:
- Только валидация данных при загрузке сцены (никаких изменений FX/таймингов).

Checklist:
- [x] Собирается индекс `snapshot.links` → `Set(keyEdge(source,target))`
- [x] Проверяются все edge-arrays из events (tx + clearing)
- [x] Ошибка содержит `event_id` и ребро `from->to`

Acceptance:
- Playwright screenshots: без diffs.
- Любой неправильный плейлист ломается fail-fast (без silent fail)

#### PR-3 (P1): Base graph — явный overlay слой для focus/active
Scope:
- Рефактор рендера рёбер: разделить базовый viz‑проход и overlay‑проход.
- Не трогаем `VIZ_MAPPING`.

Checklist:
- [x] Базовый проход всегда использует `viz_width_key/viz_alpha_key` как источник правды
- [x] Overlay усиливает/подсвечивает без полной подмены базового стиля (мультипликативно + clamp)
- [x] Active/focus остаются визуально такими же (проверено по Scene A–E: Playwright screenshots без diffs)

Статус:
- Реализовано в `baseGraph.ts`: base = viz_*, overlay = focus/active.
- Это считается **семантической правкой** (correctness-first). Скриншотные тесты должны проходить; если базовые картинки изменятся — обновление baseline допускается как осознанный шаг.

Acceptance:
- Playwright screenshots: без diffs.

#### PR-4 (TODO-A): DemoControls MVP — Play/Pause/Step/Labels/Quality
Scope:
- Добавляем “как продукт” панель, но не переписываем весь App.vue.
- Внутри используем общий player с timer registry.

Checklist:
- [x] Scene D/E поддерживают Play/Pause/Step
- [x] Labels LOD реализован детерминированно (и не взрывает DOM)
- [x] Quality меняет dprClamp, а test-mode фиксирует dpr

Acceptance:
- Playwright screenshots: без diffs (в test-mode, где эффекты отключены/стабилизированы).
- Ручной smoke: controls работают предсказуемо.

#### PR-5 (TODO-A): Камера (pan/zoom) + Reset view
Scope:
- Вводим camera state и применяем transform в одном месте.

Checklist:
- [x] `Reset view` возвращает дефолтный кадр
- [x] Test-mode фиксирует камеру (скриншоты стабильны)

Acceptance:
- Playwright screenshots: без diffs.

#### PR-6 (TODO-B): Рельсовые плейлисты Scene D/E (данные)
Scope:
- Меняем только fixtures/генератор, без изменения UI-логики.

Детализация требований, чтобы не сломать/не изменить анимацию “случайно”:

Scene D (Tx burst) — как именно расширять:
- Делаем 10–30 событий `tx.updated`.
- Включаем минимум:
  - 2–3 single-edge tx (как сейчас)
  - 1–2 multi-hop (path длиной 3–6 узлов)
- Рекомендованный диапазон `ttl_ms`: держать одинаковым (например 1200 как сейчас) или в узком диапазоне (например 900–1400), но **не менять UI-алгоритм**.
- Если добавляем “паузу” между событиями — это задача player (PR-4), а не данных. В данных только события; расписание (интервал) задаём в player.

Scene E (Clearing plan) — как именно расширять:
- `clearing.plan`: 3–8 шагов.
- Каждый шаг — отдельный клиринг одного цикла долгов (3–6 участников по протоколу; чаще 3–4).
- В каждом шаге предпочтительно иметь:
  - `highlight_edges` — чтобы цикл читался целиком
  - `particles_edges` — чтобы “пройти” цикл микротранзакциями
- В рамках одного плейлиста клиринга избегаем повторов направленных рёбер `from→to` между шагами (реалистичнее и визуально понятнее).
- `at_ms` шагов: монотонно возрастают; рекомендуемо кратно 250–450ms, чтобы ритм был стабильным и предсказуемым.

Общие ограничения (страховка от “испортить анимацию”):
- В рамках PR-6 запрещено менять визуальные формулы/константы в `fxRenderer.ts` и choreography в `App.vue`.
- Любые изменения внешнего вида должны быть отдельным `visual-change` PR.

Checklist:
- [x] Scene D: 10–30 tx.updated + хотя бы 1–2 multi-hop
- [x] Scene E: 3–8 шагов клиринга (3–6 участников), без повторов направленных рёбер между шагами
- [x] Данные детерминированы (генератор воспроизводим)

Acceptance:
- Playwright screenshots: обновляются только осознанно (это потенциально `visual-change`).
- Ручной просмотр: сцены действительно демонстрируют нужную метафору (не single-edge/треугольник).

#### PR-7 (качество): README + localStorage allow-list
Scope:
- Только документация и восстановление состояния UI.

Checklist:
- [x] README: корректные пути (legacy=v1, запуск=v2)
- [x] localStorage restore принимает все реальные layout modes

Acceptance:
- Playwright screenshots: без diffs.

#### PR-8 (декомпозиция, no-visual-change): вынос player/scene state из App.vue
Scope:
- Только перенос кода в модули с теми же входами/выходами.

Checklist:
- [x] 1 модуль за PR (вынесен timer registry; дальнейшая декомпозиция возможна по мере надобности)
- [x] Никаких новых констант/таймингов/рандома (для рефакторинга таймеров/ресайза — только без изменения поведения)

Acceptance:
- Playwright screenshots: без diffs.



---

## 6) Отдельные замечания по текущему состоянию (нейтрально)

- `fixtures.ts` хорошо делает fail-fast для snapshot (dangling links) и строгую политику viz_* (это плюс). Дополнительно edge existence для плейлистов теперь валидируется на загрузке сцен D/E.
- `fxRenderer.ts` уже содержит нужные примитивы (sparks/pulses/bursts), и App.vue теперь использует их для clearing steps (`highlight_edges` + `particles_edges`).
- Плейлисты D/E уже «нагружают» FX серией событий и подходят для визуальных регрессий; следующий крупный шаг — DemoControls (Play/Pause/Step/Quality/LOD) и камера.

---

## 7) Декомпозиция App.vue — план выноса подсистем

### 7.1 Проблема

`App.vue` содержит ~1900 строк и объединяет:
- загрузку fixtures + scene switching
- layout engine (force-directed, constellations)
- render loop (base + fx)
- input handling (click, pan, zoom, wheel)
- picking (node hit-test, edge pick, spatial index)
- camera state + transforms
- demo player (Tx + Clearing choreography)
- overlay UI helpers (floating labels, hoveredEdge, activeEdges)
- playlist state machine (play/pause/step)

Это делает правки "слишком широкими" и повышает риск визуального/поведенческого дрейфа.

### 7.2 Принципы рефакторинга

1. **Поведение/тайминги/визуал не меняются** — только перенос кода
2. **Детерминизм сохраняется** — Playwright Scene A–E без diffs
3. **Каждый модуль тестируем "в изоляции"** — чистые функции + минимум зависимостей
4. **Один PR = одна подсистема** — не смешиваем рефакторинг структуры и изменение поведения

### 7.3 Анализ App.vue по блокам

| Блок | Строки | Риск | Выигрыш | Зависимости |
|------|--------|------|---------|-------------|
| **Demo Player (Tx + Clearing)** | ~350 | Низкий | 🔥 Высокий | fxState, timers, applyPatches, pushFloatingLabel |
| **Picking / Interaction** | ~150 | Низкий | Средний | layout, camera |
| **Camera + Transforms** | ~100 | Низкий | Средний | layout.w/h |
| **Layout Coordinator** | ~80 | Средний | Средний | snapshot, layoutMode |
| **Overlay UI Helpers** | ~100 | Низкий | Средний | layout, state.floatingLabels |
| **Force Layout Math** | ~400 | Низкий | Средний | чистая математика |

### 7.4 Предлагаемые модули

#### Пакет 1: Demo Player → `composables/useDemoPlayer.ts`

**Что выносим:**
- `runTxEvent()`, `runClearingStep()`, `runClearingOnce()`, `runTxOnce()`
- `playlist` reactive state
- `txRunSeq`, `clearingRunSeq` (runId guards)
- `demoTogglePlay()`, `demoStepOnce()`, `demoReset()`
- `stopPlaylistPlayback()`, `resetPlaylistPointers()`

**Интерфейс:**
```ts
interface DemoPlayerDeps {
  // Patches
  applyPatches: (evt: DemoEvent) => void
  
  // FX spawning (facade to fxRenderer)
  spawnSparks: typeof spawnSparks
  spawnNodeBursts: typeof spawnNodeBursts
  spawnEdgePulses: typeof spawnEdgePulses
  
  // UI feedback
  pushFloatingLabel: (opts: FloatingLabelOpts) => void
  resetOverlays: () => void
  fxColorForNode: (id: string, fallback: string) => string
  
  // Timing
  scheduleTimeout: (fn: () => void, ms: number) => number
  clearScheduledTimeouts: () => void
  
  // Layout access (read-only)
  getLayoutNode: (id: string) => LayoutNode | undefined
  
  // Config
  isTestMode: ComputedRef<boolean>
  isWebDriver: boolean
  effectiveEq: ComputedRef<string>
}

interface UseDemoPlayerReturn {
  playlist: { playing: boolean; txIndex: number; clearingStepIndex: number }
  
  runTxEvent: (evt: TxUpdatedEvent, opts?: { onFinished?: () => void }) => void
  runClearingStep: (stepIndex: number, plan: ClearingPlanEvent, done: ClearingDoneEvent | null, opts?: { onFinished?: () => void }) => void
  
  demoTogglePlay: (scene: SceneId, txEvents: TxUpdatedEvent[], clearingPlan: ClearingPlanEvent | null, clearingDone: ClearingDoneEvent | null) => void
  demoStepOnce: (scene: SceneId, txEvents: TxUpdatedEvent[], clearingPlan: ClearingPlanEvent | null) => void
  demoReset: () => void
  
  stopPlaylistPlayback: () => void
}
```

**Тестирование:**
```ts
// useDemoPlayer.test.ts
test('runTxEvent spawns sparks and schedules cleanup', () => {
  const spawnSparks = vi.fn()
  const scheduleTimeout = vi.fn((fn, ms) => setTimeout(fn, ms))
  
  const player = useDemoPlayer({ spawnSparks, scheduleTimeout, ... })
  player.runTxEvent(mockTxEvent)
  
  expect(spawnSparks).toHaveBeenCalledWith(expect.objectContaining({
    edges: mockTxEvent.edges,
    kind: 'beam'
  }))
})
```

#### Пакет 2: Picking / Interaction → `composables/usePicking.ts`

**Что выносим:**
- `nodePickGrid` computed
- `edgePickGrid` computed
- `pickNodeAt(clientX, clientY)`
- `pickEdgeAt(clientX, clientY)`
- `dist2PointToSegment()`, `closestPointOnSegment()`

**Интерфейс:**
```ts
interface UsePickingDeps {
  layoutNodes: ComputedRef<LayoutNode[]>
  layoutLinks: ComputedRef<LayoutLink[]>
  camera: { panX: number; panY: number; zoom: number }
  hostEl: Ref<HTMLElement | null>
  screenToWorld: (x: number, y: number) => { x: number; y: number }
  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
}

interface UsePickingReturn {
  nodePickGrid: ComputedRef<Map<string, LayoutNode[]>>
  edgePickGrid: ComputedRef<{ cellSizeW: number; cells: Map<string, EdgeSeg[]> }>
  pickNodeAt: (clientX: number, clientY: number) => LayoutNode | null
  pickEdgeAt: (clientX: number, clientY: number) => EdgeSeg | null
}
```

**Тестирование:**
```ts
test('pickNodeAt returns closest node within hit radius', () => {
  const picking = usePicking({
    layoutNodes: computed(() => [
      { id: 'A', __x: 100, __y: 100 },
      { id: 'B', __x: 200, __y: 200 }
    ]),
    camera: { panX: 0, panY: 0, zoom: 1 },
    ...
  })
  
  expect(picking.pickNodeAt(105, 105)?.id).toBe('A')
  expect(picking.pickNodeAt(500, 500)).toBeNull()
})
```

#### Пакет 3: Camera + Transforms → `composables/useCamera.ts`

**Что выносим:**
- `camera` reactive state (`panX`, `panY`, `zoom`)
- `panState` reactive (for drag)
- `wheelState` reactive (for throttled zoom)
- `worldToScreen()`, `screenToWorld()`, `clientToScreen()`
- `worldToCssTranslate()`
- `clampCameraPan()`, `resetCamera()`
- `getWorldBounds()`

**Интерфейс:**
```ts
interface UseCameraDeps {
  layoutNodes: ComputedRef<LayoutNode[]>
  layoutW: ComputedRef<number>
  layoutH: ComputedRef<number>
  hostEl: Ref<HTMLElement | null>
  isTestMode: ComputedRef<boolean>
}

interface UseCameraReturn {
  camera: { panX: number; panY: number; zoom: number }
  panState: { active: boolean; pointerId: number; ... }
  wheelState: { pendingDeltaY: number; ... }
  
  worldToScreen: (x: number, y: number) => { x: number; y: number }
  screenToWorld: (x: number, y: number) => { x: number; y: number }
  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
  worldToCssTranslate: (x: number, y: number) => string
  
  clampCameraPan: () => void
  resetCamera: () => void
  
  // Event handlers (to be wired in App.vue)
  onPointerDown: (ev: PointerEvent, hitNode: LayoutNode | null) => void
  onPointerMove: (ev: PointerEvent) => void
  onPointerUp: (ev: PointerEvent) => boolean // returns true if was click (not pan)
  onWheel: (ev: WheelEvent) => void
}
```

#### Пакет 4: Layout Coordinator → `composables/useLayoutCoordinator.ts`

**Что выносим:**
- `resizeCanvases()`
- `requestResizeAndLayout()`
- `requestRelayoutDebounced()`
- `recomputeLayout()` + `lastLayoutKey` cache
- `onWindowResize()`
- `resizeRafId`, `relayoutDebounceId`

**Интерфейс:**
```ts
interface UseLayoutCoordinatorDeps {
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>
  hostEl: Ref<HTMLDivElement | null>
  snapshot: ComputedRef<GraphSnapshot | null>
  layoutMode: Ref<LayoutMode>
  dprClamp: ComputedRef<number>
  isTestMode: ComputedRef<boolean>
  clampCameraPan: () => void
}

interface UseLayoutCoordinatorReturn {
  layout: { nodes: LayoutNode[]; links: LayoutLink[]; w: number; h: number }
  
  resizeAndLayout: () => void
  requestResizeAndLayout: () => void
  requestRelayoutDebounced: (delayMs?: number) => void
  
  // Lifecycle
  setupResizeListener: () => void
  teardownResizeListener: () => void
}
```

#### Пакет 5: Overlay UI Helpers → `composables/useOverlayState.ts`

**Что выносим:**
- `state.floatingLabels` + TTL management
- `floatingLabelThrottleAtMsByKey` + pruning
- `pushFloatingLabel()`
- `hoveredEdge` reactive + `clearHoveredEdge()`
- `state.activeEdges`
- `resetOverlays()`
- `floatingLabelsView` computed

**Интерфейс:**
```ts
interface UseOverlayStateDeps {
  layoutNodeMap: ComputedRef<Map<string, LayoutNode>>
  camera: { zoom: number }
  sizeForNode: (node: LayoutNode) => { w: number; h: number }
}

interface UseOverlayStateReturn {
  floatingLabels: Array<FloatingLabel>
  floatingLabelsView: ComputedRef<Array<{ id: number; x: number; y: number; text: string; color: string }>>
  
  hoveredEdge: { key: string | null; fromId: string; toId: string; amountText: string; screenX: number; screenY: number }
  
  activeEdges: Set<string>
  
  pushFloatingLabel: (opts: FloatingLabelOpts) => void
  clearHoveredEdge: () => void
  resetOverlays: () => void
  
  // Called from render loop
  pruneExpiredLabels: (nowMs: number) => void
}
```

#### Пакет 6 (опционально): Force Layout Math → `layout/forceLayout.ts`

**Что выносим:**
- `fnv1a()` (hash function)
- `computeOrganicGroupAnchors()`
- `applyForceLayout()`
- `computeLayoutAdminForce()`, `computeLayoutCommunityClusters()`
- `computeLayoutConstellations()`, `computeLayoutTypeSplit()`, `computeLayoutStatusSplit()`, `computeLayoutBalanceSplit()`

**Интерфейс:**
```ts
// Чистые функции без Vue зависимостей
export function applyForceLayout(opts: ForceLayoutOptions): { nodes: LayoutNode[]; links: LayoutLink[] }
export function computeLayoutForMode(snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode, isTestMode: boolean): { nodes: LayoutNode[]; links: LayoutLink[] }
```

**Тестирование:**
```ts
test('applyForceLayout produces deterministic positions', () => {
  const result1 = applyForceLayout({ snapshot: mockSnapshot, w: 800, h: 600, seedKey: 'test' })
  const result2 = applyForceLayout({ snapshot: mockSnapshot, w: 800, h: 600, seedKey: 'test' })
  
  expect(result1.nodes).toEqual(result2.nodes)
})
```

### 7.5 Порядок внедрения (пакеты)

Рекомендуемый порядок (от наименьшего риска к большему):

| # | Пакет | Риск | Зависит от | Приоритет |
|---|-------|------|------------|-----------|
| 1 | **Demo Player** | Низкий | timerRegistry (уже есть) | 🔥 Высокий |
| 2 | **Picking / Interaction** | Низкий | camera (можно передать как deps) | Средний |
| 3 | **Camera + Transforms** | Низкий | layout (read-only) | Средний |
| 4 | **Layout Coordinator** | Средний | camera.clampPan | Средний |
| 5 | **Overlay UI Helpers** | Низкий | layout, camera | Низкий |
| 6 | **Force Layout Math** | Низкий | ничего | Низкий (по желанию) |

### 7.6 Чеклист рефакторинга

#### PR-9: Demo Player → `composables/useDemoPlayer.ts`

Scope:
- Выносим всю логику Tx/Clearing проигрывания без изменения таймингов/визуала.

Checklist:
- [x] Создан `simulator-ui/v2/src/composables/useDemoPlayer.ts`
- [x] Интерфейс принимает callbacks (не импортирует напрямую fxRenderer/state)
- [x] `runTxEvent()` перенесён без изменений таймингов
- [x] `runClearingStep()` перенесён без изменений таймингов
- [x] `playlist` state вынесен
- [x] runId guards (`txRunSeq`, `clearingRunSeq`) работают корректно
- [x] App.vue использует composable через destructuring
- [x] Добавлены базовые unit-тесты (spawn calls, cleanup scheduling)

Acceptance:
- Playwright screenshots: без diffs
- Ручной smoke: Scene D/E работают идентично

#### PR-10: Picking → `composables/usePicking.ts`

Scope:
- Выносим spatial index и hit-test логику.

Checklist:
- [x] Создан `simulator-ui/v2/src/composables/usePicking.ts`
- [x] `nodePickGrid` и `edgePickGrid` computed вынесены
- [x] `pickNodeAt()` и `pickEdgeAt()` вынесены
- [x] Геометрические хелперы (`dist2PointToSegment`, `closestPointOnSegment`) вынесены
- [x] App.vue использует composable
- [x] Добавлены unit-тесты на hit-test

Acceptance:
- Playwright screenshots: без diffs
- Ручной smoke: клик по узлам и hover по рёбрам работают

#### PR-11: Camera → `composables/useCamera.ts`

Scope:
- Выносим camera state и transforms.

Checklist:
- [x] Создан `simulator-ui/v2/src/composables/useCamera.ts`
- [x] `camera`, `panState`, `wheelState` вынесены
- [x] Transform функции (`worldToScreen`, `screenToWorld`, etc.) вынесены
- [x] `clampCameraPan()` вынесен
- [x] Pointer/wheel handlers вынесены (или экспортируются как методы)
- [x] Test-mode блокирует pan/zoom (как сейчас)
- [x] App.vue использует composable

Acceptance:
- Playwright screenshots: без diffs
- Ручной smoke: pan/zoom работают

#### PR-12: Layout Coordinator → `composables/useLayoutCoordinator.ts`

Scope:
- Выносим resize/relayout логику.

Checklist:
- [x] Создан `simulator-ui/v2/src/composables/useLayoutCoordinator.ts`
- [x] `layout` reactive вынесен
- [x] `resizeCanvases()`, `recomputeLayout()` вынесены
- [x] Debounce/RAF логика вынесена
- [x] `lastLayoutKey` кэш работает корректно
- [x] Window resize listener управляется через lifecycle hooks composable

Acceptance:
- Playwright screenshots: без diffs
- Ручной smoke: resize окна работает

#### PR-13: Overlay Helpers → `composables/useOverlayState.ts`

Scope:
- Выносим floating labels и hovered edge state.

Checklist:
- [x] Создан `simulator-ui/v2/src/composables/useOverlayState.ts`
- [x] `floatingLabels` + throttle map вынесены
- [x] `hoveredEdge` state вынесен
- [x] `activeEdges` Set вынесен
- [x] `pushFloatingLabel()` с throttling/TTL вынесен
- [x] `resetOverlays()` вынесен
- [x] `floatingLabelsView` computed вынесен

Acceptance:
- Playwright screenshots: без diffs
- Ручной smoke: floating labels появляются и исчезают

#### PR-14 (опционально): Force Layout → `layout/forceLayout.ts`

Scope:
- Выносим математику layout в чистый модуль.

Checklist:
- [x] Создан `simulator-ui/v2/src/layout/forceLayout.ts`
- [x] Все `computeLayout*` функции вынесены
- [x] `applyForceLayout()` вынесен
- [x] `fnv1a()` вынесен в `utils/hash.ts`
- [x] Функции не зависят от Vue
- [x] Добавлены тесты на детерминизм

Acceptance:
- Playwright screenshots: без diffs

### 7.7 Дополнительные модули (можно вынести позже)

1. [x] **Scene State** → `composables/useSceneState.ts`
   - `eq`, `scene`, `layoutMode` refs
   - `loadScene()` логика
   - URL params parsing

2. [x] **Edge Tooltip** → `composables/useEdgeTooltip.ts`
   - `formatEdgeAmountText()`
   - `edgeTooltipStyle()`
   - Логика "показывать только когда выбран узел и ребро инцидентно"

3. [x] **Node Card** → `composables/useNodeCard.ts`
   - `selectedNode` computed
   - `nodeCardStyle()`

4. [x] **Render Loop** → `composables/useRenderLoop.ts`
   - `renderFrame()`
   - `ensureRenderLoop()`, `stopRenderLoop()`
   - RAF management

### 7.8 Guardrails

1. **Test-mode поведение не меняется**
   - Все composables должны принимать `isTestMode` и блокировать недетерминированное поведение

2. **Нет новых таймингов/констант**
   - Рефакторинг = перенос кода "как есть"
   - Если нужно изменить тайминг — отдельный PR с пометкой `visual-change`

3. **Зависимости через DI (dependency injection)**
   - Composables принимают callbacks/refs, не импортируют глобальный state
   - Это позволяет тестировать в изоляции

4. **Один PR = один composable**
   - Не смешиваем выносы разных подсистем
   - Каждый PR проходит полный цикл проверок (Playwright + smoke)

---

## Приложение A: Дальнейшая декомпозиция (следующий этап, опционально)

Цель: сделать `App.vue` тонким оркестратором, чтобы фичи (игроки, interactions, рендер, UI) развивались независимо и тестировались изолированно.

### A.1 Demo player (уровень домена)

- `src/demo/txPlayer.ts`
  - `runTxEvent()` и вся логика Scene D (tx playlist) как чистый модуль (тайминги/визуал не менять).
- `src/demo/clearingPlayer.ts`
  - `runClearingStep()` и логика Scene E (clearing playlist).
- `src/demo/eventLog.ts`
  - типы событий + нормализация/валидация входных events.

### A.2 Interaction (уровень UI)

- `src/composables/useDragPin.ts`
  - drag state machine + pin/unpin API.
  - Guardrail: отключать drag/pin в test-mode (чтобы Playwright был детерминирован).
- `src/composables/useKeyboardShortcuts.ts`
  - хоткеи для pin/unpin, reset camera, toggle overlays и т.п. (также отключать в test-mode).

### A.3 Renderer split (уровень рендера)

- `src/render/graphRenderer.ts`
  - чистое рисование графа (canvas) на основе подготовленных `layout` + `snapshot`.
  - минимизировать связность с Vue: входные данные → отрисовка.
- `src/render/overlayRenderer.ts`
  - отдельная отрисовка overlay слоёв (labels, highlights), чтобы легче контролировать визуальные изменения.

### A.4 UI components

- `src/components/DemoControls.vue`
  - панель управления demo (play/pause/step/reset), чтобы убрать UI-логику из `App.vue`.
- `src/components/NodeCard.vue`
  - карточка выбранного узла (включая Pin/Unpin), чтобы не раздувать шаблон `App.vue`.

### A.5 Риски и правила

- Любая правка, меняющая внешний вид, маркируется как `visual-change` и требует обновления Playwright snapshots.
- В test-mode запрещаем: drag/pan/zoom и любые источники недетерминизма (random, time-based animation без фиксированной симуляции).
