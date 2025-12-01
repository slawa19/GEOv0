# MVP GEO Architecture for Local Community  
**Version B: Community-hub + Light Clients**

This document describes Minimum Viable Product (MVP) architecture of GEO system for **one local community** with evolution possibility:

- to **node cluster** within community;
- to **settlements between multiple communities** (cluster-to-cluster).

Goal — provide sufficiently detailed description of components, data, protocols and recommended stack so working prototype can be planned and implemented based on it.

---

## 1. MVP Goals and Framework

### 1.1. Goals

- Implement **mutual credit economy** within one local community (10–500 participants):
  - trust lines between participants;
  - "bought on credit" operations;
  - automatic search and launch of simple clearing cycles (3–4 nodes, basic).
- Ensure **convenient UX**:
  - web client (desktop/mobile browser);
  - if possible — mobile application based on same API.
- Ensure **sufficient architecture openness**:
  - for further transition to:
    - multi-hub configuration (several communities);
    - partial decentralization (own nodes of large participants, hub clusters).

### 1.2. MVP Limitations and Assumptions

- One **community-hub** per community (without cluster yet).
- No global blockchain/ledger; there are:
  - hub local DB;
  - participant signatures on key records (to allow future migration).
- Simplified protocol:

  - transactions go through hub, which:
    - selects routes;
    - coordinates confirmations;
    - stores operation log;
  - meanwhile participants **sign** critical changes (trust, payments) so data doesn't depend completely on trust to hub administrator.

- Inter-community exchange — **outside MVP**, but architecture provides extension points.

---

## 2. Architecture General Overview

### 2.1. High-level Schema

Logically system looks like this:

```text
+---------------------------+
|     Users (UX)           |
|  - Web-client (SPA/PWA)  |
|  - Mobile (optional)     |
+-------------+-------------+
              |
              | HTTPS / WebSocket (JSON/REST)
              v
+---------------------------+
|     API Gateway / BFF     |
+-------------+-------------+
              |
              v
+---------------------------+
|     Community Hub Core    |
|  - Auth & Identity        |
|  - Trust Lines Service    |
|  - Payments Engine        |
|  - Routing (Pathfinding)  |
|  - Clearing Engine        |
|  - Reporting & Metrics    |
+-------------+-------------+
              |
              v
+---------------------------+
|       Data Layer          |
|  - PostgreSQL (main)      |
|  - Redis (cache, sessions)|
+---------------------------+

+---------------------------+
| Crypto / Key Management   |
|  - Client-side key storage|
|  - Operation signing      |
+---------------------------+
```

Important principle:

- **Hub shouldn't be "bank"** making decisions for people;
- it's:

  - computational and communication node;
  - data indexer;
  - coordinating service for GEO protocol execution.

---

## 3. Main Components

### 3.1. Community-hub Backend

Functions:

- Participant management:
  - registration/invitations;
  - participant profiles;
  - status (active/frozen/left).
- Authentication and sessions:
  - email/phone + password/OTP login;
  - binding to participant cryptographic keys.
- Trust line management:
  - creation/modification/closure;
  - limit storage, current balances, status.
- Payment engine:
  - payment request acceptance;
  - route selection;
  - `prepare/commit` protocol execution;
  - signature validation.
- Clearing engine:
  - cycle search (3–4 vertices in MVP);
  - clearing transaction formation;
  - confirmation coordination.
- Reporting:
  - aggregated participant data;
  - balances, turnovers;
  - service analytics.

### 3.2. API Gateway / BFF (Backend For Frontend)

- HTTP(S) REST + WebSocket (or SSE) for:

  - real-time operations (payments, notifications);
  - UI updates without constant polling.

- BFF can be implemented in same application as core (at MVP stage).

### 3.3. Data Layer

**Main DB: PostgreSQL**

