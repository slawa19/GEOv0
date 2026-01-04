# План разработки GEO Hub Backend MVP v0.1 (Объединённый)

> Объединяет лучшее из двух вариантов плана с детальной структурой и конкретными задачами.

---

## Конечный результат MVP

После завершения разработки будет:

### Работающий Backend:
- **REST API** — полная реализация OpenAPI спецификации (`api/openapi.yaml`, ~15 эндпоинтов)
- **Challenge-Response Auth** — Ed25519 + JWT аутентификация
- **Платежи через кредитные сети** — BFS/k-shortest маршрутизация, 2PC транзакции
- **Автоклиринг** — поиск замкнутых циклов долгов, упрощение
- **Seed-данные** — демо-сообщество для тестирования (`seeds/*.json`)

### Инфраструктура:
- **Docker Compose** — один `docker compose up` для запуска всего стека
- **PostgreSQL** — с миграциями и seed-данными
- **Redis** — кэш и prepare-локи для 2PC
- **Health checks** — мониторинг готовности сервисов

### Качество:
- **~80% покрытие тестами** ключевых сценариев
- **OpenAPI соответствие** — автогенерация клиентов возможна
- **Документация** — README с quickstart, переменные окружения

---

## Структура каталогов (финальная)

```
GEOv0-PROJECT/
├── app/                          # Основной код приложения
│   ├── __init__.py
│   ├── main.py                   # FastAPI entry point
│   ├── config.py                 # Pydantic Settings (загрузка из env)
│   │
│   ├── api/                      # HTTP слой
│   │   ├── __init__.py
│   │   ├── deps.py               # Dependency injection (get_db, get_current_user)
│   │   ├── router.py             # Главный роутер (подключает v1)
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── auth.py           # POST /auth/challenge, /auth/login
│   │       ├── participants.py   # GET/POST /participants, GET /participants/{pid}
│   │       ├── trustlines.py     # CRUD /trustlines, /trustlines/{id}
│   │       ├── payments.py       # /payments, /payments/capacity, /payments/max-flow
│   │       └── balance.py        # GET /balance, /balance/debts
│   │
│   ├── core/                     # Бизнес-логика (domain services)
│   │   ├── __init__.py
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── service.py        # AuthService: challenge, verify, issue_jwt
│   │   │   └── crypto.py         # Ed25519 sign/verify (PyNaCl)
│   │   ├── participants/
│   │   │   ├── __init__.py
│   │   │   └── service.py        # ParticipantService: CRUD операции
│   │   ├── trustlines/
│   │   │   ├── __init__.py
│   │   │   └── service.py        # TrustLineService: create, update, close
│   │   ├── payments/
│   │   │   ├── __init__.py
│   │   │   ├── service.py        # PaymentService: create, execute
│   │   │   ├── router.py         # RoutingService: find_paths, max_flow, capacity
│   │   │   └── engine.py         # PaymentEngine: 2PC state machine
│   │   ├── clearing/
│   │   │   ├── __init__.py
│   │   │   └── service.py        # ClearingService: find_cycles, execute
│   │   └── balance/
│   │       ├── __init__.py
│   │       └── service.py        # BalanceService: aggregate balances
│   │
│   ├── db/                       # Слой данных (ORM)
│   │   ├── __init__.py
│   │   ├── session.py            # AsyncEngine + AsyncSession factory
│   │   ├── base.py               # Base model class
│   │   └── models/               # SQLAlchemy модели (из миграций)
│   │       ├── __init__.py       # Экспорт всех моделей
│   │       ├── participant.py    # Participant
│   │       ├── equivalent.py     # Equivalent
│   │       ├── trustline.py      # TrustLine
│   │       ├── debt.py           # Debt
│   │       ├── transaction.py    # Transaction
│   │       ├── prepare_lock.py   # PrepareLock
│   │       ├── auth_challenge.py # AuthChallenge
│   │       ├── audit_log.py      # AuditLog
│   │       ├── integrity_checkpoint.py # IntegrityCheckpoint
│   │       └── config.py         # Config (runtime)
│   │
│   ├── schemas/                  # Pydantic модели (API contracts)
│   │   ├── __init__.py
│   │   ├── common.py             # ErrorEnvelope, Pagination
│   │   ├── auth.py               # ChallengeRequest/Response, LoginRequest, TokenPair
│   │   ├── participant.py        # Participant, ParticipantCreate, ParticipantsList
│   │   ├── trustline.py          # TrustLine, TrustLineCreate/Update, TrustLinesList
│   │   ├── payment.py            # PaymentCreate, PaymentResult, CapacityResponse, MaxFlowResponse
│   │   └── balance.py            # BalanceSummary, DebtsDetails
│   │
│   └── utils/                    # Утилиты
│       ├── __init__.py
│       ├── exceptions.py         # Кастомные исключения (AppException, NotFoundError, etc.)
│       └── security.py           # JWT encode/decode helpers
│
├── tests/                        # Тесты
│   ├── __init__.py
│   ├── conftest.py               # Fixtures: test_db, test_client, auth_headers
│   ├── unit/                     # Unit тесты (изолированные)
│   │   ├── __init__.py
│   │   ├── test_auth_service.py
│   │   ├── test_routing.py
│   │   └── test_payment_engine.py
│   └── integration/              # Интеграционные тесты (API + DB)
│       ├── __init__.py
│       ├── test_auth_api.py
│       ├── test_participants_api.py
│       ├── test_trustlines_api.py
│       ├── test_payments_api.py
│       └── test_scenarios.py     # E2E сценарии из docs/ru/08-test-scenarios.md
│
├── migrations/                   # Alembic миграции (уже есть!)
│   ├── alembic.ini
│   ├── env.py                    # Адаптировать для async
│   └── versions/
│       └── 001_initial_schema.py # Полная схема БД
│
├── seeds/                        # Seed-данные (уже есть!)
│   ├── equivalents.json
│   ├── participants.json
│   └── trustlines.json
│
├── scripts/                      # Вспомогательные скрипты
│   ├── seed_db.py                # Загрузка seed-данных в БД
│   └── generate_keypair.py       # Генерация Ed25519 ключевой пары
│
├── docker/
│   ├── Dockerfile                # Multi-stage build для app
│   └── docker-entrypoint.sh      # Миграции + seed + uvicorn
│
├── api/
│   └── openapi.yaml              # OpenAPI спецификация (уже есть!)
│
├── docs/                         # Документация (уже есть!)
│
├── plans/                        # Планы разработки
│   └── geo_backend_mvp_detailed.md  # Этот файл
│
├── docker-compose.yml            # PostgreSQL + Redis + App
├── requirements.txt              # Основные зависимости
├── requirements-dev.txt          # Dev зависимости
├── pyproject.toml                # Настройки black, ruff, pytest
├── .env.example                  # Пример переменных окружения
└── README.md                     # Обновить с quickstart
```

