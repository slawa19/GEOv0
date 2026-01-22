# GEO Simulator — быстрая демка для утверждения визуала (на фикстурах)

**Статус:** проектная спецификация / план реализации демо

Цель: собрать **быструю, воспроизводимую демку** (без реальных данных и без поднятия backend), чтобы утвердить:
- палитру и семантику цветов,
- размеры узлов (net-balance/кластеры),
- читаемость связей (alpha/width/LOD),
- состояния (suspended/left/deleted),
- поведение при фокусе/выборе/событиях (tx/clearing).

Ключевое правило (как в Admin UI): **клиент не вычисляет семантику визуализации** — визуальные решения приходят готовыми как `viz_*` (см. источник правды: [docs/ru/simulator/frontend/docs/api.md](../api.md)).

---

## 0) Архитектура демо (чтобы не повторять ошибки)

Цель демо — утвердить визуал, поэтому архитектура обязана быть:
- **воспроизводимой** (одни и те же входные данные → один и тот же результат),
- **fixture-first** (без backend),
- **совместимой с реальным API** (те же структуры `snapshot` и `events`).

### 0.1 Слои (рекомендуемая схема)

1) **Фон**: звёздная пыль/туманность (можно `tsparticles`), отдельный слой.
2) **Базовый граф** (Canvas/WebGL): узлы + рёбра, LOD по зуму.
3) **FX overlay** (второй canvas поверх графа): искры Tx, клиринг по маршруту/циклу, вспышки/кольца.
4) **HUD (HTML)**: контролы демо, карточка узла, (опционально) FPS/счётчики.

Примечание из phase1 tech spec: glow/bloom допустим как «атмосфера», но **знак баланса кодируем цветом узла, не внешним свечением**.

### 0.2 Поток данных

- Источник снапшота/событий: `public/simulator-fixtures/...`.
- На входе выполняем валидацию структуры (минимум: `nodes`, `links`, корректные `source/target`).
- События применяются как «оверлеи»:
  - не пересобирать граф полностью на каждый шаг,
  - временные эффекты держать отдельно от базового снапшота,
  - для перерисовки использовать лёгкий `refresh()`.

### 0.3 Детерминизм и fail-fast (обязательное)

- В демо **запрещён** runtime-fallback на случайный граф при ошибке загрузки.
- Нельзя использовать `Math.random()` для решений, влияющих на утверждение визуала (цвет/размер/классы/анимационные тайминги).
- При ошибке fixtures показываем понятную ошибку (где файл/что не так), чтобы быстро чинить пайплайн.

### 0.4 Анти-паттерны (ошибки первой демки — не повторять)

1) Смешивать загрузку данных, рендер, физику, ввод и FX в одном большом компоненте.
2) Вычислять семантику визуализации на клиенте (debt-bins/квантили/классы/толщины/альфы).
3) Генерировать случайные данные при ошибках вместо явного падения.
4) Постоянно пересоздавать граф/симуляцию на каждый клик/событие вместо accessors + `refresh()`.

## 1) Что считаем «быстрой демкой»

Демка должна запускаться командой:
- `cd simulator-ui`
- `npm install`
- `npm run dev`

И открываться в браузере (Vite порт): http://localhost:5176/

**Никаких Docker/Postgres/Qdrant.**

---

## 2) Источник данных (только фикстуры)

### 2.0 Откуда берутся фикстуры (контекст для будущих правок)

В проекте уже есть полный детерминированный пайплайн:

1) **Seed-документы (человекочитаемая спецификация сообщества)**
  - `docs/ru/seeds/*`
  - гайд/воркфлоу: `docs/ru/seeds/README.md`

2) **Детерминированная генерация канонических датасетов**
  - единая точка входа: `admin-fixtures/tools/generate_fixtures.py`
  - конкретные seed-скрипты (используются внутренне):
    - `admin-fixtures/tools/generate_seed_greenfield_village_100.py`
    - `admin-fixtures/tools/generate_seed_riverside_town_50.py`

  Команды (из корня репо):
  - `.\.venv\Scripts\python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100`
  - `.\.venv\Scripts\python.exe admin-fixtures/tools/generate_fixtures.py --seed riverside-town-50`

  Результат: перезапись канонического набора `admin-fixtures/v1/datasets/*.json`.

  Примечание: есть режим “pack”, чтобы не ломать канон случайно:
  - `.\.venv\Scripts\python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100 --pack`
  - `.\.venv\Scripts\python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100 --pack --activate`

