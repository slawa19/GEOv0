# MVP GEO Architecture for Local Community

**Version B, revision v0.1: community-hub + light clients + GEO v0.1 protocol**

This document describes Minimum Viable Product (MVP) architecture of GEO system for **one local community** with evolution possibility:

- to **node cluster** within community;
- to **settlements between multiple communities** (cluster-to-cluster);
- to partial **p2p mode** between "thick" clients.

Architecture is aligned with **GEO v0.1 protocol**: trustlines, debts, payments and clearing are implemented as formal transactions and state machines, with community-hub playing coordinator role in MVP.

------

## 1. MVP Goals and Framework

### 1.1. Goals

- Implement **mutual credit economy** within one local community (10–500 participants):
  - trust lines (TrustLines) between participants;
  - debt edges (Debts/Obligations);
  - `PAYMENT` operations through trust network;
  - automatic search and launch of simple clearing cycles (`CLEARING`) of length 3–4.
- Ensure **acceptable UX** suitable for non-technical community:
  - web client (desktop/mobile browser, PWA);
  - if possible — light adaptation to mobile application.
- Preserve **structural openness**:
  - in future — multi-hub configuration (several communities connected by trustlines between hubs);
  - partial decentralization (own nodes of large participants, p2p communications);
  - potential integration with blockchains and payment gateways.

### 1.2. MVP Limitations and Assumptions

- **One** community-hub per community.
- No global blockchain/ledger; there are:
  - hub's local DB as source of truth for community;
  - participant cryptographic signatures on critical operations (trustlines, clearing, gradually — payments).
- Use simple, proven protocol:
  - routing — BFS with light multi-path;
  - path consistency — two-phase commit (2PC);
  - cycle search for clearing — locally-triggered on small lengths (3–4).
- Inter-community exchange:
  - **protocolly** described (hubs as regular participants),
  - can be implemented later, without changing basic entities.

------

### 2. General Architectural Overview

#### 2.1. High-level Schema

```
+-----------------------------+
|         Users              |
|  - Client application      |
|    (mobile/desktop)        |
|  - Web interface (admin)   |
+--------------+--------------+
               |
               | HTTPS / WebSocket (JSON, protocol messages)
               v
+-----------------------------+
|      API / Gateway Layer    |
|  (part of backend core)     |
+--------------+--------------+
               |
               v
+-----------------------------+
|     Community Hub Core      |
|                             |
|  - Auth & Identity          |
|  - TrustLines Service       |
|  - RoutingService           |
|  - PaymentEngine (2PC)      |
|  - ClearingEngine (cycles)  |
|  - Reporting & Analytics    |
|  - Add-on System            |
+--------------+--------------+
               |
               v
+-----------------------------+
|          Data Layer         |
|  - Relational DB (ACID)     |
|  - Cache/queues             |
+-----------------------------+

+-----------------------------+
|   Crypto / Key Management   |
|  - Client-side keys         |
|  - Operation signing        |
+-----------------------------+
```

#### 2.2. Hub Role

Hub:

- coordinates `PAYMENT` and `CLEARING` transactions;
- stores and indexes trust network and debt state within community;
- provides unified API (HTTP/WebSocket) for clients and add-ons;
- acts as Participant in inter-community exchange.

Architecturally hub implemented as **core + add-on system**:

- core handles GEO v0.1 protocol, basic domain entities and integrity guarantees;
- add-ons/integrations can add:
  - additional reports and interfaces;
  - specific business logic;
  - integrations with external systems (e.g., messengers, gateways etc.).

Meanwhile:

- participant private keys stored only on client devices;
- hub doesn't own funds, only records and coordinates obligations between participants.

------

Below — reworked and extended **section 3. Entities and protocol model**, in old format but with more detailed logic and object relationships.

------

## 3. Entities and Protocol Model

Section describes **minimal entity set** on which GEO v0.1 protocol and MVP architecture are built. Important not only to list fields, but understand:

- who "controls" what;
- what invariants system must maintain;
- how these entities participate in payments and clearing.

### 3.1. Participant

**Participant** is any entity that can:

- give and receive trust;
- accumulate debts and credits;
- initiate transactions (trustlines, payments, clearing).

This can be:

- individual;
- business;
- hub (community node that also participates in network as Participant).

Main fields:

- `PID`: stable participant identifier, logically derived from public key (`Ed25519`). Example possible scheme:

  ```text
  PID = base58(sha256(public_key))
  ```

  Important: PID is **pure function** of public key. This allows other participants to verify that given PID actually belongs to owner of specific pubkey.

- `public_key`: Ed25519 public key.

- Profile:

  - "human" name or organization name;
  - participant type (`person`, `business`, `hub` etc.);
  - additional metadata (contacts, description, links etc.).

- Status:

  - `active` — participant is active, can open trustlines and participate in payments;
  - `suspended` — temporarily frozen (by community or participant decision);
  - `left` — formally left community, but their past obligations may still be accounted;
  - `deleted` — logically deleted (used carefully, usually for migrations/errors).

**Invariants and properties:**

- All actions with long-term consequences (trustline changes, clearing agreement, critical settings) must be **signed** with participant's private key and verified by their `public_key`.
- Participant can simultaneously be:
  - "trust node" (hold trustlines with others);
  - "hub" (if they have additional coordination responsibilities).
- Participant always exists **in context of some community** (local hub). In inter-community scenario same PID can figure in multiple hubs, but each instance stores its local trustlines/debts set.

------

### 3.2. Equivalent

**Equivalent** is way to name and formalize what debt is measured in.

This can be:

- monetary unit (e.g., `"UAH"`, `"USD"`);
- hour of time (`"HOUR"`, `"HOUR_DEV"`);
- resource unit (`"kWh"`, `"KG_WHEAT"`).

Main fields:

- `code`: string uniquely identifying equivalent within hub (and preferably stable when exchanging data between hubs). Examples:
  - `"UAH"`, `"EUR"`, `"HOUR_CARE"`, `"LOCAL_UNIT"`.
- `precision`: decimal places count allowed for operations with this equivalent. For example:
  - fiat usually 2 places;
  - electricity can be 3–4;
  - work hours — 2 places quite sufficient.
- `metadata`:
  - human-readable description;
  - equivalent type (fiat, time, resource, community internal conventional unit);
  - possible binding to external identifier (ISO currency code, goods code etc.).

**Key points:**

- System has **no "default currency"**. Any operation always explicitly specifies equivalent (E).
- Same participants can have:
  - trustlines in different equivalents;
  - debts in different equivalents.
- Equivalent doesn't set exchange rate. In MVP GEO doesn't handle automatic conversion between equivalents.

------

### 3.3. Trust Line (TrustLine)

**TrustLine** is agreement like: "participant A allows participant B to owe them up to certain limit in equivalent E".

Important to understand:

- this is **not debt itself**, but only ceiling of acceptable debt;
- this is **unilateral** trust expression: `A → B` is not same as `B → A`.

Fields:

- `from` (PID) — participant giving trust (creditor by limit).
- `to` (PID) — participant who can become debtor within this trust.
- `equivalent` (E) — equivalent in which future debts are measured.
- `limit` — maximum debt volume `to` can have to `from` in equivalent E.
- `policy` (JSON) — set of additional rules and settings, e.g.:
  - auto-agreement flag for clearing participation: can debts be automatically reduced in cycles without separate confirmation;
  - permission/prohibition to be intermediary in payment routes (`can_be_intermediate = true/false`);
  - daily turnover limitation (`daily_outgoing_limit`) etc.
- `status` — `active | frozen | closed`:
  - `active` — line can be used;
  - `frozen` — temporarily not used for new operations (but debt may remain);
  - `closed` — line closed; new debts not created on it, remaining debts must be repaid or zeroed through clearing.

