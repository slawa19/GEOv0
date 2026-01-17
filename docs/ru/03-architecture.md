# GEO Hub: Архитектура системы

**Версия:** 0.1  
**Дата:** Ноябрь 2025

---

## Содержание

1. [Обзор архитектуры](#1-обзор-архитектуры)
2. [Этап 1: MVP — Базовый Hub](#2-этап-1-mvp--базовый-hub)
3. [Этап 2: Расширенные возможности](#3-этап-2-расширенные-возможности)
4. [Этап 3: Федерация Hub'ов](#4-этап-3-федерация-hubов)
5. [Этап 4: Продвинутые сценарии](#5-этап-4-продвинутые-сценарии)
6. [Технологический стек](#6-технологический-стек)
7. [Модель данных](#7-модель-данных)
8. [Безопасность](#8-безопасность)
9. [Мониторинг и эксплуатация](#9-мониторинг-и-эксплуатация)
10. [Будущее развитие: путь к децентрализации](#10-будущее-развитие-путь-к-децентрализации)

---

## 1. Обзор архитектуры

### 1.1. Принципы

| Принцип | Описание |
|---------|----------|
| **Простота** | Минимум компонентов, понятная структура |
| **Модульность** | Ядро + аддоны, чёткие границы |
| **Расширяемость** | Возможность эволюции без рефакторинга |
| **Тестируемость** | Изолированные компоненты, чистые интерфейсы |

### 1.2. Высокоуровневая схема

```
┌─────────────────────────────────────────────────────────────┐
│                      Клиенты                                │
│  ┌─────────────────────┐  ┌──────────────────────────────┐  │
│  │   Web Client (PWA)  │  │   Admin Web Panel            │  │
│  │   (Primary for MVP) │  │   (Vue 3 + TS + Vite)        │  │
│  └──────────┬──────────┘  └──────────┬───────────────────┘  │
└─────────────┼────────────────────────┼──────────────────────┘
              │                        │
              └────────────────────────┘
                      │ HTTPS / WebSocket
                      ▼
┌─────────────────────────────────────────────────────────────┐
│                    Community Hub                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                   API Layer                          │    │
│  │  ┌─────────┐  ┌──────────┐  ┌─────────────────┐     │    │
│  │  │  REST   │  │WebSocket │  │  Admin Routes   │     │    │
│  │  │  API    │  │  Server  │  │                 │     │    │
│  │  └────┬────┘  └────┬─────┘  └────────┬────────┘     │    │
│  └───────┼────────────┼─────────────────┼──────────────┘    │
│          └────────────┼─────────────────┘                   │
│                       ▼                                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                 Core Services                        │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │    │
│  │  │   Auth   │ │TrustLine │ │ Payment  │ │Clearing│  │    │
│  │  │ Service  │ │ Service  │ │ Engine   │ │ Engine │  │    │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │    │
│  │  │ Routing  │ │Reporting │ │    Addon Manager     │ │    │
│  │  │ Service  │ │ Service  │ │                      │ │    │
│  │  └──────────┘ └──────────┘ └──────────────────────┘ │    │
│  │  ┌───────────────────────────────────────────────┐  │    │
│  │  │           Integrity Checker                    │  │    │
│  │  │  (Zero-Sum, Limits, Checksum verification)    │  │    │
│  │  └───────────────────────────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────┘    │
│                       │                                      │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                  Data Layer                          │    │
│  │  ┌──────────────┐        ┌──────────────┐           │    │
│  │  │  PostgreSQL  │        │    Redis     │           │    │
│  │  │  (primary)   │        │ (cache/locks)│           │    │
│  │  └──────────────┘        └──────────────┘           │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 1.3. Карта эволюции

```
Этап 1: MVP              Этап 2: Extended       Этап 3: Federation     Этап 4: Advanced
─────────────────────────────────────────────────────────────────────────────────────────►

┌─────────────────┐     ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Single Hub      │     │ + Offline       │    │ + Inter-Hub     │    │ + P2P Nodes     │
│ Basic Protocol  │────►│ + Analytics     │───►│   Protocol      │───►│ + Consensus     │
│ Web + Mobile    │     │ + Disputes      │    │ + Discovery     │    │ + Sharding      │
│ PostgreSQL      │     │ + KYC Hooks     │    │ + Routing       │    │                 │
└─────────────────┘     └─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## 2. Этап 1: MVP — Базовый Hub

### 2.1. Scope MVP

**Включено:**
- Регистрация участников с Ed25519 ключами
- Управление линиями доверия
- Платежи с маршрутизацией (single + **limited multipath**; baseline 2–3 маршрута, параметры в [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1))
- **Full multipath** (опционально; экспериментальный режим для бенчмарков, включается только через `feature_flags.full_multipath_enabled`, см. [`docs/ru/02-protocol-spec.md`](docs/ru/02-protocol-spec.md:1) и [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1))
- Автоматический клиринг: **триггерный** поиск циклов 3–4 (по умолчанию) + **периодический** поиск 5–6 (опционально; параметры в [`docs/ru/config-reference.md`](docs/ru/config-reference.md:1))
- REST API + WebSocket уведомления
- Базовая админка (операторские функции и доступ к конфигу/feature flags — см. [`docs/ru/admin/README.md`](docs/ru/admin/README.md:1))
- Web-клиент **PWA** (primary клиент для MVP)

**Не включено (отложено):**
- Flutter клиент (mobile + desktop) — опционально на следующих этапах
- Межхабовое взаимодействие
- Расширенная аналитика
- Офлайн-режим клиента
- KYC интеграции
- Механизм споров

### 2.2. Компоненты MVP

```
GEOv0-PROJECT/
├── app/                        # Backend (FastAPI)
│   ├── __init__.py
│   ├── main.py                 # FastAPI entry point
│   ├── config.py               # Configuration (env)
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py             # Dependencies (auth, db session)
│   │   └── v1/                 # API v1 (REST + WS)
│   │       ├── __init__.py
│   │       ├── router.py       # Main router
│   │       ├── auth.py
│   │       ├── participants.py
│   │       ├── trustlines.py
│   │       ├── payments.py
│   │       ├── clearing.py
│   │       ├── integrity.py
│   │       ├── admin.py        # Admin API endpoints
│   │       └── websocket.py
│   │
│   ├── core/                   # Business logic
│   ├── db/                     # SQLAlchemy models + sessions
│   └── schemas/                # Pydantic schemas (API DTO)
│
├── admin-ui/                   # Admin UI (Vue 3 + TypeScript + Vite)
├── migrations/                 # Alembic migrations
├── tests/                      # Unit + integration tests
├── docker/                     # Docker image build
├── docker-compose.yml          # Dev compose (Postgres + Redis + API)
├── requirements.txt            # Backend runtime deps (pinned)
├── requirements-dev.txt        # Backend dev deps (pinned)
└── README.md
```

### 2.3. Сервисы MVP

#### AuthService

```python
class AuthService:
    """Аутентификация и управление сессиями"""
    
    async def register(
        self, 
        public_key: bytes, 
        display_name: str,
        profile: dict
    ) -> Participant:
        """Регистрация нового участника"""
        
    async def verify_signature(
        self,
        message: bytes,
        signature: bytes,
        public_key: bytes
    ) -> bool:
        """Проверка подписи Ed25519"""
        
    async def create_session(
        self,
        participant_id: str,
        device_info: dict
    ) -> Session:
        """Создание JWT сессии"""
```

#### TrustLineService

```python
class TrustLineService:
    """Управление линиями доверия"""
    
    async def create(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        limit: Decimal,
        policy: TrustLinePolicy,
        signature: bytes
    ) -> TrustLine:
        """Создание линии доверия"""
        
    async def update(
        self,
        trust_line_id: UUID,
        limit: Decimal | None,
        policy: TrustLinePolicy | None,
        signature: bytes
    ) -> TrustLine:
        """Обновление линии"""
        
    async def close(
        self,
        trust_line_id: UUID,
        signature: bytes
    ) -> TrustLine:
        """Закрытие линии"""
        
    async def get_available_credit(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str
    ) -> Decimal:
        """Расчёт доступного кредита"""
```

#### RoutingService

```python
class RoutingService:
    """Маршрутизация платежей"""
    
    async def find_paths(
        self,
        source: str,
        target: str,
        equivalent: str,
        amount: Decimal,
        constraints: RoutingConstraints
    ) -> list[PaymentPath]:
        """Поиск путей для платежа"""
        
    async def split_payment(
        self,
        amount: Decimal,
        paths: list[PaymentPath]
    ) -> list[PaymentRoute]:
        """Разбиение платежа по маршрутам"""
```

#### PaymentEngine

```python
class PaymentEngine:
    """Исполнение платежей"""
    
    async def create_payment(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        amount: Decimal,
        description: str,
        signature: bytes
    ) -> Transaction:
        """Создание и исполнение платежа"""
        
    async def prepare(
        self,
        tx_id: UUID,
        routes: list[PaymentRoute]
    ) -> PrepareResult:
        """Фаза PREPARE"""
        
    async def commit(self, tx_id: UUID) -> Transaction:
        """Фаза COMMIT"""
        
    async def abort(self, tx_id: UUID, reason: str) -> Transaction:
        """Фаза ABORT"""
```

#### ClearingEngine

```python
class ClearingEngine:
    """Клиринг долгов"""
    
    async def find_cycles(
        self,
        equivalent: str,
        max_length: int = 4,
        min_amount: Decimal = Decimal("0.01")
    ) -> list[ClearingCandidate]:
        """Поиск циклов для клиринга"""
        
    async def execute_clearing(
        self,
        cycle: list[str],
        equivalent: str,
        amount: Decimal
    ) -> Transaction:
        """Исполнение клиринга"""
        
    async def process_triggered(
        self,
        changed_edges: list[tuple[str, str, str]]
    ) -> list[Transaction]:
        """Триггерный клиринг после транзакции"""
```

#### IntegrityChecker

```python
class IntegrityChecker:
    """
    Сервис проверки целостности системы.
    
    Обеспечивает верификацию инвариантов, контрольных сумм
    и корректности клиринга.
    """
    
    async def check_zero_sum(
        self,
        equivalent: str
    ) -> ZeroSumCheckResult:
        """
        Проверка инварианта нулевой суммы.
        
        Сумма всех балансов по эквиваленту должна = 0
        """
        
    async def check_trust_limits(
        self,
        equivalent: str
    ) -> TrustLimitCheckResult:
        """
        Проверка, что все долги в пределах лимитов доверия
        """
        
    async def check_debt_symmetry(
        self,
        equivalent: str
    ) -> DebtSymmetryCheckResult:
        """
        Проверка, что между парой участников долг только в одном направлении
        """
        
    async def verify_clearing_neutrality(
        self,
        cycle: list[str],
        amount: Decimal,
        equivalent: str,
        positions_before: dict[str, Decimal]
    ) -> bool:
        """
        Проверка, что клиринг не изменил чистые позиции участников
        """
        
    async def compute_state_checksum(
        self,
        equivalent: str
    ) -> str:
        """
        Вычисление SHA-256 контрольной суммы состояния долгов
        """
        
    async def run_full_check(
        self,
        equivalent: str
    ) -> IntegrityReport:
        """
        Выполнение всех проверок и формирование отчёта
        """
        
    async def save_checkpoint(
        self,
        equivalent: str,
        checksum: str
    ) -> IntegrityCheckpoint:
        """
        Сохранение контрольной точки для последующего аудита
        """
        
    async def handle_violation(
        self,
        violation: IntegrityViolation
    ) -> None:
        """
        Обработка нарушения целостности:
        - блокировка операций
        - уведомление администраторов
        - создание отчёта
        """
```

**Модели данных IntegrityChecker:**

```python
@dataclass
class IntegrityReport:
    """Отчёт о проверке целостности"""
    equivalent: str
    timestamp: datetime
    checksum: str
    checks: dict[str, CheckResult]
    all_passed: bool
    violations: list[IntegrityViolation]

@dataclass  
class CheckResult:
    """Результат отдельной проверки"""
    name: str
    passed: bool
    value: Any
    details: dict | None = None

@dataclass
class IntegrityViolation:
    """Информация о нарушении целостности"""
    type: str  # ZERO_SUM | TRUST_LIMIT | DEBT_SYMMETRY | CLEARING_NEUTRALITY
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    equivalent: str
    details: dict
    detected_at: datetime

@dataclass
class IntegrityCheckpoint:
    """Контрольная точка состояния"""
    id: UUID
    equivalent_id: UUID
    checksum: str
    invariants_status: dict[str, Any]
    created_at: datetime
```

### 2.4. Схема данных MVP

```sql
-- Participants
CREATE TABLE participants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pid VARCHAR(64) UNIQUE NOT NULL,
    public_key BYTEA NOT NULL,
    display_name VARCHAR(255),
    profile JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'active',
    verification_level INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_participants_pid ON participants(pid);
CREATE INDEX idx_participants_status ON participants(status);

-- Equivalents
CREATE TABLE equivalents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(16) UNIQUE NOT NULL,
    precision INTEGER NOT NULL DEFAULT 2,
    description TEXT,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Trust Lines
CREATE TABLE trust_lines (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_participant_id UUID REFERENCES participants(id),
    to_participant_id UUID REFERENCES participants(id),
    equivalent_id UUID REFERENCES equivalents(id),
    "limit" DECIMAL(20, 8) NOT NULL,
    policy JSONB DEFAULT '{}',
    status VARCHAR(20) DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(from_participant_id, to_participant_id, equivalent_id)
);

CREATE INDEX idx_trust_lines_from ON trust_lines(from_participant_id);
CREATE INDEX idx_trust_lines_to ON trust_lines(to_participant_id);
CREATE INDEX idx_trust_lines_status ON trust_lines(status);

-- Debts
CREATE TABLE debts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    debtor_id UUID REFERENCES participants(id),
    creditor_id UUID REFERENCES participants(id),
    equivalent_id UUID REFERENCES equivalents(id),
    amount DECIMAL(20, 8) NOT NULL CHECK (amount > 0),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    
    UNIQUE(debtor_id, creditor_id, equivalent_id)
);

CREATE INDEX idx_debts_debtor ON debts(debtor_id);
CREATE INDEX idx_debts_creditor ON debts(creditor_id);

-- Transactions
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tx_id VARCHAR(64) UNIQUE NOT NULL,
    idempotency_key VARCHAR(128),
    type VARCHAR(50) NOT NULL,
    initiator_id UUID REFERENCES participants(id),
    payload JSONB NOT NULL,
    signatures JSONB DEFAULT '[]',
    state VARCHAR(30) NOT NULL,
    error JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Allowed states (as implemented in Hub v0.1 DB constraint):
-- NEW, ROUTED, PREPARE_IN_PROGRESS, PREPARED, COMMITTED, ABORTED, PROPOSED, WAITING, REJECTED

CREATE INDEX idx_transactions_tx_id ON transactions(tx_id);
CREATE INDEX idx_transactions_state ON transactions(state);
CREATE INDEX idx_transactions_type ON transactions(type);
CREATE INDEX idx_transactions_initiator ON transactions(initiator_id);

-- Prepare Locks (для 2PC)
CREATE TABLE prepare_locks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tx_id VARCHAR(64) NOT NULL,
    participant_id UUID REFERENCES participants(id),
    effects JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    
    UNIQUE(tx_id, participant_id)
);

CREATE INDEX idx_prepare_locks_tx ON prepare_locks(tx_id);
CREATE INDEX idx_prepare_locks_expires ON prepare_locks(expires_at);

-- Integrity Audit Log (журнал аудита целостности)
CREATE TABLE integrity_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    operation_type VARCHAR(50) NOT NULL,
    tx_id VARCHAR(64),
    equivalent_code VARCHAR(16) NOT NULL,
    state_checksum_before VARCHAR(64) NOT NULL,
    state_checksum_after VARCHAR(64) NOT NULL,
    affected_participants JSONB NOT NULL,
    invariants_checked JSONB NOT NULL,
    verification_passed BOOLEAN NOT NULL,
    error_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_timestamp ON integrity_audit_log(timestamp);
CREATE INDEX idx_audit_tx_id ON integrity_audit_log(tx_id);
CREATE INDEX idx_audit_failures ON integrity_audit_log(verification_passed) 
WHERE verification_passed = false;

-- Integrity Checkpoints (контрольные точки состояния)
CREATE TABLE integrity_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    equivalent_id UUID NOT NULL REFERENCES equivalents(id),
    checksum VARCHAR(64) NOT NULL,
    invariants_status JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_integrity_checkpoints_equivalent ON integrity_checkpoints(equivalent_id);
CREATE INDEX idx_integrity_checkpoints_created_at ON integrity_checkpoints(created_at);

-- Integrity Violations (зафиксированные нарушения)
CREATE TABLE integrity_violations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    equivalent_code VARCHAR(16) NOT NULL,
    details JSONB NOT NULL,
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMPTZ,
    resolution_notes TEXT,
    detected_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_violations_unresolved ON integrity_violations(resolved, detected_at)
WHERE resolved = false;
```

### 2.5. API Endpoints MVP

```
Auth:
    POST   /api/v1/participants           # Регистрация участника
    POST   /api/v1/auth/challenge         # Старт challenge-response
  POST   /api/v1/auth/login             # Логин (challenge-response)
  POST   /api/v1/auth/refresh           # Обновление токена

Participants:
  GET    /api/v1/participants/me        # Текущий участник
  PATCH  /api/v1/participants/me        # Обновление профиля
  GET    /api/v1/participants/{pid}     # Профиль участника
    GET    /api/v1/participants           # Поиск участников

TrustLines:
  POST   /api/v1/trustlines             # Создать линию
  GET    /api/v1/trustlines             # Список линий
  GET    /api/v1/trustlines/{id}        # Детали линии
  PATCH  /api/v1/trustlines/{id}        # Обновить линию
  DELETE /api/v1/trustlines/{id}        # Закрыть линию

Payments:
  POST   /api/v1/payments               # Создать платёж
  GET    /api/v1/payments               # История платежей
  GET    /api/v1/payments/{tx_id}       # Детали платежа
  GET    /api/v1/payments/capacity      # Проверить ёмкость

Balance:
  GET    /api/v1/balance                # Общий баланс
  GET    /api/v1/balance/debts          # Долги (входящие/исходящие)


WebSocket:
  WS     /api/v1/ws                     # Real-time уведомления

Примечание про «асинхронность» и оффлайн:
- В Hub v0.1 WebSocket используется для best-effort уведомлений (возможны пропуски/дубликаты); после переподключения клиент сверяет состояние через REST.
- Оффлайн клиента (UX) не требует протокольных состояний согласования. Протокольные состояния `PROPOSED/WAITING/REJECTED` и сетевые ACK-фазы относятся к расширенному (распределённому) режиму и зарезервированы.

Integrity:
    GET    /api/v1/integrity/status               # Статус/настройки
    GET    /api/v1/integrity/checksum/{equivalent}  # Последний checksum по эквиваленту
    POST   /api/v1/integrity/verify               # Запуск проверок инвариантов + запись в audit log
    GET    /api/v1/integrity/audit-log            # Просмотр audit log
```

---

## 3. Этап 2: Расширенные возможности

### 3.1. Дополнительные компоненты

```
app/
├── core/
│   ├── analytics/              # Аналитика
│   │   ├── service.py
│   │   └── reports.py
│   ├── disputes/               # Споры
│   │   ├── service.py
│   │   └── workflow.py
│   ├── verification/           # KYC
│   │   ├── service.py
│   │   └── providers/
│   │       ├── base.py
│   │       └── manual.py
│   └── offline/                # Офлайн поддержка
│       ├── service.py
│       └── sync.py
│
└── addons/                     # Система аддонов
    ├── __init__.py
    ├── base.py                 # Базовый класс аддона
    ├── registry.py             # Реестр аддонов
    └── hooks.py                # Хуки событий
```

### 3.2. Система аддонов

```python
# addons/base.py
from abc import ABC, abstractmethod

class AddonBase(ABC):
    """Базовый класс для аддонов"""
    
    name: str
    version: str
    
    @abstractmethod
    async def on_load(self, app) -> None:
        """Вызывается при загрузке аддона"""
        pass
    
    @abstractmethod
    async def on_unload(self) -> None:
        """Вызывается при выгрузке"""
        pass
    
    def register_routes(self, router) -> None:
        """Регистрация дополнительных маршрутов"""
        pass
    
    def register_hooks(self, event_bus) -> None:
        """Подписка на события"""
        pass
```

```python
# Пример аддона
class TelegramNotificationsAddon(AddonBase):
    name = "telegram_notifications"
    version = "1.0.0"
    
    async def on_load(self, app):
        self.bot = TelegramBot(app.config.telegram_token)
        
    def register_hooks(self, event_bus):
        event_bus.subscribe("payment.committed", self.on_payment)
        event_bus.subscribe("clearing.committed", self.on_clearing)
    
    async def on_payment(self, event):
        # Отправить уведомление получателю
        pass
```

### 3.3. Механизм споров

```python
class DisputeService:
    """Управление спорами"""
    
    async def open_dispute(
        self,
        tx_id: str,
        opened_by: str,
        reason: str,
        evidence: list[str],
        requested_outcome: str
    ) -> Dispute:
        """Открытие спора"""
        
    async def respond_to_dispute(
        self,
        dispute_id: UUID,
        responder: str,
        response: str,
        evidence: list[str]
    ) -> Dispute:
        """Ответ на спор"""
        
    async def resolve_dispute(
        self,
        dispute_id: UUID,
        resolver: str,
        resolution: DisputeResolution,
        compensation_tx: Transaction | None
    ) -> Dispute:
        """Разрешение спора"""
```

### 3.4. Офлайн режим клиента

```python
class OfflineSyncService:
    """Синхронизация офлайн операций"""
    
    async def get_state_snapshot(
        self,
        participant_id: str
    ) -> StateSnapshot:
        """Снимок состояния для кэширования"""
        
    async def process_offline_queue(
        self,
        participant_id: str,
        operations: list[OfflineOperation]
    ) -> SyncResult:
        """Обработка очереди офлайн операций"""
        
    async def get_delta_since(
        self,
        participant_id: str,
        since: datetime
    ) -> list[StateChange]:
        """Изменения с момента последней синхронизации"""
```

### 3.5. KYC Hooks

```python
class VerificationService:
    """Верификация участников"""
    
    async def request_verification(
        self,
        participant_id: str,
        level: int,
        documents: list[str]
    ) -> VerificationRequest:
        """Запрос на верификацию"""
        
    async def approve_verification(
        self,
        request_id: UUID,
        approver: str,
        level: int,
        notes: str
    ) -> Participant:
        """Подтверждение верификации"""
        
    async def check_verification_required(
        self,
        participant_id: str,
        operation: str,
        amount: Decimal
    ) -> bool:
        """Проверка требований к верификации"""
```

---

## 4. Этап 3: Федерация Hub'ов

### 4.1. Архитектура федерации

```
┌─────────────────────────────────────────────────────────────────┐
│                        Federation Layer                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    Hub Discovery                           │  │
│  │  (DNS-based / Registry / Manual configuration)            │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│    Hub A      │     │    Hub B      │     │    Hub C      │
│  Community A  │◄───►│  Community B  │◄───►│  Community C  │
│               │     │               │     │               │
│ [participants]│     │ [participants]│     │ [participants]│
└───────────────┘     └───────────────┘     └───────────────┘

Inter-Hub Trust Lines:
  HubA ←→ HubB (limit: 100,000 UAH)
  HubB ←→ HubC (limit: 50,000 UAH)
```

### 4.2. Компоненты федерации

```
app/
├── federation/
│   ├── __init__.py
│   ├── discovery/              # Обнаружение Hub'ов
│   │   ├── __init__.py
│   │   ├── service.py
│   │   ├── dns.py              # DNS-based discovery
│   │   └── registry.py         # Central registry
│   ├── routing/                # Межхабовая маршрутизация
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── path_finder.py
│   ├── protocol/               # Межхабовый протокол
│   │   ├── __init__.py
│   │   ├── client.py           # Outgoing requests
│   │   ├── server.py           # Incoming handlers
│   │   └── messages.py         # Protocol messages
│   └── settlement/             # Расчёты между Hub'ами
│       ├── __init__.py
│       └── service.py
```

### 4.3. Discovery Service

```python
class HubDiscoveryService:
    """Обнаружение и регистрация Hub'ов"""
    
    async def register_hub(
        self,
        hub_info: HubInfo
    ) -> HubRegistration:
        """Регистрация Hub'а в сети"""
        
    async def discover_hubs(
        self,
        filter_by: HubFilter | None = None
    ) -> list[HubInfo]:
        """Поиск доступных Hub'ов"""
        
    async def get_hub_by_participant(
        self,
        pid: str
    ) -> HubInfo | None:
        """Найти Hub по PID участника"""
        
    async def health_check(
        self,
        hub_endpoint: str
    ) -> HubHealthStatus:
        """Проверка доступности Hub'а"""
```

### 4.4. Inter-Hub Protocol

```python
class InterHubProtocol:
    """Протокол взаимодействия между Hub'ами"""
    
    async def request_payment(
        self,
        target_hub: str,
        request: InterHubPaymentRequest
    ) -> InterHubPaymentResponse:
        """Запрос платежа в другой Hub"""
        
    async def prepare_payment(
        self,
        tx_id: str,
        effects: list[InterHubEffect]
    ) -> PrepareResponse:
        """PREPARE фаза межхабового платежа"""
        
    async def commit_payment(
        self,
        tx_id: str
    ) -> CommitResponse:
        """COMMIT фаза"""
        
    async def abort_payment(
        self,
        tx_id: str,
        reason: str
    ) -> AbortResponse:
        """ABORT фаза"""
```

### 4.5. Межхабовая маршрутизация

```python
class InterHubRoutingService:
    """Маршрутизация между Hub'ами"""
    
    async def find_inter_hub_path(
        self,
        source_hub: str,
        source_pid: str,
        target_hub: str,
        target_pid: str,
        equivalent: str,
        amount: Decimal
    ) -> InterHubRoute:
        """Найти маршрут между Hub'ами"""
        
    async def get_hub_capacity(
        self,
        hub_a: str,
        hub_b: str,
        equivalent: str
    ) -> Decimal:
        """Доступная ёмкость между Hub'ами"""

---

## 5. Этап 4: Продвинутые сценарии

### 5.1. P2P Nodes (частичная децентрализация)

```
┌─────────────────────────────────────────────────────────────┐
│                    Hybrid Network                            │
│                                                              │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                  │
│  │ P2P Node│◄──►│   Hub   │◄──►│ P2P Node│                  │
│  │ (power  │    │(coord.) │    │ (power  │                  │
│  │  user)  │    │         │    │  user)  │                  │
│  └─────────┘    └────┬────┘    └─────────┘                  │
│                      │                                       │
│         ┌────────────┼────────────┐                         │
│         ▼            ▼            ▼                         │
│    [light]      [light]      [light]                        │
│    clients      clients      clients                        │
└─────────────────────────────────────────────────────────────┘
```

### 5.2. Консенсус между Hub'ами

Для критичных операций — распределённый консенсус:

```python
class HubConsensusService:
    """Консенсус между Hub'ами (Raft-подобный)"""
    
    async def propose_transaction(
        self,
        tx: Transaction,
        participating_hubs: list[str]
    ) -> ConsensusResult:
        """Предложить транзакцию для консенсуса"""
        
    async def vote(
        self,
        tx_id: str,
        vote: Vote
    ) -> None:
        """Голосовать за/против транзакции"""
```

### 5.3. Шардирование данных

Для масштабирования крупных Hub'ов:

```
┌─────────────────────────────────────────┐
│              Large Hub                   │
│                                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │ Shard 1 │  │ Shard 2 │  │ Shard 3 │  │
│  │ A-H     │  │ I-P     │  │ Q-Z     │  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                    │                     │
│              Coordinator                 │
└─────────────────────────────────────────┘
```

---

## 6. Технологический стек

### 6.1. Backend

| Компонент | Технология | Обоснование |
|-----------|------------|-------------|
| Язык | Python 3.11+ | Простота, экосистема, AI-friendly |
| Framework | FastAPI | Async, OpenAPI, Pydantic |
| ORM | SQLAlchemy 2.x | Надёжность, миграции |
| Миграции | Alembic | Стандарт для SQLAlchemy |
| База данных | PostgreSQL 16+ | ACID, JSONB, производительность |
| Кэш | Redis 7+ | Скорость, pub/sub, locks |
| Очереди / фоновые задачи | (не внедрено в коде) | Опционально, будет добавлено при необходимости |
| Тесты | pytest | Стандарт Python |

### 6.2. Клиенты

| Компонент | Технология | Обоснование |
|-----------|------------|-------------|
| Mobile/Desktop | Flutter (Dart) | Кроссплатформенность (Native) |
| PWA Client | Vue.js 3 (PWA) + Tailwind (опционально) | Легкость, отсутствие установки, минимализм |
| Админка | Vue.js 3 + TypeScript + Vite + Element Plus + Pinia | Отзывчивость, быстрое прототипирование, визуализация графов |
| Криптография | libsodium / tweetnacl | Ed25519, проверенные библиотеки |

### 6.3. Инфраструктура

| Компонент | Технология | Обоснование |
|-----------|------------|-------------|
| Контейнеры | Docker | Стандарт |
| Оркестрация | Docker Compose → K8s | От простого к сложному |
| CI/CD | GitHub Actions | Интеграция с репозиторием |
| Мониторинг | Prometheus + Grafana | Стандарт индустрии |
| Логи | Loki / ELK | Централизованные логи |

### 6.4. Зависимости и версии (источник истины)

Актуальные версии **фиксируются в манифестах репозитория**:

- Backend runtime: `requirements.txt`
- Backend dev: `requirements-dev.txt`
- Admin UI: `admin-ui/package.json`

Backend (runtime, excerpt):

```text
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

Admin UI (excerpt):

```text
vue, vue-router, vue-i18n
pinia
vite
typescript
element-plus
cytoscape
zod
```

---

## 7. Модель данных

### 7.1. ER-диаграмма

```
┌─────────────────┐       ┌─────────────────┐
│   Participant   │       │   Equivalent    │
├─────────────────┤       ├─────────────────┤
│ id (PK)         │       │ id (PK)         │
│ pid (unique)    │       │ code (unique)   │
│ public_key      │       │ precision       │
│ display_name    │       │ description     │
│ profile (JSONB) │       │ metadata        │
│ status          │       └────────┬────────┘
│ verification    │                │
└────────┬────────┘                │
         │                         │
         │    ┌────────────────────┼────────────────────┐
         │    │                    │                    │
         ▼    ▼                    ▼                    ▼
┌─────────────────┐       ┌─────────────────┐  ┌─────────────────┐
│   TrustLine     │       │      Debt       │  │  Transaction    │
├─────────────────┤       ├─────────────────┤  ├─────────────────┤
│ id (PK)         │       │ id (PK)         │  │ id (PK)         │
│ from_id (FK)    │       │ debtor_id (FK)  │  │ tx_id (unique)  │
│ to_id (FK)      │       │ creditor_id (FK)│  │ type            │
│ equivalent (FK) │       │ equivalent (FK) │  │ initiator (FK)  │
│ limit           │       │ amount          │  │ payload (JSONB) │
│ policy (JSONB)  │       └─────────────────┘  │ signatures      │
│ status          │                            │ state           │
└─────────────────┘                            └─────────────────┘
```

### 7.2. Индексы для производительности

```sql
-- Быстрый поиск маршрутов
CREATE INDEX idx_trustlines_routing 
ON trust_lines(from_participant_id, to_participant_id, equivalent_id, status)
WHERE status = 'active';

-- Быстрый поиск циклов
CREATE INDEX idx_debts_cycles 
ON debts(debtor_id, creditor_id, equivalent_id, amount)
WHERE amount > 0;

-- История транзакций участника
CREATE INDEX idx_transactions_participant_history 
ON transactions(initiator_id, created_at DESC);
```

---

## 8. Безопасность

### 8.1. Аутентификация

```
┌─────────────────────────────────────────────────────────┐
│                 Challenge-Response Auth                  │
│                                                          │
│  Client                              Server              │
│    │                                   │                 │
│    │──── POST /auth/challenge ────────►│                 │
│    │      { pid }                      │                 │
│    │                                   │                 │
│    │◄─── { challenge: "random" } ──────│                 │
│    │                                   │                 │
│    │  sign(challenge, private_key)     │                 │
│    │                                   │                 │
│    │──── POST /auth/login ────────────►│                 │
│    │      { pid, challenge, signature }│                 │
│    │                                   │ verify(sig, pk) │
│    │◄─── { access_token, refresh } ────│                 │
│    │                                   │                 │
└─────────────────────────────────────────────────────────┘
```

### 8.2. Авторизация

| Ресурс | Владелец | Права |
|--------|----------|-------|
| TrustLine | `from` | Создание, изменение, закрытие |
| Участник | сам участник | Изменение профиля |
| Платёж | инициатор | Создание |
| Спор | участники TX | Открытие, ответ |

### 8.3. Валидация подписей

```python
async def validate_signed_request(
    request: SignedRequest,
    expected_signer: str
) -> bool:
    """Проверка подписи запроса"""
    
    # 1. Проверить формат подписи
    if not is_valid_signature_format(request.signature):
        raise InvalidSignature("Bad format")
    
    # 2. Получить публичный ключ
    participant = await get_participant(expected_signer)
    if not participant:
        raise UnknownParticipant(expected_signer)
    
    # 3. Проверить подпись
    message = canonical_json(request.payload)
    if not verify_ed25519(message, request.signature, participant.public_key):
        raise InvalidSignature("Verification failed")
    
    # 4. Проверить timestamp (защита от replay)
    if abs(now() - request.timestamp) > MAX_TIMESTAMP_DRIFT:
        raise ExpiredRequest("Timestamp drift too large")
    
    return True
```

### 8.4. Rate Limiting

```python
RATE_LIMITS = {
    "auth/login": "5/minute",
    "payments": "30/minute",
    "trustlines": "10/minute",
    "default": "100/minute"
}
```

### 8.5. Защита данных

| Данные | Хранение | Передача |
|--------|----------|----------|
| Приватные ключи | Только на клиенте | Никогда |
| Пароли | bcrypt/argon2 | HTTPS |
| Сессии | JWT (short-lived) | HTTPS |
| PII | Encrypted at rest | HTTPS |

### 8.6. Приватность транзакций

В текущей архитектуре MVP Hub централизованно хранит данные о транзакциях, что создаёт определённые риски для приватности. Ниже описаны меры защиты для MVP и планы на будущее развитие.

#### 8.6.1. Меры защиты приватности (MVP)

**Шифрование чувствительных данных:**

```python
# Поля, шифруемые на уровне БД
ENCRYPTED_FIELDS = {
    "participants": ["profile.contacts", "profile.personal_data"],
    "transactions": ["payload.description"],
}

# Использование pgcrypto для шифрования at rest
class EncryptedField:
    """Поле с прозрачным шифрованием/дешифрованием"""
    
    def __init__(self, key_id: str):
        self.key_id = key_id
    
    def encrypt(self, value: str) -> bytes:
        """Шифрование перед записью в БД"""
        return pgp_sym_encrypt(value, get_key(self.key_id))
    
    def decrypt(self, data: bytes) -> str:
        """Дешифрование при чтении из БД"""
        return pgp_sym_decrypt(data, get_key(self.key_id))
```

```sql
-- Шифрование на уровне PostgreSQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Пример зашифрованного поля
ALTER TABLE transactions 
ADD COLUMN description_encrypted BYTEA;

-- Функция шифрования
CREATE OR REPLACE FUNCTION encrypt_description(text_data TEXT, key TEXT)
RETURNS BYTEA AS $$
BEGIN
    RETURN pgp_sym_encrypt(text_data, key);
END;
$$ LANGUAGE plpgsql;
```

**Минимизация хранимых метаданных:**

| Категория | Что хранится | Что НЕ хранится |
|-----------|--------------|-----------------|
| Транзакции | tx_id, тип, участники, сумма, время | IP-адреса, device fingerprints |
| Участники | PID, публичный ключ, статус | Геолокация, история входов |
| TrustLines | Участники, лимит, политика | История изменений лимитов |

**Политики хранения данных:**

```python
class DataRetentionPolicy:
    """Политики хранения и удаления данных"""
    
    # Транзакции старше 2 лет — удалять детали, оставлять агрегаты
    TRANSACTION_DETAIL_RETENTION_DAYS = 730
    
    # Логи аудита — 5 лет (для compliance)
    AUDIT_LOG_RETENTION_DAYS = 1825
    
    # Prepare locks — удалять через 24 часа после завершения
    PREPARE_LOCK_RETENTION_HOURS = 24
    
    async def cleanup_old_transaction_details(self):
        """Очистка деталей старых транзакций"""
        cutoff = datetime.now() - timedelta(days=self.TRANSACTION_DETAIL_RETENTION_DAYS)
        await db.execute("""
            UPDATE transactions 
            SET payload = jsonb_build_object(
                'type', payload->>'type',
                'amount', payload->>'amount',
                'equivalent', payload->>'equivalent'
                -- description и другие детали удаляются
            )
            WHERE created_at < :cutoff
        """, {"cutoff": cutoff})
```

**Право на удаление данных (GDPR compliance):**

```python
class ParticipantDataService:
    async def request_data_export(
        self,
        participant_id: str
    ) -> DataExport:
        """Экспорт всех данных участника (GDPR Art. 20)"""
        
    async def request_data_deletion(
        self,
        participant_id: str
    ) -> DeletionRequest:
        """
        Запрос на удаление данных (GDPR Art. 17).
        
        Условия:
        - Все долги погашены (баланс = 0)
        - Нет открытых споров
        - Нет pending транзакций
        """
        
    async def anonymize_participant(
        self,
        participant_id: str
    ) -> None:
        """
        Анонимизация участника (если удаление невозможно).
        
        - Удаление display_name, profile, contacts
        - PID заменяется на хеш
        - Транзакции остаются для целостности, но без идентификации
        """
```

#### 8.6.2. Будущее развитие приватности

В следующих версиях протокола планируется существенное усиление приватности:

**Распределённое хранение данных:**

```
Целевая архитектура (v2.0+):
┌─────────────────────────────────────────────────────────────┐
│  Участник A                    Участник B                   │
│  ┌─────────────────┐          ┌─────────────────┐          │
│  │ Локальное       │          │ Локальное       │          │
│  │ хранилище:      │          │ хранилище:      │          │
│  │ - Мои TrustLines│◄────────►│ - Мои TrustLines│          │
│  │ - Мои долги     │          │ - Мои долги     │          │
│  │ - Мои транзакции│          │ - Мои транзакции│          │
│  └─────────────────┘          └─────────────────┘          │
│                                                             │
│  Hub хранит только:                                        │
│  - Граф связей (без сумм)                                  │
│  - Индексы для маршрутизации                               │
│  - Агрегированные метрики                                  │
└─────────────────────────────────────────────────────────────┘
```

**Протокол приватной маршрутизации:**

При маршрутизации промежуточные ноды не должны видеть отправителя и получателя:

```
Текущий протокол (v0.1):
A → [Hub знает: A платит D через B,C] → D

Целевой протокол (v2.0+):
A → [B видит: A→B, B→?] → [C видит: ?→C, C→?] → D
     (onion routing с шифрованием слоёв)
```

```python
class PrivateRoutingProtocol:
    """
    Протокол приватной маршрутизации (концепция для v2.0+)
    
    Использует принцип onion routing:
    - Каждый промежуточный узел видит только предыдущего и следующего
    - Сумма и конечный получатель зашифрованы для промежуточных
    """
    
    def build_onion_packet(
        self,
        path: list[str],
        amount: Decimal,
        payload: bytes
    ) -> OnionPacket:
        """
        Построение "луковичного" пакета.
        
        Каждый слой шифруется публичным ключом соответствующего узла.
        Узел расшифровывает свой слой и узнаёт только следующего в цепочке.
        """
        packet = payload
        for node_pid in reversed(path):
            node_pubkey = get_public_key(node_pid)
            packet = encrypt_layer(packet, node_pubkey)
        return OnionPacket(data=packet)
```

**Протокол подтверждения без раскрытия:**

```python
class ZeroKnowledgeBalanceProof:
    """
    Доказательство достаточности баланса без раскрытия суммы.
    
    Участник может доказать, что:
    - Его долг не превышает лимит TrustLine
    - У него достаточно "кредита" для платежа
    
    Без раскрытия точных сумм.
    """
    
    def prove_balance_sufficient(
        self,
        available_credit: Decimal,
        required_amount: Decimal
    ) -> ZKProof:
        """Создать ZK-доказательство: available >= required"""
        
    def verify_proof(
        self,
        proof: ZKProof,
        commitment: bytes
    ) -> bool:
        """Проверить доказательство без знания сумм"""
```

---

## 9. Мониторинг и эксплуатация

### 9.1. Метрики

```python
# Prometheus metrics
from prometheus_client import Counter, Gauge, Histogram

# HTTP-метрики (безопасно инкрементировать всегда)
http_requests_total = Counter(
    "geo_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
http_request_duration_seconds = Histogram(
    "geo_http_request_duration_seconds",
    "HTTP request duration (seconds)",
    ["method", "path"],
)

# Бизнес-метрики (best-effort: метрики не должны ломать запросы)
routing_failures_total = Counter(
    "geo_routing_failures_total",
    "Routing failures",
    ["reason"],
)
payment_events_total = Counter(
    "geo_payment_events_total",
    "Payment events",
    ["event", "result"],
)
clearing_events_total = Counter(
    "geo_clearing_events_total",
    "Clearing events",
    ["event", "result"],
)
recovery_events_total = Counter(
    "geo_recovery_events_total",
    "Recovery/maintenance events",
    ["event", "result"],
)

# === Метрики целостности системы ===

# Счётчики проверок
integrity_checks_total = Counter(
    'geo_integrity_checks_total', 
    'Total integrity checks performed',
    ['equivalent', 'check_type', 'result']  # result: passed|failed
)

integrity_violations_total = Counter(
    'geo_integrity_violations_total',
    'Total integrity violations detected',
    ['equivalent', 'violation_type', 'severity']
)

# Гистограммы времени проверок
integrity_check_duration = Histogram(
    'geo_integrity_check_duration_seconds',
    'Time spent on integrity checks',
    ['check_type']
)

# Gauges состояния
integrity_status = Gauge(
    'geo_integrity_status',
    'Current integrity status (1=healthy, 0=violation)',
    ['equivalent']
)

zero_sum_balance = Gauge(
    'geo_zero_sum_balance',
    'Current zero-sum balance (should be 0)',
    ['equivalent']
)

trust_limit_violations_current = Gauge(
    'geo_trust_limit_violations_current',
    'Current number of trust limit violations',
    ['equivalent']
)

debt_symmetry_violations_current = Gauge(
    'geo_debt_symmetry_violations_current', 
    'Current number of debt symmetry violations',
    ['equivalent']
)

last_checkpoint_age_seconds = Gauge(
    'geo_last_checkpoint_age_seconds',
    'Time since last integrity checkpoint',
    ['equivalent']
)
```

### 9.2. Health Checks

```python
@app.get("/health")
async def health_check():
    return {"status": "ok"}

@app.get("/healthz")
async def healthz_check():
    return {"status": "ok"}

@app.get("/health/db")
async def health_db_check():
    """Проверка доступности БД"""
    return {"status": "ok"}
```

### 9.3. Логирование

```python
import structlog

logger = structlog.get_logger()

# Structured logging
logger.info(
    "payment_created",
    tx_id=tx.tx_id,
    from_pid=tx.from_pid,
    to_pid=tx.to_pid,
    amount=str(tx.amount),
    equivalent=tx.equivalent,
)
```

### 9.4. Алерты

| Метрика | Порог | Действие |
|---------|-------|----------|
| Error rate | > 1% | PagerDuty |
| Payment latency p99 | > 5s | Warning |
| DB connections | > 80% | Warning |
| Disk usage | > 85% | Critical |
| Failed clearings | > 10/hour | Warning |

**Алерты целостности системы:**

| Метрика | Порог | Severity | Действие |
|---------|-------|----------|----------|
| `geo_integrity_status` | = 0 | **CRITICAL** | PagerDuty + блокировка операций |
| `geo_zero_sum_balance` | ≠ 0 | **CRITICAL** | PagerDuty + немедленное расследование |
| `geo_trust_limit_violations_current` | > 0 | **HIGH** | Slack + заморозка линий |
| `geo_debt_symmetry_violations_current` | > 0 | **HIGH** | Slack + ручная проверка |
| `geo_last_checkpoint_age_seconds` | > 600 (10 мин) | **MEDIUM** | Warning в логах |
| `geo_integrity_checks_total{result="failed"}` | > 0 за 5 мин | **HIGH** | Slack + расследование |
| `geo_integrity_check_duration_seconds` p99 | > 30s | **MEDIUM** | Warning + оптимизация |

**Пример конфигурации Prometheus Alertmanager:**

```yaml
groups:
  - name: geo_integrity_alerts
    rules:
      - alert: IntegrityViolationCritical
        expr: geo_integrity_status == 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "CRITICAL: Integrity violation detected"
          description: "Equivalent {{ $labels.equivalent }} has integrity status 0"
          
      - alert: ZeroSumViolation
        expr: abs(geo_zero_sum_balance) > 0.01
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "CRITICAL: Zero-sum invariant violated"
          description: "Balance for {{ $labels.equivalent }} is {{ $value }}, should be 0"
          
      - alert: TrustLimitViolation
        expr: geo_trust_limit_violations_current > 0
        for: 1m
        labels:
          severity: high
        annotations:
          summary: "Trust limit violations detected"
          description: "{{ $value }} trust limit violations in {{ $labels.equivalent }}"
          
      - alert: CheckpointStale
        expr: geo_last_checkpoint_age_seconds > 600
        for: 5m
        labels:
          severity: medium
        annotations:
          summary: "Integrity checkpoint is stale"
          description: "Last checkpoint for {{ $labels.equivalent }} was {{ $value }}s ago"
```

### 9.5. Backup & Recovery

```bash
# Ежедневный backup PostgreSQL
pg_dump -Fc geo_hub > backup_$(date +%Y%m%d).dump

# Восстановление
pg_restore -d geo_hub backup_20251129.dump

# Point-in-time recovery
# Требует настройки WAL archiving
```

---

---

## 10. Будущее развитие: путь к децентрализации

Текущая архитектура GEO v0.1 является **осознанным компромиссом** для быстрого запуска MVP. Hub-центричная модель проще в разработке, отладке и поддержке, но не соответствует полностью децентрализованной концепции оригинального GEO Protocol.

Этот раздел описывает планируемую эволюцию к целевой архитектуре.

### 10.1. Сравнение текущей и целевой архитектуры

| Аспект | Текущая (v0.1) | Целевая (v2.0+) |
|--------|----------------|-----------------|
| **Топология** | Hub как центр | P2P сеть с опциональными Hub'ами |
| **Хранение данных** | PostgreSQL на Hub | Распределённая БД (trustNet) |
| **Консенсус** | 2PC с Hub-координатором | Локальный между участниками |
| **Приватность** | Hub видит все транзакции | Участники видят только свои |
| **Single Point of Failure** | Hub | Нет |
| **Масштабирование** | Вертикальное (один Hub) | Горизонтальное (сеть узлов) |

### 10.2. Архитектурная roadmap

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Путь к децентрализации                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  v0.1 (MVP)          v1.0 (Stable)       v1.5 (Federation)     v2.0 (P2P)      │
│  ──────────          ────────────        ────────────────      ────────        │
│                                                                                 │
│  ┌─────────┐        ┌─────────┐         ┌─────────────┐      ┌───────────┐    │
│  │ Single  │        │ Multiple│         │ Connected   │      │Full P2P   │    │
│  │ Hub     │───────►│ Hubs    │────────►│ Federation  │─────►│Network    │    │
│  │         │        │(isolated)│         │             │      │           │    │
│  └─────────┘        └─────────┘         └─────────────┘      └───────────┘    │
│                                                                                 │
│  Функции:           Добавлено:          Добавлено:           Добавлено:       │
│  • Basic protocol   • Stability         • Inter-Hub proto    • No central Hub │
│  • Single community • Production-ready  • Hub discovery      • trustNet DB    │
│  • Web + Mobile     • Monitoring        • Cross-community    • Local consensus│
│                     • Disputes            payments           • Full privacy    │
│                                                                                 │
│  Timeline:          Timeline:           Timeline:            Timeline:         │
│  Q1 2025            Q2-Q3 2025          Q4 2025              2026+             │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 10.3. Локальный консенсус (v2.0)

В целевой архитектуре транзакции подтверждаются **только участниками операции**, без центрального координатора.

**Текущий протокол (v0.1):**
```
A хочет заплатить C через B

1. A → Hub: PAYMENT_REQUEST
2. Hub: находит маршрут A → B → C
3. Hub → B: PREPARE
4. Hub → C: PREPARE  
5. Hub: собирает ответы
6. Hub → A,B,C: COMMIT или ABORT

Hub — точка координации и потенциального отказа
```

**Целевой протокол (v2.0):**
```
A хочет заплатить C через B

1. A: находит маршрут (запрос к B, C напрямую или через индекс)
2. A → B: PREPARE(signed_by_A)
3. B: проверяет, подписывает
4. B → C: PREPARE(signed_by_A, signed_by_B)
5. C: проверяет, подписывает
6. C → B → A: COMMIT(all_signatures)

Нет единого координатора, каждый участник подписывает своё
```

**Криптографический пакет:**

```python
class TransactionPacket:
    """
    Криптографический пакет для локального консенсуса.
    
    Содержит все данные операции и подписи всех участников.
    Атомарность гарантируется: пакет действителен только когда все подписали.
    """
    
    tx_id: str
    operation_type: str  # PAYMENT, CLEARING
    
    # Участники пути
    participants: list[str]  # [A, B, C]
    
    # Эффекты для каждого участника
    effects: dict[str, Effect]  # {A: -100, B: 0, C: +100}
    
    # Подписи участников
    signatures: dict[str, Signature]  # {A: sig_a, B: sig_b, C: sig_c}
    
    # Таймстампы
    created_at: datetime
    expires_at: datetime
    
    def is_complete(self) -> bool:
        """Пакет полный, когда все участники подписали"""
        return set(self.participants) == set(self.signatures.keys())
    
    def verify_all_signatures(self) -> bool:
        """Проверить все подписи"""
        for pid, sig in self.signatures.items():
            pubkey = get_public_key(pid)
            if not verify_signature(self.canonical_bytes(), sig, pubkey):
                return False
        return True
```

### 10.4. Распределённая база данных trustNet (v2.0)

**Концепция:**

В оригинальном GEO Protocol описан trustNet — распределённая БД, где:
- Нет центрального хранилища всех данных
- Каждый участник хранит только свои данные
- При операциях данные синхронизируются между затронутыми участниками

```
┌─────────────────────────────────────────────────────────────┐
│                     trustNet Architecture                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │ Node A   │    │ Node B   │    │ Node C   │              │
│  │          │    │          │    │          │              │
│  │ TL: A→B  │◄──►│ TL: A→B  │    │ TL: B→C  │              │
│  │ TL: A→X  │    │ TL: B→C  │◄──►│ TL: C→D  │              │
│  │ Debt: B  │    │ TL: B→Y  │    │ Debt: B  │              │
│  │          │    │ Debt: A,C│    │          │              │
│  └──────────┘    └──────────┘    └──────────┘              │
│       │               │               │                     │
│       └───────────────┼───────────────┘                     │
│                       │                                      │
│              ┌────────▼────────┐                            │
│              │  Routing Index  │ (опционально, для ускорения)│
│              │  (Hub или DHT)  │                            │
│              └─────────────────┘                            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Синхронизация данных:**

```python
class TrustNetNode:
    """
    Узел распределённой сети trustNet.
    
    Хранит только данные, касающиеся этого участника.
    """
    
    def __init__(self, participant_id: str):
        self.pid = participant_id
        self.local_store = LocalStorage()  # SQLite или аналог
    
    async def store_trust_line(
        self,
        trust_line: TrustLine
    ) -> None:
        """
        Сохранить TrustLine.
        
        TrustLine хранится на обоих концах:
        - У создателя (from)
        - У получателя (to)
        """
        await self.local_store.save(trust_line)
        
        # Синхронизировать с другим концом
        other_pid = trust_line.to if trust_line.from_ == self.pid else trust_line.from_
        await self.sync_with_peer(other_pid, trust_line)
    
    async def sync_with_peer(
        self,
        peer_pid: str,
        data: Any
    ) -> None:
        """Синхронизировать данные с другим узлом"""
        peer_endpoint = await self.discover_peer(peer_pid)
        await send_sync_message(peer_endpoint, data)
```

### 10.5. Hub как опциональный индексатор (v2.0)

В целевой архитектуре Hub не исчезает, но **меняет роль**:

| Роль Hub | v0.1 (обязательно) | v2.0 (опционально) |
|----------|--------------------|--------------------|
| Хранение данных | ✅ Все данные | ❌ Только индексы |
| Координация транзакций | ✅ 2PC | ❌ Не участвует |
| Маршрутизация | ✅ Поиск путей | ✅ Ускоренный индекс |
| Резервное хранение | ❌ | ✅ Опциональный backup |

**Hub как индексатор:**

```python
class HubIndexService:
    """
    Hub как индексатор для ускорения маршрутизации.
    
    НЕ хранит суммы, только граф связей.
    """
    
    async def index_trust_line(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str
    ) -> None:
        """
        Добавить ребро в граф.
        
        Лимит и долг НЕ хранятся — только факт наличия связи.
        """
        await self.graph.add_edge(from_pid, to_pid, equivalent)
    
    async def find_potential_paths(
        self,
        source: str,
        target: str,
        equivalent: str,
        max_hops: int = 6
    ) -> list[list[str]]:
        """
        Найти потенциальные пути в графе.
        
        Возвращает только топологию, без ёмкостей.
        Участник сам запрашивает ёмкости у узлов на пути.
        """
        return await self.graph.bfs_paths(source, target, max_hops)
```

### 10.6. Миграция данных

При переходе от v0.1 к v2.0 необходима миграция данных с Hub на клиенты:

```python
class MigrationService:
    """Миграция данных с Hub на распределённую архитектуру"""
    
    async def export_participant_data(
        self,
        participant_id: str
    ) -> ParticipantDataBundle:
        """
        Экспорт всех данных участника для миграции на его устройство.
        
        Включает:
        - TrustLines (входящие и исходящие)
        - Текущие долги
        - История транзакций (опционально)
        - Подписанные пакеты незавершённых операций
        """
    
    async def verify_migration_integrity(
        self,
        hub_checksum: str,
        distributed_checksum: str
    ) -> bool:
        """
        Проверить, что данные после миграции идентичны.
        
        Использует контрольные суммы состояния.
        """
```

### 10.7. Обратная совместимость

Переход к v2.0 будет **постепенным**:

```
┌─────────────────────────────────────────────────────────────┐
│               Гибридный режим (переходный период)            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────┐                                ┌─────────┐     │
│  │ Legacy  │                                │ P2P     │     │
│  │ Client  │◄────── Hub (coordinator) ─────►│ Node    │     │
│  │ (v0.1)  │                                │ (v2.0)  │     │
│  └─────────┘                                └─────────┘     │
│       │                                          │          │
│       │        Платёж между старым и новым       │          │
│       │                                          │          │
│       └──────────────── Hub ─────────────────────┘          │
│                    (транслирует протоколы)                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘

Hub умеет:
- Принимать запросы от legacy клиентов (v0.1 протокол)
- Взаимодействовать с P2P нодами (v2.0 протокол)
- Транслировать между протоколами
```

---

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [01-concepts.md](01-concepts.md) — Ключевые концепции
- [02-protocol-spec.md](02-protocol-spec.md) — Спецификация протокола
- [04-api-reference.md](04-api-reference.md) — Справочник API
- [05-deployment.md](05-deployment.md) — Развёртывание