3) **Предвычисленные `participants.viz-<EQ>.json` (эмуляция backend-viz для моков)**
  - генератор: `admin-fixtures/tools/generate_participants_viz_datasets.py`
  - читает: `participants.json`, `equivalents.json`, опционально `debts.json`
  - пишет: `admin-fixtures/v1/datasets/participants.viz-<EQ>.json`

  Команда:
  - `.\.venv\Scripts\python.exe admin-fixtures/tools/generate_participants_viz_datasets.py --v1 admin-fixtures/v1`

4) **Синхронизация в Admin UI + валидация**
  - sync: `admin-ui/scripts/sync-fixtures.mjs`
  - validate: `admin-ui/scripts/validate-fixtures.mjs`

  Команды:
  - `cd admin-ui`
  - `npm run sync:fixtures`
  - `npm run validate:fixtures`

Guardrail: `validate-fixtures` держит allow-list `seed_id` (и ожидаемые размеры) — при добавлении нового seed нужно обновлять этот allow-list.

Отдельно: существует генератор “синтетики” для UI-прототипирования (не seed-based):
- `admin-fixtures/tools/generate_admin_fixtures.py` (по умолчанию пишет в `admin-fixtures/v1-synthetic`, в канон писать не рекомендуется)

### 2.1 Источник правды по данным
Используем существующие «канонические» датасеты:
- `admin-fixtures/v1/datasets/participants.viz-<EQ>.json` (узлы уже с `viz_*`)
- `admin-fixtures/v1/datasets/trustlines.json` (рёбра: `from→to`, `limit/used/available/status`)

Плюс опционально (если нужно для демо-лейблов/инфо в карточке):
- `admin-fixtures/v1/datasets/participants.json`
- `admin-fixtures/v1/datasets/debts.json`

**Важно:** направление trustline: `from → to` = creditor → debtor (risk limit), не наоборот.

### 2.2 Что именно кладём в демо
Демо потребляет **готовые snapshot JSON**, соответствующие контракту [docs/ru/simulator/frontend/docs/api.md](../api.md):
- `GraphSnapshot` (nodes/links, `viz_*` ключи)
- события (плейлист) для `tx.updated` / `clearing.plan` / `clearing.done`

То есть в рантайме UI **не собирает граф из сырых датасетов**.

---

## 3) Файловая структура демо-фикстур

Предлагаемая структура (обслуживается Vite как static assets):

- `simulator-ui/public/simulator-fixtures/v1/`
  - `<EQ>/snapshot.json` (пример: `HOUR/snapshot.json`)
  - `<EQ>/events/`
    - `demo-tx.json` (массив событий или JSONL)
    - `demo-clearing.json`
    - `demo-mixed.json`

Где `<EQ>`: `HOUR`, `EUR`, `UAH` (минимум один, предпочтительно `HOUR` — там больше разнообразия по `debt-*`).

---

## 4) Генерация демо-snapshot (офлайн, детерминированно)

### 4.1 Зачем генератор
Чтобы демо было:
- одинаковым у всех,
- без «вычислений на фронте»,
- устойчивым к изменению UI.

### 4.2 Генератор (предложение)
Добавить скрипт (офлайн):
- `admin-fixtures/tools/generate_simulator_demo_snapshots.py`

Вход:
- `admin-fixtures/v1/datasets/participants.viz-<EQ>.json`
- `admin-fixtures/v1/datasets/trustlines.json`

Выход:
- `admin-fixtures/v1/api-snapshots/simulator/<EQ>/snapshot.json`
- (опционально) `admin-fixtures/v1/api-snapshots/simulator/<EQ>/events/*.json`

И затем синк в `simulator-ui/public/...` (как в admin-ui, но отдельной командой).

### 4.3 Правила сборки snapshot
- `nodes`:
  - берём только участников, которые встречаются в trustlines выбранного эквивалента (или top-N по degree).
  - переносим `viz_color_key`, `viz_size`, `net_balance_atoms`, `net_sign` как есть.
