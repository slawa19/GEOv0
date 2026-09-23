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

**The launcher needs a running PostgreSQL** — programme 017 removed the SQLite engine, so there is no
file-backed fallback. `docker compose up -d db`, or, on a machine without Docker,
[`docs/ru/backend/postgres-local-portable.md`](docs/ru/backend/postgres-local-portable.md).

```powershell
.\scripts\run_local.ps1 start
```

Common actions:

```powershell
.\scripts\run_local.ps1 status
.\scripts\run_local.ps1 stop

# Readiness of the launcher's database: schema at head, the recipe's population, the baseline
.\scripts\run_local.ps1 check-db

# Recreate the launcher's own database and run the community recipe through the domain services
.\scripts\run_local.ps1 reset-db
```

**The launcher's database.** Each launcher run owns exactly one PostgreSQL database,
`geov0_dev_<DbSlug>` (`geov0_dev_local` by default), on `127.0.0.1:5432` as role `geo`. Only a name
matching that contract can ever be reset — `scripts/dev_database.py` refuses anything else, and no
flag overrides it. Parallel agents each take their own slug (`-DbSlug p017t1710`) so they do not
share a mutable database. Override the cluster with `-PgHost` / `-PgPort` / `-PgUser` and the
environment variable `GEO_DEV_PG_PASSWORD`.

**Leftover `.local-run/*.db` files are yours.** The launchers no longer read, write or delete them:
removing the SQLite engine did not authorize deleting anybody's data
(`docs/ru/09-decisions-and-defaults.md:17-19`). If you still need what is in one, open it with any
SQLite client before you delete it; otherwise delete it yourself when you are ready. Nothing in the
repository will do it for you.

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

# 4b. Right after seeding, before any client traffic or simulator run: take the debt reconciliation
#     baseline. The API container is already up here (seeding runs inside it); an idle API is not a
#     writer. See "Debt reconciliation baseline" below.
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app python scripts/take_reconciliation_baseline.py --all

# 5. API is now available at:
# - default: http://localhost:8000
# - with GEO_API_PORT override: http://localhost:18000
# Docs: /docs
```

If Docker is unavailable, run a portable PostgreSQL
([`docs/ru/backend/postgres-local-portable.md`](docs/ru/backend/postgres-local-portable.md)) and point
the backend at a database of your own:

```powershell
$env:ENV = 'dev'
$env:DATABASE_URL = 'postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_local'

# 1) Create the database and bring it to head (the one migration entry)
python scripts/dev_database.py ensure
python -m alembic -c migrations/alembic.ini upgrade head

# 2) Seed by running a community's committed recipe through the domain services. The recipe takes
#    the debt reconciliation baseline itself, on empty debts and before the first payment.
python scripts/seed_db.py --source recipe --community riverside-town-50

# 2b) Keep this database's own copy of the seed's ref -> PID table: the seed writes one per
#     community and the next run of that community overwrites it.
python scripts/dev_database.py adopt --community riverside-town-50

# 3) Check what you got: schema, population, baseline
python scripts/dev_database.py ready

