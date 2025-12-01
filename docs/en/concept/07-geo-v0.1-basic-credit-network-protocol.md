## GEO v0.1 — Basic Credit Network Protocol for Local Communities and Clusters

*This document is refined considering ideas from original GEO Protocol, but remains maximally simple and suitable for MVP on community-hub architecture.*

------

## 0. Protocol Purpose

**GEO v0.1** is protocol for:

- p2p mutual credit economy:
  - between individual participants (people, organizations);
  - between communities (their hubs act as regular participants);
- without single currency and without global ledger:
  - only **obligations** between participants and **trust lines** (risk limits);
- with:
  - simple and proven algorithms (BFS, 2PC, short cycle search);
  - clear message model and transaction states;
  - possibility of evolution to p2p and cluster hubs.

------

## 1. Protocol Entity Model

### 1.1. Participant

**Participant ID (PID)**:

- participant crypto-identity:
  - Ed25519 key pair (`public_key`, `secret_key`);
  - `PID = base58(sha256(public_key))` (specific format fixed by implementation).
- any protocol entities refer to participants by `PID`.

**Roles (logical):**

- end user (person, business);
- community-hub (community server);
- aggregator/gateway (for fiat/crypto, later).

**Locality principle:**

- Trust line `A → B` — **A's property**;
- Debts `X→Y` — part of cryptographically confirmable state of X and Y (signatures on transactions).

### 1.2. Equivalent

Unit of account:

- `code`: string code (`"UAH"`, `"HOUR_DEV"`, `"kWh"`, ...);
- `precision`: decimal places count;
- `metadata`: description.

All trustlines and debts **always** specify equivalent. No "default currency".

### 1.3. Trust Line (TrustLine)

TrustLine (TL):

- `from`: `PID_from` — who **gives trust**;
- `to`: `PID_to` — who is trusted;
- `equivalent`: `E`;
- `limit`: maximum volume `to` can owe `from` in `E`;
- `policy` (optional):
  - auto-agreement for clearing;
  - restrictions on use as intermediary;
  - daily movement limits etc.

Semantics:

- at any moment: (debt[to \to from, E] \le limit).

TrustLine creation/modification — operation **signed by `from`**.

### 1.4. Obligation (Debt)

Debt edge:

- `debtor`: PID_X;
- `creditor`: PID_Y;
- `equivalent`: E;
- `amount`: number > 0.

Semantics:

- X owes Y `amount` in equivalent E.

For each pair `(X,Y,E)` one aggregated edge `debt[X→Y,E]` is allowed (protocol doesn't impose storing "each deal" — that's application level).

Connection to TrustLine:

- for edges like `B→A` must hold:
  - `debt[B→A, E] ≤ limit(A→B,E)`.

### 1.5. Transaction

Any state change (`trustlines`, `debt`) — transaction result.

General fields:

- `tx_id`: global identifier (can be `hash(payload)` or UUID);
- `type`:
  - `TRUST_LINE_CREATE | TRUST_LINE_UPDATE | TRUST_LINE_CLOSE`;
  - `PAYMENT`;
  - `CLEARING`.
- `initiator`: PID;
- `payload`: typed object (see below);
- `signatures`: participating party signatures (see further);
- `timestamp`: creation/initiation moment.

### 1.6. Transaction State (State machine)

For clarity and compatibility with possible p2p mode introduce **simple state machines**.

**For `PAYMENT`:**

States:

- `NEW` — payment request formed (initiator → coordinator);
- `ROUTED` — routes found (paths, amounts per them);
- `PREPARE_IN_PROGRESS` — `PREPARE` being sent, awaiting `ACK`;
- `COMMITTED` — all participants applied changes;
- `ABORTED` — transaction cancelled (routing error or refusal/timeout `PREPARE`).

**For `CLEARING`:**

- `NEW` — cycle candidate found;
- `PROPOSED` — proposals sent to participants (if explicit consent needed);
- `WAITING_CONFIRMATIONS` — awaiting responses;
- `COMMITTED` — clearing applied;
- `REJECTED` — at least one participant refused/didn't respond.

State transitions fixed in coordinator's (hub/node) local journal and can be recovered on failures.

------

## 2. Protocol Operations

### 2.1. Trust Line Management

#### 2.1.1. TRUST_LINE_CREATE / UPDATE

**Message (at protocol level):**

```json
{
  "msg_type": "TRUST_LINE_CREATE", // or UPDATE
  "tx_id": "…",
  "from": "PID_A",
  "payload": {
    "from": "PID_A",
    "to": "PID_B",
    "equivalent": "UAH",
    "limit": 100.0,
    "policy": { /* optional */ }
  },
  "signature": "sig_A"
}
```

Requirements:

- signature `signature` = `Sign_A(msg_type, tx_id, payload, ...)`;
- coordinator (hub or node) verifies signature and applies changes.

Application algorithm:

1. Check A's signature.
2. Check limit acceptability (local rules).
3. Create or update TrustLine.
4. Create transaction `TRUST_LINE_CREATE/UPDATE (COMMITTED)` in their journal.