- `links`:
  - `source = from`, `target = to`.
  - `trust_limit/used/available/status` копируем.
  - `viz_width_key` и `viz_alpha_key` **вычисляет генератор** (не UI), например:
    - `viz_width_key`: `hairline|thin|mid|thick` по квантилям `limit` или `used`.
    - `viz_alpha_key`: `bg|muted|active|hi` по `status` и/или `used/limit`.

---

## 5) Сценарии демо (минимальный набор для утверждения визуала)

Нужны 4–6 «сцен» (как презентационные пресеты), переключаемые из UI.

### Scene A — Overview (читаемость)
- Эквивалент: `HOUR`
- Цель: в зуме «общая карта» видно кластеры и основные долги.
- Проверяем: фоновые линии не шумят, debtor bins читаются.

### Scene B — Focus (карточка + локальные связи)
- Выбор узла + подсветка соседей.
- Проверяем: типографика карточки и контраст текста/фона.

### Scene C — Statuses
- В snapshot должны быть узлы со статусами `suspended/left/deleted`.
- Проверяем: паттерны/оттенки не путаются с debtor-цветами.

### Scene D — Tx burst (события)
- Плейлист событий `tx.updated` (10–30 событий за 10–20 секунд).
- В плейлисте обязательно должны быть:
  - 2–3 одиночных tx по одному ребру,
  - 1–2 tx по маршруту из нескольких рёбер (multi-hop), чтобы визуальная метафора не «запирала» нас в A→B.
- Проверяем: частицы/подсветка не убивают FPS; событие читается.

### Scene E — Clearing plan
- Один сценарий `clearing.plan` на 3–8 шагов + `clearing.done`.
- Желательно: хотя бы один цикл длиной 4–6 узлов (не только треугольник), чтобы проверить читаемость на «реальном» масштабе.
- Проверяем: ритм и считываемость, overlay снимается корректно.

---

## 6) Требования к UI демо (минимум, но “как продукт”)

### 6.1 Панель демо-контролов
- Выбор `Equivalent` (селект)
- Выбор `Scene` (селект)
- Кнопки:
  - `Play/Pause` (плейлист событий)
  - `Step` (следующее событие)
  - `Reset view` (вернуть дефолтный зум/пан)
- Переключатели:
  - `Labels: off / selection / neighbors` (LOD)
  - `Quality: low/med/high` (dprClamp)

### 6.2 Типографика и текстовые роли
Для всего текста в демо (панель/карточки/подсказки) использовать принципы:
- [docs/ru/admin-ui/typography.md](../../../../admin-ui/typography.md)

(Да, демо не на Element Plus, но роли/масштаб/иерархия должны совпадать.)

---

## 7) Критерии «визуал утверждён» (Definition of Done)

- На `Scene A` в 1–2 уровнях зума:
  - кластеры визуально различимы,
  - линии не превращаются в «шерсть»,
  - debtor bins считываются (не сливаются в один цвет).
- На `Scene C` статусные состояния не путаются с debtor-градиентом.
- На `Scene D/E`:
  - UI держит стабильный рендер без заметных провалов (визуально),
  - overlay/частицы ограничены по количеству,
  - после `clearing.done` эффекты снимаются и сеть возвращается к «базе».

---

## 8) Негативные требования (что НЕ делаем в демке)

- Не подключаем реальный backend.
- Не делаем авторизацию.
- Не делаем реальные расчёты маршрутов/клиринга.
- Не строим сложную аналитику.

---

## 9) Мини-план внедрения (чтобы сделать за 0.5–1 день)

1) Сгенерировать `snapshot.json` для `HOUR` из существующих датасетов (офлайн скриптом).
2) Положить snapshot в `simulator-ui/public/simulator-fixtures/v1/HOUR/snapshot.json`.
3) Добавить в `simulator-ui` режим `DEMO_FIXTURES=1`, который грузит snapshot локально.
4) Добавить 2 плейлиста событий: `demo-tx`, `demo-clearing`.
5) Сделать панель выбора `Equivalent/Scene`.

---

## 10) Примечания по совместимости с текущим кодом `simulator-ui`

Сейчас `simulator-ui` пытается грузить `${VITE_API_BASE_URL}/graph/snapshot` и при ошибке падает в генератор случайного графа.

В демо-режиме предлагается:
- не использовать сетевой API,
- грузить static snapshot из `public/simulator-fixtures/...`.

Это позволит утвердить визуал на «настоящих» (пусть и фикстурных) данных и без нестабильности от рандома.
