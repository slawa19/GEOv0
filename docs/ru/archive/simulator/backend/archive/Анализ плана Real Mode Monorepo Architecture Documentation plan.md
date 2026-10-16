# ARCHIVED: Анализ плана Real Mode Monorepo Architecture

Этот документ перенесён в архив, чтобы не отвлекать от реализации.

Актуальный вход для разработки:
- `docs/ru/simulator/backend/pre-implementation-readiness-report.md`

Оригинальное содержимое сохранено ниже без правок.

---

## Анализ плана Real Mode Monorepo Architecture

Изучив план и связанные спецификации, вот мой анализ:

---

### 1. Чего НЕ ХВАТАЕТ для начала реализации

#### 🔴 Критические пробелы:

| Недостающий артефакт                                  | Почему важен                                                 | Рекомендация                                        |
| ----------------------------------------------------- | ------------------------------------------------------------ | --------------------------------------------------- |
| **JSON Schema для сценария симулятора**               | В спеке есть пример формата (раздел 7.2), но нет формальной JSON Schema для валидации | Создать `fixtures/simulator/scenario.schema.json`   |
| **Контракт API Simulator Backend**                    | В плане есть список endpoints (раздел 2.2), но нет детальных request/response schemas | Добавить в `api/openapi.yaml` секцию `/simulator/*` |
| **Формат WebSocket/SSE сообщений**                    | Решено использовать WebSocket, но нет детального контракта событий | Описать типы сообщений (pause/resume/tick/event)    |
| **Mapping существующих fixtures → формат симулятора** | Текущие fixtures (participants.json, trustlines.json) не содержат `behaviorProfiles`, `groups`, `events` | Доработать скрипт-конвертер                         |

#### 🟠 Средние пробелы:

- **Нет описания PaymentEngine интеграции** — как SimulationRunner будет вызывать реальный API платежей
- **Нет описания физической модели "tick"** — что происходит за один tick симуляции
- **Нет метрик для мониторинга** — какие Prometheus метрики экспортировать

---

### 2. Какие спецификации взяты за основу

**Основные документы:**

| Документ                                    | Что описывает                                               | Уровень детализации            |
| ------------------------------------------- | ----------------------------------------------------------- | ------------------------------ |
| `GEO-community-simulator-application.md`    | Backend логика: сценарии, поведение, метрики, AI-интеграция | 🟢 Высокий (но без JSON Schema) |
| `GEO-game-interface-spec.md`                | Frontend UI: визуализация, производительность, компоненты   | 🟢 Высокий                      |
| `live-physics-layout-spec.md`               | Physics engine для графа (d3-force)                         | 🟢 Высокий                      |
| `api.md` (simulator frontend)               | Контракт snapshot/events для UI                             | 🟢 Высокий                      |
| `real-mode-monorepo-architecture.md` (план) | Общая архитектура и решения                                 | 🟡 Средний (roadmap уровень)    |

---

### 3. Достаточно ли спецификации детальные?

**По областям:**

| Область                     | Детализация    | Комментарий                                           |
| --------------------------- | -------------- | ----------------------------------------------------- |
| **UI визуализация**         | ✅ Достаточно   | Есть палитры, LOD, FX playbook, примеры кода          |
| **Формат сценария**         | ⚠️ Частично     | Есть JSON примеры, но нет формальной JSON Schema      |
| **Backend API**             | ❌ Недостаточно | Только список endpoints, нет request/response schemas |
| **SimulationRunner логика** | ⚠️ Частично     | Описана концепция tick-based, но нет алгоритма        |
| **WebSocket протокол**      | ❌ Недостаточно | Выбран как решение, но формат сообщений не описан     |
| **Интеграция с GEO Core**   | ❌ Недостаточно | Упоминается "через API", но нет детальной схемы       |

---

### 4. Рекомендуемая последовательность действий