#### 2.1.2. TRUST_LINE_CLOSE

Similarly, but:

- may require all debts `debt[B→A,E]` to be repaid (or transferred/cleared);
- otherwise — rejection.

------

### 2.2. Payment (PAYMENT)

#### 2.2.1. PAYMENT_REQUEST (to coordinator)

This is not protocol transaction, but application request (usually by HTTP/WS) to nearest coordinator (community-hub).

```json
{
  "msg_type": "PAYMENT_REQUEST",
  "from": "PID_A",
  "payload": {
    "to": "PID_B",
    "equivalent": "UAH",
    "amount": 60.0,
    "constraints": {
      "max_hops": 4,
      "avoid": ["PID_XYZ"]
    }
  },
  "signature": "sig_A" // optional in MVP
}
```

Coordinator:

- validates request;
- initiates internal `PAYMENT` (creates `tx_id`, state `NEW`).

#### 2.2.2. Routing (RoutingService)

**Task:** based on current trust network state find 1–3 paths from A to B.

For each directed edge (A→B,E):

[ available_credit(A \to B, E) = limit(A \to B, E) - debt[B \to A, E] ]

Edge conditions:

- `available_credit > 0`.

Light-multi-path algorithm:

1. Find first path `P1` with BFS (length limit `max_hops`).
2. Calculate `c1 = capacity(P1) = min(available_credit(e) for e ∈ P1)`.
3. If `c1 ≥ S` — ok, one path sufficient.
4. If `c1 < S`:
   - temporarily "subtract" `c1` from `available_credit` on `P1` edges;
   - find second path `P2` not using exhausted edges;
   - calculate `c2`.
   - if `c1 + c2 ≥ S` — split payment:
     - `amount_1 = min(c1, S)`,
     - `amount_2 = S - amount_1`.
5. Similarly can add 3rd path (optionally), but better limit to two in v0.1.

Result — **route set**:

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

Coordinator moves transaction to `ROUTED` state.

#### 2.2.3. PAYMENT Transaction

Protocol norm:

```json
{
  "tx_id": "…",
  "type": "PAYMENT",
  "initiator": "PID_A",
  "payload": {
    "from": "PID_A",
    "to": "PID_B",
    "equivalent": "UAH",
    "total_amount": 60.0,
    "routes": [
      { "path": ["A", "X1", "B"], "amount": 40.0 },
      { "path": ["A", "Y1", "Y2", "B"], "amount": 20.0 }
    ]
  },
  "signatures": {
    "from": "sig_A" // in advanced variant may be mandatory
  }
}
```

#### 2.2.4. Payment execution (PaymentEngine, 2PC)

**Phase 1. PREPARE**

For each participant affected by transaction (except coordinator), coordinator forms `PAYMENT_PREPARE` message:

```json
{
  "msg_type": "PAYMENT_PREPARE",
  "tx_id": "…",
  "from": "COORDINATOR_PID",
  "to": "PID_X",
  "payload": {
    "equivalent": "UAH",
    "local_effects": [
      {
        "debtor": "PID_X",
        "creditor": "PID_Y",
        "delta": +10.0
      },
      ...
    ]
  },
  "signature": "sig_COORD"
}
```

Node/hub X:

1. Checks that each `delta`:

   - doesn't lead to `debt[...]` > `limit` per corresponding TrustLines;
   - doesn't violate their local policies (by trustline `policy`, maximums etc.).

2. If all ok:

   - **reserves** resources (marks in local state that these limits are occupied);

   - responds `PAYMENT_PREPARE_ACK`:

     ```json
     {
       "msg_type": "PAYMENT_PREPARE_ACK",
       "tx_id": "…",
       "from": "PID_X",
       "payload": { "status": "OK" },
       "signature": "sig_X"
     }
     ```

3. If not:

   - `status = "FAIL"` (and reason).

Coordinator awaits all `ACK` → transaction state:

- if all `OK` → `PREPARE_IN_PROGRESS`;
- if someone `FAIL` → immediately `ABORTED`.

**Phase 2. COMMIT / ABORT**

If `PREPARE` successful:

- coordinator sends `PAYMENT_COMMIT`:

  ```json
  {
    "msg_type": "PAYMENT_COMMIT",
    "tx_id": "…",
    "from": "COORDINATOR_PID",
    "to": "PID_X",
    "signature": "sig_COORD"
  }
  ```

- each participant:

  - removes reserves,
  - actually updates debts `debt[...]` according to `local_effects`,
  - marks transaction `COMMITTED` locally.

If `PREPARE` failed:

- `PAYMENT_ABORT` sent instead of `COMMIT`,
- participants remove reserves without debt changes.

**Idempotency:**

- Repeated `COMMIT` on same `tx_id` shouldn't change state more than once (node stores transaction state locally).

------

### 2.3. Clearing (CLEARING)

#### 2.3.1. Cycle search (ClearingEngine)

**Locally-triggered approach:**

- After each `PAYMENT.COMMITTED`:
  - take all debt edges that changed;
  - consider them as "center" for short cycle search in 2–3 step radius.

Search for:

- length 3 cycles:
  - `A → B → C → A` (by positive `debt`).
- length 4 cycles:
  - `A → B → C → D → A`.

For each cycle (C = (V0, V1, ..., V(k-1), V0)):

[ S = \min(debt[Vi \to V(i+1)]) ]

If (S > \epsilon) (threshold, e.g., `0.01`), can form candidate:

- `CLEARING` for amount S.

Periodically can do broader search (cron) across entire network, but locally basic sufficient.

#### 2.3.2. CLEARING Transaction

```json
{
  "tx_id": "…",
  "type": "CLEARING",
  "initiator": "COORDINATOR_PID",
  "payload": {
    "equivalent": "UAH",
    "cycle": ["A", "B", "C", "A"],
    "amount": 15.0
  },
  "signatures": {
    "initiator": "sig_COORD"
  }
}
```

**Effect:**
 for each arc `Vi → V(i+1)`:

- `debt[Vi→V(i+1),E] -= amount`.

#### 2.3.3. Clearing confirmation

Two modes (configured by participating trustline/node policies):

1. **Auto-agreement by default**:
   - if trustline `X→Y` doesn't prohibit auto-clearing,
   - and `debt` reduction always "improves" participant position,
   - coordinator can consider agreement **by default**;
   - still can send `CLEARING_NOTICE` notification.
2. **Explicit consent**:
   - coordinator sends `CLEARING_PROPOSE` to cycle participants;
   - they respond `CLEARING_ACCEPT`/`REJECT`;
   - if all `ACCEPT` — launch 2PC (`PREPARE`/`COMMIT`) with debt reduction on edges;
   - if someone `REJECT` — `REJECTED`.

Internally for applying changes can use same 2PC mechanism as for `PAYMENT`.

------

## 3. Inter-community Exchange (hubs as participants)

Each **community-hub**:

- has own `PID`;
- can open trustlines with other hubs:
  - `Hub1 → Hub2 (limit=L, E)`.

Payment `A@Hub1 → B@Hub2` logically:

- `A → ... → Hub1 → Hub2 → ... → B`.

Coordinator can be:

- Hub1 (by default, if `from` there);
- or separate agreed mechanism (later).

Protocol **exactly the same**:

- `PAYMENT_REQUEST` → routing with hub participation;
- 2PC across all chain participants (including hubs);
- `CLEARING` between hubs — based on accumulated debts `debt[Hub1→Hub2, E]` and `debt[Hub2→Hub1, E]`.

No separate "inter-hub" protocol needed in v0.1 — we use same operations.

------

## 4. Protocol Messages and Transport

### 4.1. Protocol message types

- Configuration/state:
  - `TRUST_LINE_CREATE`, `TRUST_LINE_UPDATE`, `TRUST_LINE_CLOSE`.
- Payments:
  - `PAYMENT_REQUEST` (to coordinator, usually HTTP/WS, may not be part of "pure protocol");
  - `PAYMENT_PREPARE`, `PAYMENT_PREPARE_ACK`;
  - `PAYMENT_COMMIT`, `PAYMENT_ABORT`.
- Clearing:
  - `CLEARING_PROPOSE`, `CLEARING_ACCEPT`, `CLEARING_REJECT`;
  - `CLEARING_PREPARE` (if using 2PC for application);
  - `CLEARING_COMMIT`, `CLEARING_ABORT`.

All messages have common basic format:

```json
{
  "msg_id": "UUID",
  "msg_type": "STRING",
  "tx_id": "UUID or null",
  "from": "PID",
  "to": "PID or HUB or null",
  "payload": { ... },
  "signature": "BASE64(ed25519_signature)" // from 'from'
}
```

### 4.2. Transport

- Within community-hub:
  - messages can be implemented as service calls + DB records (REST/WS over JSON for clients).
- Between hubs / p2p:
  - same message types can go:
    - via WebSocket,
    - via gRPC,
    - via any reliable or semi-reliable channel.

Important requirement:

- **message format and semantics independent of transport**, so can add:
  - p2p level without changing "protocol language".

------

## 5. Idempotency and Failure Handling

- `tx_id` unique within coordinator:
  - repeated `COMMIT` on already `COMMITTED` transaction **changes nothing**;
  - repeated `PREPARE` with already `COMMITTED/ABORTED` should get response according to final state.

Recommendations:

- store locally at each node:
  - `transactions_local_state` table:
    - `tx_id`,
    - `state` (`NEW`, `PREPARE_OK`, `COMMITTED`, `ABORTED`, ...),
    - `last_update`.

During network failures:

- coordinator can resend `PREPARE`/`COMMIT`;
- participant, seeing already processed `tx_id`, simply returns stable response.

------

## 6. Extensibility (where to go next)

Protocol v0.1 preserves:

- implementation **simplicity** for community-hub;
- and at same time:
  - allows:
    - complicating routing (k-shortest paths, max-flow),
    - transferring coordination to p2p level (when participants have full nodes),
    - introducing hub clusters with consensus over transaction journal,
    - integrating with blockchains (through separate Participant types and equivalents).

------