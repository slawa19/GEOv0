# GEO Protocol Core Ideas

## Part 0. Basic Concepts
- **Trust line** — one-sided credit limit between two participants.
- **Obligation** — promise to return goods/services in the future.
- **Trust chain** — sequence of trust lines that connects two participants.
- **Clearing** — mutual debt netting in closed cycles.

## Part 1. Key Protocol Design Forks (Options)

### Option 1: Who coordinates transactions along the path?

**1A. Coordinator = payment initiator**

- Initiator (payer) themselves:
  - find route;
  - send `PREPARE` to everyone along path;
  - collect responses and send `COMMIT`/`ABORT`.
- Pros:
  - maximally p2p;
  - easily scalable — no "center".
- Cons:
  - harder to implement on weak clients and browsers;
  - need initiator online until transaction completion;
  - harder to debug.

**1B. Coordinator = "nearest hub" (community-hub)**

- Client sends simple payment request to their hub.
- Hub:
  - searches for routes;
  - coordinates `PREPARE`/`COMMIT`;
  - writes log.
- Pros:
  - greatly simplifies clients;
  - easier to implement and debug;
  - works well in community-hub architecture you chose.
- Cons:
  - coordination point appears within community;
  - but this is solvable by clustering and signatures.

👉 **For protocol v0:**
 Choose **1B** as main scenario, but describe protocol so that *in principle* any node can be coordinator (needed for p2p evolution).

------

### Option 2: State format — "debt edges" or "balance per trust lines"?

**2A. Explicit debt edges (Obligations)**

- For each pair `(X,Y,E)` store `debt[X→Y,E]` — how much X owes Y in equivalent E.
- Trust line `A→B` is:
  - limit on `debt[B→A]` (i.e. how much B can owe A in this equivalent).
- Pros:
  - convenient to search cycles and do clearing;
  - very explicit who owes whom how much.
- Cons:
  - need separate table for debts.

**2B. Only `used` in trust-line, without separate table**

- Balance per pair must be derived from two directed lines: `A→B` and `B→A`.
- Pros:
  - fewer tables;
- Cons:
  - clearing and routing logically complicated.

👉 **For v0:** take **2A** — separate debt edges `Obligation` / `Debt`.

------

### Option 3: How to format clearing?

**3A. Special transaction type `CLEARING`**

- Explicit transaction type:
  - list of cycle edges;
  - debt reduction on each edge by same amount `S`.
- Pros:
  - simple and transparent model;
  - easy to analyze history and debug.