```
┌─────────────────────────────────────────────────────────────────┐
│  ФАЗА 0: ПОДГОТОВКА (2-3 дня)                                   │
├─────────────────────────────────────────────────────────────────┤
│  0.1. Создать JSON Schema для формата сценария                  │
│  0.2. Дополнить OpenAPI спецификацию endpoints симулятора       │
│  0.3. Описать WebSocket протокол (типы сообщений)               │
│  0.4. Создать конвертер fixtures → simulator scenario           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ФАЗА 1: SIMULATOR BACKEND MVP (5-7 дней)                       │
├─────────────────────────────────────────────────────────────────┤
│  1.1. Создать app/core/simulator/ модуль                        │
│       - scenarios.py (загрузка/валидация JSON)                  │
│       - runner.py (tick-based движок)                           │
│       - events.py (генерация событий)                           │
│  1.2. Добавить REST API endpoints                               │
│       - POST /simulator/scenarios (загрузить сценарий)          │
│       - POST /simulator/runs (запустить симуляцию)              │
│       - GET/POST для управления (pause/resume/stop)             │
│  1.3. Добавить WebSocket endpoint для событий                   │
│  1.4. Интеграция с PaymentEngine (вызов реальных платежей)      │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ФАЗА 2: SIMULATOR UI REAL MODE (3-5 дней)                      │
├─────────────────────────────────────────────────────────────────┤
│  2.1. Добавить src/api/ слой (apiMode: mock | real)             │
│       - realApi.ts (WebSocket клиент)                           │
│       - mockApi.ts (существующие fixtures)                      │
│  2.2. Рефакторинг useDemoPlayer для live режима                 │
│  2.3. Добавить HUD для управления симуляцией                    │
│       - интенсивность (слайдер 0-100%)                          │
│       - pause/resume/stop                                       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│  ФАЗА 3: ИНТЕГРАЦИЯ И ТЕСТИРОВАНИЕ (3-5 дней)                   │
├─────────────────────────────────────────────────────────────────┤
│  3.1. E2E тесты (backend ↔ frontend)                            │
│  3.2. Нагрузочное тестирование (100+ узлов)                     │
│  3.3. Документация и примеры сценариев                          │
└─────────────────────────────────────────────────────────────────┘
```

---

### 5. Критерии приёмки (Acceptance Criteria)

#### ФАЗА 0: Подготовка
- [ ] JSON Schema `scenario.schema.json` валидирует пример из спеки
- [ ] OpenAPI spec содержит все `/simulator/*` endpoints с request/response schemas
- [ ] WebSocket message types описаны в отдельном документе
- [ ] Конвертер `generate_simulator_scenario.py` создаёт валидный сценарий из greenfield-village-100

#### ФАЗА 1: Backend MVP
- [ ] `POST /simulator/scenarios` загружает JSON и валидирует по схеме
- [ ] `POST /simulator/runs` запускает симуляцию с `run_id`
- [ ] `GET /simulator/runs/{id}` возвращает статус (running/paused/stopped)
- [ ] `POST /simulator/runs/{id}/pause|resume|stop` управляют симуляцией
- [ ] WebSocket `/simulator/runs/{id}/ws` стримит события (`tx.started`, `tx.completed`, `clearing.plan`)
- [ ] Один tick генерирует 0-N событий на основе `behaviorProfiles`
- [ ] Интеграция: симуляция вызывает реальный `POST /api/v1/payments`

#### ФАЗА 2: UI Real Mode
- [ ] `apiMode: 'real'` подключается к WebSocket backend
- [ ] `apiMode: 'mock'` работает как раньше (fixtures)
- [ ] HUD показывает статус симуляции (tick, метрики)
- [ ] Слайдер интенсивности меняет скорость tick'ов
- [ ] FX визуализирует события в реальном времени

#### ФАЗА 3: Интеграция
- [ ] E2E тест: запуск сценария → получение событий → визуализация в UI
- [ ] Нагрузка: 100 участников, 300 trustlines, 60 FPS
- [ ] README с примерами запуска

---

### Резюме: Что делать дальше?

**Перед началом разработки рекомендую:**

1. **Создать недостающие спецификации** (Фаза 0) — это займёт 2-3 дня, но сэкономит время на исправление несовместимостей
2. **Начать с Backend** (Фаза 1) — UI уже работает в mock режиме, backend критичнее
3. **Использовать существующие fixtures** — конвертер позволит не создавать данные с нуля

Хотите, чтобы я детализировал какую-либо из этих областей? Или начнём с создания JSON Schema для формата сценария?



## План работы с документацией

### 📝 ДОПОЛНИТЬ существующие документы

| Документ                                     | Что добавить                                                 | Приоритет  |
| -------------------------------------------- | ------------------------------------------------------------ | ---------- |
| **`api/openapi.yaml`**                       | Секция `/api/v1/simulator/*` со всеми endpoints (scenarios, runs, events) — request/response schemas | 🔴 Критично |
| **`GEO-community-simulator-application.md`** | JSON Schema для формата сценария (вынести в отдельный файл + ссылка) | 🔴 Критично |
| **`real-mode-monorepo-architecture.md`**     | Добавить критерии приёмки (Acceptance Criteria) для каждой фазы | 🟠 Важно    |

---

### 📄 СОЗДАТЬ новые документы

#### 1. **`docs/ru/simulator/backend/scenario-schema.json`** 🔴
JSON Schema для валидации сценариев симулятора.