- Tables:

  - `participants` — participants;
  - `equivalents` — equivalents (UAH, kWh, "hour of work", …);
  - `trust_lines` — trust lines;
  - `obligations` or `edges` — current obligations/debts per participant pairs;
  - `transactions` — operation facts;
  - `transaction_participants` — transaction participants with their roles;
  - `clearing_cycles` — recorded clearing cycles.

- Choice reasons:

  - transactional integrity (ACID);
  - good tooling for complex queries (cycle search can be partially done SQL + application);
  - reliability, maturity, ecosystem.

**Cache / sessions: Redis**

- Short-lived data:

  - user sessions;
  - temporary reserves on trust lines during `prepare`;
  - task queues (can use separate message broker instead, but Redis often sufficient for MVP).

### 3.4. Client Applications

**Web client (SPA/PWA)**

- Implements:

  - UI for:

    - participant profile;
    - trust line management;
    - payment creation;
    - history and balance viewing;
    - trust network overview (simplified graphs);

  - local storage of private key (in browser storage — WebCrypto + IndexedDB);
  - signing important operations (trust line, payment).

- Recommended technologies:

  - React or Vue (team choice);
  - TypeScript;
  - PWA support (offline mode, phone installation).

**Mobile client (optional)**

- React Native / Flutter / Capacitor (wrapper over web client).
- Repeats web client functionality with mobile UX.

### 3.5. Crypto / Key Management

Fundamentally:

- **User private keys stored client-side**, not on server.
- Server:

  - sees only public keys;
  - receives signatures from clients;
  - verifies them when accepting operations.

Implementation:

- Use `libsodium` / `TweetNaCl` or similar libraries:
  - signature schemes: `Ed25519` (convenient and widely supported);
  - encryption: `XChaCha20-Poly1305` (as needed).

Participant identifier format:

- `participant_id` can be:

  - either arbitrary UUID (store mapping to public key);
  - or function of public key (e.g., `base58` of `SHA-256(pubkey)`).

### 3.6. Routing & Clearing Engine

**Routing (payment path selection)**

- Building paths from A to B:

  - path length limitation (e.g., up to 4–5 nodes in MVP);
  - preference for more "reliable" nodes (by reputation/liquidity).

- Implementation:

  - first version — breadth-first search (BFS) algorithm with filtering by:

    - available limit (remaining trust);
    - equivalent;
    - possible policy restrictions (blacklists etc.).

  - later — transition to:

    - k-shortest paths;
    - max-flow for composite payments.

**Clearing (cycle search and clearing)**

- Algorithms for finding cycles of length 3–4:

  - on pre-built subgraphs;
  - or through SQL queries + application processing:
    - triplets/quadruplets `(A,B,C)`/`(A,B,C,D)` with non-zero mutual debts.

- Scheduler (cron/worker):

  - after each transaction — search for short cycles in its vicinity;
  - periodically (once per hour/day) — background search for additional cycles.

- Clearing execution:

  - formation of special "zero payment along cycle" transaction;
  - confirmation request from all cycle participants (auto-agreement options may exist);
  - balance changes.

---

## 4. Data Model (Logical Level)

### 4.1. Participant

Main fields:

- `id`: UUID / hash(pubkey).
- `public_key`: public key (Ed25519).
- `display_name`: display name.
- `profile`: arbitrary metadata (description, contacts, participant type — individual, organization, hub etc.).
- `status`: `active | suspended | left | deleted`.
- Aggregated indicators (can store in separate tables or as materialized views):

  - `total_incoming_trust` (sum of trust limits _to them_);
  - `total_outgoing_trust` (sum of trust limits _from them_);
  - `total_debt` (their debt to others);
  - `total_credit` (others' debts to them);
  - `net_balance` (net position).

### 4.2. Equivalent

- `id`: UUID.
- `code`: string (e.g., `"UAH"`, `"HOUR_DEV"`, `"kWh"`).
- `description`: human-readable description.
- `precision`: decimal places count.

### 4.3. TrustLine

- `id`: UUID.
- `from_participant_id`: who **gives trust**.
- `to_participant_id`: who is trusted.
- `equivalent_id`.
- `limit`: maximum trust volume.
- `used`: current used volume (as _net_ debt of `to` to `from` in this equivalent, limited by limit).
- `status`: `active | frozen | closed`.
- Metadata:

  - `created_at`, `updated_at`;
  - `policies` (conditions, e.g., clearing auto-confirmation etc.).

Important:

- `used` is not same as total debt \(to \to from\); this is specifically **this line usage** within all operations.
- Net debt between two participants per one equivalent can be calculated as function of several lines (if architecture allows more complex schemes), but in MVP can start with **one trust line per pair+equivalent**.

### 4.4. Obligation / Debt

Two options exist:

1. **Explicit debt table (edges)**

   - `from_participant_id` — debtor;
   - `to_participant_id` — creditor;
   - `equivalent_id`;
   - `amount`: current debt (can be positive/negative with symmetry);
   - connection to transactions (through `transaction_effects` table).

   Meanwhile:

   - trust line `A→B` can be considered as:
     - maximum `amount` on `A→B` (or `B→A` depending on model);

2. **Store only `used` in `trust_lines` and calculate net debts on the fly**

   - simpler by tables, but harder to track long cycles, business logic will handle this.

For MVP practical to **go path 1**:

- separate `debts` (or `obligations`) table where explicitly store net debt per each pair and equivalent;
- `trust_lines` set boundaries for these debt acceptable values.

### 4.5. Transaction

- `id`: UUID.
- `type`: `TRUST_LINE_CREATE | TRUST_LINE_UPDATE | PAYMENT | CLEARING | OTHER`.
- `status`: `PENDING | COMMITTED | ROLLED_BACK | FAILED`.
- `created_at`, `committed_at`.
- `initiator_id`: who initiated transaction.
- `payload`: JSON with business data (structured format).
- `signatures`: array of digital signatures (participants for whom transaction is critical).

Separate table:

- `transaction_participants`:

  - `transaction_id`;
  - `participant_id`;
  - `role`: `PAYER | PAYEE | INTERMEDIATE | TRUSTOR | TRUSTEE | CLEARING_MEMBER | ...`;
  - `signature` (if storing by roles).

---

## 5. Life Cycles (flows)

### 5.1. Participant registration and keys

1. User opens web client.
2. Client generates key pair (Ed25519) locally and saves private key:
   - in `IndexedDB`/secure storage + optional seed phrase export.
3. User fills profile (name etc.) and sends to server:
   - `public_key`,
   - profile,
   - optionally signature of `REGISTER` statement with this key (for crypto binding).
4. Server:
   - creates `participant` record;
   - binds to `public_key`;
   - returns `participant_id` and profile data.
5. Client saves `participant_id` and binding to key.

Options possible:

- registration through invitation;
- basic KYC procedure (depends on community).

### 5.2. Trust line creation/modification

**Scenario:** A gives B trust of 100 in UAH equivalent.

1. In UI A selects B, enters limit.
2. A's client forms request:

   - `from = A.id`,
   - `to = B.id`,
   - `equivalent = UAH`,
   - `limit = 100`,
   - `nonce` / `timestamp`.

   and **signs** it with their private key.

3. Request goes to server.
4. Server:

   - verifies A's signature;
   - checks internal policies (default limits, thresholds etc.);
   - creates or updates record in `trust_lines`;
   - creates transaction of type `TRUST_LINE_CREATE/UPDATE` with A's signature;
   - optionally notifies B (via WebSocket/push).

5. For changes requiring B's consent (e.g., complex policies):

   - server sends B proposal;
   - B signs agreement in their client.

In MVP can start **with unilateral trust line** not requiring second party signature (sufficient that A bears risk).

### 5.3. Payment (credit purchase)

**Scenario:** A pays C 60 in UAH.

Simplified variant (one path, short length):

1. A in UI selects:

   - recipient C;
   - amount and equivalent.

2. A's client sends `CreatePayment` request to server (without signature or with signature, can simplify in MVP).
3. Server:

   - finds one or several routes \(A → N_1 → ... → C\);
   - checks available limits and remainders on trust lines and debts;
   - forms draft `PAYMENT` transaction:

     - list of debt changes on edges;
     - participants and their roles.

4. Server sends route participants `PREPARE`:

   - each node (through their client or in MVP — through server rule) checks:
     - does it fit within limits;
     - are there internal restrictions;
   - in advanced variant: each node's client **signs** their agreement.

5. With `OK` from all:

   - server applies changes to `debts`/`trust_lines.used` in DB;
   - transaction gets `COMMITTED` status;
   - participants sent notifications.

6. With any `FAIL`:

   - transaction marked `ROLLED_BACK`;
   - temporary reserves removed.

For MVP acceptable to:

- **not implement full 2-phase scheme with client confirmations**, but do server-side checking, relying on its coordinator role (especially in trusted community).
- But architecturally still lay foundation for:

  - `signatures` field;
  - possibility to transfer part of confirmation logic to client later.

### 5.4. Clearing (closed cycle of 3–4 nodes)

**Example cycle:** A → B → C → A.

1. After each transaction or by schedule server:

   - builds subgraph around affected nodes;
   - searches for 3–4 node cycles with non-zero mutual debts.

2. Found cycle:

   - calculates \(S = \min(\text{debt per each edge})\);
   - forms clearing draft:
     - "reduce debt on each cycle edge by S".

3. Creates `CLEARING` type transaction with `PENDING` status.
4. Sends cycle participants notification:

   - in MVP can implement:
     - **auto-agreement mode by default** with opt-out possibility;
     - or explicit agreement through UI.

5. After everyone's agreement:

   - applies changes to `debts`;
   - saves transaction as `COMMITTED`.

6. If at least one against/doesn't respond:

   - transaction `FAILED`;
   - can propose clearing for smaller amount (optionally).

---

## 6. Security, Privacy and Resilience

### 6.1. Security

- Store passwords only as `bcrypt/argon2` hashes.
- Authorization:

  - JWT tokens with short lifetime;
  - refresh tokens with longer lifetime;
  - TLS everywhere.

- Signatures:

  - all trust setup and clearing operations can be (gradually) moved to **mandatory crypto signing**;
  - for MVP can start with signing at least trust establishment.

- Protection from injections, CSRF, XSS:

  - standard web development practices;
  - CSP, CSRF-tokens etc.

### 6.2. Privacy

- Hub sees all transactions: this is **MVP-level compromise**.
- To reduce harm:

  - minimize sensitive data logging;
  - clear administrator access policy;
  - option to encrypt part of fields (transaction descriptions, comments).

- In future can:

  - transfer part of logic and data to p2p between clients;
  - use client-client encryption for transaction content.

### 6.3. Resilience

- Backups:

  - regular PostgreSQL backup (full + incremental);
  - configuration and metadata backup.

- Vertical scaling for MVP:

  - one hub within 50–500 participants will handle load easily.

- Clustering preparation (transition to Architecture C):

  - clear separation:

    - "operation journal" level;
    - "projections/indexes" level;

  - so when needed can set replicas, then introduce consensus between them.

---

## 7. Recommended Technology Stack

### 7.1. Backend

Option 1 (very common):

- **Language:** TypeScript
- **Framework:** NestJS or Fastify (with modular architecture).
- **DB:** PostgreSQL (via TypeORM/Prisma/knex).
- **Cache/queues:** Redis.
- **Testing:** Jest.
- **Infrastructure:** Docker, docker-compose for dev; Kubernetes or bare-metal for prod.

Pros:

- huge ecosystem;
- many developers;
- easy integration with JS frontend.

Option 2 (for functionality and high load lovers):

- **Language:** Go or Elixir (Phoenix).
- Similar DB + Redis stack.

For MVP reasonable to take **TypeScript + NestJS** if team has no strong preferences.

### 7.2. Frontend

- **React + TypeScript**:

  - UI kit: MUI / Chakra UI / TailwindCSS;
  - request library: React Query / TanStack Query;
  - Graph rendering (for trust network visualization): `vis-network`, `d3`, `react-force-graph` (by taste).

- PWA configuration for "quasi-mobile" experience.

### 7.3. Cryptography

- Libraries:

  - `libsodium` or wrappers (`tweetnacl`, `libsodium-wrappers`);
  - Standards:

    - signing: Ed25519;
    - hashing: SHA-256/512.

- Private key storage:

  - browser: WebCrypto + IndexedDB;
  - mobile clients: platform secure storage (Keychain / Keystore).

---

## 8. Architecture Evolution (Future Preparations)

### 8.1. Hub clustering

Transition from one hub to several:

- Introduce:

  - 2–3 backend instances;
  - shared PostgreSQL DB (first step);
  - load balancer (Nginx/Traefik).

- Later optionally:

  - PostgreSQL replicas (hot standby);
  - read/write separation;
  - in perspective — consensus journal introduction (Raft etc.).

### 8.2. Inter-community interaction

For uniting several hubs into cluster:

- Add **Community / Hub** entity:

  - each hub — participant node at GEO meta-network level;
  - trust lines open between hubs (at legal entity/organization level).

- Introduce protocol:

  - `Hub-to-Hub`:

    - payment requests between participants of different hubs;
    - aggregated clearing between hubs.

- At MVP level sufficient to design API contracts:

  - `POST /interhub/payment-request`
  - `POST /interhub/clearing-proposal`
  - etc., but not implement them fully.

### 8.3. Partial P2P transition

For large/technically prepared participants:

- possibility to run **own node** that:

  - stores all their data locally;
  - synchronizes with hub through extended API;
  - for some operations can communicate directly with other nodes (in future).

This will require:

- further key protocol development (ID, signatures, routing);
- but already now:

  - important **not to "weld" logic to one server**;
  - always store cryptographic confirmations of participant actions.

---

## 9. MVP Scope (what's included / not included)

### 9.1. Included in MVP

- Participant registration and basic profile.
- Trust line management (minimum — unilateral limits).
- Payment creation:

  - direct (A → B);
  - through one intermediary (A → X → B) — at minimum.
- Simple path search engine (BFS) with path length limitation.
- Search and execution of clearing for 3–4 participant cycles.
- Web client with main screens:

  - balance dashboard;
  - trust line list and setup;
  - payment form;
  - operation history;
  - (optionally) simple trust network visualization.

### 9.2. Not included (but considered by architecture)

- Full P2P mode between clients.
- Complex inter-hub protocol and inter-community clearing.
- Formal consensus between multiple hubs (mini-ledger).
- Complex economic policies (dynamic limits, risk scoring etc.).
- Deep integration with external payment systems/fiat.

---

## 10. Conclusion

Proposed **community-hub + light clients** architecture:

- gives realistic path to **working MVP** for one local community;
- preserves key GEO ideas:

  - trust lines and mutual credit;
  - payment path search;
  - automatic cycle clearing;

- meanwhile:

  - doesn't prematurely introduce complex distributed consensus;
  - clearly separates protocol (signatures, entities) from specific implementation (one server);
  - leaves space for evolution to:

    - community hub cluster;
    - inter-community network;
    - partially p2p architecture.

Next step can:

1. Narrow technology stack to specific libraries and versions.
2. Compile **user stories list** and functional requirements for MVP (backlog).
3. Describe **specific API endpoints** (REST/WS) and message formats for:

   - trust lines;
   - payments;
   - clearing.

After this can proceed to DB schema design and prototype implementation start.