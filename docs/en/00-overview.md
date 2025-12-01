# GEO Protocol: Project Overview

**Version:** 0.1  
**Date:** November 2025

---

## What is GEO

**GEO** is an open protocol for a decentralized credit network designed to organize mutual credit economies within and between local communities.

### Key Idea

Instead of using traditional money as an intermediary, network participants in GEO:
- Open **lines of trust** (credit limits) for each other
- Perform **payments** via the trust network (via chains of connections)
- Automatically **clear debts** in closed cycles (clearing)

### What it is NOT

- **Not a cryptocurrency** — no GEO tokens, no mining, no speculation
- **Not a blockchain** — no global ledger for all transactions
- **Not a bank** — the hub does not own funds and does not decide for participants

---

## Problems it Solves

### 1. Liquidity Shortage “On the Ground”

**Problem:** There are mutual needs and opportunities, but no “money” as an intermediary.

**Solution:** GEO enables exchange based on mutual trust without waiting for “external” money.

### 2. Outflow of Value from Local Economies

**Problem:** Money spent in local business quickly leaks to banks, corporations, or other regions.

**Solution:** GEO obligations remain within the community and are settled through local goods/services.

### 3. Interest Rate as a Barrier

**Problem:** Loan interest makes many projects unprofitable.

**Solution:** Mutual credit in GEO is inherently interest-free.

### 4. Centralization and Vulnerability

**Problem:** Dependence on banks and regulators creates points of failure.

**Solution:** Each community operates its own node; federation of communities — without a center.

---

## Target Audience

### Target Segments

| Segment | Example Use Cases |
|---------|------------------|
| **Local Communities** | Cooperatives, resident associations, communes |
| **Small Business** | Supplier-client clusters, B2B settlements |
| **Professional Networks** | Freelancers, artisans, consultants |
| **Alternative Economies** | Time banks, LETS systems, local currencies |

### Scale

- **MVP:** 10–500 participants in one community
- **Growth:** up to 1–2 thousand participants per hub
- **Federation:** multiple communities united via inter-hub connections

---

## Key Features

### Implemented in MVP (v0.1)

| Feature | Description |
|---------|-------------|
| **Cryptographic Identity** | Registration with Ed25519 keys |
| **Lines of Trust** | One-way credit limits between participants |
| **Payments via Network** | Routing through trust chains |
| **Automatic Clearing** | Debt offsetting in cycles of 3–6 nodes |
| **Multi-path Payments** | Splitting payments into 2–3 routes |
| **Equivalents** | Any units of account (UAH, hour, kWh) |

### Planned Features

| Feature | Description |
|---------|-------------|
| **Inter-hub Exchange** | Payments between participants of different communities |
| **Clustering** | Multiple hub instances for resilience |
| **Thick Clients** | p2p mode for large participants |
| **Extended Analytics** | Risk scoring, network visualization |

---

## Architecture (High-Level Overview)

```
┌─────────────────────────────┐
│     Clients (Flutter)       │
│  - Mobile App               │
│  - Desktop Client           │
└──────────────┬──────────────┘
               │ HTTPS / WebSocket
               ▼
┌─────────────────────────────┐
│    Community Hub            │
│  - API (FastAPI)            │
│  - GEO Protocol v0.1        │
│  - Addon System             │
└──────────────┬──────────────┘
               │
               ▼
┌─────────────────────────────┐
│    Data Storage             │
│  - PostgreSQL               │
│  - Redis                    │
└─────────────────────────────┘
```

### Role of Hub

The Hub is a **coordinator and indexer**, not a bank:
- Stores the state of the trust and debt network
- Coordinates transactions (PREPARE/COMMIT)
- Searches for routes and cycles for clearing
- Provides API for clients

At the same time:
- Private keys are stored only on user devices
- Critical operations are signed by participants
- The hub cannot perform an operation without the owner's signature

---

## Technology Stack

| Component      | Technology                    |
|----------------|------------------------------|
| **Backend**    | Python 3.11+, FastAPI, Pydantic |
| **Database**   | PostgreSQL                   |
| **Cache/Queue**| Redis                        |
| **Clients**    | Flutter (Dart)               |
| **Cryptography**| Ed25519 (libsodium)         |
| **Containerization**| Docker                  |

---

## Non-Functional Requirements

### Performance

| Metric            | Target Value              |
|-------------------|--------------------------|
| **Average Load**  | 0.1–1 transactions/sec   |
| **Peak Load**     | up to 10 transactions/sec|
| **Margin (stress test)**| 50–100 req/sec     |

### Payment Latency

| Stage             | Target Time             |
|-------------------|------------------------|
| **Route Search**  | < 200–300 ms        |
| **PREPARE Phase** | < 500 ms            |
| **Total Time**    | < 2 sec (95%), < 5 sec (99%) |
| **2PC Timeout**   | 3–5 seconds            |

### Availability

| Metric            | Target Value                    |
|-------------------|---------------------------------|
| **Uptime (MVP)**  | 99% (up to 7 hours downtime/month) |
| **Uptime (mature)**| 99.5% (up to 3.5 hours downtime/month) |
| **RPO**           | 0 (regular backups)             |
| **RTO**           | 1–2 hours                       |

---

## Related Documents

| Document                      | Description                 |
|-------------------------------|----------------------------|
| [01-concepts.md](01-concepts.md)         | Key Concepts                  |
| [02-protocol-spec.md](02-protocol-spec.md) | Protocol Specification        |
| [03-architecture.md](03-architecture.md)   | System Architecture           |
| [04-api-reference.md](04-api-reference.md) | API Reference                 |
| [05-deployment.md](05-deployment.md)       | Deployment                    |
| [06-contributing.md](06-contributing.md)   | Contributing                  |

---

## License and Community

GEO is an open project. Code, documentation, and specifications available under an open license.

We welcome:
- Developers willing to contribute code
- Communities ready to launch a pilot
- Economists and researchers for model analysis
- Designers to improve UX