# 4) Run API
python -m uvicorn app.main:app --reload --port 18000
```

`.\scripts\run_local.ps1 start` does all four steps for you.

### Debt reconciliation baseline

The scheduled integrity loop checks every equivalent's `debts` against the debt journal (programme 015,
criterion (a): detection of changes made around the application). The check needs one **baseline** per
equivalent; without it the stored result is `UNVERIFIABLE`.

- **Fresh local database — automatic, and taken by the seed itself.** `scripts/run_local.ps1`
  (`start` on an empty database and `reset-db`), `scripts/run_full_stack.ps1` (an empty database and
  `-ResetDb`) and `scripts/verify_admin_phase4_real_contract.ps1` all seed by running a community's
  recipe, and that recipe takes the baseline on empty debts before its first payment
  (`scripts/seed_recipe.py`). They then re-check it, and fail if it does not hold. There is no
  separate baseline step on these paths, and adding one after seeding would adopt the whole seed and
  certify nothing. After a manual seed by some other route, run
  `python scripts/take_reconciliation_baseline.py --all` yourself before any payments.
- **Upgrading an existing database — explicit and manual (cutover).** Make the system quiet first: stop
  client traffic and simulator runs, and stop anything that writes `debts` around the application
  (maintenance SQL, a restore, a script that bypasses the debt journal). A running but idle API process
  is not a writer and may stay up: a payment or clearing that does happen takes the equivalent's owner
  lock, which the baseline takes too, and a seed operation racing the baseline is refused under the
  application's `SERIALIZABLE` isolation (`40001`). Then run
  `python scripts/take_reconciliation_baseline.py --all`, or `--equivalent CODE` (repeatable).
  - `--all` takes a baseline for every equivalent that has none yet and skips the ones that already
    have one. `--equivalent CODE` refuses an equivalent that already has a baseline (exit code 1).
  - Each equivalent is one transaction under its owner lock.
  - Whatever the journal cannot explain at that moment — for example debts written before the journal
    existed — is recorded as a per-edge offset. **The baseline does not certify those debts**; it only
    makes later changes checkable.
  - **Nothing re-baselines.** There is exactly one baseline per equivalent.

Health endpoints (also available as `/api/v1/*` aliases):

- `GET /health` and `GET /healthz` → `{ "status": "ok" }`
- `GET /health/db` → DB connectivity check (`{ "status": "ok" }` or HTTP 503)

### Testing (single entry point)

The canonical required local gate is the root PowerShell verifier. It runs the
backend pytest tier on PostgreSQL (excluding `slow`), asserts a single Alembic
head, and runs Admin UI lint/unit/build plus Simulator UI v2
lint/typecheck/unit/build. The backend tier needs a running PostgreSQL: see
[`docs/ru/backend/postgres-local-portable.md`](docs/ru/backend/postgres-local-portable.md)
if the machine has none.

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

The verifier gives pytest a task-specific PostgreSQL database and basetemp by default:
with `TEST_DATABASE_URL` unset it uses
`postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_<TaskSlug>`, creates that
database if it is missing, and sets the reset opt-in for that derived name only. Parallel
agents must pass a unique slug, for example
`.\scripts\verify_local.ps1 -TaskSlug agent_contract_review`.

Pytest cache/basetemp, logs, PID/NDJSON and browser-test output belong under the
ignored `.local-run/` runtime root. Any SQLite file left there, and a legacy
`./geov0.db` in the repository root, is **your data**: no script moves, reads or
deletes it any more, and removing the SQLite engine did not authorize deleting it.
The launchers' reset action is restricted to their own `geov0_dev_<slug>`
PostgreSQL database and fails closed for every other name.

GitHub Actions runs the same verifier with Python 3.11 and Node 22.12; its required
backend job runs the whole tier on a `postgres:16` service on every pull request.
Production container/schema smoke, simulator super-smoke, Admin E2E,
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

#### The test database is PostgreSQL

Every backend test runs on PostgreSQL; there is no SQLite tier and no `postgres`
marker any more (programme 017, stage 2). The commands above use the database the
verifier derives from the task slug. To point the tier at a database you chose
yourself, name it explicitly - the reset opt-in is then yours to give:

```powershell
$taskSlug = "agent_contract_review"
docker compose up -d db
docker exec geov0-db createdb -U geo "geov0_test_$taskSlug" 2>$null
$env:TEST_DATABASE_URL = "postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_test_$taskSlug"
$env:GEO_TEST_ALLOW_DB_RESET = "1"
.\scripts\verify_local.ps1 -TaskSlug $taskSlug -BackendOnly -BackendSelector tests/integration/test_concurrent_prepare_routes_bottleneck_postgres.py
```

No Docker on the machine? See [`docs/ru/backend/postgres-local-portable.md`](docs/ru/backend/postgres-local-portable.md)
for a portable PostgreSQL that needs neither Docker nor administrator rights, matched to the version CI pins.

The harness rejects a database unless both its name matches `geov0_test_*` and
`GEO_TEST_ALLOW_DB_RESET=1` is set. The opt-in flag cannot override an unsafe name.
A SQLite or missing `TEST_DATABASE_URL` ends the run before any test is collected
(exit 4), in the verifier and in direct pytest alike; a SQLite run is not accepted
as evidence. Never point it at developer, shared, staging, or
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

Refresh the database from the community recipe:

```powershell
# Riverside (50 participants) - the default
.\scripts\run_local.ps1 reset-db
.\scripts\run_local.ps1 start
```

`-SeedCommunity` names the community, but `riverside-town-50` is the only one that can be seeded
today: `greenfield-village-100` declares nine frozen trust lines that no product operation creates,
so the seed refuses it by name rather than writing a domain column directly (`specs/BACKLOG.md`,
2026-09-22). The Admin UI token is handed to the Admin UI in `VITE_ADMIN_TOKEN`, and the Simulator
UI gets its own `VITE_GEO_DEV_ACCESS_TOKEN`; both default to the backend's dev token, and
`GEO_DEV_ADMIN_TOKEN` overrides both. If you set it, clear the Simulator UI's stored token
(`geo.sim.v2.accessToken` in localStorage), which wins over the variable.

Stop:

```powershell
.\scripts\run_local.ps1 stop
```

Manual (Docker):

```powershell
# 1) Start backend + DB
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

# Optional seed, then the reconciliation baseline right after it (see "Debt reconciliation baseline")
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app python scripts/seed_db.py
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec app python scripts/take_reconciliation_baseline.py --all

# 2) Run Admin UI
npm --prefix admin-ui install
$env:VITE_API_MODE = 'real'
$env:VITE_API_BASE_URL = 'http://localhost:8000'
npm --prefix admin-ui run dev
```

No-Docker quickstart (portable PostgreSQL —
[`docs/ru/backend/postgres-local-portable.md`](docs/ru/backend/postgres-local-portable.md)):

```powershell
$env:ENV = 'dev'
$env:DATABASE_URL = 'postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_local'

python scripts/dev_database.py ensure
python -m alembic -c migrations/alembic.ini upgrade head

# The recipe runs real participants, trust lines, payments and a clearing through the domain
# services, and takes the reconciliation baseline itself before the first payment.
python scripts/seed_db.py --source recipe --community riverside-town-50
python scripts/dev_database.py adopt --community riverside-town-50
python scripts/dev_database.py ready

python -m uvicorn app.main:app --reload --port 18000

npm --prefix admin-ui install
$env:VITE_API_MODE = 'real'
$env:VITE_API_BASE_URL = 'http://127.0.0.1:18000'
$env:VITE_ADMIN_TOKEN = 'dev-admin-token-change-me'
npm --prefix admin-ui run dev
```

**Seeding by performing the operations instead of inserting their result** (programme 017, `T1711`).
The fixture paths above insert debts and transactions a generator invented; this one runs the
community's hand-written recipe (`seeds/communities/<id>/recipe.json`) through
`ParticipantService` / `TrustLineService` / `PaymentService` / `ClearingService` and the admin freeze
handler, with a key pair generated per participant per run and kept only in memory:

```powershell
python scripts/seed_db.py --source recipe --community riverside-town-50
```

It takes the reconciliation baseline itself, on empty debts before the first payment, so
`take_reconciliation_baseline.py` is **not** run afterwards - and it finishes by reconciling what it
produced, printing one line per acceptance check. The symbolic `ref -> PID` table of the run is
written to `.local-run/seed-recipe/<community>/participants.json`; the private keys are not written
anywhere, which is why a second run is refused rather than replayed.

Two refusals are deliberate: a database that is not empty (the earlier run's keys are gone, so its
participants cannot be addressed again), and `greenfield-village-100`, whose description declares
nine `frozen` trust lines that no product operation can write (`specs/BACKLOG.md`, 2026-09-22).

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
