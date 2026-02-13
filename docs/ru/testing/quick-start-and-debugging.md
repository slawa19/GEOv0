# GEO v0 — Quick Start: запуск, тестирование, отладка

**Версия:** 1.0  
**Дата:** 2026-02-03  
**Аудитория:** разработчики, Copilot/AI-ассистенты

---

## 1. Структура проекта

```
GEOv0-PROJECT/
├── app/                    # FastAPI backend (Python)
│   ├── api/v1/            # API endpoints
│   ├── core/              # Бизнес-логика (simulator, clearing, routing)
│   ├── db/                # SQLAlchemy models
│   └── schemas/           # Pydantic schemas
├── admin-ui/              # Vue 3 + TypeScript (Admin панель)
├── simulator-ui/          # Vue 3 + TypeScript (Визуализатор сети)
│   └── v2/                # Текущая версия
├── tests/                 # Тесты
│   ├── unit/              # Unit-тесты (pytest)
│   ├── contract/          # OpenAPI contract tests
│   └── e2e/               # E2E тесты
├── admin-fixtures/        # Канонические fixture-данные
├── scripts/               # Скрипты запуска и утилиты
└── docs/ru/               # Документация
```

---

## 2. Запуск проекта

### 2.1 Full Stack (рекомендуемый способ)

```powershell
# Активировать venv (если не активирован)
& .\.venv\Scripts\Activate.ps1

# Запуск всего стека
.\scripts\run_full_stack.ps1 -Action start

# С пересозданием БД
.\scripts\run_full_stack.ps1 -Action start -ResetDb -FixturesCommunity greenfield-village-100
```

После запуска доступны:
| Сервис | URL | Назначение |
|--------|-----|------------|
| Backend API | http://127.0.0.1:18000/docs | Swagger UI |
| Admin UI | http://localhost:5173/ | Админка |
| Simulator UI | http://localhost:5176/?mode=real | Визуализатор |

### 2.2 Только Backend

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 18000 --reload
```

### 2.3 VS Code Tasks

В `.vscode/tasks.json` есть готовые задачи:
- `Full Stack: start [Backend + Admin UI + Simulator UI]`
- `Full Stack: start with DB reset (greenfield)`
- `Full Stack: stop`
- `Full Stack: restart`
- `Full Stack: status`

---

## 3. Тестирование

### 3.1 Unit-тесты

```powershell
# Все тесты
.\.venv\Scripts\python.exe -m pytest tests/unit/ -v

# По ключевому слову
.\.venv\Scripts\python.exe -m pytest tests/unit/ -k "clearing" -v
.\.venv\Scripts\python.exe -m pytest tests/unit/ -k "simulator" -v

# Конкретный файл
.\.venv\Scripts\python.exe -m pytest tests/unit/test_clearing_plan_edges_extraction.py -v

# С coverage
.\.venv\Scripts\python.exe -m pytest tests/unit/ --cov=app --cov-report=term-missing
```

### 3.2 Contract-тесты (OpenAPI)

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_openapi_contract.py -v
```

### 3.3 E2E-тесты (Playwright)

```powershell
cd admin-ui
npx playwright test
```

### 3.4 Валидация fixtures

```powershell
cd admin-ui
npm run validate:fixtures
```

---

## 4. Отладка типичных проблем

### 4.1 Backend не запускается

**Симптом:** `ModuleNotFoundError` или порт занят.

**Решение:**
```powershell
# Проверить, что venv активирован
.\.venv\Scripts\python.exe -c "import app; print('OK')"

# Проверить занятость порта
netstat -ano | findstr :18000

# Убить процесс (если нужно)
taskkill /PID <pid> /F
```

### 4.2 SSE события не приходят

**Симптом:** Simulator UI не обновляется, нет анимаций.

**Диагностика:**
1. Проверить, что backend работает: `http://127.0.0.1:18000/api/v1/health`
2. Открыть DevTools → Network → Filter: `EventStream`
3. Проверить консоль на ошибки

**Частая причина:** Неправильная сериализация Pydantic (см. [pydantic-alias-serialization.md](backend/pydantic-alias-serialization.md)).

### 4.3 Визуализация клиринга не работает

**Симптом:** Клиринг выполняется, но нет FX-эффектов.

