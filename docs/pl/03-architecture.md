# GEO Hub: Architektura systemu

**Wersja:** 0.1  
**Data:** Listopad 2025

---

## Spis treści

1. [Przegląd architektury](#1-przegląd-architektury)
2. [Etap 1: MVP — Bazowy Hub](#2-etap-1-mvp--bazowy-hub)
3. [Etap 2: Rozszerzone możliwości](#3-etap-2-rozszerzone-możliwości)
4. [Etap 3: Federacja hubów](#4-etap-3-federacja-hubów)
5. [Etap 4: Zaawansowane scenariusze](#5-etap-4-zaawansowane-scenariusze)
6. [Stos technologiczny](#6-stos-technologiczny)
7. [Model danych](#7-model-danych)
8. [Bezpieczeństwo](#8-bezpieczeństwo)
9. [Monitoring i eksploatacja](#9-monitoring-i-eksploatacja)
10. [Przyszły rozwój: droga do decentralizacji](#10-przyszły-rozwój-droga-do-decentralizacji)

---

## 1. Przegląd architektury

### 1.1. Zasady

| Zasada | Opis |
|--------|------|
| **Prostota** | Minimum komponentów, czytelna struktura |
| **Modułowość** | Rdzeń + dodatki, wyraźne granice |
| **Rozszerzalność** | Możliwość ewolucji bez przepisywania od zera |
| **Testowalność** | Izolowane komponenty, czyste interfejsy |

### 1.2. Schemat wysokiego poziomu

```
┌─────────────────────────────────────────────────────────────┐
│                      Klienci                                │
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

### 1.3. Mapa ewolucji

```
Etap 1: MVP              Etap 2: Extended       Etap 3: Federation     Etap 4: Advanced
─────────────────────────────────────────────────────────────────────────────────────────►

┌─────────────────┐     ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ Single Hub      │     │ + Offline       │    │ + Inter-Hub     │    │ + P2P Nodes     │
│ Basic Protocol  │────►│ + Analytics     │───►│   Protocol      │───►│ + Consensus     │
│ Web + Mobile    │     │ + Disputes      │    │ + Discovery     │    │ + Sharding      │
│ PostgreSQL      │     │ + KYC Hooks     │    │ + Routing       │    │                 │
└─────────────────┘     └─────────────────┘    └─────────────────┘    └─────────────────┘
```

---

## 2. Etap 1: MVP — Bazowy Hub

### 2.1. Zakres MVP

**Zawiera:**

- Rejestrację uczestników z kluczami Ed25519
- Zarządzanie liniami zaufania
- Płatności z routingiem (single + multi-path)
- Automatyczny kliring (cykle 3–4)
- REST API + powiadomienia WebSocket
- Podstawowy panel admina
- Klient Flutter (mobile + desktop)

**Nie zawiera (odroczone):**

- Interakcji między hubami
- Rozszerzonej analityki
- Trybu offline klienta
- Integracji KYC
- Mechanizmu sporów

### 2.2. Komponenty MVP

```
geo-hub/
├── app/
│   ├── __init__.py
│   ├── main.py                 # Aplikacja FastAPI
│   ├── config.py               # Konfiguracja
│   │
│   ├── api/                    # Warstwa API
│   │   ├── __init__.py
│   │   ├── deps.py             # Zależności (auth, sesja DB)
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py       # Główny router
│   │   │   ├── auth.py         # Endpointy auth
│   │   │   ├── participants.py # CRUD uczestników
│   │   │   ├── trustlines.py   # Operacje na TrustLine
│   │   │   ├── payments.py     # Operacje płatności
│   │   │   ├── clearing.py     # Operacje kliringu
│   │   │   └── websocket.py    # Handlery WebSocket
│   │   └── admin/
│   │       ├── __init__.py
│   │       └── routes.py       # Trasy panelu admina
│   │
│   ├── core/                   # Core Services
│   │   ├── __init__.py
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── service.py      # AuthService
│   │   │   └── crypto.py       # Operacje Ed25519
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
│   │   │   └── cycles.py       # Wykrywanie cykli
│   │   ├── integrity/
│   │   │   ├── __init__.py
│   │   │   ├── service.py      # IntegrityChecker
│   │   │   ├── invariants.py   # Definicje inwariantów
│   │   │   ├── checksum.py     # Wyliczanie checksum stanu
│   │   │   └── recovery.py     # Procedury odzyskiwania
│   │   └── events/
│   │       ├── __init__.py
│   │       └── bus.py          # Wewnętrzna szyna zdarzeń
│   │
│   ├── models/                 # Modele Pydantic
│   │   ├── __init__.py
│   │   ├── participant.py
│   │   ├── trustline.py
│   │   ├── debt.py
│   │   ├── transaction.py
│   │   └── messages.py         # Wiadomości protokołu
│   │
│   ├── db/                     # Baza danych
│   │   ├── __init__.py
│   │   ├── base.py             # Bazowy model
│   │   ├── session.py          # Zarządzanie sesją
│   │   └── models/             # Modele SQLAlchemy
│   │       ├── __init__.py
│   │       ├── participant.py
│   │       ├── trustline.py
│   │       ├── debt.py
│   │       └── transaction.py
│   │
│   └── templates/              # Szablony Jinja2 dla admina
│       ├── base.html
│       ├── dashboard.html
│       └── ...
│
├── migrations/                 # Migracje Alembic
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

### 2.3. Serwisy MVP

#### AuthService

```python
class AuthService:
    """Autentykacja i zarządzanie sesjami"""
    
    async def register(
        self, 
        public_key: bytes, 
        display_name: str,
        profile: dict
    ) -> Participant:
        """Rejestracja nowego uczestnika"""
        
    async def verify_signature(
        self,
        message: bytes,
        signature: bytes,
        public_key: bytes
    ) -> bool:
        """Weryfikacja podpisu Ed25519"""
        
    async def create_session(
        self,
        participant_id: str,
        device_info: dict
    ) -> Session:
        """Tworzenie sesji JWT"""
```

#### TrustLineService

```python
class TrustLineService:
    """Zarządzanie liniami zaufania"""
    
    async def create(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        limit: Decimal,
        policy: TrustLinePolicy,
        signature: bytes
    ) -> TrustLine:
        """Tworzenie linii zaufania"""
        
    async def update(
        self,
        trust_line_id: UUID,
        limit: Decimal | None,
        policy: TrustLinePolicy | None,
        signature: bytes
    ) -> TrustLine:
        """Aktualizacja linii"""
        
    async def close(
        self,
        trust_line_id: UUID,
        signature: bytes
    ) -> TrustLine:
        """Zamykanie linii"""
        
    async def get_available_credit(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str
    ) -> Decimal:
        """Obliczanie dostępnego kredytu"""
```

#### RoutingService

```python
class RoutingService:
    """Routing płatności"""
    
    async def find_paths(
        self,
        source: str,
        target: str,
        equivalent: str,
        amount: Decimal,
        constraints: RoutingConstraints
    ) -> list[PaymentPath]:
        """Wyszukiwanie ścieżek dla płatności"""
        
    async def split_payment(
        self,
        amount: Decimal,
        paths: list[PaymentPath]
    ) -> list[PaymentRoute]:
        """Podział płatności na trasy"""
```

#### PaymentEngine

```python
class PaymentEngine:
    """Wykonanie płatności"""
    
    async def create_payment(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        amount: Decimal,
        description: str,
        signature: bytes
    ) -> Transaction:
        """Tworzenie i wykonanie płatności"""
        
    async def prepare(
        self,
        tx_id: UUID,
        routes: list[PaymentRoute]
    ) -> PrepareResult:
        """Faza PREPARE"""
        
    async def commit(self, tx_id: UUID) -> Transaction:
        """Faza COMMIT"""
        
    async def abort(self, tx_id: UUID, reason: str) -> Transaction:
        """Faza ABORT"""
```

#### ClearingEngine

```python
class ClearingEngine:
    """Kliring długów"""
    
    async def find_cycles(
        self,
        equivalent: str,
        max_length: int = 4,
        min_amount: Decimal = Decimal("0.01")
    ) -> list[ClearingCandidate]:
        """Szukanie cykli do kliringu"""
        
    async def execute_clearing(
        self,
        cycle: list[str],
        equivalent: str,
        amount: Decimal
    ) -> Transaction:
        """Wykonanie kliringu"""
        
    async def process_triggered(
        self,
        changed_edges: list[tuple[str, str, str]]
    ) -> list[Transaction]:
        """Kliring wyzwalany po transakcji"""
```

#### IntegrityChecker

```python
class IntegrityChecker:
    """
    Serwis sprawdzania integralności systemu.
    
    Zapewnia weryfikację inwariantów, sum kontrolnych
    i poprawności kliringu.
    """
    
    async def check_zero_sum(
        self,
        equivalent: str
    ) -> ZeroSumCheckResult:
        """
        Sprawdzenie inwariantu sumy zerowej.
        
        Suma wszystkich sald dla ekwiwalentu musi wynosić 0.
        """
        
    async def check_trust_limits(
        self,
        equivalent: str
    ) -> TrustLimitCheckResult:
        """
        Sprawdzenie, że wszystkie długi mieszczą się w limitach zaufania.
        """
        
    async def check_debt_symmetry(
        self,
        equivalent: str
    ) -> DebtSymmetryCheckResult:
        """
        Sprawdzenie, że między parą uczestników dług jest tylko w jednym kierunku.
        """
        
    async def verify_clearing_neutrality(
        self,
        cycle: list[str],
        amount: Decimal,
        equivalent: str,
        positions_before: dict[str, Decimal]
    ) -> bool:
        """
        Sprawdzenie, że kliring nie zmienił pozycji netto uczestników.
        """
        
    async def compute_state_checksum(
        self,
        equivalent: str
    ) -> str:
        """
        Wyliczenie SHA-256 sumy kontrolnej stanu długów.
        """
        
    async def run_full_check(
        self,
        equivalent: str
    ) -> IntegrityReport:
        """
        Wykonanie pełnego zestawu kontroli i utworzenie raportu.
        """
        
    async def save_checkpoint(
        self,
        equivalent: str,
        checksum: str
    ) -> IntegrityCheckpoint:
        """
        Zapisanie punktu kontrolnego stanu do późniejszego audytu.
        """
        
    async def handle_violation(
        self,
        violation: IntegrityViolation
    ) -> None:
        """
        Obsługa naruszenia integralności:
        - blokada operacji
        - powiadomienie administratorów
        - utworzenie raportu
        """
```

**Modele danych IntegrityChecker:**

```python
@dataclass
class IntegrityReport:
    """Raport z kontroli integralności"""
    equivalent: str
    timestamp: datetime
    checksum: str
    checks: dict[str, CheckResult]
    all_passed: bool
    violations: list[IntegrityViolation]

@dataclass  
class CheckResult:
    """Wynik pojedynczej kontroli"""
    name: str
    passed: bool
    value: Any
    details: dict | None = None

@dataclass
class IntegrityViolation:
    """Informacja o naruszeniu integralności"""
    type: str  # ZERO_SUM | TRUST_LIMIT | DEBT_SYMMETRY | CLEARING_NEUTRALITY
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    equivalent: str
    details: dict
    detected_at: datetime

@dataclass
class IntegrityCheckpoint:
    """Punkt kontrolny stanu"""
    id: UUID
    equivalent: str
    checksum: str
    timestamp: datetime
    total_debt: Decimal
    participant_count: int
    debt_count: int
```

### 2.4. Schemat danych MVP

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

-- Prepare Locks (dla 2PC)
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

-- Integrity Audit Log (dziennik audytu integralności)
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

-- Integrity Checkpoints (punkty kontrolne stanu)
CREATE TABLE integrity_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    equivalent_code VARCHAR(16) NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    total_debt DECIMAL(20, 8) NOT NULL,
    participant_count INTEGER NOT NULL,
    debt_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_checkpoints_equivalent ON integrity_checkpoints(equivalent_code, created_at DESC);

-- Integrity Violations (zarejestrowane naruszenia)
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

### 2.5. Endpointy API MVP

```
Auth:
  POST   /api/v1/auth/register          # Rejestracja
  POST   /api/v1/auth/login             # Logowanie (challenge-response)
  POST   /api/v1/auth/refresh           # Odświeżenie tokenu

Participants:
  GET    /api/v1/participants/me        # Bieżący uczestnik
  PATCH  /api/v1/participants/me        # Aktualizacja profilu
  GET    /api/v1/participants/{pid}     # Profil uczestnika
  GET    /api/v1/participants/search    # Wyszukiwanie uczestników

TrustLines:
  POST   /api/v1/trustlines             # Utworzenie linii
  GET    /api/v1/trustlines             # Lista linii
  GET    /api/v1/trustlines/{id}        # Szczegóły linii
  PATCH  /api/v1/trustlines/{id}        # Aktualizacja linii
  DELETE /api/v1/trustlines/{id}        # Zamknięcie linii

Payments:
  POST   /api/v1/payments               # Utworzenie płatności
  GET    /api/v1/payments               # Historia płatności
  GET    /api/v1/payments/{tx_id}       # Szczegóły płatności
  GET    /api/v1/payments/capacity      # Sprawdzenie pojemności

Balance:
  GET    /api/v1/balance                # Bilans ogólny
  GET    /api/v1/balance/debts          # Długi (przychodzące/wychodzące)
  GET    /api/v1/balance/history        # Historia zmian

WebSocket:
  WS     /api/v1/ws                     # Powiadomienia w czasie rzeczywistym
```

---

## 3. Etap 2: Rozszerzone możliwości

### 3.1. Dodatkowe komponenty

```
app/
├── core/
│   ├── analytics/              # Analityka
│   │   ├── service.py
│   │   └── reports.py
│   ├── disputes/               # Spory
│   │   ├── service.py
│   │   └── workflow.py
│   ├── verification/           # KYC
│   │   ├── service.py
│   │   └── providers/
│   │       ├── base.py
│   │       └── manual.py
│   └── offline/                # Tryb offline
│       ├── service.py
│       └── sync.py
│
└── addons/                     # System dodatków
    ├── __init__.py
    ├── base.py                 # Klasa bazowa dodatku
    ├── registry.py             # Rejestr dodatków
    └── hooks.py                # Hooki zdarzeń
```

### 3.2. System dodatków

```python
# addons/base.py
from abc import ABC, abstractmethod

class AddonBase(ABC):
    """Bazowa klasa dla dodatków"""
    
    name: str
    version: str
    
    @abstractmethod
    async def on_load(self, app) -> None:
        """Wywoływane przy załadowaniu dodatku"""
        pass
    
    @abstractmethod
    async def on_unload(self) -> None:
        """Wywoływane przy wyładowaniu"""
        pass
    
    def register_routes(self, router) -> None:
        """Rejestracja dodatkowych tras"""
        pass
    
    def register_hooks(self, event_bus) -> None:
        """Subskrypcja zdarzeń"""
        pass
```

```python
# Przykładowy dodatek
class TelegramNotificationsAddon(AddonBase):
    name = "telegram_notifications"
    version = "1.0.0"
    
    async def on_load(self, app):
        self.bot = TelegramBot(app.config.telegram_token)
        
    def register_hooks(self, event_bus):
        event_bus.subscribe("payment.committed", self.on_payment)
        event_bus.subscribe("clearing.committed", self.on_clearing)
    
    async def on_payment(self, event):
        # Wysyłka powiadomienia do odbiorcy
        pass
```

### 3.3. Mechanizm sporów

```python
class DisputeService:
    """Zarządzanie sporami"""
    
    async def open_dispute(
        self,
        tx_id: str,
        opened_by: str,
        reason: str,
        evidence: list[str],
        requested_outcome: str
    ) -> Dispute:
        """Otwarcie sporu"""
        
    async def respond_to_dispute(
        self,
        dispute_id: UUID,
        responder: str,
        response: str,
        evidence: list[str]
    ) -> Dispute:
        """Odpowiedź na spór"""
        
    async def resolve_dispute(
        self,
        dispute_id: UUID,
        resolver: str,
        resolution: DisputeResolution,
        compensation_tx: Transaction | None
    ) -> Dispute:
        """Rozwiązanie sporu"""
```

### 3.4. Tryb offline klienta

```python
class OfflineSyncService:
    """Synchronizacja operacji offline"""
    
    async def get_state_snapshot(
        self,
        participant_id: str
    ) -> StateSnapshot:
        """Migawka stanu do cache'u"""
        
    async def process_offline_queue(
        self,
        participant_id: str,
        operations: list[OfflineOperation]
    ) -> SyncResult:
        """Przetwarzanie kolejki operacji offline"""
        
    async def get_delta_since(
        self,
        participant_id: str,
        since: datetime
    ) -> list[StateChange]:
        """Zmiany od ostatniej synchronizacji"""
```

### 3.5. Hooki KYC

```python
class VerificationService:
    """Weryfikacja uczestników (KYC)"""
    
    async def request_verification(
        self,
        participant_id: str,
        level: int,
        documents: list[str]
    ) -> VerificationRequest:
        """Żądanie weryfikacji"""
        
    async def approve_verification(
        self,
        request_id: UUID,
        approver: str,
        level: int,
        notes: str
    ) -> Participant:
        """Zatwierdzenie weryfikacji"""
        
    async def check_verification_required(
        self,
        participant_id: str,
        operation: str,
        amount: Decimal
    ) -> bool:
        """Sprawdzenie wymogów weryfikacji"""
```

---

## 4. Etap 3: Federacja hubów

### 4.1. Architektura federacji

```
┌─────────────────────────────────────────────────────────────────┐
│                        Warstwa Federacji                        │
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

### 4.2. Komponenty federacji

```
app/
├── federation/
│   ├── __init__.py
│   ├── discovery/              # Odkrywanie hubów
│   │   ├── __init__.py
│   │   ├── service.py
│   │   ├── dns.py              # Discovery przez DNS
│   │   └── registry.py         # Centralny rejestr
│   ├── routing/                # Routing między hubami
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── path_finder.py
│   ├── protocol/               # Protokół między hubami
│   │   ├── __init__.py
│   │   ├── client.py           # Zapytania wychodzące
│   │   ├── server.py           # Handlery przychodzące
│   │   └── messages.py         # Wiadomości protokołu
│   └── settlement/             # Rozliczenia między hubami
│       ├── __init__.py
│       └── service.py
```

### 4.3. Discovery Service

```python
class HubDiscoveryService:
    """Odkrywanie i rejestracja hubów"""
    
    async def register_hub(
        self,
        hub_info: HubInfo
    ) -> HubRegistration:
        """Rejestracja hubu w sieci"""
        
    async def discover_hubs(
        self,
        filter_by: HubFilter | None = None
    ) -> list[HubInfo]:
        """Wyszukiwanie dostępnych hubów"""
        
    async def get_hub_by_participant(
        self,
        pid: str
    ) -> HubInfo | None:
        """Znajdź hub po PID uczestnika"""
        
    async def health_check(
        self,
        hub_endpoint: str
    ) -> HubHealthStatus:
        """Sprawdzenie dostępności hubu"""
```

### 4.4. Protokół Inter-Hub

```python
class InterHubProtocol:
    """Protokół interakcji między hubami"""
    
    async def request_payment(
        self,
        target_hub: str,
        request: InterHubPaymentRequest
    ) -> InterHubPaymentResponse:
        """Żądanie płatności do innego hubu"""
        
    async def prepare_payment(
        self,
        tx_id: str,
        effects: list[InterHubEffect]
    ) -> PrepareResponse:
        """Faza PREPARE dla płatności między hubami"""
        
    async def commit_payment(
        self,
        tx_id: str
    ) -> CommitResponse:
        """Faza COMMIT"""
        
    async def abort_payment(
        self,
        tx_id: str,
        reason: str
    ) -> AbortResponse:
        """Faza ABORT"""
```

### 4.5. Routing między hubami

```python
class InterHubRoutingService:
    """Routing między hubami"""
    
    async def find_inter_hub_path(
        self,
        source_hub: str,
        source_pid: str,
        target_hub: str,
        target_pid: str,
        equivalent: str,
        amount: Decimal
    ) -> InterHubRoute:
        """Znajdź trasę między hubami"""
        
    async def get_hub_capacity(
        self,
        hub_a: str,
        hub_b: str,
        equivalent: str
    ) -> Decimal:
        """Dostępna pojemność między hubami"""
```

---

## 5. Etap 4: Zaawansowane scenariusze

### 5.1. Węzły P2P (częściowa decentralizacja)

```
┌─────────────────────────────────────────────────────────────┐
│                    Sieć Hybrydowa                           │
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

### 5.2. Konsensus między hubami

Dla krytycznych operacji — rozproszony konsensus:

```python
class HubConsensusService:
    """Konsensus między hubami (w stylu Raft)"""
    
    async def propose_transaction(
        self,
        tx: Transaction,
        participating_hubs: list[str]
    ) -> ConsensusResult:
        """Propozycja transakcji do konsensusu"""
        
    async def vote(
        self,
        tx_id: str,
        vote: Vote
    ) -> None:
        """Głosowanie za/przeciw transakcji"""
```

### 5.3. Sharding danych

Dla skalowania dużych hubów:

```
┌─────────────────────────────────────────┐
│              Duży Hub                   │
│                                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │ Shard 1 │  │ Shard 2 │  │ Shard 3 │  │
│  │ A-H     │  │ I-P     │  │ Q-Z     │  │
│  └─────────┘  └─────────┘  └─────────┘  │
│                    │                     │
│              Koordynator                 │
└─────────────────────────────────────────┘
```

---

## 6. Stos technologiczny

### 6.1. Backend

| Komponent | Technologia | Uzasadnienie |
|----------|-------------|--------------|
| Język | Python 3.11+ | Prostota, ekosystem, „AI-friendly" |
| Framework | FastAPI | Async, OpenAPI, Pydantic |
| ORM | SQLAlchemy 2.x | Stabilność, migracje |
| Migracje | Alembic | Standard dla SQLAlchemy |
| Baza danych | PostgreSQL 15+ | ACID, JSONB, wydajność |
| Cache | Redis 7+ | Szybkość, pub/sub, locki |
| Kolejki | Redis/Arq | Prostota, wystarczy dla MVP |
| Testy | pytest | Standard w Pythonie |

### 6.2. Klienci

| Komponent | Technologia | Uzasadnienie |
|----------|-------------|--------------|
| Mobile/Desktop | Flutter (Dart) | Cross-platform |
| Admin panel | Jinja2 + HTMX | Prostota, bez SPA |
| Kryptografia | libsodium | Sprawdzona biblioteka Ed25519 |

### 6.3. Infrastruktura

| Komponent | Technologia | Uzasadnienie |
|----------|-------------|--------------|
| Kontenery | Docker | Standard |
| Orkiestracja | Docker Compose → K8s | Od prostego do złożonego |
| CI/CD | GitHub Actions | Integracja z repozytorium |
| Monitoring | Prometheus + Grafana | Standard branżowy |
| Logi | Loki / ELK | Centralizacja logów |

### 6.4. Zależności (pyproject.toml)

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
    "arq>=0.25.0",            # Zadania w tle
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

## 7. Model danych

### 7.1. Diagram ER

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

### 7.2. Indeksy wydajnościowe

```sql
-- Szybki routing
CREATE INDEX idx_trustlines_routing 
ON trust_lines(from_participant_id, to_participant_id, equivalent_id, status)
WHERE status = 'active';

-- Szybkie szukanie cykli
CREATE INDEX idx_debts_cycles 
ON debts(debtor_id, creditor_id, equivalent_id, amount)
WHERE amount > 0;

-- Historia transakcji uczestnika
CREATE INDEX idx_transactions_participant_history 
ON transactions(initiator_id, created_at DESC);
```

---

## 8. Bezpieczeństwo

### 8.1. Autentykacja

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

### 8.2. Autoryzacja

| Zasób | Właściciel | Uprawnienia |
|-------|-----------|-------------|
| TrustLine | `from` | Tworzenie, zmiana, zamknięcie |
| Uczestnik | sam uczestnik | Zmiana profilu |
| Płatność | inicjator | Tworzenie |
| Spór | uczestnicy TX | Otwarcie, odpowiedź |

### 8.3. Weryfikacja podpisów

```python
async def validate_signed_request(
    request: SignedRequest,
    expected_signer: str
) -> bool:
    """Weryfikacja podpisanego żądania"""
    
    # 1. Sprawdź format podpisu
    if not is_valid_signature_format(request.signature):
        raise InvalidSignature("Bad format")
    
    # 2. Pobierz klucz publiczny
    participant = await get_participant(expected_signer)
    if not participant:
        raise UnknownParticipant(expected_signer)
    
    # 3. Zweryfikuj podpis
    message = canonical_json(request.payload)
    if not verify_ed25519(message, request.signature, participant.public_key):
        raise InvalidSignature("Verification failed")
    
    # 4. Sprawdź timestamp (ochrona przed replay)
    if abs(now() - request.timestamp) > MAX_TIMESTAMP_DRIFT:
        raise ExpiredRequest("Timestamp drift too large")
    
    return True
```

### 8.4. Rate limiting

```python
RATE_LIMITS = {
    "auth/login": "5/minute",
    "payments": "30/minute",
    "trustlines": "10/minute",
    "default": "100/minute"
}
```

### 8.5. Ochrona danych

| Dane | Przechowywanie | Transmisja |
|------|----------------|-----------|
| Klucze prywatne | Tylko po stronie klienta | Nigdy |
| Hasła | bcrypt/argon2 | HTTPS |
| Sesje | JWT (krótkie życie) | HTTPS |
| PII | Szyfrowanie at rest | HTTPS |

### 8.6. Prywatność transakcji

W obecnej architekturze MVP hub przechowuje dane o transakcjach, co tworzy pewne ryzyka dla prywatności. Zastosowane zabezpieczenia:

- Szyfrowanie wrażliwych pól (np. opisy transakcji) po stronie DB (pgcrypto)
- Minimalizacja przechowywanych metadanych (brak IP, fingerprintów urządzeń w core DB)
- Polityki retencji (obcinanie szczegółów starych transakcji, pozostawienie agregatów)
- Mechanizmy eksportu i anonimizacji danych uczestnika (zgodność z RODO)

W przyszłych wersjach planowana jest:

- Dystrybucja danych (trustNet, lokalne magazyny u uczestników)
- Prywatny routing (w stylu onion routing – węzły pośrednie nie znają nadawcy/odbiorcy)
- Dowody wiedzy zerowej dla sald (udowadnianie wystarczającej pojemności bez ujawniania sum)

---

## 9. Monitoring i eksploatacja

### 9.1. Metryki

```python
from prometheus_client import Counter, Histogram, Gauge

# Liczniki
payments_total = Counter('geo_payments_total', 'Total payments', ['status'])
clearings_total = Counter('geo_clearings_total', 'Total clearings', ['status'])

# Histogramy
payment_duration = Histogram('geo_payment_duration_seconds', 'Payment processing time')
routing_duration = Histogram('geo_routing_duration_seconds', 'Path finding time')

# Gauges
active_participants = Gauge('geo_active_participants', 'Active participants count')
total_debt = Gauge('geo_total_debt', 'Total debt in system', ['equivalent'])

# Metryki integralności
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

integrity_check_duration = Histogram(
    'geo_integrity_check_duration_seconds',
    'Time spent on integrity checks',
    ['check_type']
)

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

### 9.2. Health checks

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
    """Gotowość do przyjmowania ruchu"""
    return {"ready": True}
```

### 9.3. Logowanie

```python
import structlog

logger = structlog.get_logger()

logger.info(
    "payment_created",
    tx_id=tx.tx_id,
    from_pid=tx.from_pid,
    to_pid=tx.to_pid,
    amount=str(tx.amount),
    equivalent=tx.equivalent,
)
```

### 9.4. Alerty

| Metryka | Próg | Działanie |
|---------|------|-----------|
| Error rate | > 1% | PagerDuty |
| Payment latency p99 | > 5 s | Warning |
| DB connections | > 80% | Warning |
| Disk usage | > 85% | Critical |
| Failed clearings | > 10/h | Warning |

Dla integralności:

| Metryka | Próg | Poziom | Działanie |
|---------|------|--------|-----------|
| `geo_integrity_status` | = 0 | CRITICAL | PagerDuty + blokada operacji |
| `geo_zero_sum_balance` | ≠ 0 | CRITICAL | PagerDuty + dochodzenie |
| `geo_trust_limit_violations_current` | > 0 | HIGH | Slack + zamrożenie linii |
| `geo_debt_symmetry_violations_current` | > 0 | HIGH | Slack + ręczna analiza |
| `geo_last_checkpoint_age_seconds` | > 600 | MEDIUM | Warning |
| `geo_integrity_checks_total{result="failed"}` | > 0 / 5 min | HIGH | Slack + analiza |
| `geo_integrity_check_duration_seconds` p99 | > 30 s | MEDIUM | Optymalizacja |

### 9.5. Backup & recovery (wysoki poziom)

- Regularne kopie bazy PostgreSQL (pg_dump, WAL archiving)
- Kopie Redis (AOF)
- Testy odtwarzania (disaster recovery drills)
- Backup konfiguracji i tajemnic (np. w Sealed Secrets / Vault)

---

## 10. Przyszły rozwój: droga do decentralizacji

Obecna architektura GEO v0.1 jest świadomym kompromisem — hub-centrycznym MVP, łatwym do wdrożenia i testowania. Docelowo system ma ewoluować w stronę maksymalnej decentralizacji.

### 10.1. Porównanie obecnej i docelowej architektury

| Aspekt | Obecna (v0.1) | Docelowa (v2.0+) |
|--------|---------------|------------------|
| Topologia | Hub jako centrum | Sieć P2P z opcjonalnymi hubami |
| Przechowywanie danych | PostgreSQL na hubie | Rozproszona baza (trustNet) |
| Konsensus | 2PC z koordynatorem | Lokalny, między uczestnikami |
| Prywatność | Hub widzi wszystkie TX | Uczestnik widzi tylko własne |
| Single Point of Failure | Hub | Brak |
| Skalowanie | Pionowe (jeden hub) | Poziome (wiele węzłów) |

### 10.2. Roadmap architektoniczny (skrót)

- **v0.1 (MVP)** — pojedynczy hub, podstawowy protokół, jedna społeczność
- **v1.0 (Stable)** — stabilizacja, hardening, produkcyjne wdrożenia
- **v1.5 (Federation)** — wiele hubów, protokół między hubami, federacja społeczności
- **v2.0 (P2P)** — pełna sieć P2P, lokalne magazyny, prywatny routing, brak centralnego koordynatora

---

## Powiązane dokumenty

- [00-overview.md](00-overview.md) — Przegląd projektu  
- [01-concepts.md](01-concepts.md) — Kluczowe koncepcje  
- [02-protocol-spec.md](02-protocol-spec.md) — Specyfikacja protokołu  
- [04-api-reference.md](04-api-reference.md) — Dokumentacja API  
- [05-deployment.md](05-deployment.md) — Wdrożenie