- Cons:
  - additional operation type (but it's needed anyway).

**3B. Encode clearing as "serial set of payments"**

- Formally execute series of regular `PAYMENT` to achieve same effect.
- Pros:
  - fewer transaction types.
- Cons:
  - inconvenient and confusing;
  - hard to restore economic meaning.

👉 **For v0:** definitely **3A** — separate `CLEARING`.

------

### Option 4: Inter-community exchange

**4A. "Hubs as regular participants"**

- Each hub-community in protocol is just participant (node).
- Trust lines open between them like between any participants.
- Payments between people of different communities:
  - routed through corresponding hubs, but **same protocol** (PAYMENT, CLEARING).
- Pros:
  - maximum simplicity — one protocol for all levels;
  - easy to explain: "communities befriend each other like people".
- Cons:
  - require good risk policy design between hubs.

**4B. Special inter-hub protocol**

- Separate "layer" between hubs:
  - own message types, possibly different clearing format (Netting).
- Pros:
  - can optimize for large volumes.
- Cons:
  - more complex specification;
  - more code.

👉 **For v0:** take **4A** — hubs = regular participants, same protocol.

------

Further I describe **unified GEO v0 protocol**, built on options 1B, 2A, 3A, 4A.

------

## Part 2. GEO v0 Protocol Document

### 0. Protocol Goals

GEO v0 protocol is intended for:

- **p2p mutual credit economy**:
  - between individual people/organizations;
  - between local communities (through their hubs).
- Without single currency and global ledger:
  - network has only participant obligations,
  - and trust lines (credit risk limits).
- Ensuring:
  - **implementation simplicity** (minimum entities, reliance on classical algorithms);
  - **scalability** (local consensus, graph routing);
  - **extensibility** (p2p clients, hub clusters, complex clearing policies).

------

## 1. Protocol Data Model

This is logical description — how exactly this will be stored in specific hub's DB, we discussed separately. Here — "protocol language".

### 1.1. Identity and keys

**Participant (node)**:

- Has one or several cryptographic key pairs (in v0 — one main).
- Main signature scheme: **Ed25519**.
- Participant identifier (PID) = `base58(sha256(public_key))` or similar (fix in implementation).

In protocol any state-changing operation must be:

- either initiated by participant and signed with their key;
- or (for simplified MVP) authorized through hub authentication, but in model **signatures must still be present** (even if not used strictly temporarily).

### 1.2. Equivalents

Equivalent (E):

- code (string): `"UAH"`, `"HOUR_DEV"`, `"kWh"` etc.;
- precision;
- description.

Protocol treats equivalent simply as **value measurement unit**, imposing no monetary properties.

### 1.3. Trust Line

Trust line (TL):

- `from`: (PID_A) — who **gives trust**;
- `to`: (PID_B) — who is trusted;
- `E`: equivalent;
- `limit`: maximum amount B can owe A in E;
- `flags`: policy (auto-clearing, route limits etc.).

Semantics:

- in deals for this equivalent **B's debt to A** across all operations must not exceed `limit`.

In protocol important is **fact of TL existence** and its `limit`. Current limit usage reflected in separate layer — in debt edges.

### 1.4. Obligation (Debt)

Debt edge (D):

- `debtor`: (PID_X) — debtor;
- `creditor`: (PID_Y) — creditor;
- `E`: equivalent;
- `amount`: current debt amount.

Semantics:

- (X) owes (Y) amount `amount` in equivalent E.

Connection to trust lines:

- If there's trust line `A → B (limit=L)` and debt arises `debt[B→A,E] = d`,
  - protocol requires: (d ≤ L).
- If no limit, protocol can:
  - prohibit creating such debt,
  - or allow only with explicit participant consent (extension).

### 1.5. Transaction

Any state change is result of transaction:

General transaction fields:

- `tx_id`: globally unique identifier (UUID/content hash).
- `type`: one of:
  - `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE`;
  - `PAYMENT`;
  - `CLEARING`.
- `initiator`: (PID).
- `payload`: typed object (see below).
- `signatures`: set of participant signatures for whom transaction is critical.
- `timestamp`: logical/physical time.

Protocol doesn't impose global transaction order across entire network — only within coordinator (hub/node) to ensure local consistency.

------

## 2. Protocol Operations

### 2.1. Trust line management

#### 2.1.1. Trust line creation/modification

**Message:** `TRUST_LINE_CREATE` / `TRUST_LINE_UPDATE`

`payload` fields:

- `from`: PID_A;
- `to`: PID_B;
- `equivalent`: E;
- `limit`: number.

Requirements:

- Must be **signed** by `from` (A).
- In simplest variant doesn't require `to` (B) signature — A takes risk themselves.

Application algorithm:

1. Check A's signature.
2. Check that such `(from,to,E)` combination is possible (no conflicting policy).
3. Create/update TrustLine.
4. Record transaction in hub/node local journal.

#### 2.1.2. Trust line closure

`TRUST_LINE_CLOSE`:

- same fields `from`, `to`, `E`.
- Can be prohibited if pair has non-zero debt (need to close debt first).

------

### 2.2. Payment (PAYMENT)

Payment is creating / redistributing debts along trust graph path.

#### 2.2.1. High-level semantics

- Initiator A wants to "pay" B amount `S` in equivalent E.
- System must:
  - find path(s) in trust graph;
  - create or change **debts** along edges of these paths so that:
    - B ends up in better position (owed more or owes less);
    - no participant violates trust limits.

#### 2.2.2. `PAYMENT_REQUEST` message (to coordinator)

This is **not protocol transaction**, but request from client to coordinator (hub/node):

Fields:

- `from`: PID_A (payer);
- `to`: PID_B (recipient);
- `equivalent`: E;
- `amount`: S;
- `constraints` (optional):
  - maximum path length;
  - undesired intermediaries etc.

Message can be signed by A, but at v0 protocol level not strictly required (depends on trust level to coordinator).

#### 2.2.3. Route finding (Routing)

Algorithm (minimally sufficient for v0):

1. Based on current:

   - TrustLines `(from→to,E)` with `limit`,
   - Debts `debt[X→Y,E]`,

   for each directed edge determine **available credit**:

   [ available_credit(A \to B, E) = limit(A \to B, E) - debt[B \to A, E] ]

   (B can owe A only up to `limit`, everything else is violation).

2. Run **breadth-first search (BFS)** from A to B along edges where:

   - `available_credit(edge) > 0`;
   - path length <= `max_hops` (e.g., 4).

3. Find one or several paths `P1, P2, ...` with corresponding `capacity(Pi)`:

   [ capacity(Pi) = \min(available_credit(e) \text{ for all edges } e \in Pi) ]

4. Choose subset of paths (P_1,...,P_k) so that:

   [ \sum capacity(P_i) \geq S ]

   (in v0 can simply take first path if its capacity >= S; combinations of several paths — extension).

Result — **routing plan**:

```json
{
  "routes": [
    {
      "path": ["A", "X1", "X2", "B"],
      "amount": 60.0
    }
  ]
}
```

#### 2.2.4. `PAYMENT` transaction

```
type = PAYMENT
```

`payload`:

- `from`: A;
- `to`: B;
- `equivalent`: E;
- `total_amount`: S;
- `routes`: array:
  - `path`: [PID_0, PID_1, ..., PID_n]; (PID_0 = A, PID_n = B)
  - `amount`: how much goes through this path.

Each route generates debt changes along edges:

- For each adjacent `(U,V)` on path A→...→B:
  - debt `debt[V→U,E]` **increases** by `amount`.

Why this way:

- If payment goes A→X→B, then:
  - X now owes A (or vice versa) — depends on chosen convention, but we already defined:
    - for payment along path A→N1→...→B we make chain: "each next owes previous";
    - so debt from A to B "distributes" along chain.

**Simple and clear variant:**

- For payment A→B, along edge (U → V):
  - debt `debt[V → U] += amount` (V owes U).

Then in chain A→X→B:

- B owes X;
- X owes A.

Result: B indirectly owes A, which is needed.

#### 2.2.5. Coordination (2PC) along route

Use classical **two-phase commit** (2PC), but in local sense — hub/node acts as coordinator.

Phase 1: `PREPARE_PAYMENT`

- Coordinator forms draft `PAYMENT` (as above).

- For each route participant (`path`):

  - `PREPARE` message sent:

    ```json
    {
      "tx_id": "...",
      "type": "PAYMENT",
      "payload": {...},
      "participant": "PID_X",
      "local_effects": [
        {
          "edge": ["debtor", "creditor"],
          "equivalent": "E",
          "delta_amount": "+/- S"
        },
        ...
      ]
    }
    ```

  - Node (or its hub):

    - checks:
      - that after applying `delta_amount`:
        - no trust line limit exceeded;
        - internal policies not violated (e.g., not trusting certain nodes);
    - on success:
      - temporarily **reserves** this capacity (e.g., "locked" flag in DB memory);
    - sends coordinator `PREPARE_ACK (OK | FAIL)`.

Phase 2: `COMMIT_PAYMENT` or `ABORT_PAYMENT`

- If **all** participants across all routes responded `OK`:
  - coordinator sends everyone `COMMIT`:
    - nodes apply local changes (increase debts, update limit cache);
- If at least one responded `FAIL` or didn't respond by timeout:
  - coordinator sends `ABORT`:
    - nodes remove temporary reserves;
    - no debt changes occur.

In practice this can go through one hub (in MVP): hub itself "emulates" nodes. But **in protocol** we consider them logically separate.

------

### 2.3. Clearing (CLEARING)

Clearing is special transaction type that reduces debts in closed cycle.

#### 2.3.1. Cycle search

On coordinator (hub/node) side:

1. Have debt graph `debt[X→Y, E] > 0`.

2. For fixed node A search for:

   - length 3 cycles:
     - `A -> B -> C -> A`, where all three edges have positive debt;
   - length 4 cycles:
     - `A -> B -> C -> D -> A`.

3. For found cycle (C = (V_0,...,V_{k-1}, V_0)):

   - determine:

     [ S = \min(debt[V_{i} \to V_{i+1}], i = 0..k-1) ]

4. If (S > 0), can propose clearing for amount S.

Algorithmically:

- for small k (3–4) sufficient simple nested loops / SQL JOINs;
- for large k (5–6) — separate background process, but for v0 can start with k ≤ 4.

#### 2.3.2. `CLEARING` transaction

`payload`:

- `equivalent`: E;
- `cycle`: PID array: `[V0, V1, ..., Vk-1, V0]`;
- `amount`: S.

Effect:

- for each cycle edge `Vi -> V(i+1)`:
  - `debt[Vi -> V(i+1),E] -= S`.

2PC phases — same `PREPARE`/`COMMIT`/`ABORT`, but:

- `local_effects` — only debt reduction on edges touching given participant.
- In simplest variant clearing can be considered **risk-free** for everyone, so allow auto-agreement (if participant policy doesn't prohibit).

------

## 3. Inter-community Exchange

According to option 4A:

- **each community-hub** — regular `Participant` (node) in global sense.

### 3.1. Trust lines between communities

- Hubs H1 and H2 open TrustLines to each other:
  - `H1 → H2 (limit = L12, E)` and/or `H2 → H1 (limit = L21, E)`.
- These lines determine:
  - credit risk limit communities are ready to have toward each other.

### 3.2. Payments between participants of different hubs

Example: `A` (inside H1) wants to pay `B` (inside H2).

Route logically:

- `A -> ... -> H1 -> H2 -> ... -> B`.

Coordinator can be:

- either hub H1 (by default),
- or more complex system (in future).

Protocol **exactly the same**:

- `PAYMENT_REQUEST` to coordinator;
- routing considering inter-hub TrustLines and debts;
- `PREPARE`/`COMMIT`/`ABORT` along entire chain, including H1 and H2;
- when large number of debts accumulate between H1 and H2, regular `CLEARING` in their subgraph on top.

That is:

- **no separate inter-hub protocol** in v0 — same GEO.

------

## 4. Message Protocol (in abstraction)

At protocol level define **message types**; specific transport binding (HTTP, WebSocket, gRPC, p2p) — implementation question.

### 4.1. Basic message format

```json
{
  "msg_id": "UUID",
  "type": "PAYMENT_REQUEST | PREPARE | PREPARE_ACK | COMMIT | ABORT | CLEARING_PROPOSE | ...",
  "from": "PID_sender",
  "to": "PID_receiver_or_broadcast",
  "tx_id": "UUID (if relates to transaction)",
  "payload": { ... },
  "signature": "sig_over_(type,from,to,tx_id,payload)"
}
```

- `msg_id` needed for deduplication and responses.
- In hub implementation part of messages can be "virtual" — processed inside server without physical network sending.

### 4.2. Key message types

- `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE`:
  - initiator → coordinator (initiator signature).
- `PAYMENT_REQUEST`:
  - client → coordinator.
- `PREPARE`:
  - coordinator → participant;
- `PREPARE_ACK`:
  - participant → coordinator (`OK`/`FAIL`).
- `COMMIT` / `ABORT`:
  - coordinator → participant.
- `CLEARING_PROPOSE`:
  - coordinator → cycle participant (optional, for UX);
- `CLEARING_ACCEPT` / `CLEARING_REJECT`:
  - participant → coordinator.

------

## 5. Algorithmic Foundation (what's "proven")

Protocol relies on following proven solutions:

1. **Two-phase commit (2PC)** for path transaction coordination:
   - classical method in distributed DBs;
   - guarantees atomicity in terms "either all participants applied changes, or nobody".
2. **BFS/Shortest Path** for routing:
   - breadth-first search with filtering by available capacity and path length;
   - simple, well-known, easily optimized.
3. **Small-length cycle search in graph**:
   - for k=3–4 can use simple algorithms with triple/quadruple JOINs or nested loops;
   - classical graph technique, doesn't require complex structures.
4. **Ed25519 cryptography**:
   - widely used, fast, with good library support;
   - applicable on server and in browser/mobile.
5. **ACID DB (PostgreSQL)** on coordinating hub side:
   - each 2PC step (reserves, change commits) done within DB transactions;
   - gives local state consistency for coordinator node.

------

## 6. Error Behavior and Retries

### 6.1. Idempotency

- `tx_id` is unique.
- On repeated message receipt with same `tx_id` node:
  - if transaction already `COMMITTED` — applies **nothing** repeatedly (returns success);
  - if `ABORTED` — returns refusal;
  - if in intermediate state — continues procedure (depending on step).

### 6.2. Network segmentation

- If part of network temporarily unavailable:
  - transactions requiring unavailable node participation don't pass (get `ABORT`);
  - local operations (in subgraph within connected component) work.
- Recovery:
  - debts and limits in stable state at coordinating hubs, can be synchronized by signed snapshots and latest transactions;

(detailed resynchronization protocol — next version, in v0 can limit to "coordinator is source of truth" within community).

------

## 7. Protocol Extensions and Evolution

Protocol v0 is intentionally minimal, but allows extensions:

- **p2p mode without central hub**
  - payment coordinator becomes initiator or chosen participant;
  - `PREPARE/COMMIT` messages go directly over p2p network;
  - message formats same, only transport changes.
- **custom trust policies**
  - additional `policy` field in TrustLine:
    - operation frequency limits;
    - automatic/manual clearing participation;
    - intermediary white/black list.
- **complex routing**
  - instead of simple BFS implement k-shortest paths or max-flow;
  - useful for network growth.
- **formal consensus within hub cluster**
  - several hubs share load and jointly maintain journal:
    - Raft/Tendermint over transaction logs;
    - from message protocol perspective nothing changes.

------

If you want, further we can:

- turn this specification into **formal RFC-format document** (with stricter type definitions, error codes etc.);
- or directly proceed to **specific HTTP/WebSocket API** design, where each protocol message maps to endpoints and request/response formats.

Below — set of ideas from original GEO that **will really strengthen v0**, but almost won't complicate life. For each:

1. idea essence in original GEO,
2. how to carefully embed it in our community-hub variant.

------

## 1. Explicit state machines for payments and clearing

**In GEO:**
 Transactions have clear states and transitions (protocol step set), not just "success/error" flag.

**What to take in v0:**

Make **simple but explicit state machine**:

- For `PAYMENT`:
  - `NEW` → `ROUTED` → `PREPARE_IN_PROGRESS` → (`COMMITTED` | `ABORTED`)
- For `CLEARING`:
  - `NEW` → `PROPOSED` → (`WAITING_CONFIRMATIONS`) → (`COMMITTED` | `REJECTED`)

**Why useful:**

- Simplifies debugging: always see "which step died" operation.
- Prepares ground for p2p mode: when coordinator becomes not only hub, can simply repeat same state machine.
- In backend (hub) code this is +several enums and transition table — almost no complexity increase.

------

## 2. Strict separation: "protocol" vs "transport"

**In GEO:**
 Has own binary protocol over TCP/UDP. Message format and semantics are **separated** from delivery method.

**What to take in v0:**

- Formulate protocol **at message level**, and choose transport:
  - within one community-hub: HTTP/JSON or WebSocket;
  - between hubs later: gRPC, WebSocket, p2p.
- That is:
  - in code: describe `Message` and `Transaction` as **clear structures** (types, fields, signatures),
  - not "as it comes" under specific REST endpoint.

**Why useful:**

- Easy to change/extend transport (add p2p without rewriting logic).
- Simplifies compatibility between different implementations (if other clients/hubs appear later).
- Minimal complication: need to design API contracts anyway, just make them bit more "protocol-like".

------

## 3. Multi-path payments (but in "light" variant)

**In GEO:**
 Strong emphasis on **splitting payment across multiple routes** (multi-path) to increase network effective capacity.

**What to take in v0:**

- Don't do complex `max-flow` right away,
- but:
  - allow **up to 2–3 routes** in one payment;
  - implementation:
    1. Find first path `P1` with capacity `c1`.
    2. If `c1 < S`, try to find second independent path `P2` (not using same edges) and take `c2`.
    3. If `c1 + c2 >= S` — split payment into two routes.

**Why useful:**

- Already gives **real network liquidity boost** in bottlenecks.
- Almost free complexity-wise: essentially two BFS calls and bit of amount splitting logic.
- Good step toward "real GEO routing" without overloading MVP.

------

## 4. Module separation: routing / execution / cycle-closing

**In GEO:**
 Architecturally separated:

- routing module (path search);
- payment protocol execution module;
- cycle closing module.

**What to take in v0:**

Inside community-hub clearly separate three services:

1. **RoutingService**
   - input: request `from, to, E, amount, constraints`;
   - output: path list `routes[]` (without debt changes yet).
2. **PaymentEngine**
   - input: `PAYMENT` with already chosen routes;
   - executes 2PC (`PREPARE`/`COMMIT`), changes debts.
3. **ClearingEngine**
   - periodically scans debt graph;
   - generates `CLEARING` candidates and runs them through same 2PC.

**Why useful:**

- Can change/complicate one module (e.g., routing) **without** breaking others.
- Easy to extract routing/clearing to separate services or even "pluggable modules" in future.
- In NestJS/Go/whatever code this is just three services/packages with clear interfaces — almost no additional complexity.

------

## 5. Clear typing and message semantics (message types)

**In GEO:**
 Large number of strictly typed messages: trustline creation, payment, confirmations, cycle closing etc.

**What to take in v0:**

Don't limit to "few REST endpoints", but **introduce clear message types** for protocol layer:

- `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE`;
- `PAYMENT_REQUEST`, `PAYMENT_PREPARE`, `PAYMENT_COMMIT`, `PAYMENT_ABORT`;
- `CLEARING_PROPOSE`, `CLEARING_ACCEPT`, `CLEARING_REJECT`.

And fix format:

```json
{
  "msg_type": "PAYMENT_PREPARE",
  "tx_id": "...",
  "from": "PID",
  "to": "PID or HUB",
  "payload": { ... },
  "signature": "..."
}
```

Even if everything goes **inside one backend** for now, such structure:

- makes protocol code explicit and "self-documenting";
- allows later:
  - reuse same types over p2p or inter-hub connections.

------

## 6. Strict locality of knowledge and responsibility

**In GEO:**
 Each node:

- knows and controls only **their own** trustlines and local state;
- no node that "knows everything" (no global ledger).

**What to take in v0, even with hub:**

Logically consider that:

- **owner** of trust line `A → B` is **A**:
  - any significant limit changes must be **signed by A**;
- debt state affecting participant X (debts X→Y and Y→X) is their responsibility zone:
  - exportable/signable state that X can take with them.

Practically:

- in MVP hub stores all data,
- but:
  - all trustline changes / large payments **can be signed by participant keys**;
  - provide **export** of signed participant state slice (their lines + debts).

**Why useful:**

- Gives "soft" decentralization: even if hub is central service, participant data is cryptographically tied to them.
- Facilitates future migration to p2p or new hub.
- Minimal complication: add signatures to protocol and export state endpoints.

------

## 7. Conceptual "currency-agnosticism" and multi-equivalency

**In GEO:**
 Protocol not tied to specific currency; any unit of account is just "asset"/equivalent.

**What to take in v0 (we already partially took):**

- Clearly treat each unit as `Equivalent`:
  - code + precision + metadata;
- Separate trustlines and debts **per each equivalent**.

Additionally (from GEO approach):

- Build **unbiased support for multiple equivalents** into protocol:
  - don't make "default currency";
  - in API and UI always explicitly specify equivalent.

**Why useful:**

- Allows from start to maintain:
  - credits in hours, goods, services;
  - fiat equivalents through gateways.
- Matches GEO spirit and almost doesn't complicate implementation: just additional `equivalent` field in needed places.

------

## 8. "Smart" clearing trigger based on local changes

**In GEO:**
 Clearing is not just periodic cron, but part of "live" protocol: cycle search and closing especially active around new transactions.

**What to take in v0:**

- Instead of only "cron once a day":
  - after **each successful PAYMENT**:
    - run quick short cycle search **around affected edges** (their depth 2–3 neighborhood);
  - and full global network search — do less frequently.

**Why useful:**

- Debts start collapsing **almost immediately after formation**;
- improves network liquidity without heavy periodic tasks;
- implementation simple: run search only in local subgraph, not entire DB.

------

## 9. "Hubs as participants" — unified protocol for people and communities

**In GEO (by idea):**
 Any node is credit network participant, be it person, organization or service.

**What to take in v0:**

- Strictly follow principle:
  - **hub is same `Participant`** as regular user:
    - has `PID`, keys, trustlines;
    - inter-community exchange = same `PAYMENT`/`CLEARING`, just with hub participation.

**Why useful:**

- Community unification into clusters **requires no new protocol at all** — only policies and admin interfaces.
- Greatly simplifies mental model and implementation.

------

## 10. Errors and repeated messages (idempotency)

**In GEO:**
 Protocol is complex, so very important:

- operation idempotency;
- correct handling of message duplicates/losses.

**What to take in v0:**

- Strictly:
  - `tx_id` unique;
  - any `COMMIT`/`ABORT` on already completed transaction is **safe** (changes nothing).
- At API level:
  - `PAYMENT_REQUEST` can return same `tx_id` if request essentially repeats (protection from "sent form twice").

**Why useful:**

- Fewer "phantom" bugs;
- simplifies network failure and client restart handling;
- implemented by relatively simple DB logic.

------

If you want, further I can:

- take this idea list and **directly weave them** into our GEO v0 document (protocol and community-hub architecture) — as "v0.1";
- or choose 3–4 most important (e.g.: multi-path light, explicit state machines, routing/payment/clearing separation, trustline signatures) and detail specific changes in DB schemas and API.