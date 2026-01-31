# GEO Simulator Frontend — Performance & Quality Policy (software-only / low FPS)

Статус: Active
Область: simulator/frontend
Последнее обновление: 2026-01-31

Цель: сделать UI симулятора **устойчивым к окружениям без GPU-ускорения**, не меняя семантику визуализации и контрактов данных.

Контекст: при включении software-only рендера в Chrome (например, `Microsoft Basic Render Driver`) стоимость каждого кадра (Canvas 2D + композитинг) может вырасти на порядки. Поэтому сравнение с SimpleBrowser VS Code не всегда корректно: SimpleBrowser может работать через реальный GPU, а Chrome — через софт.

## 0) Термины

- **GPU-ускорение доступно**: браузер использует аппаратный рендеринг для compositing/canvas (обычно через реальный GPU).
- **software-only**: браузер рендерит через софт (CPU), часто с `WebGL`/compositing в режиме software.

## 1) Диагностика (для разработчиков)

- Встроенный оверлей диагностики: открыть симулятор с `?perf=1`.
- Браузерный уровень: `chrome://gpu` (ищем `Software only` и признаки `Microsoft Basic Render Driver`).

## 2) Heuristic: определение software-only

UI выполняет «лёгкую» проверку доступности GPU-ускорения:

1) Пытаемся получить WebGL контекст (не для рендера, а для диагностики/идентификации).
2) Если доступно расширение `WEBGL_debug_renderer_info`, читаем `UNMASKED_RENDERER_WEBGL`.
3) Если renderer указывает на software-рендер (например, `Microsoft Basic Render Driver`, `SwiftShader`, `llvmpipe`) — считаем режим software-only.

Важно:
- Это **эвристика**, а не криптографически точная проверка.
- Политика деградации должна быть safe-by-default: лучше «слегка ухудшить визуал», чем получить ~1 FPS и неработоспособный UI.

## 3) Политика качества (авто-деградация)

### 3.1 Базовое правило (software-only)

Если режим распознан как software-only, UI автоматически:

- выбирает `quality=low` как безопасный дефолт (если пользователь не успел вручную переключить качество вскоре после старта),
- отключает дорогие UI-эффекты типа `backdrop-filter` через признак окружения (см. CSS gating).

### 3.2 Safety net на старте ("1 FPS сразу после открытия")

Если на стартовом экране измеренный FPS очень низкий (порядка < ~12 за короткое окно) и пользователь не успел вручную переключить качество, UI также переводит качество в `low`.

Это отдельная защита на случай, когда WebGL-эвристика не сработала/недоступна, но baseline кадр уже слишком дорогой.

## 4) Политика рендера (Canvas)

Принцип: деградируем **только визуальные эффекты/LOD**, не меняем данные, направление trustlines, семантику цветов/толщин.

### 4.1 Shadow blur

Per-object `shadowBlur` на Canvas 2D — один из самых дорогих эффектов в software-only режиме.

- В `quality=low` blur выключается.
- В software-only режиме glow для нод рисуется через **pre-baked sprite** (см. ниже), чтобы сохранить эстетичный glow без per-frame blur.

### 4.2 Pre-baked glow sprites (software-only fallback)

В software-only режиме glow слои (bloom/rim) рисуются как кэшированные спрайты (генерируются один раз и переиспользуются через `drawImage`).

Это сохраняет «приятный» glow-вид, но резко снижает стоимость кадра по сравнению с `shadowBlur`.

## 5) CSS gating (DOM/UI)

Дорогие CSS эффекты (например, `backdrop-filter`) отключаются не только по `quality`, но и по признаку окружения (software-only), чтобы избежать композитинга blur в «плохих» окружениях.

## 6) Где реализовано (код)

- Политика качества + эвристика GPU: `simulator-ui/v2/src/composables/useSimulatorApp.ts`
- Оверлей диагностики: `simulator-ui/v2/src/components/DevPerfOverlay.vue`
- CSS gating: `simulator-ui/v2/src/App.css`
- Pre-baked glow sprites cache: `simulator-ui/v2/src/render/glowSprites.ts`
- Использование спрайтов в рендере нод: `simulator-ui/v2/src/render/nodePainter.ts`