**Диагностика:**
```powershell
# Проверить, есть ли циклы в БД
$result = Invoke-WebRequest -Uri "http://127.0.0.1:18000/api/v1/admin/clearing/cycles?equivalent=UAH" `
  -Headers @{"X-Admin-Token"="dev-admin-token-change-me"}
$result.Content | ConvertFrom-Json | Select-Object -First 3
```

**Проверить сериализацию событий:**
```powershell
.\.venv\Scripts\python.exe -c "
from app.schemas.simulator import SimulatorClearingDoneEvent
import json
evt = SimulatorClearingDoneEvent(
  event_id='1', ts='2025-01-01T00:00:00Z', type='clearing.done',
  equivalent='UAH', plan_id='p1', cleared_cycles=1, cleared_amount='10.00',
  cycle_edges=[{'from': 'A', 'to': 'B'}],
)
print(json.dumps(evt.model_dump(mode='json', by_alias=True), indent=2))
"
```

Ожидаемый результат: `"from": "A"` (не `"from_": "A"`).

### 4.4 База данных в невалидном состоянии

**Симптом:** Ошибки constraint violation, inconsistent data.

**Решение:**
```powershell
# Проверка состояния БД
.\.venv\Scripts\python.exe scripts/check_sqlite_db.py

# Пересоздание БД с fixtures
.\scripts\run_full_stack.ps1 -Action start -ResetDb -FixturesCommunity greenfield-village-100
```

### 4.5 Admin API требует авторизацию

**Симптом:** HTTP 401/403 на admin endpoints.

**Решение:** Добавить заголовок `X-Admin-Token`:
```powershell
$headers = @{"X-Admin-Token"="dev-admin-token-change-me"}
Invoke-WebRequest -Uri "http://127.0.0.1:18000/api/v1/admin/..." -Headers $headers
```

Токен задаётся в `app/config.py` → `ADMIN_TOKEN`.

---

## 5. Ключевые конфигурации

### 5.1 Backend (app/config.py)

| Параметр | По умолчанию | Описание |
|----------|--------------|----------|
| `DATABASE_URL` | `sqlite:///./geov0.db` | Путь к БД |
| `ADMIN_TOKEN` | `dev-admin-token-change-me` | Токен для admin API |
| `LOG_LEVEL` | `INFO` | Уровень логов |

### 5.2 Simulator

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `SIMULATOR_CLEARING_POLICY` | `static` | Политика клиринга: `static` (каждые N тиков) или `adaptive` (динамически по сигналам сети) |
| `SIMULATOR_CLEARING_EVERY_N_TICKS` | `25` | Частота клиринга |
| `SIMULATOR_REAL_AMOUNT_CAP` | — | Лимит суммы транзакции |
| `SIMULATOR_REAL_ENABLE_INJECT` | — | Включить inject-режим |

### 5.3 Frontend (.env.local)

```
VITE_API_BASE_URL=http://127.0.0.1:18000
VITE_SIMULATOR_MODE=real
```

---

## 6. Полезные скрипты

| Скрипт | Назначение |
|--------|------------|
| `scripts/run_full_stack.ps1` | Управление всем стеком |
| `scripts/check_sqlite_db.py` | Диагностика БД |
| `scripts/seed_db.py` | Загрузка seed-данных |
| `scripts/init_sqlite_db.py` | Инициализация пустой БД |
| `scripts/cleanup_simulator_runs.py` | Очистка старых runs |

---

## 7. Часто используемые API endpoints

### Health check
```
GET /api/v1/health
```

### Participants
```
GET /api/v1/participants
GET /api/v1/participants/{pid}
```

### Trustlines
```
GET /api/v1/trustlines?equivalent=UAH
```

### Debts
```
GET /api/v1/debts?equivalent=UAH
```

### Clearing (admin)
```
GET /api/v1/admin/clearing/cycles?equivalent=UAH
Headers: X-Admin-Token: dev-admin-token-change-me
```

### Simulator
```
POST /api/v1/simulator/runs
GET /api/v1/simulator/runs/{run_id}
POST /api/v1/simulator/runs/{run_id}/start
GET /api/v1/simulator/runs/{run_id}/events (SSE)
```

---

## 8. Связанные документы

- [Pydantic Alias Serialization](backend/pydantic-alias-serialization.md) — критическая ошибка
- [Testing Framework](../10-testing-framework.md) — полная спецификация тестов
- [API Reference](../04-api-reference.md) — описание всех endpoints
- [Config Reference](../config-reference.md) — все параметры конфигурации
