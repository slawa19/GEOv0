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
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ Mobile App  │  │ Desktop App │  │   Admin Web Panel   │  │
│  │  (Flutter)  │  │  (Flutter)  │  │   (Jinja2+HTMX)     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼────────────────┼─────────────────────┼────────────┘
          │                │                     │
          └────────────────┼─────────────────────┘
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
- Платежи с маршрутизацией (single + multi-path)
- Автоматический клиринг (циклы 3–4)
- REST API + WebSocket уведомления
- Базовая админка
- Flutter клиент (mobile + desktop)

**Не включено (отложено):**
- Межхабовое взаимодействие
- Расширенная аналитика
- Офлайн-режим клиента
- KYC интеграции
- Механизм споров

### 2.2. Компоненты MVP

```
geo-hub/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Configuration
│   │
│   ├── api/                    # API Layer
│   │   ├── __init__.py
│   │   ├── deps.py             # Dependencies (auth, db session)
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py       # Main router
│   │   │   ├── auth.py         # Auth endpoints
│   │   │   ├── participants.py # Participant CRUD
│   │   │   ├── trustlines.py   # TrustLine operations
│   │   │   ├── payments.py     # Payment operations
│   │   │   ├── clearing.py     # Clearing operations
│   │   │   └── websocket.py    # WebSocket handlers
│   │   └── admin/
│   │       ├── __init__.py
│   │       └── routes.py       # Admin panel routes
│   │
│   ├── core/                   # Core Services
│   │   ├── __init__.py
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── service.py      # AuthService
│   │   │   └── crypto.py       # Ed25519 operations
│   │   ├── participants/
│   │   │   ├── __init__.py
│   │   │   └── service.py      # ParticipantService
│   │   ├── trustlines/
│   │   │   ├── __init__.py
│   │   │   └── service.py      # TrustLineService
│   │   ├── payments/
│   │   │   ├── __init__.py
│   │   │   ├── service.py      # PaymentEngine
│   │   │   └── routing.py      # RoutingService
│   │   ├── clearing/
│   │   │   ├── __init__.py
│   │   │   ├── service.py      # ClearingEngine
│   │   │   └── cycles.py       # Cycle detection
│   │   └── events/
│   │       ├── __init__.py
│   │       └── bus.py          # Internal event bus
│   │
│   ├── models/                 # Pydantic models
│   │   ├── __init__.py
│   │   ├── participant.py
│   │   ├── trustline.py
│   │   ├── debt.py
│   │   ├── transaction.py
│   │   └── messages.py         # Protocol messages
│   │
│   ├── db/                     # Database
│   │   ├── __init__.py
│   │   ├── base.py             # Base model
│   │   ├── session.py          # Session management
│   │   └── models/             # SQLAlchemy models
│   │       ├── __init__.py
│   │       ├── participant.py
│   │       ├── trustline.py
│   │       ├── debt.py
│   │       └── transaction.py
│   │
│   └── templates/              # Jinja2 templates for admin
│       ├── base.html
│       ├── dashboard.html
│       └── ...
│
├── migrations/                 # Alembic migrations
│   ├── versions/
│   └── env.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── pyproject.toml
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
    type VARCHAR(50) NOT NULL,
    initiator_id UUID REFERENCES participants(id),
    payload JSONB NOT NULL,
    signatures JSONB DEFAULT '[]',
    state VARCHAR(30) NOT NULL,
    error_info JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

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
```

### 2.5. API Endpoints MVP

```
Auth:
  POST   /api/v1/auth/register          # Регистрация
  POST   /api/v1/auth/login             # Логин (challenge-response)
  POST   /api/v1/auth/refresh           # Обновление токена

Participants:
  GET    /api/v1/participants/me        # Текущий участник
  PATCH  /api/v1/participants/me        # Обновление профиля
  GET    /api/v1/participants/{pid}     # Профиль участника
  GET    /api/v1/participants/search    # Поиск участников

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
  GET    /api/v1/balance/history        # История изменений

WebSocket:
  WS     /api/v1/ws                     # Real-time уведомления
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
| База данных | PostgreSQL 15+ | ACID, JSONB, производительность |
| Кэш | Redis 7+ | Скорость, pub/sub, locks |
| Очереди | Redis/Arq | Простота, достаточно для MVP |
| Тесты | pytest | Стандарт Python |

### 6.2. Клиенты

| Компонент | Технология | Обоснование |
|-----------|------------|-------------|
| Mobile/Desktop | Flutter (Dart) | Кроссплатформенность |
| Админка | Jinja2 + HTMX | Простота, без SPA |
| Криптография | libsodium | Ed25519, проверенная библиотека |

### 6.3. Инфраструктура

| Компонент | Технология | Обоснование |
|-----------|------------|-------------|
| Контейнеры | Docker | Стандарт |
| Оркестрация | Docker Compose → K8s | От простого к сложному |
| CI/CD | GitHub Actions | Интеграция с репозиторием |
| Мониторинг | Prometheus + Grafana | Стандарт индустрии |
| Логи | Loki / ELK | Централизованные логи |

### 6.4. Зависимости (pyproject.toml)

```toml
[project]
name = "geo-hub"
version = "0.1.0"
requires-python = ">=3.11"

dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "pydantic>=2.5.0",
    "sqlalchemy>=2.0.0",
    "alembic>=1.12.0",
    "asyncpg>=0.29.0",
    "redis>=5.0.0",
    "pynacl>=1.5.0",          # Ed25519
    "pyjwt>=2.8.0",
    "python-multipart>=0.0.6",
    "jinja2>=3.1.0",
    "httpx>=0.25.0",
    "arq>=0.25.0",            # Background tasks
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "black>=23.0.0",
    "ruff>=0.1.0",
    "mypy>=1.6.0",
]
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
│    │──── GET /auth/challenge ─────────►│                 │
│    │                                   │                 │
│    │◄─── { challenge: "random" } ──────│                 │
│    │                                   │                 │
│    │  sign(challenge, private_key)     │                 │
│    │                                   │                 │
│    │──── POST /auth/login ────────────►│                 │
│    │      { pid, signature }           │                 │
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

---

## 9. Мониторинг и эксплуатация

### 9.1. Метрики

```python
# Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge

# Счётчики
payments_total = Counter('geo_payments_total', 'Total payments', ['status'])
clearings_total = Counter('geo_clearings_total', 'Total clearings', ['status'])

# Гистограммы
payment_duration = Histogram('geo_payment_duration_seconds', 'Payment processing time')
routing_duration = Histogram('geo_routing_duration_seconds', 'Path finding time')

# Gauges
active_participants = Gauge('geo_active_participants', 'Active participants count')
total_debt = Gauge('geo_total_debt', 'Total debt in system', ['equivalent'])
```

### 9.2. Health Checks

```python
@app.get("/health")
async def health_check():
    checks = {
        "database": await check_database(),
        "redis": await check_redis(),
        "disk_space": check_disk_space(),
    }
    
    status = "healthy" if all(checks.values()) else "unhealthy"
    return {"status": status, "checks": checks}

@app.get("/ready")
async def readiness_check():
    """Готовность принимать трафик"""
    return {"ready": True}
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

## Связанные документы

- [00-overview.md](00-overview.md) — Обзор проекта
- [01-concepts.md](01-concepts.md) — Ключевые концепции
- [02-protocol-spec.md](02-protocol-spec.md) — Спецификация протокола
- [04-api-reference.md](04-api-reference.md) — Справочник API
- [05-deployment.md](05-deployment.md) — Развёртывание