---

## Этапы разработки

### Этап 1: Инициализация и Инфраструктура (0.5 дня)
**Цель:** Подготовить проект к разработке, настроить окружение.

**Источники:** требования из `docs/ru/concept/Проект GEO — 8. Требования к стеку.md`

- [ ] **Структура проекта**
  - Создать структуру директорий (`app/`, `tests/`, `docker/`, `scripts/`)
  - Создать `__init__.py` во всех пакетах

- [ ] **Зависимости**
  - `requirements.txt`:
    ```
    fastapi==0.109.0
    uvicorn[standard]==0.27.0
    sqlalchemy[asyncio]==2.0.25
    asyncpg==0.29.0
    alembic==1.13.1
    pydantic==2.5.3
    pydantic-settings==2.1.0
    pyjwt==2.8.0
    redis==5.0.1
    pynacl==1.5.0
    httpx==0.26.0
    ```
  - `requirements-dev.txt`:
    ```
    pytest==7.4.4
    pytest-asyncio==0.23.3
    pytest-cov==4.1.0
    black==24.1.1
    ruff==0.1.14
    ```
  - `pyproject.toml` для настроек инструментов (black, ruff, pytest)

- [ ] **Конфигурация**
  - `app/config.py` — Pydantic Settings с загрузкой из переменных окружения
  - `.env.example` — шаблон переменных
  - Настройка логирования (structlog или стандартный logging)