```
Содержание:
- participants (required): массив с id, name, type, groupId, behaviorProfileId
- trustlines (required): массив с from, to, limit
- behaviorProfiles (required): массив профилей поведения
- groups (optional): массив групп/кластеров
- events (optional): запланированные события (стресс-сценарии)
```

---

#### 2. **`docs/ru/simulator/backend/websocket-protocol.md`** 🔴
Протокол WebSocket для стриминга событий.

```
Содержание:
- Типы сообщений: Client → Server
  - { type: "pause" }
  - { type: "resume" }
  - { type: "set_intensity", value: 0.5 }
  - { type: "trigger_action", participantId, action }

- Типы сообщений: Server → Client
  - { type: "tick", tick: 123, metrics: {...} }
  - { type: "tx.started", ... }
  - { type: "tx.completed", ... }
  - { type: "clearing.plan", ... }
  - { type: "error", message: "..." }
  - { type: "run_status", status: "running|paused|stopped" }

- Heartbeat/reconnect логика
- Формат ошибок
```

---

#### 3. **`docs/ru/simulator/backend/runner-algorithm.md`** 🟠
Алгоритм работы SimulationRunner.

```
Содержание:
- Что такое "tick" и его длительность
- Порядок обработки behaviorProfiles за один tick
- Как генерируются события (платежи, изменения trustlines)
- Интеграция с PaymentEngine (вызов реального API)
- Обработка ошибок и retry
- Формула интенсивности (intensity → events per tick)
```

---

#### 4. **`docs/ru/simulator/backend/api-examples.md`** 🟡
Примеры использования API.

```
Содержание:
- Пример: загрузить сценарий (curl)
- Пример: запустить симуляцию
- Пример: подключиться к WebSocket и получить события
- Пример: изменить интенсивность на лету
- Пример: остановить симуляцию и получить summary
```

---

#### 5. **`fixtures/simulator/greenfield-village-100/scenario.json`** 🔴
Первый готовый сценарий (конвертированный из admin-fixtures).

```
Содержание:
- participants: 100 участников из admin-fixtures + groupId + behaviorProfileId
- trustlines: связи из admin-fixtures
- behaviorProfiles: 3-5 базовых профилей (normal_consumer, hoarder, panic_prone, merchant)
- groups: 3-4 группы (по типам или "районам")
- events: пустой массив (стресс-сценарии добавить позже)
```

---

#### 6. **`admin-fixtures/tools/generate_simulator_scenario.py`** 🔴
Скрипт-конвертер.

```
Содержание:
- Читает participants.json + trustlines.json из admin-fixtures
- Генерирует behaviorProfiles на основе типа участника
- Автоматически группирует участников (по типу или случайно)
- Выводит scenario.json в формате симулятора
- Валидирует результат по JSON Schema
```

---

### 📊 Итоговая структура документации

```
docs/ru/simulator/
├── README.md                    # (существует)
├── frontend/
│   └── docs/
│       ├── api.md               # (существует) — контракт snapshot/events для UI
│       ├── GEO-community-simulator-application.md  # (существует) ← ДОПОЛНИТЬ
│       └── specs/
│           └── ...              # (существуют)
│
└── backend/                     # ← НОВАЯ ПАПКА
    ├── scenario-schema.json     # ← СОЗДАТЬ (JSON Schema)
    ├── websocket-protocol.md    # ← СОЗДАТЬ
    ├── runner-algorithm.md      # ← СОЗДАТЬ
    └── api-examples.md          # ← СОЗДАТЬ

fixtures/simulator/              # ← НОВАЯ ПАПКА
├── greenfield-village-100/
│   └── scenario.json            # ← СОЗДАТЬ (первый сценарий)
└── scenario.schema.json         # ← СОЗДАТЬ (или в docs/ru/simulator/backend/)

api/
└── openapi.yaml                 # (существует) ← ДОПОЛНИТЬ секцией /simulator/*
```

---

### Порядок создания

| #    | Документ                               | Зависимости       | Время |
| ---- | -------------------------------------- | ----------------- | ----- |
| 1    | `scenario-schema.json`                 | —                 | 2-3 ч |
| 2    | `websocket-protocol.md`                | —                 | 2-3 ч |
| 3    | `openapi.yaml` (секция simulator)      | schema, websocket | 3-4 ч |
| 4    | `runner-algorithm.md`                  | schema            | 2 ч   |
| 5    | `generate_simulator_scenario.py`       | schema            | 3-4 ч |
| 6    | `greenfield-village-100/scenario.json` | скрипт конвертер  | 1 ч   |
| 7    | `api-examples.md`                      | openapi           | 1-2 ч |

