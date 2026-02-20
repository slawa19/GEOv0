# Simulator UI v2 — HUD: инструкция пользователя (RU)

Документ объясняет, **что делает каждый элемент HUD** в Simulator UI v2 и как им пользоваться в режимах Sandbox / Auto‑Run / Interact.

## 1) Как открыть

- Full stack: `http://localhost:5176/?mode=real`
- Режимы задаются параметрами URL (их выставляет сам UI):
  - `mode=fixtures` — Sandbox (без backend)
  - `mode=real` — Real mode (backend required)
  - `ui=interact` — Interact UI поверх real mode

## 2) Базовые действия на сцене

- **Панорамирование/зум** — управляется “камерой” (мышь/тачпад в зависимости от окружения).
- **Выбор узла**: один клик по узлу выделяет его.
- **Карточка узла (Node Card)**: двойной клик по узлу открывает карточку.
- **Снятие выделения**: клик по пустому месту снимает выделение.
- **Подсказки по рёбрам (Edge tooltip)**: появляются при наведении на рёбра (см. описание Edge Tooltip ниже; есть ограничения по режимам).

## 3) Верхняя панель — TopBar

TopBar — основной HUD для режимов и управления запуском.

Примечание про тестовый режим:
- Если UI запущен с `VITE_TEST_MODE=1`, в TopBar может отображаться бейдж `TEST MODE` (чтобы было видно, что включено automation‑поведение).

### 3.1 Переключение режимов

Слева — сегменты:

- **Sandbox** (fixtures): офлайн режим для просмотра сцен/топологии.
- **Auto‑Run** (real): запуск/пауза/стоп симуляции в backend.
- **Interact** (real + interact UI): интерактивные действия (payment/trustline/clearing) поверх real‑run.

Важно:
- Interact доступен только в real mode.
- Если вы уже в Interact, повторный клик по сегменту может привести к перезагрузке страницы (в текущей реализации).

### 3.2 Управление run (Auto‑Run)

В режиме Auto‑Run доступны:

- Выбор сценария.
- `Start` / `Pause` / `Resume` / `Stop`.
- Статусы:
  - SSE connection (open/connecting/reconnecting/closed)
  - Run state (created/running/paused/stopping/stopped/error)
  - Последняя ошибка (если есть).

### 3.3 Advanced (скрытый блок)

В раскрывающемся `Advanced` обычно находятся:

- переключатели pipeline (fixtures/real) для real‑run,
- интенсивность FX (в процентах) и кнопка Apply.

Примечание:
- В Interact UI интенсивность принудительно удерживается на 0% (контракт режима).

## 4) Нижняя панель — BottomBar

BottomBar отвечает за настройки отображения и инструменты.

### 4.1 View settings

- **EQ** (Equivalent): выбор единицы (например `UAH`, `HOUR`, `EUR`).
- **Layout**: режим раскладки графа.
- **Scene**: выбор сцены доступен в offline/fixtures (не в real mode).
- **Quality**: качество рендера.
- **Labels**: уровень подписей (off/selection/neighbors).

### 4.2 Tools

Правая часть содержит инструменты по режиму:

- В real mode: инструменты run/snapshot (например refresh snapshot).
- Dev tools (только dev‑сборки): кнопки для Demo UI / FX Debug.

## 5) Interact HUD (только real + ui=interact)

Interact HUD добавляет набор элементов для ручных действий.

Подробнее про сценарии использования, предусловия (feature flag) и пошаговые флоу: см. [`interact-mode-user-guide.md`](interact-mode-user-guide.md).

### 5.1 ActionBar

ActionBar — панель действий Interact:

- **Send Payment** — ручной платёж (manual payment flow).
- **Manage Trustline** — создание/обновление/закрытие trustline.
- **Run Clearing** — запуск клиринга.

Состояния:
- Кнопки могут быть отключены, если UI занят (busy) или run в терминальном состоянии.

### 5.2 SystemBalanceBar

SystemBalanceBar показывает системные агрегаты:

- Total Debt
- Available Capacity
- Trustlines
- Participants
- Utilization (progress)

Эти значения помогают понимать, есть ли «пространство» для новых платежей и как загружена сеть.

### 5.3 ManualPaymentPanel

Открывается при активном payment flow и ведёт по шагам:

1) **Pick From** — выберите отправителя кликом по узлу на сцене или в dropdown `From`.
2) **Pick To** — выберите получателя кликом по узлу или в dropdown `To`.
3) **Confirm** — введите сумму и нажмите `Confirm`.

Поля и валидация:
- Amount должен быть > 0.
- Если известна доступная capacity, UI показывает `Available` и предупреждает, если Amount её превышает.

Кнопки:
- `Confirm` — подтверждает платёж.
- `Cancel` — отменяет текущий payment flow.

### 5.4 TrustlineManagementPanel

Открывается при активном trustline flow.

Сценарии:

- **Create trustline**:
  1) Pick From
  2) Pick To
  3) Введите `Limit` и нажмите `Create`.

- **Edit trustline**:
  - Показывает Used / Limit / Available.
  - Введите `New limit` и нажмите `Update`.

- **Close trustline**:
  - Кнопка `Close TL` работает как «arming»:
    - первый клик — переводит в состояние подтверждения,
    - второй клик — подтверждает закрытие,
    - `Cancel close` или `ESC` снимает arm.

Dropdowns:
- `From` / `To` — выбор участников.
- `Existing` — выбор существующей trustline (если доступно).

### 5.5 EdgeDetailPopup (оверлей редактирования trustline)

Появляется при редактировании выбранной trustline (обычно после клика по ребру в Interact).

Возможности:
- Быстрый доступ к действию `Change limit` (переводит фокус на поле `New limit` в TrustlineManagementPanel).
- `Close line` — destructive действие, может требовать подтверждения.

### 5.6 ClearingPanel

Открывается при clearing flow:

- **Run clearing** (confirm): `Confirm` запускает clearing cycle.
- **Clearing preview**: показывает количество циклов и total cleared (и список циклов, если доступен).
- **Clearing running**: показывает состояние выполнения.

Кнопки:
- `Cancel`/`Close` — закрывает панель/отменяет flow.

## 6) Подсказки и оверлеи

### 6.1 EdgeTooltip

Появляется при наведении на trustline edge и показывает краткие значения (used/limit/available) в выбранном эквиваленте.

Ограничения/особенности:
- Поведение подсказок может отличаться в e2e/WebDriver режимах.

### 6.2 LabelsOverlayLayers

Слой подписей узлов и floating labels (например, суммы FX‑событий).

- Масштаб подписей адаптируется через CSS `--overlay-scale`.

### 6.3 NodeCardOverlay

Карточка выбранного узла (показывает имя/тип/метрики/баланс и т.п.).

- Открывается двойным кликом по узлу.
- Может поддерживать pin/unpin (в зависимости от режима и окружения).

### 6.4 DevPerfOverlay

Диагностический оверлей (включается параметром `perf=1`).

Показывает:
- WebGL vendor/renderer,
- предупреждения про software renderer,
- полезные метрики/текст для копирования.

## 7) Горячие клавиши

- `ESC` в Interact: отменяет активный flow.
- Если где-то включено destructive confirmation (arming), `ESC` сначала снимает arm (панель “consumes” ESC), и только если никто не “consumed” — выполняется глобальная отмена flow.