- [ ] **Docker окружение**
  - Обновить `docker-compose.yml`: добавить `db` (Postgres), `redis`, `app`
  - Создать `docker/Dockerfile` (multi-stage build)
  - Создать `docker/docker-entrypoint.sh` (миграции → seed → uvicorn)
  - Проверить: `docker compose up` → контейнеры работают

- [ ] **Базовый FastAPI app**
  - `app/main.py` — FastAPI с healthcheck endpoint
  - Проверка: `GET /health` → 200 OK

---

### Этап 2: Слой данных (Data Layer) (1 день)
**Цель:** Обеспечить работу с базой данных через ORM, используя существующую схему.

**Источники:** `migrations/versions/001_initial_schema.py`

- [ ] **Настройка SQLAlchemy**
  - `app/db/session.py` — AsyncEngine + async_sessionmaker
  - `app/db/base.py` — Base class с общими полями (id, created_at, updated_at)

- [ ] **ORM Модели**
  - Перенести структуру из миграций в SQLAlchemy модели:
    - `app/db/models/equivalent.py` — Equivalent
    - `app/db/models/participant.py` — Participant
    - `app/db/models/trustline.py` — TrustLine
    - `app/db/models/debt.py` — Debt
    - `app/db/models/transaction.py` — Transaction
    - `app/db/models/prepare_lock.py` — PrepareLock
    - `app/db/models/auth_challenge.py` — AuthChallenge
    - `app/db/models/audit_log.py` — AuditLog
    - `app/db/models/integrity_checkpoint.py` — IntegrityCheckpoint
    - `app/db/models/config.py` — Config
  - `app/db/models/__init__.py` — экспорт всех моделей

- [ ] **Интеграция миграций**
  - Адаптировать `migrations/env.py` для async драйвера (asyncpg)
  - Проверить: `alembic upgrade head` работает корректно

- [ ] **Seed-данные**
  - `scripts/seed_db.py` — загрузка JSON из `seeds/` → DB
  - Проверка: после `seed_db.py` данные в таблицах

---

### Этап 3: Аутентификация и Участники (Auth & Participants) (1 день)
**Цель:** Реализовать регистрацию и challenge-response аутентификацию.

**Источники:** `api/openapi.yaml` (Auth, Participants), `seeds/participants.json`

- [ ] **Pydantic Схемы**
  - `app/schemas/common.py` — ErrorEnvelope, SignedRequest
  - `app/schemas/auth.py` — ChallengeRequest, ChallengeResponse, LoginRequest, TokenPair
  - `app/schemas/participant.py` — Participant, ParticipantCreateRequest, ParticipantsList

- [ ] **Криптография**
  - `app/core/auth/crypto.py`:
    - `verify_signature(public_key, message, signature)` — Ed25519 проверка (PyNaCl)
    - `generate_keypair()` — для тестов и скриптов

- [ ] **Auth Service**
  - `app/core/auth/service.py`:
    - `create_challenge(pid)` → создать random challenge, сохранить в auth_challenges, вернуть
    - `verify_challenge(pid, challenge, signature)` → проверить подпись public_key участника
    - `issue_tokens(pid)` → JWT access_token (15 min) + refresh_token (7 days)

- [ ] **Auth API**
  - `app/api/v1/auth.py`:
    - `POST /auth/challenge` — запрос challenge
    - `POST /auth/login` — верификация + выдача токенов

- [ ] **JWT Middleware**
  - `app/utils/security.py` — encode_jwt, decode_jwt
  - `app/api/deps.py`:
    - `get_db()` — dependency для AsyncSession
    - `get_current_participant()` — извлечение из JWT, проверка статуса

- [ ] **Participants Service**
  - `app/core/participants/service.py`:
    - `create(display_name, public_key, type)` — регистрация, pid = hash(public_key)
    - `get_by_pid(pid)` → Participant или None
    - `search(q, type, limit)` → List[Participant]