**Итого: ~15-20 часов работы перед началом кодирования.**

---

## Ответ на вопросы по Simulator Backend

### 1. Есть ли отдельная спецификация на Simulator Backend?

**❌ НЕТ** — отдельной спецификации на Simulator Backend **не существует**.

**Что есть сейчас:**
- `GEO-community-simulator-application.md` — описывает **концепцию** (цели, поведение, AI-интеграция), но:
  - Нет API schemas (request/response)
  - Нет детального алгоритма SimulationRunner
  - Нет WebSocket протокола
  - Нет интеграции с существующим `app/core/payments/`

- `real-mode-monorepo-architecture.md` — описывает **план** (где создать файлы, какие endpoints), но:
  - Нет формальных критериев приёмки
  - Нет описания тестовой стратегии
  - Нет smoke test сценариев

---

### 2. Критерии приёмки Simulator Backend (предложение)

Вот что я предлагаю как формальные критерии:

#### Функциональные критерии:

| #         | Критерий                                                     | Метод проверки                 |
| --------- | ------------------------------------------------------------ | ------------------------------ |
| **SB-01** | `POST /api/v1/simulator/scenarios` принимает JSON сценарий и валидирует по JSON Schema | Unit test + Contract test      |
| **SB-02** | `POST /api/v1/simulator/runs` создаёт симуляцию и возвращает `run_id` | Unit test                      |
| **SB-03** | `GET /api/v1/simulator/runs/{id}` возвращает текущий статус (running/paused/stopped/completed) | Unit test                      |
| **SB-04** | `POST /api/v1/simulator/runs/{id}/pause` приостанавливает tick-цикл | Integration test               |
| **SB-05** | `POST /api/v1/simulator/runs/{id}/resume` возобновляет tick-цикл | Integration test               |
| **SB-06** | `POST /api/v1/simulator/runs/{id}/stop` останавливает симуляцию и освобождает ресурсы | Integration test               |
| **SB-07** | WebSocket `/api/v1/simulator/runs/{id}/ws` стримит события (`tick`, `tx.*`, `clearing.*`) | Integration test               |
| **SB-08** | Один tick генерирует события согласно `behaviorProfiles` сценария | Unit test (детерминизм с seed) |
| **SB-09** | Симулятор вызывает реальный PaymentEngine для платежей (не mock) | Integration test               |
| **SB-10** | При ошибке PaymentEngine возвращает событие `tx.failed` с причиной | Unit test                      |

#### Нефункциональные критерии:

| #            | Критерий                                                | Метод проверки     |
| ------------ | ------------------------------------------------------- | ------------------ |
| **SB-NF-01** | 100 участников, 300 trustlines — tick < 100ms           | Performance test   |
| **SB-NF-02** | WebSocket держит 10 одновременных подключений           | Load test          |
| **SB-NF-03** | Память не растёт при 1000+ тиках (нет утечек)           | Soak test (10 мин) |
| **SB-NF-04** | Воспроизводимость: одинаковый seed → одинаковые события | Unit test          |

---

### 3. Как это будет покрыто тестами?

```
tests/
├── unit/
│   └── simulator/
│       ├── test_scenario_loader.py      # SB-01: валидация JSON
│       ├── test_runner_tick.py          # SB-08: генерация событий
│       ├── test_runner_determinism.py   # SB-NF-04: воспроизводимость
│       └── test_behavior_profiles.py    # правила поведения
│
├── integration/
│   └── simulator/
│       ├── test_simulator_api.py        # SB-02..SB-06: REST API
│       ├── test_simulator_websocket.py  # SB-07: WebSocket стриминг
│       ├── test_payment_integration.py  # SB-09, SB-10: PaymentEngine
│       └── test_full_scenario_run.py    # E2E прогон сценария
│
├── contract/
│   └── simulator/
│       └── test_scenario_schema.py      # JSON Schema валидация
│
└── performance/
    └── simulator/
        ├── test_tick_latency.py         # SB-NF-01
        └── test_websocket_load.py       # SB-NF-02
```

**Примерный unit test (SB-08):**

