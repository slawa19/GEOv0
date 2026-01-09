# GEO Hub: System Architecture

**Version:** 0.1  
**Date:** November 2025

---

## Contents

1. [Architecture Overview](#1-architecture-overview)  
2. [Stage 1: MVP — Basic Hub](#2-stage-1-mvp--basic-hub)  
3. [Stage 2: Extended Features](#3-stage-2-extended-features)  
4. [Stage 3: Hub Federation](#4-stage-3-hub-federation)  
5. [Stage 4: Advanced Scenarios](#5-stage-4-advanced-scenarios)  
6. [Technology Stack](#6-technology-stack)  
7. [Data Model](#7-data-model)  
8. [Security](#8-security)  
9. [Monitoring and Operations](#9-monitoring-and-operations)  
10. [Future Development: Path to Decentralization](#10-future-development-path-to-decentralization)

---

## 1. Architecture Overview

### 1.1. Principles

| Principle        | Description                                   |
|------------------|-----------------------------------------------|
| **Simplicity**   | Minimal components, clear structure           |
| **Modularity**   | Core + addons, clearly defined boundaries     |
| **Extensibility**| Ability to evolve without heavy refactors     |
| **Testability**  | Isolated components, clean interfaces         |

### 1.2. High-Level Diagram

```text
┌─────────────────────────────────────────────────────────────┐
│                          Clients                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ Mobile App  │  │ Desktop App │  │   Admin Web Panel   │ │
│  │  (Flutter)  │  │  (Flutter)  │  │   (Jinja2+HTMX)     │ │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
└─────────┼────────────────┼─────────────────────┼────────────┘
          │                │                     │
          └────────────────┼─────────────────────┘
                           │ HTTPS / WebSocket
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                        Community Hub                        │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                      API Layer                      │   │
│  │  ┌─────────┐  ┌──────────┐  ┌─────────────────┐    │   │
│  │  │  REST   │  │WebSocket │  │   Admin Routes  │    │   │
│  │  │   API   │  │  Server  │  │                 │    │   │
│  │  └────┬────┘  └────┬─────┘  └────────┬────────┘    │   │
│  └───────┼────────────┼─────────────────┼─────────────┘   │
│          └────────────┼─────────────────┘                 │
│                       ▼                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                    Core Services                    │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │   │
│  │  │   Auth   │ │TrustLine │ │ Payment  │ │Clearing│  │   │
│  │  │ Service  │ │ Service  │ │  Engine  │ │ Engine │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────┘  │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐ │   │
│  │  │ Routing  │ │Reporting │ │    Addon Manager     │ │   │
│  │  │ Service  │ │ Service  │ │                      │ │   │
│  │  └──────────┘ └──────────┘ └──────────────────────┘ │   │
│  │  ┌───────────────────────────────────────────────┐  │   │
│  │  │               Integrity Checker               │  │   │
│  │  │ (Zero-Sum, Limits, Checksum verification)    │  │   │
│  │  └───────────────────────────────────────────────┘  │   │
│  └─────────────────────────────────────────────────────┘   │
│                       │                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                      Data Layer                     │   │
│  │  ┌──────────────┐        ┌──────────────┐          │   │
│  │  │  PostgreSQL  │        │    Redis     │          │   │
│  │  │   (primary)  │        │ (cache/locks)│          │   │
│  │  └──────────────┘        └──────────────┘          │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 1.3. Evolution Map

```text
Stage 1: MVP              Stage 2: Extended        Stage 3: Federation      Stage 4: Advanced
─────────────────────────────────────────────────────────────────────────────────────────────►

┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Single Hub      │     │ + Offline       │     │ + Inter-Hub     │     │ + P2P Nodes     │
│ Basic Protocol  │────►│ + Analytics     │────►│   Protocol      │────►│ + Consensus     │
│ Web + Mobile    │     │ + Disputes      │     │ + Discovery     │     │ + Sharding      │
│ PostgreSQL      │     │ + KYC Hooks     │     │ + Routing       │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
```

---

## 2. Stage 1: MVP — Basic Hub

### 2.1. MVP Scope

**Included:**

- Participant registration with Ed25519 keys  
- Trust line management  
- Payments with routing (single + multi-path)  
- Automatic clearing (cycles of length 3–4)  
- REST API + WebSocket notifications  
- Basic admin panel  
- Flutter client (mobile + desktop)  

**Not included (postponed):**

- Inter-hub interaction  
- Extended analytics  
- Client offline mode  
- KYC integrations  
- Dispute mechanism  

### 2.2. MVP Components

```text
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
│   │   ├── integrity/
│   │   │   ├── __init__.py
│   │   │   ├── service.py      # IntegrityChecker
│   │   │   ├── invariants.py   # Invariant definitions
│   │   │   ├── checksum.py     # State checksum calculations
│   │   │   └── recovery.py     # Recovery procedures
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

### 2.3. MVP Services

#### AuthService

```python
class AuthService:
    """Authentication and session management."""

    async def register(
        self,
        public_key: bytes,
        display_name: str,
        profile: dict
    ) -> Participant:
        """Register a new participant."""

    async def verify_signature(
        self,
        message: bytes,
        signature: bytes,
        public_key: bytes
    ) -> bool:
        """Verify Ed25519 signature."""

    async def create_session(
        self,
        participant_id: str,
        device_info: dict
    ) -> "Session":
        """Create a JWT session."""
```

#### TrustLineService

```python
class TrustLineService:
    """Trust line management."""

    async def create(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        limit: Decimal,
        policy: "TrustLinePolicy",
        signature: bytes
    ) -> "TrustLine":
        """Create a trust line."""

    async def update(
        self,
        trust_line_id: UUID,
        limit: Decimal | None,
        policy: "TrustLinePolicy | None",
        signature: bytes
    ) -> "TrustLine":
        """Update an existing trust line."""

    async def close(
        self,
        trust_line_id: UUID,
        signature: bytes
    ) -> "TrustLine":
        """Close a trust line."""

    async def get_available_credit(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str
    ) -> Decimal:
        """Calculate available credit."""
```

#### RoutingService

```python
class RoutingService:
    """Payment routing."""

    async def find_paths(
        self,
        source: str,
        target: str,
        equivalent: str,
        amount: Decimal,
        constraints: "RoutingConstraints"
    ) -> list["PaymentPath"]:
        """Find paths for a payment."""

    async def split_payment(
        self,
        amount: Decimal,
        paths: list["PaymentPath"]
    ) -> list["PaymentRoute"]:
        """Split a payment across routes."""
```

#### PaymentEngine

```python
class PaymentEngine:
    """Payment execution."""

    async def create_payment(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        amount: Decimal,
        description: str,
        signature: bytes
    ) -> "Transaction":
        """Create and execute a payment."""

    async def prepare(
        self,
        tx_id: UUID,
        routes: list["PaymentRoute"]
    ) -> "PrepareResult":
        """PREPARE phase."""

    async def commit(self, tx_id: UUID) -> "Transaction":
        """COMMIT phase."""

    async def abort(self, tx_id: UUID, reason: str) -> "Transaction":
        """ABORT phase."""
```

#### ClearingEngine

```python
class ClearingEngine:
    """Debt clearing."""

    async def find_cycles(
        self,
        equivalent: str,
        max_length: int = 4,
        min_amount: Decimal = Decimal("0.01")
    ) -> list["ClearingCandidate"]:
        """Find cycles for clearing."""

    async def execute_clearing(
        self,
        cycle: list[str],
        equivalent: str,
        amount: Decimal
    ) -> "Transaction":
        """Execute a clearing operation."""

    async def process_triggered(
        self,
        changed_edges: list[tuple[str, str, str]]
    ) -> list["Transaction"]:
        """Triggered clearing after a transaction."""
```

#### IntegrityChecker

```python
class IntegrityChecker:
    """
    System integrity checker.

    Verifies invariants, checksums and clearing correctness.
    """

    async def check_zero_sum(
        self,
        equivalent: str
    ) -> "ZeroSumCheckResult":
        """
        Check the zero-sum invariant:

        The sum of all balances for the equivalent must be 0.
        """

    async def check_trust_limits(
        self,
        equivalent: str
    ) -> "TrustLimitCheckResult":
        """
        Verify that all debts are within defined trust limits.
        """

    async def check_debt_symmetry(
        self,
        equivalent: str
    ) -> "DebtSymmetryCheckResult":
        """
        Verify that between a pair of participants, debt only
        exists in one direction.
        """

    async def verify_clearing_neutrality(
        self,
        cycle: list[str],
        amount: Decimal,
        equivalent: str,
        positions_before: dict[str, Decimal]
    ) -> bool:
        """
        Verify that clearing did not change net positions.
        """

    async def compute_state_checksum(
        self,
        equivalent: str
    ) -> str:
        """
        Compute SHA-256 checksum of the debt state.
        """

    async def run_full_check(
        self,
        equivalent: str
    ) -> "IntegrityReport":
        """
        Run all checks and produce a report.
        """

    async def save_checkpoint(
        self,
        equivalent: str,
        checksum: str
    ) -> "IntegrityCheckpoint":
        """
        Save a checkpoint for later audit.
        """

    async def handle_violation(
        self,
        violation: "IntegrityViolation"
    ) -> None:
        """
        Handle an integrity violation:
        - block operations
        - notify administrators
        - create a detailed report
        """
```

**IntegrityChecker data models:**

```python
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from typing import Any

@dataclass
class IntegrityReport:
    """Integrity check report."""
    equivalent: str
    timestamp: datetime
    checksum: str
    checks: dict[str, "CheckResult"]
    all_passed: bool
    violations: list["IntegrityViolation"]

@dataclass
class CheckResult:
    """Single check result."""
    name: str
    passed: bool
    value: Any
    details: dict | None = None

@dataclass
class IntegrityViolation:
    """Integrity violation information."""
    type: str      # ZERO_SUM | TRUST_LIMIT | DEBT_SYMMETRY | CLEARING_NEUTRALITY
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    equivalent: str
    details: dict
    detected_at: datetime

@dataclass
class IntegrityCheckpoint:
    """State checkpoint."""
    id: UUID
    equivalent: str
    checksum: str
    timestamp: datetime
    total_debt: Decimal
    participant_count: int
    debt_count: int
```

### 2.4. MVP Data Schema

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

-- Prepare Locks (for 2PC)
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

-- Integrity Audit Log
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

-- Integrity Checkpoints
CREATE TABLE integrity_checkpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    equivalent_code VARCHAR(16) NOT NULL,
    checksum VARCHAR(64) NOT NULL,
    total_debt DECIMAL(20, 8) NOT NULL,
    participant_count INTEGER NOT NULL,
    debt_count INTEGER NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_checkpoints_equivalent 
ON integrity_checkpoints(equivalent_code, created_at DESC);

-- Integrity Violations
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

CREATE INDEX idx_violations_unresolved 
ON integrity_violations(resolved, detected_at)
WHERE resolved = false;
```

### 2.5. MVP API Endpoints

```text
Auth:
    POST   /api/v1/participants           # Register participant
    POST   /api/v1/auth/challenge         # Start challenge-response
  POST   /api/v1/auth/login             # Login (challenge-response)
  POST   /api/v1/auth/refresh           # Refresh token

Participants:
  GET    /api/v1/participants/me        # Current participant
  PATCH  /api/v1/participants/me        # Update profile
  GET    /api/v1/participants/{pid}     # Participant profile
    GET    /api/v1/participants           # Search participants

TrustLines:
  POST   /api/v1/trustlines             # Create trust line
  GET    /api/v1/trustlines             # List trust lines
  GET    /api/v1/trustlines/{id}        # Trust line details
  PATCH  /api/v1/trustlines/{id}        # Update trust line
  DELETE /api/v1/trustlines/{id}        # Close trust line

Payments:
  POST   /api/v1/payments               # Create payment
  GET    /api/v1/payments               # Payment history
  GET    /api/v1/payments/{tx_id}       # Payment details
  GET    /api/v1/payments/capacity      # Check capacity

Balance:
  GET    /api/v1/balance                # Aggregate balance
  GET    /api/v1/balance/debts          # Debts (incoming/outgoing)
  GET    /api/v1/balance/history        # Balance history

WebSocket:
  WS     /api/v1/ws                     # Real-time notifications
```

---

## 3. Stage 2: Extended Features

### 3.1. Additional Components

```text
app/
├── core/
│   ├── analytics/              # Analytics
│   │   ├── service.py
│   │   └── reports.py
│   ├── disputes/               # Disputes
│   │   ├── service.py
│   │   └── workflow.py
│   ├── verification/           # KYC
│   │   ├── service.py
│   │   └── providers/
│   │       ├── base.py
│   │       └── manual.py
│   └── offline/                # Offline support
│       ├── service.py
│       └── sync.py
│
└── addons/                     # Addon system
    ├── __init__.py
    ├── base.py                 # Base addon class
    ├── registry.py             # Addon registry
    └── hooks.py                # Event hooks
```

### 3.2. Addon System

```python
# addons/base.py
from abc import ABC, abstractmethod

class AddonBase(ABC):
    """Base class for hub addons."""

    name: str
    version: str

    @abstractmethod
    async def on_load(self, app) -> None:
        """Called when the addon is loaded."""
        raise NotImplementedError

    @abstractmethod
    async def on_unload(self) -> None:
        """Called when the addon is unloaded."""
        raise NotImplementedError

    def register_routes(self, router) -> None:
        """Register additional HTTP routes."""
        ...

    def register_hooks(self, event_bus) -> None:
        """Subscribe to internal events."""
        ...
```

```python
# Example addon
class TelegramNotificationsAddon(AddonBase):
    name = "telegram_notifications"
    version = "1.0.0"

    async def on_load(self, app):
        self.bot = TelegramBot(app.config.telegram_token)

    async def on_unload(self) -> None:
        await self.bot.close()

    def register_hooks(self, event_bus):
        event_bus.subscribe("payment.committed", self.on_payment)
        event_bus.subscribe("clearing.committed", self.on_clearing)

    async def on_payment(self, event):
        # Send notification to recipient
        ...

    async def on_clearing(self, event):
        # Notify participants of clearing event
        ...
```

### 3.3. Dispute Mechanism

```python
class DisputeService:
    """Dispute management service."""

    async def open_dispute(
        self,
        tx_id: str,
        opened_by: str,
        reason: str,
        evidence: list[str],
        requested_outcome: str
    ) -> "Dispute":
        """Open a dispute for a transaction."""

    async def respond_to_dispute(
        self,
        dispute_id: UUID,
        responder: str,
        response: str,
        evidence: list[str]
    ) -> "Dispute":
        """Respond to an existing dispute."""

    async def resolve_dispute(
        self,
        dispute_id: UUID,
        resolver: str,
        resolution: "DisputeResolution",
        compensation_tx: "Transaction | None"
    ) -> "Dispute":
        """Resolve a dispute and optionally create a compensation TX."""
```

### 3.4. Offline Client Mode

```python
class OfflineSyncService:
    """Offline operation synchronization."""

    async def get_state_snapshot(
        self,
        participant_id: str
    ) -> "StateSnapshot":
        """Return a state snapshot for local caching."""

    async def process_offline_queue(
        self,
        participant_id: str,
        operations: list["OfflineOperation"]
    ) -> "SyncResult":
        """Process a queue of offline operations."""

    async def get_delta_since(
        self,
        participant_id: str,
        since: datetime
    ) -> list["StateChange"]:
        """Return changes since the last synchronization."""
```

### 3.5. KYC Hooks

```python
class VerificationService:
    """Participant verification / KYC."""

    async def request_verification(
        self,
        participant_id: str,
        level: int,
        documents: list[str]
    ) -> "VerificationRequest":
        """Create a verification request."""

    async def approve_verification(
        self,
        request_id: UUID,
        approver: str,
        level: int,
        notes: str
    ) -> "Participant":
        """Approve verification and update participant."""

    async def check_verification_required(
        self,
        participant_id: str,
        operation: str,
        amount: Decimal
    ) -> bool:
        """Check if verification is required for an operation."""
```

---

## 4. Stage 3: Hub Federation

### 4.1. Federation Architecture

```text
┌─────────────────────────────────────────────────────────────────┐
│                        Federation Layer                         │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      Hub Discovery                        │  │
│  │  (DNS-based / Registry / Manual configuration)           │  │
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

### 4.2. Federation Components

```text
app/
├── federation/
│   ├── __init__.py
│   ├── discovery/              # Hub discovery
│   │   ├── __init__.py
│   │   ├── service.py
│   │   ├── dns.py              # DNS-based discovery
│   │   └── registry.py         # Central registry
│   ├── routing/                # Inter-hub routing
│   │   ├── __init__.py
│   │   ├── service.py
│   │   └── path_finder.py
│   ├── protocol/               # Inter-hub protocol
│   │   ├── __init__.py
│   │   ├── client.py           # Outgoing requests
│   │   ├── server.py           # Incoming handlers
│   │   └── messages.py         # Protocol messages
│   └── settlement/             # Inter-hub settlement
│       ├── __init__.py
│       └── service.py
```

### 4.3. Discovery Service

```python
class HubDiscoveryService:
    """Hub discovery and registration."""

    async def register_hub(
        self,
        hub_info: "HubInfo"
    ) -> "HubRegistration":
        """Register a hub in the network."""

    async def discover_hubs(
        self,
        filter_by: "HubFilter | None" = None
    ) -> list["HubInfo"]:
        """Discover available hubs."""

    async def get_hub_by_participant(
        self,
        pid: str
    ) -> "HubInfo | None":
        """Find a hub by participant PID."""

    async def health_check(
        self,
        hub_endpoint: str
    ) -> "HubHealthStatus":
        """Check hub availability."""
```

### 4.4. Inter-Hub Protocol

```python
class InterHubProtocol:
    """Inter-hub communication protocol."""

    async def request_payment(
        self,
        target_hub: str,
        request: "InterHubPaymentRequest"
    ) -> "InterHubPaymentResponse":
        """Request a payment in another hub."""

    async def prepare_payment(
        self,
        tx_id: str,
        effects: list["InterHubEffect"]
    ) -> "PrepareResponse":
        """PREPARE phase for an inter-hub payment."""

    async def commit_payment(
        self,
        tx_id: str
    ) -> "CommitResponse":
        """COMMIT phase."""

    async def abort_payment(
        self,
        tx_id: str,
        reason: str
    ) -> "AbortResponse":
        """ABORT phase."""
```

### 4.5. Inter-Hub Routing

```python
class InterHubRoutingService:
    """Routing between hubs."""

    async def find_inter_hub_path(
        self,
        source_hub: str,
        source_pid: str,
        target_hub: str,
        target_pid: str,
        equivalent: str,
        amount: Decimal
    ) -> "InterHubRoute":
        """Find a route between hubs."""

    async def get_hub_capacity(
        self,
        hub_a: str,
        hub_b: str,
        equivalent: str
    ) -> Decimal:
        """Get available capacity between hubs."""
```

---

## 5. Stage 4: Advanced Scenarios

### 5.1. P2P Nodes (Partial Decentralization)

```text
┌─────────────────────────────────────────────────────────────┐
│                        Hybrid Network                      │
│                                                             │
│  ┌─────────┐    ┌─────────┐    ┌─────────┐                 │
│  │ P2P Node│◄──►│   Hub   │◄──►│ P2P Node│                 │
│  │ (power  │    │(coord.) │    │ (power  │                 │
│  │  user)  │    │         │    │  user)  │                 │
│  └─────────┘    └────┬────┘    └─────────┘                 │
│                      │                                      │
│         ┌────────────┼────────────┐                        │
│         ▼            ▼            ▼                        │
│     [light]      [light]      [light]                     │
│     clients      clients      clients                     │
└─────────────────────────────────────────────────────────────┘
```

### 5.2. Consensus Between Hubs

For critical operations, a distributed consensus can be used:

```python
class HubConsensusService:
    """Consensus between hubs (Raft-like)."""

    async def propose_transaction(
        self,
        tx: "Transaction",
        participating_hubs: list[str]
    ) -> "ConsensusResult":
        """Propose a transaction for consensus."""

    async def vote(
        self,
        tx_id: str,
        vote: "Vote"
    ) -> None:
        """Vote for or against a transaction."""
```

### 5.3. Data Sharding

For scaling large hubs:

```text
┌─────────────────────────────────────────┐
│              Large Hub                 │
│                                         │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
│  │ Shard 1 │  │ Shard 2 │  │ Shard 3 │ │
│  │ A–H     │  │ I–P     │  │ Q–Z     │ │
│  └─────────┘  └─────────┘  └─────────┘ │
│                    │                    │
│              Coordinator                │
└─────────────────────────────────────────┘
```

---

## 6. Technology Stack

### 6.1. Backend

| Component   | Technology           | Rationale                              |
|-------------|----------------------|----------------------------------------|
| Language    | Python 3.11+         | Simplicity, ecosystem, AI-friendly    |
| Framework   | FastAPI              | Async, OpenAPI, Pydantic              |
| ORM         | SQLAlchemy 2.x       | Reliability, migrations                |
| Migrations  | Alembic              | Standard with SQLAlchemy               |
| Database    | PostgreSQL 15+       | ACID, JSONB, performance               |
| Cache       | Redis 7+             | Speed, pub/sub, locks                  |
| Queue       | Redis/Arq            | Simplicity, enough for MVP             |
| Tests       | pytest               | De facto standard                      |

### 6.2. Clients

| Component      | Technology   | Rationale                |
|----------------|-------------|--------------------------|
| Mobile/Desktop | Flutter     | Cross-platform           |
| Admin Panel    | Jinja2+HTMX | Simplicity, no heavy SPA |
| Cryptography   | libsodium   | Ed25519, battle-tested   |

### 6.3. Infrastructure

| Component      | Technology          | Rationale                |
|----------------|---------------------|--------------------------|
| Containers     | Docker              | Standard                 |
| Orchestration  | Docker Compose → K8s| From simple to complex   |
| CI/CD          | GitHub Actions      | Repo integration         |
| Monitoring     | Prometheus + Grafana| Industry standard       |
| Logs           | Loki / ELK          | Centralized logging      |

### 6.4. Dependencies (pyproject.toml)

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

## 7. Data Model

### 7.1. ER Diagram

```text
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

### 7.2. Performance Indexes

```sql
-- Fast routing lookup
CREATE INDEX idx_trustlines_routing 
ON trust_lines(from_participant_id, to_participant_id, equivalent_id, status)
WHERE status = 'active';

-- Fast cycle detection
CREATE INDEX idx_debts_cycles 
ON debts(debtor_id, creditor_id, equivalent_id, amount)
WHERE amount > 0;

-- Participant transaction history
CREATE INDEX idx_transactions_participant_history 
ON transactions(initiator_id, created_at DESC);
```

---

## 8. Security

### 8.1. Authentication

```text
┌─────────────────────────────────────────────────────────┐
│                 Challenge-Response Auth                 │
│                                                         │
│  Client                              Server             │
│    │                                   │                │
│    │──── GET /auth/challenge ─────────►│                │
│    │                                   │                │
│    │◄─── { challenge: "random" } ──────│                │
│    │                                   │                │
│    │  sign(challenge, private_key)     │                │
│    │                                   │                │
│    │──── POST /auth/login ────────────►│                │
│    │      { pid, signature }           │                │
│    │                                   │ verify(sig, pk)│
│    │◄─── { access_token, refresh } ────│                │
│    │                                   │                │
└─────────────────────────────────────────────────────────┘
```

### 8.2. Authorization

| Resource    | Owner          | Rights                         |
|-------------|----------------|--------------------------------|
| TrustLine   | `from`         | Create, update, close          |
| Participant | Self           | Update profile                 |
| Payment     | Initiator      | Create                         |
| Dispute     | TX participants| Open, respond                  |

### 8.3. Signature Validation

```python
async def validate_signed_request(
    request: "SignedRequest",
    expected_signer: str
) -> bool:
    """Validate a signed request."""
    # 1. Format
    if not is_valid_signature_format(request.signature):
        raise InvalidSignature("Bad format")

    # 2. Get public key
    participant = await get_participant(expected_signer)
    if not participant:
        raise UnknownParticipant(expected_signer)

    # 3. Verify signature
    message = canonical_json(request.payload)
    if not verify_ed25519(message, request.signature, participant.public_key):
        raise InvalidSignature("Verification failed")

    # 4. Timestamp / replay protection
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
    "default": "100/minute",
}
```

### 8.5. Data Protection

| Data             | Storage              | Transport |
|------------------|----------------------|----------|
| Private keys     | Client only          | Never    |
| Passwords        | bcrypt/argon2       | HTTPS    |
| Sessions         | Short-lived JWT     | HTTPS    |
| PII              | Encrypted at rest   | HTTPS    |

### 8.6. Transaction Privacy

In the MVP architecture, the hub centrally stores transaction data, which creates privacy risks. Mitigations for v0.1 and future directions are:

#### 8.6.1. Privacy Measures (MVP)

**Encrypted fields:**

```python
ENCRYPTED_FIELDS = {
    "participants": ["profile.contacts", "profile.personal_data"],
    "transactions": ["payload.description"],
}

class EncryptedField:
    """Transparent DB field encryption/decryption."""

    def __init__(self, key_id: str):
        self.key_id = key_id

    def encrypt(self, value: str) -> bytes:
        return pgp_sym_encrypt(value, get_key(self.key_id))

    def decrypt(self, data: bytes) -> str:
        return pgp_sym_decrypt(data, get_key(self.key_id))
```

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE transactions 
ADD COLUMN description_encrypted BYTEA;

CREATE OR REPLACE FUNCTION encrypt_description(text_data TEXT, key TEXT)
RETURNS BYTEA AS $$
BEGIN
    RETURN pgp_sym_encrypt(text_data, key);
END;
$$ LANGUAGE plpgsql;
```

**Data minimization:**

| Category    | Stored                                  | Not stored                     |
|-------------|-----------------------------------------|-------------------------------|
| Transactions| tx_id, type, participants, amount, time| IP, device fingerprints       |
| Participants| PID, public key, status                 | Geolocation, login history    |
| TrustLines  | Participants, limit, policy             | Full limit change history     |

**Retention policies:**

```python
class DataRetentionPolicy:
    """Data retention & deletion policies."""

    TRANSACTION_DETAIL_RETENTION_DAYS = 730   # 2 years
    AUDIT_LOG_RETENTION_DAYS = 1825           # 5 years
    PREPARE_LOCK_RETENTION_HOURS = 24

    async def cleanup_old_transaction_details(self):
        cutoff = datetime.now() - timedelta(days=self.TRANSACTION_DETAIL_RETENTION_DAYS)
        await db.execute(
            """
            UPDATE transactions 
            SET payload = jsonb_build_object(
                'type', payload->>'type',
                'amount', payload->>'amount',
                'equivalent', payload->>'equivalent'
            )
            WHERE created_at < :cutoff
            """,
            {"cutoff": cutoff},
        )
```

**Right to be forgotten / anonymization:**

```python
class ParticipantDataService:
    async def request_data_export(
        self,
        participant_id: str
    ) -> "DataExport":
        """Export all participant data (GDPR Art. 20)."""

    async def request_data_deletion(
        self,
        participant_id: str
    ) -> "DeletionRequest":
        """
        Request deletion (GDPR Art. 17).

        Conditions:
        - All debts settled (balance = 0)
        - No open disputes
        - No pending transactions
        """

    async def anonymize_participant(
        self,
        participant_id: str
    ) -> None:
        """
        Anonymize participant when full deletion is impossible.

        - Remove display_name, profile, contacts
        - Replace PID with hash
        - Keep TX history for integrity, but without identification
        """
```

#### 8.6.2. Future Privacy (v2.0+)

Plans (described more in Section 10):

- Distributed storage (`trustNet`), hub only indexes graph  
- Private routing (onion-style, intermediaries do not see sender/receiver)  
- Zero-knowledge proofs for capacity/balance where possible  

---

## 9. Monitoring and Operations

### 9.1. Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# Counters
payments_total = Counter(
    "geo_payments_total",
    "Total payments",
    ["status"],  # success | failed
)
clearings_total = Counter(
    "geo_clearings_total",
    "Total clearings",
    ["status"],
)

# Histograms
payment_duration = Histogram(
    "geo_payment_duration_seconds",
    "Payment processing time",
)
routing_duration = Histogram(
    "geo_routing_duration_seconds",
    "Path finding time",
)

# Gauges
active_participants = Gauge(
    "geo_active_participants",
    "Active participants count",
)
total_debt = Gauge(
    "geo_total_debt",
    "Total debt in system",
    ["equivalent"],
)

# Integrity metrics

integrity_checks_total = Counter(
    "geo_integrity_checks_total",
    "Total integrity checks performed",
    ["equivalent", "check_type", "result"],  # result: passed|failed
)

integrity_violations_total = Counter(
    "geo_integrity_violations_total",
    "Total integrity violations detected",
    ["equivalent", "violation_type", "severity"],
)

integrity_check_duration = Histogram(
    "geo_integrity_check_duration_seconds",
    "Time spent on integrity checks",
    ["check_type"],
)

integrity_status = Gauge(
    "geo_integrity_status",
    "Current integrity status (1=healthy, 0=violation)",
    ["equivalent"],
)

zero_sum_balance = Gauge(
    "geo_zero_sum_balance",
    "Current zero-sum balance (should be 0)",
    ["equivalent"],
)

trust_limit_violations_current = Gauge(
    "geo_trust_limit_violations_current",
    "Current number of trust limit violations",
    ["equivalent"],
)

debt_symmetry_violations_current = Gauge(
    "geo_debt_symmetry_violations_current",
    "Current number of debt symmetry violations",
    ["equivalent"],
)

last_checkpoint_age_seconds = Gauge(
    "geo_last_checkpoint_age_seconds",
    "Time since last integrity checkpoint",
    ["equivalent"],
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
    """DB connectivity check."""
    return {"status": "ok"}
```

### 9.3. Logging

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

### 9.4. Alerts

| Metric                         | Threshold             | Action        |
|--------------------------------|-----------------------|---------------|
| Error rate                     | > 1%                  | PagerDuty     |
| Payment latency p99            | > 5 s                 | Warning       |
| DB connections                 | > 80%                 | Warning       |
| Disk usage                     | > 85%                 | Critical      |
| Failed clearings               | > 10/hour             | Warning       |

**Integrity alerts (Prometheus Alertmanager example):**

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
# Daily PostgreSQL backup
pg_dump -Fc geo_hub > backup_$(date +%Y%m%d).dump

# Restore
pg_restore -d geo_hub backup_20251129.dump

# Point-in-time recovery
# Requires WAL archiving configuration
```

---

## 10. Future Development: Path to Decentralization

Current GEO v0.1 architecture is a deliberate compromise to ship an MVP quickly. The hub-centric model is simpler to build and operate, but not fully aligned with the long-term decentralized vision.

This section outlines the planned evolution.

### 10.1. Current vs Target Architecture

| Aspect            | Current (v0.1)           | Target (v2.0+)                    |
|-------------------|--------------------------|------------------------------------|
| Topology          | Hub-centric              | P2P with optional hubs             |
| Data storage      | Hub PostgreSQL           | Distributed DB (trustNet)          |
| Consensus         | 2PC with hub coordinator | Local between participants         |
| Privacy           | Hub sees all TX          | Participants see only their own    |
| Single Point Fail.| Hub                      | None                               |
| Scaling           | Vertical (single hub)    | Horizontal (network of nodes)      |

### 10.2. Architectural Roadmap

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         Decentralization Roadmap                               │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  v0.1 (MVP)          v1.0 (Stable)       v1.5 (Federation)     v2.0 (P2P)      │
│  ──────────          ────────────        ────────────────      ────────        │
│                                                                                 │
│  ┌─────────┐        ┌─────────┐         ┌─────────────┐      ┌───────────┐    │
│  │ Single  │        │ Multiple│         │ Connected   │      │ Full P2P  │    │
│  │ Hub     │───────►│ Hubs    │────────►│ Federation  │─────►│ Network   │    │
│  │         │        │(isolated)│        │             │      │           │    │
│  └─────────┘        └─────────┘         └─────────────┘      └───────────┘    │
│                                                                                 │
│  Features:          Add:                 Add:                  Add:             │
│  • Basic protocol   • Stability          • Inter-hub protocol  • No central hub │
│  • Single community • Production-ready   • Hub discovery       • trustNet DB    │
│  • Web + Mobile     • Monitoring         • Cross-community     • Local consensus│
│                     • Disputes             payments            • Full privacy   │
│                                                                                 │
│  Timeline:          Timeline:           Timeline:             Timeline:         │
│  Q1 2025            Q2–Q3 2025          Q4 2025               2026+            │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 10.3. Local Consensus (v2.0 Concept)

Target: transactions are confirmed only by involved participants, without a central coordinator.

**Current (v0.1):**

```text
A wants to pay C via B

1. A → Hub: PAYMENT_REQUEST
2. Hub: finds route A → B → C
3. Hub → B: PREPARE
4. Hub → C: PREPARE
5. Hub: collects responses
6. Hub → A,B,C: COMMIT or ABORT

Hub is the coordination and failure point.
```

**Target (v2.0):**

```text
A wants to pay C via B

1. A: finds route (directly or via index)
2. A → B: PREPARE(signed_by_A)
3. B: validates, signs
4. B → C: PREPARE(signed_by_A, signed_by_B)
5. C: validates, signs
6. C → B → A: COMMIT(all_signatures)

No central coordinator, all participants sign.
```

**Cryptographic packet:**

```python
class TransactionPacket:
    """
    Cryptographic packet for local consensus.

    Packet is valid only when all participants have signed.
    """

    tx_id: str
    operation_type: str  # PAYMENT, CLEARING

    participants: list[str]          # [A, B, C]
    effects: dict[str, "Effect"]     # {A: -100, B: 0, C: +100}
    signatures: dict[str, "Signature"]  # {A: sig_a, B: sig_b, C: sig_c}

    created_at: datetime
    expires_at: datetime

    def is_complete(self) -> bool:
        return set(self.participants) == set(self.signatures.keys())

    def verify_all_signatures(self) -> bool:
        for pid, sig in self.signatures.items():
            pubkey = get_public_key(pid)
            if not verify_signature(self.canonical_bytes(), sig, pubkey):
                return False
        return True
```

### 10.4. Distributed DB trustNet (v2.0 Concept)

**Idea:**

- No central store of all data  
- Each participant stores their own data  
- Hub (or DHT) optionally indexes topology only  

```text
┌─────────────────────────────────────────────────────────────┐
│                     trustNet Architecture                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
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
│              │  Routing Index  │ (optional, for speed)      │
│              │  (Hub or DHT)   │                            │
│              └─────────────────┘                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Data sync sketch:**

```python
class TrustNetNode:
    """
    Distributed trustNet node.

    Stores only data relevant to this participant.
    """

    def __init__(self, participant_id: str):
        self.pid = participant_id
        self.local_store = LocalStorage()  # e.g. SQLite

    async def store_trust_line(
        self,
        trust_line: "TrustLine"
    ) -> None:
        """
        Store trust line locally and sync with the other endpoint.
        """
        await self.local_store.save(trust_line)

        other_pid = trust_line.to if trust_line.from_ == self.pid else trust_line.from_
        await self.sync_with_peer(other_pid, trust_line)

    async def sync_with_peer(
        self,
        peer_pid: str,
        data: Any
    ) -> None:
        peer_endpoint = await self.discover_peer(peer_pid)
        await send_sync_message(peer_endpoint, data)
```

### 10.5. Hub as Optional Indexer (v2.0 Concept)

In v2.0, the hub does not disappear but changes role:

| Hub Role        | v0.1 (mandatory) | v2.0 (optional)           |
|-----------------|------------------|---------------------------|
| Data storage    | ✅ All data       | ❌ Only indexes/metadata  |
| TX coordination | ✅ 2PC            | ❌ Not involved           |
| Routing         | ✅ Path finding   | ✅ Fast index only        |
| Backup          | ❌                | ✅ Optional backup/store  |

**Hub index service:**

```python
class HubIndexService:
    """
    Hub as an indexer for faster routing.

    Does NOT store balances, only connection graph.
    """

    async def index_trust_line(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str
    ) -> None:
        """Add an edge to the graph."""
        await self.graph.add_edge(from_pid, to_pid, equivalent)

    async def find_potential_paths(
        self,
        source: str,
        target: str,
        equivalent: str,
        max_hops: int = 6
    ) -> list[list[str]]:
        """
        Find potential paths ignoring capacities.

        Participant then queries capacities on the path.
        """
        return await self.graph.bfs_paths(source, target, max_hops)
```

### 10.6. Data Migration

```python
class MigrationService:
    """Migrate from hub-centric to distributed architecture."""

    async def export_participant_data(
        self,
        participant_id: str
    ) -> "ParticipantDataBundle":
        """
        Export all participant data for local storage:

        - TrustLines (incoming/outgoing)
        - Current debts
        - Transaction history (optional)
        - Signed packets of pending ops
        """

    async def verify_migration_integrity(
        self,
        hub_checksum: str,
        distributed_checksum: str
    ) -> bool:
        """
        Verify data equality after migration
        using state checksums.
        """
```

### 10.7. Backward Compatibility

Transition will be gradual:

```text
┌─────────────────────────────────────────────────────────────┐
│                     Hybrid Transition Mode                 │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐                                ┌─────────┐    │
│  │ Legacy  │                                │  P2P    │    │
│  │ Client  │◄────── Hub (coordinator) ─────►│  Node   │    │
│  │ (v0.1)  │                                │ (v2.0)  │    │
│  └─────────┘                                └─────────┘    │
│       │                                          │         │
│       │        Payment between old and new       │         │
│       │                                          │         │
│       └──────────────── Hub ─────────────────────┘         │
│                  (translates between protocols)            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## Related Documents

- [00-overview.md](00-overview.md) — Project Overview  
- [01-concepts.md](01-concepts.md) — Key Concepts  
- [02-protocol-spec.md](02-protocol-spec.md) — Protocol Specification  
- [04-api-reference.md](04-api-reference.md) — API Reference  
- [05-deployment.md](05-deployment.md) — Deployment