- [ ] **Participants API**
  - `app/api/v1/participants.py`:
    - `POST /participants` — регистрация (без auth)
    - `GET /participants` — поиск (с auth)
    - `GET /participants/{pid}` — детали (с auth)

- [ ] **Тесты**
  - `tests/integration/test_auth_api.py`
  - `tests/integration/test_participants_api.py`

---

### Этап 4: Линии доверия (TrustLines) (1 день)
**Цель:** Реализовать управление линиями доверия.

**Источники:** `api/openapi.yaml` (TrustLines), `seeds/trustlines.json`

- [ ] **Pydantic Схемы**
  - `app/schemas/trustline.py` — TrustLine, TrustLineCreateRequest, TrustLineUpdateRequest, TrustLinesList

- [ ] **TrustLines Service**
  - `app/core/trustlines/service.py`:
    - `create(from_pid, to_pid, equivalent, limit, policy)`:
      - Валидация: участники существуют, эквивалент активен
      - Проверка: нет дубликата (from, to, equivalent)
      - Создание TrustLine + инициализация Debt(amount=0)
    - `update(id, limit, policy)`:
      - Проверка владельца
      - Обновление полей
    - `close(id)`:
      - Проверка: текущий долг = 0
      - Статус → closed
    - `get_by_participant(pid, direction, equivalent)` → List[TrustLine]
    - `get_available(from_pid, to_pid, equivalent)` → limit - current_debt

- [ ] **TrustLines API**
  - `app/api/v1/trustlines.py`:
    - `POST /trustlines` — создание
    - `GET /trustlines` — список (фильтры: direction, equivalent)
    - `PATCH /trustlines/{id}` — обновление
    - `DELETE /trustlines/{id}` — закрытие

- [ ] **Seed загрузка**
  - Обновить `scripts/seed_db.py` — загрузка `seeds/trustlines.json`

- [ ] **Тесты**
  - `tests/integration/test_trustlines_api.py`

---

### Этап 5: Маршрутизация и ёмкость (Routing & Capacity) (1 день)
**Цель:** Реализовать поиск путей в кредитной сети.

**Источники:** `api/openapi.yaml` (payments/capacity, payments/max-flow)

- [ ] **Routing Service**
  - `app/core/payments/router.py`:
    - `build_graph(equivalent)` → граф из TrustLines (from → to, capacity = limit - debt)
    - `find_paths(from_pid, to_pid, equivalent, amount, max_paths=5)` → List[Path]:
      - BFS/k-shortest paths
      - Фильтр путей с достаточной ёмкостью
    - `check_capacity(from_pid, to_pid, equivalent, amount)` → CapacityResponse:
      - `can_pay: bool`
      - `max_amount: Decimal`
      - `routes_count: int`
      - `estimated_hops: int`
    - `max_flow(from_pid, to_pid, equivalent)` → MaxFlowResponse:
      - Edmonds-Karp или простой BFS
      - `max_amount`, `paths[]`, `bottlenecks[]`

- [ ] **Pydantic Схемы**
  - `app/schemas/payment.py` — CapacityResponse, MaxFlowPath, Bottleneck, MaxFlowResponse

- [ ] **Payments API (частично)**
  - `app/api/v1/payments.py`:
    - `GET /payments/capacity`
    - `GET /payments/max-flow`

- [ ] **Unit тесты**
  - `tests/unit/test_routing.py` — тесты на тестовом графе

---

### Этап 6: Движок платежей (Payment Engine) (1.5 дня)
**Цель:** Реализовать выполнение платежей с 2PC.

**Источники:** `api/openapi.yaml` (payments), `docs/ru/02-protocol-spec.md`

