## 8. Technology Stack Requirements

For open-source project goals "like Home Assistant":

- core was on **Python**, with clear modular architecture and add-on system;
- clients — on **cross-platform framework** like Flutter;
- all this was maximally "readable" and convenient for development **with AI agent**.

**Architecture variants** with rationale, and then — specific recommendation.

------

## 1. Stack Requirements (from what you described)

Not to lose essence, I'll collect requirements explicitly (and then rely on them):

1. **Backend:**
   - language — simple to read and for contributor participation;
   - "core + add-ons" architecture, similar to Home Assistant;
   - well suited for fast development with AI agent (transparent data models, minimum magic);
   - normal performance and async operation capability;
   - standard DB: PostgreSQL;
   - cache/queues: Redis (or similar).
2. **Frontend / clients:**
   - not React (and generally less heavy frontend framework construction);
   - native/cross-platform applications from ready modules (Flutter — good reference);
   - so AI agent can generate most screens and logic.
3. **Open source & modularity:**
   - clear separation of core/protocol and extensions;
   - easy to read, fork and refine;
   - minimum dependency on exotic solutions.

------

## 2. Python Backend Stack Options

### Option A. FastAPI + SQLAlchemy + Pydantic (recommended)

**Idea:** make backend in spirit of Home Assistant and modern API services:

- **FastAPI**:
  - very readable, little "magic";
  - OpenAPI/Swagger auto-generation (super for AI agents — they see types and endpoints);
  - excellent async support (important for WebSocket, notifications, background).
- **Pydantic**:
  - strict data models;
  - simple validation and serialization;
  - models well read by AI agent (explicit types, docstrings).
- **SQLAlchemy (2.x) + Alembic**:
  - de-facto standard for Python;
  - clear ORM models, transactions, migrations.
- **Redis**:
  - cache;
  - prepare-locks for payments;
  - task queues (via RQ/Celery/Arq).
- **pytest**:
  - for tests; simple, clear, familiar to contributors.
- **Add-on architecture**:
  - what Home Assistant does:
    - core with clear "domains" (auth, routing, payments, clearing);
    - integrations/add-ons — separate python packages, discovered via `entry_points` (`setup.cfg` / `pyproject.toml`) or through config catalog;
    - each add-on registers its routes, event handlers, schedulers.

**Why this is good for AI agent:**

- FastAPI + Pydantic set very declaratively:

  ```python
  class PaymentRequest(BaseModel):
      from_pid: str
      to_pid: str
      equivalent: str
      amount: Decimal
  ```

  Models and endpoints easy to describe in words, AI agent can reproduce such patterns without surprises.

- Backend's OpenAPI spec gives AI structured understanding of all operations.

- Readable, normal Python, not magic framework with metaclasses.

### Option B. Django + Django REST Framework

Pros:

- one of most known ecosystems;
- built-in admin (can have quick admin UI "out of box").

Cons:

- heavy monolith, lots of "magic" and abstraction layers;
- REST API via DRF bit cumbersome for AI agent code generation;
- async and WebSocket much more inconvenient than in FastAPI.

I'd consider Django as **fallback option**, but for your goals (lean architecture + add-ons + async) FastAPI wins.

### Option C. "Pure" ASGI framework (Starlette, aiohttp)

Creates lots of freedom, but also lots of manual work. Eventually you either write semi-FastAPI or lose typing/validation. For open-source project this is unnecessary complexity.

**Backend conclusion:**
 Optimal option — **Python 3.11+ + FastAPI + Pydantic + SQLAlchemy + Redis**, with core/add-on architecture inspired by Home Assistant.

------

## 3. Client Stack Options

Here need to separate:

- **thick client for users** (mobile/desktop, main UX);
- **minimal web interface for admin/viewing** (can be server rendering, without SPA).

### 3.1. Thick client: Flutter

Flutter (Dart) quite logically fits your requirements:

- single code for Android, iOS, desktop and even web;
- rich set of ready widgets;
- well-structured application (by modules/packages);
- AI agent today excellently generates Flutter code (widgets, pages, state), unlike many more exotic frameworks;
- no black magic: regular classes, widget trees, clear patterns (BLoC, Riverpod etc.).

Can build architecture:

- **core client** (common application framework, navigation, basic screens);
- **UI plugins/modules** (screens/widgets for specific domains: trustlines, payments, clearing, reports).

For AI agent this is quite convenient landscape:
 set project structure and conventions — then generate new screens and logic for given APIs.

Alternatives:

- **React Native** — falls away due to your attitude toward JS/React ecosystem.
- **Kotlin Multiplatform Mobile (KMM)** — powerful, but for contributors and AI agent entry barrier higher, smaller ecosystem.
- **Qt/QML** — good for desktop, mobile experience disputable; community entry barrier higher.

By all factors Flutter currently most reasonable compromise.

### 3.2. Web interface (minimal, not SPA)

To not drag React, but have:

- basic admin panel;
- node state viewing;
- possibly simple forms.

Can do:

- server-rendered HTML on Python (Jinja2);
- bit of progressive JavaScript via **HTMX** or **Alpine.js**.

Get:

- simple, readable template code;
- minimum frontend magic;
- AI agent easily generates such templates and handlers in FastAPI.

This can be exactly **admin and diagnostic interface**, while "real" user experience goes through Flutter application.

------

## 4. How This Fits "Core + Add-ons like Home Assistant"

### Backend Core

- Implements:
  - GEO v0.1 protocol (models: Participant, TrustLine, Debt, Transaction);
  - basic services: routing, payments, clearing;
  - API (REST/GraphQL/WebSocket) for clients;
  - event registration (internal event-bus).
- Directory structure:
  - `core/` — domain modules (participants, trustlines, payments, clearing, equivalents);
  - `api/` — HTTP/WebSocket layer;
  - `addons/` — extension connection point.

### Add-ons

- Separate packages (pip modules), e.g.:
  - `geo_addon.reporting_advanced`;
  - `geo_addon.local_currency_X`;
  - `geo_addon.governance`.
- Each add-on:
  - registers via `entry_points` in `pyproject.toml`;
  - on startup core scans installed packages and connects them:
    - registers routes,
    - event subscriptions (e.g.: "trigger on each COMMITTED payment"),
    - task schedulers.

This is how Home Assistant is built: core + huge number of integrations.
 Same thing we can do for GEO, so:

- main codebase was compact and clear;
- everything specific moved to plugins.

For AI agent such pattern very convenient:

1. describe core API; 2) ask "write add-on that does this-and-that" — and get module with clear interface.


### 9. Technology Stack (revision for Python ecosystem and modularity)

GEO node architecture should be:

- maximally readable for contributors;
- extensible through add-ons by Home Assistant principle;
- convenient for code generation and maintenance with AI agents.

Based on this, choose following stack.

#### 9.1. Backend

- **Language:** Python 3.11+
   Reading simplicity, huge ecosystem, low entry barrier for contributors.
- **Web framework:** FastAPI
   Async "out of box", declarative request/response models (via Pydantic), OpenAPI spec auto-generation.
- **Models and validation:** Pydantic
   Clear typed models, convenient for both humans and AI agents.
- **DB access:** SQLAlchemy 2.x + Alembic
   Reliable ORM, PostgreSQL support, DB schema migrations.
- **Database:** PostgreSQL
   Main state storage (participants, trustlines, debts, transactions).
- **Cache and queues:** Redis
   Caching, temporary prepare-locks for payment phases, background task processing (via RQ/Celery/Arq).
- **Testing:** pytest
   Simple and de-facto standard testing framework in Python world.
- **Containerization:** Docker, docker-compose; later — Kubernetes when scaling
   For deploying nodes in different communities and environments.
- **Add-on architecture:**
   GEO core implements main domain model and protocol. Extensions connect as separate Python packages (add-ons) through `entry_points` mechanism. Add-ons can:
  - register their HTTP/WebSocket endpoints;
  - subscribe to internal events (e.g., "PAYMENT.COMMITTED");
  - add scheduler tasks (periodic checks, extended reports etc.).

#### 9.2. Client Applications

User experience moved to cross-platform native application, web part used mainly for admin and diagnostics.

- **Main client (mobile/desktop/web):** Flutter
   Single Dart code for Android, iOS, desktop and, when necessary, web version. Rich library of ready components, clear UI module structure. Convenient for code generation and refinement with AI agents.
- **Client architecture:**
   Main GEO client application and domain modules/widgets (trustlines, payments, clearing, reports) that can be connected and refined independently.
- **Minimal node web interface (admin):**
   Server-rendered HTML (Jinja2) on backend side + minimal JS for interactivity (HTMX/Alpine.js). This approach avoids heavy SPA frameworks while remaining convenient for developers and AI agents.

------

If you want, further we can:

- adapt already written architecture document entirely to this stack (insert new section 9 and slightly correct NestJS/React mentions in other places),
- or separately design directory structure of core/add-ons for backend and skeleton Flutter application (so it's immediately clear where AI agent should "put" generated code).