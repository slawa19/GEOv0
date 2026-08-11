# GEO Hub: How to Contribute

**Version:** 0.1  
**Date:** November 2025

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Project Structure](#2-project-structure)
3. [Development](#3-development)
4. [Code Style](#4-code-style)
5. [Testing](#5-testing)
6. [Pull Request Process](#6-pull-request-process)
7. [Architecture Decisions](#7-architecture-decisions)
8. [Creating Addons](#8-creating-addons)
9. [Documentation](#9-documentation)
10. [Community](#10-community)

---

## 1. Getting Started

### 1.1. Requirements

- Python 3.11+
- PostgreSQL 15+
- Redis 7+
- Git
- Docker (recommended)

### 1.2. Fork and clone

```bash
# Fork repository through GitHub UI

# Clone your fork
git clone https://github.com/YOUR_USERNAME/geo-hub.git
cd geo-hub

# Add upstream
git remote add upstream https://github.com/geo-protocol/geo-hub.git
```

### 1.3. Environment setup

```bash
# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies (including dev)
pip install -e ".[dev]"

# Setup pre-commit hooks
pre-commit install
```

### 1.4. Run via Docker

```bash
# Local development always uses the base file plus the dev overlay
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d db redis

# Start application
# Note: migrations are applied automatically on container startup.
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

### 1.5. Installation check

```bash
# Run canonical backend gate (PowerShell)
.\scripts\verify_local.ps1 -TaskSlug contributor_en_install -BackendOnly

# Check blocking Ruff scope
python -m ruff check app migrations --no-cache

# Open documentation (Swagger UI)
open http://localhost:8000/docs
# (or http://127.0.0.1:18000/docs when running locally via scripts/run_local.ps1)
```

---

## 2. Project Structure

```
geo-hub/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entry point
│   ├── config.py            # Configuration
│   │
│   ├── api/                 # HTTP/WebSocket endpoints
│   │   ├── v1/              # API version
│   │   │   ├── router.py    # Main router
│   │   │   ├── auth.py
│   │   │   ├── participants.py
│   │   │   ├── trustlines.py
│   │   │   ├── payments.py
│   │   │   └── websocket.py
│   │   └── admin/           # Admin panel
│   │
│   ├── core/                # Business logic
│   │   ├── auth/
│   │   ├── participants/
│   │   ├── trustlines/
│   │   ├── payments/
│   │   ├── clearing/
│   │   └── events/          # Event bus
│   │
│   ├── models/              # Pydantic models
│   │   ├── participant.py
│   │   ├── trustline.py
│   │   ├── debt.py
│   │   ├── transaction.py
│   │   └── messages.py
│   │
│   ├── db/                  # Database
│   │   ├── base.py
│   │   ├── session.py
│   │   └── models/          # SQLAlchemy models
│   │
│   └── addons/              # Addon system
│
├── migrations/              # Alembic migrations
├── tests/                   # Tests
├── docs/                    # Documentation
├── docker/                  # Docker files
│
├── pyproject.toml           # Dependencies and settings
├── alembic.ini              # Alembic configuration
└── README.md
```

### 2.1. Architecture layers

```
┌─────────────────────────────────────┐
│            API Layer                │  ← HTTP/WS handlers
│  (app/api/)                         │     Validation, serialization
├─────────────────────────────────────┤
│           Core Layer                │  ← Business logic
│  (app/core/)                        │     Services, Engines
├─────────────────────────────────────┤
│          Models Layer               │  ← Pydantic models
│  (app/models/)                      │     DTO, schemas
├─────────────────────────────────────┤
│            DB Layer                 │  ← SQLAlchemy
│  (app/db/)                          │     ORM models, sessions
└─────────────────────────────────────┘
```

---

## 3. Development

### 3.1. Creating new branch

```bash
# Sync with upstream
git fetch upstream
git checkout main
git merge upstream/main

# Create feature branch
git checkout -b feature/my-feature
```

### 3.2. Branch naming

| Type | Format | Example |
|------|--------|---------|
| Feature | `feature/description` | `feature/multi-path-payments` |
| Bugfix | `fix/description` | `fix/routing-cycle-detection` |
| Docs | `docs/description` | `docs/api-examples` |
| Refactor | `refactor/description` | `refactor/payment-engine` |

### 3.3. Run in development mode

```bash
export ENV=dev

# Start with hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 18000

# Debug logging
DEBUG=true LOG_LEVEL=DEBUG uvicorn app.main:app --reload --host 0.0.0.0 --port 18000
```

### 3.4. Working with database

```bash
# Create new migration
alembic -c migrations/alembic.ini revision --autogenerate -m "Add column X to table Y"

# Apply migrations
alembic -c migrations/alembic.ini upgrade head

# Rollback last migration
alembic -c migrations/alembic.ini downgrade -1

# View history
alembic -c migrations/alembic.ini history
```

---

## 4. Code Style

### 4.1. Python

We use:
- **Ruff** — blocking CI linter for `app migrations`
- **Black** — non-blocking formatting diagnostic
- **mypy** — not configured as a repository gate

```bash
# Check
python -m ruff check app migrations --no-cache

# Auto-fix
python -m ruff check --fix app migrations
python -m black --check app migrations
```

### 4.2. Configuration (pyproject.toml)

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
]

[tool.mypy]
python_version = "3.11"
strict = true
```

### 4.3. Code writing rules

**Services:**
```python
# app/core/payments/service.py

class PaymentEngine:
    """
    Payment execution.
    
    Responsible for:
    - Routing
    - 2PC coordination
    - Applying changes
    """
    
    def __init__(
        self,
        db: AsyncSession,
        routing: RoutingService,
        event_bus: EventBus,
    ) -> None:
        self._db = db
        self._routing = routing
        self._events = event_bus
    
    async def create_payment(
        self,
        from_pid: str,
        to_pid: str,
        equivalent: str,
        amount: Decimal,
        *,
        max_hops: int = 4,
    ) -> Transaction:
        """
        Create and execute payment.
        
        Args:
            from_pid: Payer PID
            to_pid: Recipient PID
            equivalent: Equivalent code
            amount: Payment amount
            max_hops: Maximum path length
            
        Returns:
            Transaction with result
            
        Raises:
            InsufficientCapacity: Insufficient capacity
            ParticipantNotFound: Participant not found
        """
        # Implementation...
```

**Pydantic models:**
```python
# app/models/payment.py

class PaymentCreate(BaseModel):
    """Create payment request."""
    
    to: str = Field(..., description="Recipient PID")
    equivalent: str = Field(..., min_length=1, max_length=16)
    amount: Decimal = Field(..., gt=0, decimal_places=8)
    description: str | None = Field(None, max_length=500)
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "to": "5HueCGU8rMjx...",
                "equivalent": "UAH",
                "amount": "100.00",
                "description": "For services"
            }
        }
    )
```

**API endpoints:**
```python
# app/api/v1/payments.py

@router.post(
    "",
    response_model=PaymentResponse,
    status_code=201,
    summary="Create payment",
    responses={
        201: {"description": "Payment successfully executed"},
        400: {"description": "Insufficient capacity"},
        404: {"description": "Participant not found"},
    },
)
async def create_payment(
    request: PaymentCreate,
    current_user: Annotated[Participant, Depends(get_current_user)],
    payment_engine: Annotated[PaymentEngine, Depends(get_payment_engine)],
) -> PaymentResponse:
    """
    Create new payment.
    
    Finds routes through trust network and executes payment
    using two-phase commit.
    """
    tx = await payment_engine.create_payment(
        from_pid=current_user.pid,
        to_pid=request.to,
        equivalent=request.equivalent,
        amount=request.amount,
    )
    return PaymentResponse.from_transaction(tx)
```

---

## 5. Testing

### 5.1. Test structure

```
tests/
├── conftest.py              # Common fixtures
├── unit/                    # Unit tests
│   ├── core/
│   │   ├── test_routing.py
│   │   ├── test_payments.py
│   │   └── test_clearing.py
│   └── models/
├── integration/             # Integration tests
│   ├── test_payment_flow.py
│   └── test_clearing_flow.py
└── e2e/                     # End-to-end tests
    └── test_api.py
```

### 5.2. Running tests

```powershell
# Default backend tier (excludes slow and postgres)
.\scripts\verify_local.ps1 -TaskSlug contributor_en_backend -BackendOnly

# Specific module through the canonical selector
.\scripts\verify_local.ps1 -TaskSlug contributor_en_payments -BackendOnly `
  -BackendSelector tests/unit/test_payments_2pc.py

# Explicit slow milestone; postgres remains a dedicated tier
.\scripts\verify_local.ps1 -TaskSlug contributor_en_super_smoke -BackendOnly `
  -BackendSelector tests/integration/test_simulator_super_smoke.py -IncludeExpensive
```

### 5.3. Fixtures

```python
# tests/conftest.py

@pytest.fixture
async def db_session():
    """Test DB session with rollback."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncSession(engine) as session:
        yield session
        await session.rollback()

@pytest.fixture
def participant_factory(db_session):
    """Factory for creating participants."""
    async def _create(
        display_name: str = "Test User",
        **kwargs,
    ) -> Participant:
        p = Participant(
            pid=generate_pid(),
            public_key=generate_keypair()[0],
            display_name=display_name,
            **kwargs,
        )
        db_session.add(p)
        await db_session.flush()
        return p
    
    return _create
```

### 5.4. Test examples

```python
# tests/unit/core/test_routing.py

class TestRoutingService:
    """Routing tests."""
    
    async def test_find_direct_path(
        self,
        routing_service: RoutingService,
        alice: Participant,
        bob: Participant,
    ):
        """Finds direct path between participants."""
        # Arrange
        await create_trust_line(alice, bob, limit=1000)
        
        # Act
        paths = await routing_service.find_paths(
            source=alice.pid,
            target=bob.pid,
            equivalent="UAH",
            amount=Decimal("100"),
        )
        
        # Assert
        assert len(paths) == 1
        assert paths[0].path == [alice.pid, bob.pid]
        assert paths[0].capacity >= Decimal("100")
    
    async def test_no_path_when_insufficient_trust(
        self,
        routing_service: RoutingService,
        alice: Participant,
        bob: Participant,
    ):
        """No path found when trust is insufficient."""
        # Arrange
        await create_trust_line(alice, bob, limit=50)
        
        # Act & Assert
        with pytest.raises(NoRouteFound):
            await routing_service.find_paths(
                source=alice.pid,
                target=bob.pid,
                equivalent="UAH",
                amount=Decimal("100"),
            )
```

---

## 6. Pull Request Process

### 6.1. PR preparation

```bash
# Ensure all checks pass
python -m ruff check app migrations --no-cache
.\scripts\verify_local.ps1 -TaskSlug contributor_en_pr_full

# Commit with clear message
git commit -m "feat(payments): add multi-path routing support

- Implement light multi-path algorithm
- Add path splitting logic
- Update routing service interface

Closes #123"
```

### 6.2. Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat` — new feature
- `fix` — bug fix
- `docs` — documentation
- `style` — formatting
- `refactor` — refactoring
- `test` — tests
- `chore` — misc (dependencies, CI)

### 6.3. Pre-PR checklist

- [ ] Code follows style guide
- [ ] Tests added/updated
- [ ] All tests pass
- [ ] Documentation updated (if needed)
- [ ] Type hints added
- [ ] No TODO/FIXME in code
- [ ] PR describes changes

### 6.4. Code Review

After PR creation:
1. CI automatically runs checks
2. Minimum 1 approval from maintainer
3. All comments resolved
4. Branch up-to-date with main

---

## 7. Architecture Decisions

### 7.1. ADR (Architecture Decision Records)

For significant decisions, create ADR in `docs/adr/`:

```markdown
# ADR-001: Choosing 2PC for payment coordination

## Status
Accepted

## Context
Need coordination mechanism for multi-participant payments...

## Decision
Use two-phase commit (2PC) with hub coordinator...

## Consequences
- Simple implementation
- Possible deadlocks on failures
- Need timeout mechanisms
```

### 7.2. Design principles

1. **Simplicity over universality**
   - Don't add "future" abstractions
   - YAGNI (You Aren't Gonna Need It)

2. **Explicit better than implicit**
   - No magic
   - Clear interfaces
   - Type hints everywhere

3. **Testability by default**
   - Dependency injection
   - Small, isolated functions
   - No global state

4. **Fail fast**
   - Input validation
   - Explicit errors
   - No silent failures

---

## 8. Creating Addons

### 8.1. Addon structure

```
geo_addon_telegram/
├── __init__.py
├── addon.py           # Main addon class
├── handlers.py        # Event handlers
├── routes.py          # Additional API routes
├── config.py          # Configuration
└── pyproject.toml
```

### 8.2. Base class

```python
# addon.py
from app.addons.base import AddonBase

class TelegramNotificationsAddon(AddonBase):
    """Telegram notifications for GEO Hub."""
    
    name = "telegram_notifications"
    version = "1.0.0"
    
    async def on_load(self, app) -> None:
        """Initialize on load."""
        self.config = TelegramConfig.from_env()
        self.bot = TelegramBot(self.config.token)
        
    async def on_unload(self) -> None:
        """Cleanup on unload."""
        await self.bot.close()
    
    def register_hooks(self, event_bus) -> None:
        """Subscribe to events."""
        event_bus.subscribe("payment.committed", self.on_payment)
        event_bus.subscribe("trustline.created", self.on_trustline)
    
    async def on_payment(self, event: PaymentEvent) -> None:
        """Send payment notification."""
        await self.bot.send_message(
            chat_id=self._get_chat_id(event.to_pid),
            text=f"💰 Received payment: {event.amount} {event.equivalent}",
        )
```

### 8.3. Registration in pyproject.toml

```toml
[project.entry-points."geo_hub.addons"]
telegram_notifications = "geo_addon_telegram.addon:TelegramNotificationsAddon"
```

### 8.4. Installing addon

```bash
pip install geo-addon-telegram

# Or for development
pip install -e ./geo-addon-telegram
```

---

## 9. Documentation

### 9.1. Documentation structure

```
docs/
├── 00-overview.md         # Project overview
├── 01-concepts.md         # Concepts
├── 02-protocol-spec.md    # Protocol specification
├── 03-architecture.md     # Architecture
├── 04-api-reference.md    # API
├── 05-deployment.md       # Deployment
├── 06-contributing.md     # This file
└── adr/                   # Architecture Decision Records
```

### 9.2. Docstrings

We use Google style:

```python
def find_paths(
    self,
    source: str,
    target: str,
    amount: Decimal,
    *,
    max_hops: int = 4,
) -> list[PaymentPath]:
    """
    Find payment paths.
    
    Uses BFS to search trust graph paths.
    Returns up to 3 paths with sufficient capacity.
    
    Args:
        source: Source PID
        target: Target PID
        amount: Required amount
        max_hops: Maximum path length (default: 4)
        
    Returns:
        List of found paths, sorted by capacity
        
    Raises:
        NoRouteFound: No path with sufficient capacity
        ParticipantNotFound: Participant doesn't exist
        
    Example:
        >>> paths = await routing.find_paths("alice", "bob", Decimal("100"))
        >>> print(paths[0].path)
        ['alice', 'charlie', 'bob']
    """
```

### 9.3. API documentation

Auto-generated from:
- Pydantic models
- Endpoint docstrings
- OpenAPI decorators

---

## 10. Community

### 10.1. Communication channels

- **GitHub Issues** — bugs and features
- **GitHub Discussions** — questions and ideas
- **Telegram** — @geo_protocol_dev

### 10.2. How to report a bug

1. Check if similar issue exists
2. Create issue with template:
   - Problem description
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Version and environment

### 10.3. How to suggest a feature

1. Create Discussion with idea
2. Discuss with community
3. If approved — create Issue
4. Implement (or wait for volunteer)

### 10.4. Code of Conduct

We follow [Contributor Covenant](https://www.contributor-covenant.org/).

In short:
- Respect each other
- Constructive criticism
- Focus on what's best for project

---

## Related Documents

- [00-overview.md](00-overview.md) — Project overview
- [03-architecture.md](03-architecture.md) — Architecture
- [05-deployment.md](05-deployment.md) — Deployment

---

**Thank you for your contribution to GEO!** 🙏