- [ ] **Payment Engine**
  - `app/core/payments/engine.py`:
    - State machine: `NEW → ROUTED → PREPARE_IN_PROGRESS → PREPARED → COMMITTED/ABORTED`
    - `prepare(tx_id, paths, amounts)`:
      - Для каждого сегмента пути: создать PrepareLock
      - Проверить доступный лимит
      - Установить TTL на локи (30 сек)
      - Если любой fail → вернуть ошибку
    - `commit(tx_id)`:
      - Применить изменения к Debt (увеличить/уменьшить)
      - Удалить PrepareLocks
      - Записать Transaction(state=COMMITTED)
    - `abort(tx_id)`:
      - Удалить PrepareLocks
      - Записать Transaction(state=ABORTED, error=...)
    - `cleanup_expired_locks()` — фоновая задача

- [ ] **Payment Service**
  - `app/core/payments/service.py`:
    - `create_payment(from_pid, to_pid, equivalent, amount, description)`:
      1. Валидация входных данных
      2. `router.find_paths()` → paths
      3. Если нет путей → ABORTED
      4. `engine.prepare()` для всех путей
      5. Если все OK → `engine.commit()`
      6. Иначе → `engine.abort()`
      7. Вернуть PaymentResult
    - `get_payment(tx_id)` → PaymentResult
    - `list_payments(pid, direction, equivalent, status, pagination)` → List[PaymentResult]

- [ ] **Pydantic Схемы**
  - Дополнить `app/schemas/payment.py` — PaymentCreateRequest, PaymentRoute, PaymentResult, PaymentsList

- [ ] **Payments API (завершение)**
  - `app/api/v1/payments.py`:
    - `POST /payments` — создание платежа
    - `GET /payments/{tx_id}` — детали
    - `GET /payments` — список (фильтры)

- [ ] **Тесты**
  - `tests/unit/test_payment_engine.py`
  - `tests/integration/test_payments_api.py`

---

### Этап 7: Балансы и Клиринг (Balance & Clearing) (1 день)
**Цель:** Реализовать просмотр балансов и механизм упрощения долгов.

**Источники:** `api/openapi.yaml` (balance, balance/debts), `docs/ru/02-protocol-spec.md`

- [ ] **Balance Service**
  - `app/core/balance/service.py`:
    - `get_summary(pid)` → List[BalanceEquivalent]:
      - Агрегация по эквивалентам
      - total_debt, total_credit, net_balance
      - available_to_spend (min из исходящих лимитов)
      - available_to_receive (сумма входящих доступных)
    - `get_debts(pid, equivalent, direction)` → DebtsDetails:
      - outgoing: кому должен
      - incoming: кто должен

- [ ] **Balance API**
  - `app/api/v1/balance.py`:
    - `GET /balance` — сводка по эквивалентам
    - `GET /balance/debts` — детальные долги

- [ ] **Pydantic Схемы**
  - `app/schemas/balance.py` — BalanceEquivalent, BalanceSummary, DebtsDetails

- [ ] **Clearing Service**
  - `app/core/clearing/service.py`:
    - `find_cycles(equivalent)` → List[Cycle]:
      - DFS для поиска замкнутых циклов долгов
      - Цикл: [A→B, B→C, C→A]
    - `execute_clearing(cycle)`:
      - Найти min_debt в цикле
      - Уменьшить все долги в цикле на min_debt
      - Записать Transaction(type=CLEARING)
    - `auto_clear(equivalent)`:
      - Вызывается после каждого COMMITTED платежа (hook)
      - Или как фоновая задача

- [ ] **Тесты**
  - Unit тесты для clearing алгоритма
  - Integration тест: платёж → автоклиринг

---

### Этап 8: Тестирование и Документация (1 день)
**Цель:** Убедиться в качестве кода и удобстве использования.

**Источники:** `docs/ru/08-test-scenarios.md`

- [ ] **Интеграционные тесты по сценариям**
  - `tests/integration/test_scenarios.py`:
    - Сценарий 1: Регистрация участников
    - Сценарий 2: Создание trustlines
    - Сценарий 3: Простой платёж A→B
    - Сценарий 4: Платёж через посредника A→B→C
    - Сценарий 5: Клиринг цикла
    - Сценарий 6: Недостаточный лимит (отказ)

- [ ] **Проверка соответствия OpenAPI**
  - Все эндпоинты соответствуют `api/openapi.yaml`
  - Все схемы ответов валидны

