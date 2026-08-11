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

This repository contains the GEO v0.1 implementation, its API contract, and documentation. Russian documents carry the current accepted project decisions; English and Polish documents are dated translations or historical context unless a document explicitly says otherwise.

---

<!-- CI badge: add after the published workflow has reliable run evidence -->
![Status](https://img.shields.io/badge/status-alpha-blue)
![Spec](https://img.shields.io/badge/spec-GEO%20v0.1-informational)
![Docs](https://img.shields.io/badge/docs-RU%20current%20%7C%20EN%2FPL%20translations-informational)
![License](https://img.shields.io/badge/license-TODO-lightgrey)

---

## Current entrypoints

Use these front doors instead of inferring current behavior from similarly named or translated documents:

| Need | Current entrypoint |
|---|---|
| Run the local stack | [Getting Started](#getting-started) (`scripts/run_local.ps1`) |
| System architecture | [RU architecture](docs/ru/03-architecture.md) |
| REST wire schema | [OpenAPI](api/openapi.yaml) |
| Configuration | [RU configuration reference](docs/ru/config-reference.md) |
| Required local tests | [Testing](#testing-single-entry-point) (`scripts/verify_local.ps1`) |
| Simulator | [Simulator documentation](docs/ru/simulator/README.md) |

The [documentation index](docs/README.md) defines authority and precedence when code, runtime evidence, tests, OpenAPI, or prose disagree.

---

## Table of Contents

- [Current entrypoints](#current-entrypoints)
- [Project Vision](#project-vision)
- [Key Concepts](#key-concepts)
- [Repository Layout](#repository-layout)
- [Project Status & Roadmap](#project-status--roadmap)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Running the Hub](#running-the-hub)
  - [Testing (single entry point)](#testing-single-entry-point)
- [Documentation](#documentation)
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
    ├── README.md             # Documentation authority and navigation
    ├── en/                   # Dated translations / historical context
    ├── ru/                   # Current accepted decisions and domain docs
    └── pl/                   # Dated translations / historical context
```

Start at [docs/README.md](docs/README.md). Do not assume filename parity across languages means semantic or date parity.

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
# Local development uses the base file plus the explicit dev overlay.
# If localhost:8000 is already used by another service, pick a different host port:
#   GEO_API_PORT=18000 docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
#
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# 3. Migrations
# Migrations are executed automatically on container start (see docker/docker-entrypoint.sh).
# If you want to run them manually:
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app alembic -c migrations/alembic.ini upgrade head

# 4. Seed initial data (optional)
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app python scripts/seed_db.py

# 5. API is now available at:
# - default: http://localhost:8000
# - with GEO_API_PORT override: http://localhost:18000
# Docs: /docs
```

If Docker is unavailable, you can run the backend locally using SQLite (development only):

```powershell
$env:ENV = 'dev'

# 1) Initialize SQLite schema (creates ./.local-run/geov0.db)
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

The canonical required local gate is the root PowerShell verifier. It runs the
default backend pytest tier (excluding `slow`/`postgres`), asserts a single Alembic
head, and runs Admin UI lint/unit/build plus Simulator UI v2
lint/typecheck/unit/build:

```powershell
# One-time setup
py -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt -r requirements-dev.txt
npm --prefix admin-ui ci
npm --prefix simulator-ui/v2 ci

# Required repository gates
.\scripts\verify_local.ps1
```

The verifier gives pytest a task-specific SQLite DB and basetemp by default. Parallel
agents must pass a unique slug, for example
`.\scripts\verify_local.ps1 -TaskSlug agent_contract_review`.

Local databases, pytest cache/basetemp, logs, PID/NDJSON and browser-test output
belong under the ignored `.local-run/` runtime root. An existing legacy
`./geov0.db` is never moved or deleted automatically; set
`DATABASE_URL=sqlite+aiosqlite:///./geov0.db` only when you intentionally need it.
The runner's reset action is restricted to the new `.local-run` default and fails
closed for this legacy override or any custom URL.

GitHub Actions runs the same verifier with Python 3.11 and Node 22.12. PostgreSQL
integration, production container/schema smoke, simulator super-smoke, Admin E2E,
and Windows Simulator visual E2E jobs run only on the weekly schedule or manual dispatch; see
`.github/workflows/quality.yml`. The presence of the workflow is not evidence of a
green CI run until the published job finishes successfully.

Pinned Ruff is a blocking CI gate for `app migrations`; Black still has known
repository-wide formatting debt. To run both locally after the required checks, use:

```powershell
.\scripts\verify_local.ps1 -StaticDiagnostics
```

The local `-StaticDiagnostics` wrapper reports both tools without changing its exit
status. In CI, however, Ruff is blocking and Black alone has `continue-on-error`.
Mypy is not configured. Do not report a named command/job as green unless it actually
reached a final successful state.

#### Focused backend tests

Test discovery and markers live only in `pytest.ini`. Use the verifier for focused
backend selectors so DB, basetemp, and failure artifacts stay task-local:

```powershell
$taskSlug = "agent_contract_review"
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendSelector tests/contract/test_openapi_contract.py

# Simulator SSE smoke (fixtures-mode)
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendSelector tests/integration/test_simulator_sse_smoke.py

# Expensive milestone: fixtures + deterministic real logic + real-mode HTTP startup
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendSelector tests/integration/test_simulator_super_smoke.py -IncludeExpensive
```

The super-smoke is not a debug loop. Run it after changes to simulator runtime/SSE
schemas, payments/clearing behavior consumed by the simulator, or UI-facing event
payloads. Through the verifier it writes ignored postmortem artifacts under the
task-local `.local-run/test-runs/<TaskSlug>/artifacts/` root.

#### Postgres-backed backend tests (when isolation/locking matters)

SQLite cannot validate real locking/isolation behavior. Use a dedicated disposable
PostgreSQL database and verify its name before enabling schema reset:

```powershell
$taskSlug = "agent_contract_review"
docker compose up -d db
docker exec geov0-db createdb -U geo "geov0_test_$taskSlug" 2>$null
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@localhost:5432/geov0_test_$taskSlug"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendMarker postgres -BackendSelector tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py
```

The harness rejects non-SQLite databases unless both the database name matches
`geov0_test_*` and `GEO_TEST_ALLOW_DB_RESET=1`. The opt-in flag cannot override
an unsafe name. Direct pytest also fails during collection whenever a selected
`postgres` test uses a non-PostgreSQL `TEST_DATABASE_URL`; a skipped SQLite run is
not accepted as evidence. Never point it at developer, shared, staging, or
production data.

#### UI commands and E2E

The aggregate verifier owns required UI checks. For a focused UI run:

```powershell
npm --prefix admin-ui run lint
npm --prefix admin-ui run test
npm --prefix admin-ui run build

npm --prefix simulator-ui/v2 run typecheck
npm --prefix simulator-ui/v2 run test:unit
npm --prefix simulator-ui/v2 run build

# Expensive/manual or scheduled jobs (Simulator visual baselines are Windows-specific)
npm --prefix admin-ui run e2e
npm --prefix simulator-ui/v2 run test:e2e
```

Update Playwright screenshots only after intentional visual review. Agent isolation,
protected-contract and evidence rules are in `AGENTS.md`; do not duplicate them here.

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
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# Optional seed
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app python scripts/seed_db.py

# 2) Run Admin UI
npm --prefix admin-ui install
$env:VITE_API_MODE = 'real'
$env:VITE_API_BASE_URL = 'http://localhost:8000'
npm --prefix admin-ui run dev
```

No-Docker quickstart (SQLite):

```powershell
$env:ENV = 'dev'

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

Start with the [documentation index](docs/README.md). It links to current RU project and domain front doors, identifies the OpenAPI contract, and classifies EN/PL translations, concepts, and archives.

Same-named EN, RU, and PL files are not guaranteed to be synchronized. A translation is informative unless it carries an explicit date and synchronization statement against a current authoritative source.

---

## Contributing

The project is **early‑stage** and contributions are welcome both on the **technical** and **conceptual** side.

### How to Contribute

See:

- [`docs/ru/06-contributing.md`](docs/ru/06-contributing.md)

Test commands remain in [Testing (single entry point)](#testing-single-entry-point) and run through `scripts/verify_local.ps1`.

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

Current accepted project decisions are maintained in **RU**. EN and PL are useful translations and historical context, but repository-wide parity is not claimed.

If you want to help:

- Fix wording / clarity in any language
- Add missing translations
- Record the source document and synchronization date when updating a translation

Please follow [`docs/ru/06-contributing.md`](docs/ru/06-contributing.md) and existing file naming conventions.

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