```python
# tests/unit/simulator/test_runner_tick.py
import pytest
from app.core.simulator.runner import SimulationRunner
from app.core.simulator.scenarios import load_scenario

@pytest.fixture
def simple_scenario():
    return load_scenario("fixtures/simulator/test-scenario.json")

def test_tick_generates_events_based_on_behavior(simple_scenario):
    runner = SimulationRunner(scenario=simple_scenario, seed=42)
    
    events = runner.tick()
    
    # С seed=42 и normal_consumer профилем должен быть 1 платёж
    assert len(events) == 1
    assert events[0]["type"] == "tx.started"

def test_determinism_with_same_seed(simple_scenario):
    runner1 = SimulationRunner(scenario=simple_scenario, seed=12345)
    runner2 = SimulationRunner(scenario=simple_scenario, seed=12345)
    
    events1 = [runner1.tick() for _ in range(10)]
    events2 = [runner2.tick() for _ in range(10)]
    
    assert events1 == events2  # SB-NF-04
```

---

### 4. Smoke Test со стороны UI

**Цель:** Проверить что UI может подключиться к backend и получить события.

#### Сценарий Smoke Test:

```gherkin
Feature: Simulator UI ↔ Backend Smoke Test

  Scenario: UI получает события от работающей симуляции
    Given Simulator Backend запущен на localhost:18000
    And Simulator UI запущен на localhost:5174
    And Загружен сценарий "greenfield-village-100"
    
    When Пользователь открывает UI в режиме apiMode=real
    And Нажимает "Start Simulation"
    
    Then UI подключается к WebSocket /api/v1/simulator/runs/{id}/ws
    And В течение 5 секунд UI получает минимум 3 события типа "tick"
    And В течение 30 секунд UI получает минимум 1 событие типа "tx.started"
    And Граф отображает изменения (FX эффекты на рёбрах)
    And HUD показывает текущий tick и метрики
    
    When Пользователь нажимает "Pause"
    
    Then UI отправляет {type: "pause"} через WebSocket
    And События "tick" прекращаются
    And Статус симуляции = "paused"
```

#### Реализация в Playwright (e2e):

```typescript
// simulator-ui/v2/e2e/smoke-real-mode.spec.ts
import { test, expect } from '@playwright/test';

test.describe('Simulator Smoke Test (Real Mode)', () => {
  test.skip(
    process.env.SKIP_REAL_API === 'true',
    'Skipping real API tests'
  );

  test('UI connects to backend and receives events', async ({ page }) => {
    // 1. Открыть UI в real mode
    await page.goto('http://localhost:5174/?apiMode=real');
    
    // 2. Дождаться загрузки сценария
    await expect(page.locator('[data-testid="scenario-loaded"]')).toBeVisible({ timeout: 10000 });
    
    // 3. Запустить симуляцию
    await page.click('[data-testid="start-simulation"]');
    
    // 4. Проверить что WebSocket подключён
    await expect(page.locator('[data-testid="ws-status"]')).toHaveText('connected');
    
    // 5. Дождаться tick событий
    await expect(page.locator('[data-testid="tick-counter"]')).not.toHaveText('0', { timeout: 5000 });
    
    // 6. Проверить что граф обновляется (FX)
    const fxCount = await page.locator('[data-testid="active-fx-count"]').textContent();
    expect(parseInt(fxCount || '0')).toBeGreaterThan(0);
    
    // 7. Пауза
    await page.click('[data-testid="pause-simulation"]');
    await expect(page.locator('[data-testid="simulation-status"]')).toHaveText('paused');
  });
});
```

#### Smoke Test матрица:

| Компонент     | Что проверяем      | Ожидаемый результат          |
| ------------- | ------------------ | ---------------------------- |
| **REST API**  | POST /scenarios    | 201 Created, scenario_id     |
| **REST API**  | POST /runs         | 201 Created, run_id          |
| **REST API**  | GET /runs/{id}     | 200, status: running         |
| **WebSocket** | Connect            | Connection established       |
| **WebSocket** | Receive tick       | {type: "tick", tick: N}      |
| **WebSocket** | Receive tx.started | Event с source/target        |
| **UI**        | Graph FX           | Визуальные эффекты на рёбрах |
| **UI**        | HUD update         | Tick counter увеличивается   |
| **UI**        | Pause              | Симуляция останавливается    |

---

### 5. Резюме: Что нужно создать

| Документ                                           | Статус    | Что содержит                               |
| -------------------------------------------------- | --------- | ------------------------------------------ |
| `docs/ru/simulator/backend/spec.md`                | ❌ Создать | Полная спецификация Simulator Backend      |
| `docs/ru/simulator/backend/acceptance-criteria.md` | ❌ Создать | Формальные критерии приёмки (таблица выше) |
| `docs/ru/simulator/backend/test-plan.md`           | ❌ Создать | Структура тестов, smoke test сценарии      |

Хотите, чтобы я подготовил черновик одного из этих документов? Если переключитесь в **Act Mode**, могу начать с `spec.md` или `acceptance-criteria.md`.