- [ ] **Error Handling**
  - Все ошибки обёрнуты в ErrorEnvelope
  - Коды ошибок документированы
  - HTTP статусы корректны (400, 401, 404, 500)

- [ ] **Логирование**
  - Логирование ключевых операций (auth, payment, clearing)
  - Request ID в логах
  - Структурированные логи (JSON)

- [ ] **Документация**
  - Обновить `README.md`:
    - Quick start с docker compose
    - Ссылка на OpenAPI docs (/docs, /redoc)
    - Переменные окружения
    - Примеры запросов (curl)
  - `.env.example` — все переменные с комментариями

- [ ] **Финальная проверка**
  - `docker compose up` → всё работает
  - Smoke test всех эндпоинтов

---

## Оценка времени

| Этап | Описание | Время |
|------|----------|-------|
| 1 | Инициализация и Инфраструктура | 0.5 дня |
| 2 | Слой данных (Data Layer) | 1 день |
| 3 | Auth & Participants | 1 день |
| 4 | TrustLines | 1 день |
| 5 | Routing & Capacity | 1 день |
| 6 | Payment Engine | 1.5 дня |
| 7 | Balance & Clearing | 1 день |
| 8 | Тестирование и Документация | 1 день |
| **Итого** | | **~8 дней** |

---

## Зависимости

### requirements.txt
```txt
# Core
fastapi==0.109.0
uvicorn[standard]==0.27.0
pydantic==2.5.3
pydantic-settings==2.1.0

# Database
sqlalchemy[asyncio]==2.0.25
asyncpg==0.29.0
alembic==1.13.1

# Cache & Locks
redis==5.0.1

# Security
pyjwt==2.8.0
pynacl==1.5.0

# HTTP client (for tests)
httpx==0.26.0
```

### requirements-dev.txt
```txt
pytest==7.4.4
pytest-asyncio==0.23.3
pytest-cov==4.1.0
black==24.1.1
ruff==0.1.14
pre-commit==3.6.0
```

---

## Docker Compose (обновленный)

```yaml
services:
  db:
    image: postgres:16-alpine
    container_name: geov0-db
    environment:
      POSTGRES_USER: geo
      POSTGRES_PASSWORD: geo
      POSTGRES_DB: geov0
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U geo -d geov0"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: geov0-redis
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: geov0-app
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://geo:geo@db:5432/geov0
      REDIS_URL: redis://redis:6379/0
      JWT_SECRET: change-me-in-production
      JWT_ALGORITHM: HS256
      JWT_ACCESS_TOKEN_EXPIRE_MINUTES: 15
      JWT_REFRESH_TOKEN_EXPIRE_DAYS: 7
      LOG_LEVEL: INFO
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    volumes:
      - ./seeds:/app/seeds:ro

volumes:
  postgres_data:
```

---

## Dockerfile (docker/Dockerfile)

```dockerfile
# Build stage
FROM python:3.11-slim as builder

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# Runtime stage
FROM python:3.11-slim

WORKDIR /app

# Install dependencies from wheels
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY docker/docker-entrypoint.sh ./

RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
```

---

## Переменные окружения (.env.example)

```bash
# Database
DATABASE_URL=postgresql+asyncpg://geo:geo@localhost:5432/geov0

# Redis
REDIS_URL=redis://localhost:6379/0

# JWT
JWT_SECRET=your-secret-key-change-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# Application
LOG_LEVEL=INFO
DEBUG=false

# Challenge
AUTH_CHALLENGE_EXPIRE_SECONDS=300

# Payment Engine
PREPARE_LOCK_TTL_SECONDS=30
```

---

## Критерии готовности MVP

- [ ] Все 15 эндпоинтов из OpenAPI реализованы
- [ ] Challenge-response auth работает с Ed25519
- [ ] Платежи через кредитные пути выполняются атомарно
- [ ] Автоклиринг находит и упрощает циклы
- [ ] Docker compose поднимает весь стек одной командой
- [ ] Тесты проходят с покрытием >70%
- [ ] README с quickstart актуален
