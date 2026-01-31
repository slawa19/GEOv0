# GEO v0 — Decentralized Credit Network for Local Communities

**GEO** is an open protocol for a decentralized credit network that lets people and organizations build **mutual credit economies** inside and between local communities.

Instead of moving traditional money, GEO participants:

- Open **lines of trust** (credit limits) to each other
- Perform **payments over a graph of trust** (multi‑hop, multi‑path)
- Let the network automatically **clear debts in closed cycles**

GEO is:

- **not a cryptocurrency** — no native token, no mining
- **not a blockchain** — no global ledger of all transactions
- **not a bank** — hubs coordinate, but cannot spend on behalf of users

This repository contains the **specification and architecture of GEO v0.1**, plus multilingual documentation (EN/RU/PL) for the first implementation targeting local communities and cooperatives.

---

<!-- CI badge: add when GitHub Actions workflow is configured -->
![Status](https://img.shields.io/badge/status-alpha-blue)
![Spec](https://img.shields.io/badge/spec-GEO%20v0.1-informational)
![Docs](https://img.shields.io/badge/docs-EN%20%7C%20RU%20%7C%20PL-brightgreen)
![License](https://img.shields.io/badge/license-TODO-lightgrey)

---

## Table of Contents

- [Project Vision](#project-vision)
- [Key Concepts](#key-concepts)
- [Repository Layout](#repository-layout)
- [Project Status & Roadmap](#project-status--roadmap)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Running the Hub](#running-the-hub)
  - [Testing & Development](#testing--development)
- [Documentation](#documentation)
  - [English](#english)
  - [Russian](#russian)
  - [Polish](#polish)
- [Contributing](#contributing)
  - [How to Contribute](#how-to-contribute)
  - [Translations](#translations)
- [License](#license)
- [Credits & Contact](#credits--contact)

---

## Project Vision

Modern money works reasonably well as a **medium of exchange**, but it couples that function tightly with:

- Interest‑bearing debt
- Centralized issuance and control
- Global surveillance and freezing of accounts
- Structural leakage of value out of local economies

GEO starts from a different premise:

> Pull the **function of mutual credit and exchange** out of the traditional money system and formalize it as an **open p2p protocol**, focused on local networks of trust.

**Goals of GEO v0.1**:

- Provide a **minimal, implementable protocol** for mutual credit networks
- Target **local communities** (10–500 participants) and cooperatives as first pilots
- Use a pragmatic architecture:
  - **community hub** in v0.1 (single coordinating node per community)
  - with a clear path to:
    - multi‑hub clusters,
    - partial p2p, and
    - inter‑community exchange

GEO v0.1 is intentionally modest: we want to **succeed in one cooperative or municipality first**, then evolve.

For a narrative introduction, see:

- `docs/en/concept/article1-fixing-money-without-revolution.md`
- `docs/en/concept/article2-fixing-money-without-revolution.md`

---

## Key Concepts

### Participants

- People, organizations, cooperatives, hubs.
- Identified by **PID** (`Participant ID`), derived from a public key (Ed25519).
  - PID follows the protocol spec: **`PID = base58(sha256(public_key_bytes))`**.
  - This yields a ~44-character Base58 string that is URL-safe.

Implementation notes:

- Auth supports refresh token rotation via `POST /api/v1/auth/refresh`.
- Operation signatures (registration/payment) are verified over **canonical JSON** payloads (see `api/openapi.yaml`).

### Equivalents

- Units of account in which credit and debt are measured:
  - fiat (`UAH`, `USD`)
  - time (`HOUR_DEV`)
  - resources (`kWh`, `KG_WHEAT`)
  - local units of a cooperative
- GEO is **currency‑agnostic** — it does not impose a native unit.

### TrustLines

A **TrustLine** is a directed credit limit:

> “A trusts B up to L in equivalent E”

- `from` = A (who takes risk)
- `to` = B (who can become debtor)
- `limit` = maximum amount B may owe A in E
- Encodes **local, voluntary credit risk**, not a global money balance.

### Debts / Obligations

A **Debt** is an edge in the debt graph:

> “X owes Y amount S in equivalent E”

- Stored per `(debtor, creditor, equivalent)`
- Always consistent with TrustLines (debt cannot exceed granted trust)

### Payments

Payments in GEO:

- Do **not** move a token from A to B.
- Instead:
  - find one or more paths A → … → B over TrustLines,
  - update **Debts** along those paths,
  - keep every participant’s risk within their chosen limits.

Formally:

- Routing: BFS / k‑shortest paths over the trust graph (v0.1)
- Execution: **two‑phase commit (2PC)** along the path(s) to guarantee atomicity

### Clearing

The network constantly looks for **cycles of debt**:

- `A → B → C → … → A`

Then:

- computes the maximum amount that can be simultaneously reduced for all edges
- issues a special `CLEARING` transaction:
  - conceptually: “A pays A along the cycle on amount S”
  - practically: decreases all debts in that cycle by S

Effect:

- **total nominal debt in the network shrinks**
- real‑world exchanges remain intact

Clearing cycles:

- 3–4 nodes: can be searched after each operation
- 5–6 nodes: searched periodically (e.g. nightly) for performance reasons

---

## Repository Layout

```text
GEOv0-PROJECT/
├── README.md                 # This file (English GitHub README)
├── app/                      # Backend application code (FastAPI)
├── docker/                   # Docker configuration
├── tests/                    # Tests
├── requirements.txt          # Python dependencies
└── docs/
    ├── en/                   # Main English docs
    ├── ru/                   # Russian docs (source/original context)
    └── pl/                   # Polish translations
```

Key conceptual files (English):

- `docs/en/00-overview.md` — short project overview
- `docs/en/01-concepts.md` — key ideas, why GEO, why mutual credit
- `docs/en/02-protocol-spec.md` — core protocol specification (GEO v0.1)
- `docs/en/03-architecture.md` — architecture of the v0.1 community hub
- `docs/en/concept/` — long‑form design notes and essays

---

## Project Status & Roadmap

**Current status:** MVP Backend Implementation (v0.1-alpha).

What exists now:

- **MVP Backend**: Python/FastAPI implementation of the GEO v0.1 protocol.
  - Participants & Auth (Ed25519 challenge-response)
  - TrustLines CRUD
  - Payments (Pathfinding & 2PC execution)
  - Clearing (Cycle detection & execution)
  - Balance & Limits checks
- **Documentation**: Comprehensive conceptual and technical docs.
- **Tests**: Integration scenarios covering key flows.

High‑level roadmap (subject to change):

1. **Phase 0 — Documentation consolidation** (✓)
2. **Phase 1 — MVP backend (community hub)** (✓ - Basic implementation complete)
   - [x] Protocol Core
   - [x] Database Schema
   - [x] API
   - [x] Basic Clearing
3. **Phase 2 — Client applications**
   - Flutter‑based client for end‑users (mobile/desktop/web)
   - Admin UI
4. **Phase 3 — Behavior simulator**
   - Stress‑testing protocol and implementation
5. **Phase 4 — Multi‑hub and inter‑community exchange**

---

## Getting Started

### Local dev quickstart (Windows)

Recommended: use the repo runner script (it starts **Backend + Admin UI**, manages ports, and writes `admin-ui/.env.local`).

```powershell
.\scripts\run_local.ps1 start
```

Common actions:

```powershell
.\scripts\run_local.ps1 status
.\scripts\run_local.ps1 stop

# (optional) Recreate SQLite DB and seed from canonical admin fixtures (richer demo data)
.\scripts\run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100 -RegenerateFixtures
```

### Prerequisites

- **Docker** & **Docker Compose** (Docker Desktop, or Docker Engine inside WSL2)
  - WSL2 no-Docker-Desktop runbook (RU): `docs/ru/runbook-dev-wsl2-docker-no-desktop.md`
- OR Python 3.11+ and PostgreSQL locally

### Running the Hub

The easiest way to run the GEO Hub is using Docker Compose:

```bash
# 1. Clone the repo
git clone https://github.com/slawa19/GEOv0.git
cd GEOv0-PROJECT

# 2. Start services (DB, Redis, API)
#
# If localhost:8000 is already used by another service, pick a different host port:
#   GEO_API_PORT=18000 docker compose up -d --build
#
docker compose up -d --build

# 3. Migrations
# Migrations are executed automatically on container start (see docker/docker-entrypoint.sh).
# If you want to run them manually:
docker compose exec app alembic -c migrations/alembic.ini upgrade head

# 4. Seed initial data (optional)
docker compose exec app python scripts/seed_db.py

# 5. API is now available at:
# - default: http://localhost:8000
# - with GEO_API_PORT override: http://localhost:18000
# Docs: /docs
```

If Docker is unavailable, you can run the backend locally using SQLite (development only):

```powershell
# 1) Initialize SQLite schema (creates ./geov0.db)
python scripts/init_sqlite_db.py

# 2) Seed demo data (from ./seeds/*.json)
python scripts/seed_db.py

# 3) Run API
python -m uvicorn app.main:app --reload --port 18000
```

Health endpoints (also available as `/api/v1/*` aliases):

- `GET /health` and `GET /healthz` → `{ "status": "ok" }`
- `GET /health/db` → DB connectivity check (`{ "status": "ok" }` or HTTP 503)

### Testing (single entry point)

This section is the canonical “how to run tests” entry point for this repo.

#### What tests exist

- **Backend (pytest):** unit + integration tests under `tests/`.
  - Contract test for OpenAPI: `tests/contract/test_openapi_contract.py`.
  - Simulator-focused tests: `tests/unit/test_simulator_*.py`, `tests/integration/test_simulator_*.py`.
- **Simulator UI v2:** unit tests (Vitest) + E2E screenshot tests (Playwright) under `simulator-ui/v2/`.
- **Admin UI:** unit tests (Vitest) + E2E tests (Playwright) under `admin-ui/`.

Test discovery/markers are configured in `pytest.ini`.

#### Backend tests (pytest)

Recommended on Windows:

```powershell
# 1) Create venv (once)
py -m venv .venv

# 2) Activate venv
& .\.venv\Scripts\Activate.ps1

# 3) Install runtime + dev dependencies
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt

# 4) Run all backend tests (includes OpenAPI contract test)
python -m pytest -q
```

Focused runs:

```powershell
# OpenAPI contract only
python -m pytest -q tests/contract/test_openapi_contract.py

# Simulator SSE smoke (fixtures-mode)
python -m pytest -q tests/integration/test_simulator_sse_smoke.py

# Run only simulator-related tests
python -m pytest -q tests/unit/test_simulator_*.py tests/integration/test_simulator_*.py
```

Troubleshooting:
- If `pytest` is not found in PATH, use `python -m pytest` (it always uses the active interpreter).
- If you see `ModuleNotFoundError: No module named 'pytest_asyncio'`, dev dependencies were not installed into the interpreter you are running.
  Re-run `python -m pip install -r requirements-dev.txt` after activating `.venv`.
- If venv creation fails with `ensurepip ... returned non-zero exit status 1`:
  - delete the venv and retry: `rmdir /s /q .venv` (CMD) or `Remove-Item -Recurse -Force .venv` (PowerShell)
  - ensure your base Python has bundled pip: `py -m ensurepip --upgrade`
  - workaround: `py -m pip install virtualenv` then `py -m virtualenv .venv`
- If installs fail with `No space left on device`, check where `%TEMP%` points. You can temporarily redirect temp to a drive with space:
  - CMD: `set TEMP=D:\Temp` and `set TMP=D:\Temp`
  - PowerShell: `$env:TEMP='D:\Temp'; $env:TMP='D:\Temp'`

#### Postgres-backed backend tests (when isolation/locking matters)

SQLite cannot validate real locking/isolation behavior. Use Postgres for concurrency semantics (example: TS-23).

```powershell
# 1) Start Postgres
docker compose up -d db

# 2) Create a dedicated test database inside the container (one-time)
docker exec geov0-db createdb -U geo geov0_test 2>$null

# 3) Point tests at the dedicated DB and explicitly allow schema reset
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test"
$env:GEO_TEST_ALLOW_DB_RESET = "1"  # required: test harness will DROP/CREATE schema

# 4) Run the test
python -m pytest -q tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py
```

Safety note: when `TEST_DATABASE_URL` is non-SQLite, the test harness resets schema.
Always use a dedicated test database (like `geov0_test`).

#### Simulator UI v2 tests

From repo root:

```powershell
# Unit tests (Vitest)
cd simulator-ui/v2
npm install
npm run test:unit

# E2E / screenshots (Playwright)
npm run test:e2e

# Update snapshots (only for intentional visual changes)
npm run test:e2e:update
```

Docs for simulator UI and Real Mode:
- `simulator-ui/README.md`
- `simulator-ui/v2/README.md`
- `docs/ru/simulator/frontend/docs/api.md`
- `docs/ru/simulator/backend/simulator-domain-model.md`

#### For AI assistants (repo rule-of-thumb)

If you are an AI assistant operating in this repo, follow these rules:

1) Prefer VS Code Tasks when available (they encode the correct env/ports). If you run commands manually, use PowerShell on Windows.
2) Always run pytest as `python -m pytest ...` after activating `.venv`.
3) Never point `TEST_DATABASE_URL` at a developer DB. If it is non-SQLite, set `GEO_TEST_ALLOW_DB_RESET=1` only when you are sure it targets a dedicated disposable test DB.
4) For simulator Real Mode contract changes, run at least:
   - `python -m pytest -q tests/contract/test_openapi_contract.py`
   - `python -m pytest -q tests/integration/test_simulator_sse_smoke.py`
5) Do not paste multi-line Python into PowerShell as if it were a script; run Python via `python ...` (or via the configured interpreter/tools).

---

## Admin API (MVP)

This repo includes a minimal Admin API under the normal API base path:

- Base URL (Docker default): `http://localhost:8000/api/v1`
- Base URL (repo runner default): `http://127.0.0.1:18000/api/v1`
- Admin prefix: `/admin/*`

**Auth (MVP):** admin endpoints are guarded by a shared secret header:

- Header: `X-Admin-Token: <token>`
- Config: `ADMIN_TOKEN` (env var) or default `dev-admin-token-change-me`

Examples:

```bash
curl -H "X-Admin-Token: dev-admin-token-change-me" http://localhost:8000/api/v1/admin/config
curl -H "X-Admin-Token: dev-admin-token-change-me" http://localhost:8000/api/v1/admin/feature-flags
# (repo runner default)
curl -H "X-Admin-Token: dev-admin-token-change-me" http://127.0.0.1:18000/api/v1/admin/config
```

For the canonical contract, see `api/openapi.yaml`.

---

## Admin UI (real-mode)

Admin UI lives in `admin-ui/` and can run in two modes:

- `mock` (fixtures) — deterministic JSON datasets
- `real` — calls the backend Admin API (`/api/v1/admin/*`)

Recommended (current repo setup): run real-mode using `VITE_API_BASE_URL`.

Quickstart:

Recommended on Windows (one command, avoids PowerShell quoting / port pitfalls):

```powershell
.\scripts\run_local.ps1 start
```

Choose a full community dataset (and refresh DB):

```powershell
# Greenfield (100 participants)
.\scripts\run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity greenfield-village-100 -RegenerateFixtures
.\scripts\run_local.ps1 start -SeedSource fixtures -FixturesCommunity greenfield-village-100

# Riverside (50 participants)
.\scripts\run_local.ps1 reset-db -SeedSource fixtures -FixturesCommunity riverside-town-50 -RegenerateFixtures
.\scripts\run_local.ps1 start -SeedSource fixtures -FixturesCommunity riverside-town-50
```

Stop:

```powershell
.\scripts\run_local.ps1 stop
```

Manual (Docker):

```powershell
# 1) Start backend + DB
docker compose up -d --build

# Optional seed
docker compose exec app python scripts/seed_db.py

# 2) Run Admin UI
npm --prefix admin-ui install
$env:VITE_API_MODE = 'real'
$env:VITE_API_BASE_URL = 'http://localhost:8000'
npm --prefix admin-ui run dev
```

No-Docker quickstart (SQLite):

```powershell
python scripts/init_sqlite_db.py
# Recommended: seed from canonical admin fixtures datasets (richer demo data, like fixtures-mode UI)
python scripts/seed_db.py --source fixtures

# Choose a full community pack without modifying tracked fixtures (writes to .local-run/fixture-packs):
python scripts/seed_db.py --source fixtures --community greenfield-village-100
python scripts/seed_db.py --source fixtures --community riverside-town-50

# Validate a generated pack (example: Riverside)
cd admin-ui
node scripts/validate-fixtures.mjs --only-pack --v1-dir ..\.local-run\fixture-packs\riverside-town-50\v1

# Legacy small seed set:
# python scripts/seed_db.py --source seeds

python -m uvicorn app.main:app --reload --port 18000

npm --prefix admin-ui install
$env:VITE_API_MODE = 'real'
$env:VITE_API_BASE_URL = 'http://127.0.0.1:18000'
npm --prefix admin-ui run dev
```

Note: the Admin UI role selector (`admin/operator/auditor`) is a **UI-only** convenience (stored in localStorage) that hides/disables some actions.
It is not an authorization boundary; the backend must enforce permissions.

Then open:

- `http://localhost:5173/`

Docs:

- [admin-ui/docs/real-api-integration.md](admin-ui/docs/real-api-integration.md)

---

## Documentation

### English

Core docs:

- [00 — Overview](docs/en/00-overview.md)
- [01 — Concepts](docs/en/01-concepts.md)
- [02 — Protocol Specification](docs/en/02-protocol-spec.md)
- [03 — Architecture](docs/en/03-architecture.md)
- [04 — API Reference](docs/en/04-api-reference.md)
- [05 — Deployment](docs/en/05-deployment.md)
- [06 — Contributing](docs/en/06-contributing.md)

Conceptual deep dives (`docs/en/concept/`):

- [00 — Main context, concept & requirements](docs/en/concept/00-main-context-concept-requirements.md)
- [01 — Discussion & Q&A](docs/en/concept/01-discussion-and-qa.md)
- [02 — Protocol core ideas](docs/en/concept/02-protocol-core-ideas.md)
- [03 — Existing GEO Protocol (overview)](docs/en/concept/03-existing-geo-protocol-overview.md)
- [04 — Architecture ideas](docs/en/concept/04-architecture-ideas.md)
- [05 — Architecture B (community‑hub)](docs/en/concept/05-architecture-b-community-hub.md)
- [06 — Architecture revision v0.1](docs/en/concept/06-architecture-revision-v0.1.md)
- [07 — GEO v0.1 basic credit network protocol](docs/en/concept/07-geo-v0.1-basic-credit-network-protocol.md)
- [08 — Technology stack requirements](docs/en/concept/08-technology-stack-requirements.md)
- [09 — Behavior simulator application](docs/en/concept/09-behavior-simulator-application.md)
- [10 — Target community & marketing strategy](docs/en/concept/10-target-community-marketing-strategy.md)
- [Article 1 — Fixing money without a revolution](docs/en/concept/article1-fixing-money-without-revolution.md)
- [Article 2 — Fixing money without a revolution (v2)](docs/en/concept/article2-fixing-money-without-revolution.md)

### Russian

- `docs/ru/00-overview.md`
- `docs/ru/01-concepts.md`
- …
- `docs/ru/concept/` — original long‑form materials.

### Polish

- `docs/pl/00-overview.md`
- `docs/pl/01-concepts.md`
- …
- `docs/pl/concept/` — full translations of all major conceptual documents.

---

## Contributing

The project is **early‑stage** and contributions are welcome both on the **technical** and **conceptual** side.

### How to Contribute

See:

- [`docs/en/06-contributing.md`](docs/en/06-contributing.md)

High‑level areas where help is needed:

- **Client implementation**
  - Flutter client for end‑users
  - Simple admin UI (web)
- **Behavior simulator**
  - Load testing GEO hubs
  - Visualizing trust/debt graphs
- **Modeling & research**
  - economic simulations,
  - risk models,
  - governance patterns for real communities.

### Translations

Documentation is actively maintained in **EN**, **RU** and **PL**.

If you want to help:

- Fix wording / clarity in any language
- Add missing translations
- Keep conceptual docs in sync across languages

Please follow the guidelines in `docs/en/06-contributing.md` and existing file naming conventions.

---

## License

The license is currently **TODO** and not finalized.

Planned options (to be discussed in the community):

- Permissive license for code (e.g. MIT / Apache‑2.0)
- Creative Commons for documentation (e.g. CC BY‑SA)

Until a `LICENSE` file is added, **do not assume** you can use this work beyond fair use without explicit permission from the author(s).

---

## Credits & Contact

This project builds on:

- The original **GEO Protocol** work and ideas by Dima Chizhevsky and the GEO team
- Many years of discussion around:
  - mutual credit,
  - LETS, WIR and timebanks,
  - credit clearing networks,
  - federated bookkeeping and ledger‑agnostic value transfer

Current maintainer of this repository:

- GitHub: [@slawa19](https://github.com/slawa19)

Feedback, questions, or proposals for pilots (in EN/RU/PL):

- Please open a GitHub issue in this repo, or
- Reach out via the contact channels mentioned in `docs/ru/00-overview.md` / `docs/en/00-overview.md`.

If you are a:

- **developer** — help us turn the spec into a real running system,
- **economist/researcher** — help us stress‑test the ideas,
- **community organizer / cooperative leader** — help us test GEO in the real world.