**Basic limit invariant:**

[ debt[to \to from, E] \le limit(from \to to, E) ]

That is, current `to` debt to `from` in equivalent E **never** should exceed specified limit. `PAYMENT` operation that would violate this invariant must be rejected in PREPARE phase.

**Ownership and change properties:**

- Trust line `A → B` belongs to A. Only A has right to:
  - create such line (`TRUST_LINE_CREATE`);
  - change its parameters (`TRUST_LINE_UPDATE`);
  - close it (`TRUST_LINE_CLOSE`).
- All these operations are formatted as `TRUST_LINE_*` transactions **signed with `from` private key**.
- In more complex scenarios (not necessarily in MVP) bilateral or multilateral agreements are allowed, but foundation remains same: each specific trustline record is controlled by its `from`.

------

### 3.4. Debt (Obligation)

**Debt** is no longer intention, but specific obligation: "participant X owes participant Y amount S in equivalent E".

Fields:

- `debtor` (PID_X) — debtor;
- `creditor` (PID_Y) — creditor;
- `equivalent` (E);
- `amount` > 0 — current debt amount.

Interpretation:

- X owes Y `amount` in equivalent E. This is **actual state** that can be relied upon for balance calculation, clearing, risks etc.

**Aggregation and direction:**

- For each triple `(debtor, creditor, equivalent)` **exactly one aggregated record** is stored.
- If mutual obligations exist (X owes Y and Y owes X in same E), this can be implemented:
  - either as two independent records (`X→Y` and `Y→X`),
  - or as one averaged record (but in MVP simpler and more transparent to maintain two).
- Debt is strictly directed entity: direction matters for:
  - who spent limit;
  - who bears risk;
  - how payment routes go.

**Connection to TrustLine:**

If "limitless debt" policy isn't provided, then restriction applies:

[ debt[B \to A, E] \le limit(A \to B, E) ]

That is, B's debt to A cannot exceed what A allowed in their trust line.

This is checked:

- when executing payment (in PaymentEngine during PREPARE);
- when changing trustline limit (in TrustLines Service), if logic "can't reduce limit below existing debt" works.

**Participation in payments and clearing:**

- `PAYMENT` along route increases debts on certain edges (e.g., B starts owing more to C etc.).
- `CLEARING` reduces debts on cycle edges, sometimes zeroing them.

Thus, `debts` table is **main dynamic layer** of system that changes over time, while trustlines set more stable "framework" of risks and trust.

------

### 3.5. Transaction and States

**Transaction** is change unit in system. Any significant operation is recorded as transaction, has identifier, state, signature set and payload.

General fields:

- `tx_id`:
  - global identifier (UUID or transaction content hash);
  - must be unique within hub;
  - serves for idempotency (repeated processing of same `tx_id` shouldn't change state repeatedly).
- `type` — transaction type:
  - `TRUST_LINE_CREATE`
  - `TRUST_LINE_UPDATE`
  - `TRUST_LINE_CLOSE`
  - `PAYMENT`
  - `CLEARING`
- `initiator` (PID) — participant who initiated transaction (e.g., who sent `PAYMENT_REQUEST`, or who changes trustline).
- `payload` (structured JSON):
  - contains transaction-specific data: trustline parameters, payment routes, cycle description for clearing etc.;
  - format strictly defined by protocol for each `type`.
- `signatures` (JSON: role/participant → signature):
  - one or several elements like `{ "participant": PID_A, "role": "from", "signature": "..." }`;
  - signatures allow binding specific participant agreement to specific transaction.
- `timestamp` — transaction creation/initiation time (local or logically agreed).
- `state` — **state machine status**, depending on `type`.

------

#### 3.5.1. State machine for `PAYMENT`

`PAYMENT` transaction describes attempt to transfer "value" from one participant to another through trust network.

States:

- `NEW`:
  - transaction created after receiving `PAYMENT_REQUEST` from initiator;
  - payload has basic fields: from whom, to whom, equivalent, amount, possible constraints.
- `ROUTED`:
  - RoutingService found one or several routes;
  - `routes[]` added to `payload` — path array with amount breakdown per each;
  - these routes not yet applied, only proposed for application.
- `PREPARE_IN_PROGRESS`:
  - PaymentEngine started PREPARE phase:
    - calculated local edge effects (how debts will change);
    - performing limit and policy checks;
    - temporarily reserving capacity on trustlines/debts to prevent races;
  - while transaction in this state, it's "preparing" but outcome not yet known.
- `COMMITTED`:
  - all checks and reservations completed successfully;
  - debt changes were atomically applied in DB;
  - reserves released;
  - final state reflected in `debts` table.
- `ABORTED`:
  - transaction cannot be completed:
    - no route found;
    - insufficient limits;
    - one of participants refused;
    - timeout or error occurred;
  - all temporary reserves removed;
  - debt state returned to what it was before this transaction start.

**Invariant:**

- State transitions must follow allowed steps:
  - `NEW → ROUTED → PREPARE_IN_PROGRESS → COMMITTED`
  - `NEW → ROUTED → PREPARE_IN_PROGRESS → ABORTED`
  - `NEW → ABORTED` (if couldn't find routes at all etc.)
- Repeated attempts to execute `COMMIT` or `ABORT` on already fixed `tx_id` shouldn't change state (`idempotent`).

------

#### 3.5.2. State machine for `CLEARING`

`CLEARING` transaction describes attempt to reduce (partially or completely) debt set forming cycle, without external money transfers.

States:

- `NEW`:
  - ClearingEngine detected cyclic debt structure in equivalent E;
  - calculated maximum possible amount S for "collapse";
  - created transaction with cycle and amount description.
- `PROPOSED`:
  - (optional state if explicit consensus of all cycle participants needed);
  - participants sent proposals to participate in clearing with amount S and cycle structure specified.
- `WAITING_CONFIRMATIONS`:
  - system awaits explicit responses from participants (ACCEPT/REJECT);
  - timeout possible, after which "silence" is interpreted by rules (usually as refusal).
- `COMMITTED`:
  - either:
    - auto-agreement configured (by trustline policies);
    - all conditions met, can directly apply transaction;
  - or:
    - all participants explicitly confirmed participation;
  - in both cases:
    - debts on cycle edges reduced by amount S;
    - limit and sum sign invariants preserved;
    - changes fixed in DB.
- `REJECTED`:
  - at least one participant refused;
  - or timeout occurred without needed confirmation count;
  - debt state **doesn't change** (compared to transaction creation moment).

**MVP simplification:**

- In initial implementation can:
  - use only auto-agreement mode (provided it's safe from trust perspective);
  - manage without explicit `PROPOSED` and `WAITING_CONFIRMATIONS` states — directly transition from `NEW` to:
    - `COMMITTED` if all checks and policies allow auto-clearing;
    - or `REJECTED` if obstacle detected.

------

#### 3.5.3. Trustline transactions

Operations `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE` in MVP can use simplified state model:

- `PENDING` — transaction created, signature being verified, business rules being validated;
- `COMMITTED` — changes successfully applied to trustline record in DB;
- `FAILED` — operation rejected:
  - incorrect signature;
  - internal invariant violation (e.g., attempt to reduce limit below current debt);
  - community business restrictions.

Also acceptable in MVP to immediately fix such transactions as `COMMITTED/FAILED` without long life in `PENDING` state, if checking and application take negligibly small time.

------

## 4. Backend: Services and Responsibilities

### 4.1. Auth & Identity

- Registration:
  - accepts `public_key`, profile data;
  - forms `participant` with `PID`;
  - binds to account (email/phone).
- Login:
  - by email/phone + password/OTP;
  - JWT access/refresh token issuance.
- Session management:
  - token blacklist/whitelist storage (optionally in Redis).

### 4.2. TrustLines Service

Tasks:

- Processing protocol messages `TRUST_LINE_CREATE/UPDATE/CLOSE`:
  - check `from` signature;
  - check business policies (limits, summation, participant statuses);
  - change `trust_lines` table;
  - record corresponding transaction.
- Payment validation:
  - provide available limit information for RoutingService and PaymentEngine.

### 4.3. RoutingService (route search)

Tasks:

- For payment `(A → B, E, S)`:

  - evaluate `available_credit` per each directed edge:

    [ available_credit(A \to B, E) = limit(A \to B, E) - debt[B \to A, E] ]

    (If no limit — treat as 0 or by policy.)

  - find 1–3 paths `P1, P2, ...` using BFS (path length limit `max_hops`, e.g. 4–5).

- Light multi-path:

  - step 1: find `P1`, calculate `c1 = min(available_credit(e) for e∈P1)`;
  - if `c1 ≥ S` — use only `P1`;
  - if `c1 < S`:
    - "reserve" `c1` on `P1` edges (in calculations);
    - try to find `P2`, calculate `c2`;
    - if `c1 + c2 ≥ S` — split payment into two routes;
    - can limit to two paths in MVP (third rarely required by practical network).

Result:

```json
"routes": [
  {
    "path": ["A", "X1", "B"],
    "amount": 40.0
  },
  {
    "path": ["A", "Y1", "Y2", "B"],
    "amount": 20.0
  }
]
```

### 4.4. PaymentEngine (payment execution, 2PC)

Tasks:

1. **`PAYMENT` transaction creation**:

   - on `PAYMENT_REQUEST` from client:
     - create record in `transactions` (`type=PAYMENT`, `state=NEW`, `initiator=A`);
     - call RoutingService → get `routes[]`;
     - save `routes` in `payload`;
     - change `state` to `ROUTED`.

2. **PREPARE phase**:

   - for each participant affected by routes:

     - form local effects:

       ```json
       {
         "debtor": "PID_X",
         "creditor": "PID_Y",
         "equivalent": "E",
         "delta": +amount
       }
       ```

     - check that:

       - after applying `delta` `limit` per TrustLines won't be exceeded;
       - local policies not violated (e.g., specific intermediary prohibited).

   - reserve resources:

     - real reserves — at Redis level (`prepare_locks` structures);
     - or through DB transactions (but don't hold them too long).

   - if checks for all participants and edges successful:

     - set `state = PREPARE_IN_PROGRESS`.

   - if at least one edge/participant doesn't pass checks:

     - cancel all reserves;
     - mark transaction as `ABORTED`.

3. **COMMIT / ABORT phase**:

   - on PREPARE success:
     - within DB transaction:
       - update `debts` on all edges (increase debts);
       - remove all reserves for `tx_id`;
       - mark transaction `COMMITTED`.
   - on failure:
     - remove reserves;
     - `state = ABORTED`.

In MVP hub can "emulate" `PAYMENT_PREPARE/COMMIT` messages locally, not through network, but architecture and message protocol should provide them (for future p2p/inter-hub mode).

### 4.5. ClearingEngine (clearing search and execution)

Tasks:

1. **Locally-triggered cycle search**:

   - after `PAYMENT.COMMITTED`:

     - take changed debt edges;
     - build small subgraph around these edges (radius 2–3);
     - find cycles of length 3–4:
       - example: `A → B → C → A` or `A→B→C→D→A`.

   - for each cycle:

     [ S = \min(debt[Vi \to V(i+1)]) ]

     if (S > \epsilon) (threshold, e.g., 0.01), form `CLEARING` candidate.

2. **`CLEARING` transaction formation**:

   - create record in `transactions` with type `CLEARING`, `state=NEW`;

   - `payload`:

     ```json
     {
       "equivalent": "E",
       "cycle": ["A", "B", "C", "A"],
       "amount": S
     }
     ```

3. **Participant consent**:

   - auto-agreement mode:
     - if trustline policies and general rules allow this (debt reduction considered a priori beneficial),
     - can directly proceed to application phase (2PC for debts).
   - explicit consent mode:
     - send notifications (`CLEARING_PROPOSE`) to participants;
     - await `CLEARING_ACCEPT/REJECT`;
     - on at least one refusal — `state=REJECTED`.

4. **Application (`COMMIT`)**:

   - reduce `debt[Vi→V(i+1)]` by `S` for each edge:
     - in DB transaction;
     - considering idempotency (repeated `COMMIT` doesn't change result);
   - `state = COMMITTED`.

### 4.6. Reporting & Analytics

Functions:

- aggregated indicators:
  - per each participant:
    - total debt (`total_debt`),
    - total credit (`total_credit`),
    - `net_balance`,
    - total incoming/outgoing trust (`total_incoming_trust`, `total_outgoing_trust`).
- analytical reports for community coordinators:
  - who has debt concentration;
  - which connections are underused;
  - clearing indicators (how many debts "collapsed").
- visualizations (optional):
  - trust network graph;
  - debt heat maps.

------

## 5. Data Layer: Details

### 5.1. Main PostgreSQL Tables

(Considering already described logical model; here — brief structure.)

```sql
CREATE TABLE participants (
  id UUID PRIMARY KEY,
  public_key BYTEA NOT NULL,
  display_name TEXT,
  profile JSONB,
  status TEXT NOT NULL, -- 'active', ...
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE equivalents (
  id UUID PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  precision INT NOT NULL,
  metadata JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE trust_lines (
  id UUID PRIMARY KEY,
  from_participant_id UUID REFERENCES participants(id),
  to_participant_id UUID REFERENCES participants(id),
  equivalent_id UUID REFERENCES equivalents(id),
  limit NUMERIC NOT NULL,
  policy JSONB,
  status TEXT NOT NULL, -- 'active', 'frozen', 'closed'
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (from_participant_id, to_participant_id, equivalent_id)
);

CREATE TABLE debts (
  id UUID PRIMARY KEY,
  debtor_id UUID REFERENCES participants(id),
  creditor_id UUID REFERENCES participants(id),
  equivalent_id UUID REFERENCES equivalents(id),
  amount NUMERIC NOT NULL CHECK (amount > 0),
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  UNIQUE (debtor_id, creditor_id, equivalent_id)
);

CREATE TABLE transactions (
  id UUID PRIMARY KEY, -- tx_id
  type TEXT NOT NULL,
  initiator_id UUID REFERENCES participants(id),
  payload JSONB NOT NULL,
  state TEXT NOT NULL,
  signatures JSONB,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

When necessary can add:

- `transaction_participants (transaction_id, participant_id, role, signature)`.

### 5.2. Redis

Application:

- sessions (auth, WebSocket);
- temporary `prepare_locks`:
  - e.g., key `prepare:tx_id` → map `edge_key → reserved_amount`;
  - `edge_key` = `(debtor, creditor, equivalent)`.
- queues/schedulers for ClearingEngine tasks (if not using built-in application scheduler).

------

## 6. Client Applications and UX

### 6.1. Web client (SPA/PWA)

Main screens:

1. **Dashboard**:
   - current net balance;
   - incoming/outgoing trustlines;
   - key indicators (how much I owe, how much owed to me).
2. **Trust lines**:
   - list of outgoing (I give trust) and incoming (trusted to me);
   - line creation/editing form:
     - participant selection;
     - equivalent;
     - limit;
   - operation confirmation and signing.
3. **Payment**:
   - "Pay" form:
     - recipient selection;
     - amount;
     - equivalent;
   - result viewing:
     - success/error confirmation;
     - (optionally) route visualization.
4. **Clearing**:
   - notifications about proposed clearings;
   - "Agree/Refuse" buttons (if not auto-mode).
5. **History**:
   - transaction list:
     - trustlines;
     - payments;
     - clearings;
   - filters and details per each transaction.

### 6.2. Key Management

- On first entry (or during registration):
  - Ed25519 pair generation through WebCrypto/JS library;
  - storage in protected storage (IndexedDB).
- Possibility to:
  - export seed/private key for backup;
  - import when changing device.
- All signatures:
  - formed on client;
  - server receives only public keys and signatures.

------

## 7. Flows Considering Protocol

### 7.1. TrustLine: creation

1. A in UI sets B, equivalent E, limit L.
2. A's client:
   - forms `TRUST_LINE_CREATE` object (type + payload + tx_id);
   - signs it `sig_A`;
   - sends to hub.
3. Hub:
   - verifies signature (by A's `public_key`);
   - checks business restrictions;
   - writes to DB (`trust_lines`, `transactions`);
   - notifies B via WebSocket.

### 7.2. Payment (happy-path)

1. A in UI creates `PAYMENT_REQUEST` to B for amount S in E.
2. Hub:
   - creates `transactions` record (`state=NEW`);
   - calls RoutingService → gets `routes[]`;
   - updates transaction: `payload.routes`, `state=ROUTED`.
3. PaymentEngine:
   - starts PREPARE phase:
     - calculates local effects;
     - checks limits and policies;
     - reserves resources (in Redis/DB);
   - if all ok — moves to `PREPARE_IN_PROGRESS` then to COMMIT:
     - updates `debts` on all edges;
     - removes reserves;
     - `state=COMMITTED`.
   - on error — frees reserves, `state=ABORTED`.
4. Clients A (and B) see result via WebSocket/HTTP response.

### 7.3. Clearing

1. After payment commit:
   - ClearingEngine scans local subgraph;
   - finds cycle, e.g. `A → B → C → A`;
   - determines minimum debt S on cycle edges.
2. Creates `CLEARING` transaction:
   - `state=NEW`;
   - `payload = {equivalent, cycle, amount}`.
3. Depending on settings:
   - either directly applies (2PC over `debts` with auto-agreement);
   - or sends invitations to participants and waits `ACCEPT/REJECT` → then COMMIT/REJECT.

------

## 8. Security, Privacy, Resilience

### 8.1. Security

- TLS everywhere.
- Password storage — bcrypt/argon2.
- CSRF/XSS protections.
- Signed protocol operations:
  - trustlines: mandatory;
  - clearing (in consent mode): mandatory;
  - payments: gradually make mandatory for A participation (and possibly others).

### 8.2. Privacy

- Hub sees network structure and amounts.
- No private keys reach server.
- Can encrypt transaction descriptions (comments) end-to-end between participants if important.

### 8.3. Resilience and Recovery

- PostgreSQL backups (full + incremental).
- Transaction and state logging.
- `tx_id` idempotency:
  - repeated `COMMIT`/`ABORT` don't change state;
  - allows safely repeating operations during network failures.

------

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

## 10. Architecture Evolution

### 10.1. Hub clustering

- Multiple backend instances behind load balancer.
- PostgreSQL replication (master/replica).
- In perspective — consensus transaction journal (Raft/Tendermint).

### 10.2. Inter-community level

- Each hub has PID and trustlines with other hubs.
- Payments and clearing between participants of different communities:
  - same `PAYMENT`/`CLEARING` transactions;
  - routes include hubs.
- Only need:
  - extend routing to multi-hub graph;
  - implement transport between hubs (WebSocket/gRPC).

### 10.3. Partial p2p

- Large participants can run their own nodes:
  - store their trustlines and debts locally;
  - participate in 2PC directly;
  - hub — more coordinator/indexer.
- GEO v0.1 protocol already provides such possibility:
  - same messages/states, different transport.