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
  - In the current implementation, PID is **base64url(public_key_bytes)** with **no padding** (`=` removed).
  - This makes PID safe to use inside URLs (path/query params).

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

### Prerequisites

- **Docker** & **Docker Compose**
- OR Python 3.11+ and PostgreSQL locally

### Running the Hub

The easiest way to run the GEO Hub is using Docker Compose:

```bash
# 1. Clone the repo
git clone https://github.com/slawa19/GEOv0.git
cd GEOv0-PROJECT

# 2. Start services (DB, Redis, API)
docker-compose up -d --build

# 3. Apply migrations
docker-compose exec api alembic upgrade head

# 4. Seed initial data (optional)
docker-compose exec api python scripts/seed_db.py

# 5. API is now available at http://localhost:8000
# Docs: http://localhost:8000/docs
```

### Testing & Development

To run tests locally:

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest
```

The integration tests (`tests/integration/test_scenarios.py`) cover:
- Registration & Authentication
- TrustLine management
- Direct Payments
- Multi-hop Payments
- Debt Clearing Cycles

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
