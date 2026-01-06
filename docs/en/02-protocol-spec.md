# GEO Protocol: Full Specification v0.1

**Version:** 0.1  
**Date:** November 2025  
**Status:** Final specification

---

## Contents

1. [Purpose and Scope](#1-purpose-and-scope)  
2. [Cryptographic Primitives](#2-cryptographic-primitives)  
3. [Data Model](#3-data-model)  
4. [Message Protocol](#4-message-protocol)  
5. [Trust Line Operations](#5-trust-line-operations)  
6. [Payments](#6-payments)  
7. [Clearing](#7-clearing)  
8. [Inter-hub Interaction](#8-inter-hub-interaction)  
9. [Error Handling and Recovery](#9-error-handling-and-recovery)  
10. [Dispute Resolution](#10-dispute-resolution)  
11. [System Integrity Verification](#11-system-integrity-verification)  

Appendices:

A. [Canonical JSON](#a-canonical-json)  
B. [Base58 Alphabet](#b-base58-alphabet)  
C. [Protocol Versioning](#c-protocol-versioning)  
D. [Electronic Money Platform (Future Extension)](#d-electronic-money-platform-future-extension)  
E. [Commodity Tokens (Future Extension)](#e-commodity-tokens-future-extension)  
F. [Conversion Between Equivalents (Exchange)](#f-conversion-between-equivalents-exchange)  
G. [Spam Protection via TrustLines](#g-spam-protection-via-trustlines)  
H. [Countercyclical Function](#h-countercyclical-function)  

---

## 1. Purpose and Scope

### 1.1. Protocol Goals

GEO v0.1 is a protocol for:

- **P2P mutual credit economy** between participants and communities  
- **No single currency** — only obligations in arbitrary equivalents  
- **No global ledger** — only local states and signatures  
- **Automatic clearing** of closed debt cycles  

### 1.2. Design Principles

| Principle        | Implementation                                       |
|------------------|------------------------------------------------------|
| **Simplicity**   | Proven algorithms (BFS, 2PC), minimal set of entities|
| **Locality**     | Consensus only among affected participants           |
| **Extensibility**| Protocol separated from transport                    |
| **Security**     | Cryptographic signatures on all operations           |

### 1.3. Version 0.1 Limitations

- Hub acts as transaction coordinator  
- Maximum payment path length: 6 hops  
- Clearing cycles: 3–6 nodes  
- Multi-path: up to 3 routes per payment  

---

## 2. Cryptographic Primitives

### 2.1. Signature Scheme

**Algorithm:** Ed25519 (Edwards-curve Digital Signature Algorithm)

| Parameter          | Value       |
|--------------------|------------|
| Curve              | Curve25519 |
| Private key size   | 32 bytes   |
| Public key size    | 32 bytes   |
| Signature size     | 64 bytes   |

### 2.2. Hashing

**Algorithm:** SHA-256

Used for:

- Generating PID from public key  
- Computing `tx_id` as hash of transaction contents  
- Verifying data integrity  

### 2.3. Participant Identifier (PID)

```text
PID = base58(sha256(public_key))
```

- Input: 32 bytes Ed25519 public key  
- Hash: SHA-256 → 32 bytes  
- Encoding: Base58 → ~44-char string  

**Example:**

```text
public_key: 0x3b6a27bcceb6a42d62a3a8d02a6f0d73653215771de243a63ac048a18b59da29
PID: "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ"
```

### 2.4. Signature Format

```json
{
  "signer": "PID",
  "signature": "base64(ed25519_sign(message))",
  "timestamp": "ISO8601"
}
```

**Signed message:** canonical JSON payload without the `signatures` field.

Canonical JSON algorithm (normative) is described in Appendix A (see [`docs/en/02-protocol-spec.md`](docs/en/02-protocol-spec.md) section on Canonical JSON).

---

## 3. Data Model

### 3.1. Participant

```json
{
  "pid": "string (base58)",
  "public_key": "bytes (32)",
  "display_name": "string",
  "profile": {
    "type": "person | organization | hub",
    "description": "string",
    "contacts": {}
  },
  "status": "active | suspended | left | deleted",
  "verification_level": "integer (0-3)",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Statuses:**

| Status      | Description             |
|-------------|-------------------------|
| `active`    | Active participant      |
| `suspended` | Temporarily suspended   |
| `left`      | Left the community     |
| `deleted`   | Deleted                 |

### 3.2. Equivalent

```json
{
  "code": "string (unique)",
  "precision": "integer (0-8)",
  "description": "string",
  "metadata": {
    "type": "fiat | time | commodity | custom",
    "iso_code": "string (optional)"
  },
  "created_at": "ISO8601"
}
```

**Rules:**

- `code` — unique, 1–16 characters, `A-Z0-9_`
- `precision` — number of decimal places (0–8)
- `metadata.type` — one of `fiat | time | commodity | custom`
- `metadata.iso_code` — optional; allowed only when `metadata.type == fiat` and must be in format `^[A-Z]{3}$` (e.g., `UAH`)

### 3.3. TrustLine

```json
{
  "id": "uuid",
  "from": "PID",
  "to": "PID",
  "equivalent": "string (code)",
  "limit": "decimal",
  "policy": {
    "auto_clearing": true,
    "can_be_intermediate": true,
    "daily_limit": "decimal | null",
    "blocked_participants": ["PID"]
  },
  "status": "active | frozen | closed",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Invariant:**

```text
∀ (from, to, equivalent): debt[to→from] ≤ limit
```

**Policies:**

| Field                  | Description                                  | Default |
|------------------------|----------------------------------------------|---------|
| `auto_clearing`        | Automatic consent to clearing                | `true`  |
| `can_be_intermediate`  | Allow use as an intermediate route           | `true`  |
| `daily_limit`          | Daily turnover limit (**not enforced in MVP; informational only**) | `null` (no limit) |
| `blocked_participants` | Disallow routes through specified participants| `[]`   |

### 3.4. Debt

```json
{
  "id": "uuid",
  "debtor": "PID",
  "creditor": "PID",
  "equivalent": "string (code)",
  "amount": "decimal (> 0)",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

**Rules:**

- One record per triplet `(debtor, creditor, equivalent)`  
- `amount` is always > 0 (zero records are deleted)  
- Updated atomically within transactions  

### 3.5. Transaction

```json
{
  "tx_id": "string (uuid | hash)",
  "type": "TRUST_LINE_CREATE | TRUST_LINE_UPDATE | TRUST_LINE_CLOSE | PAYMENT | CLEARING",
  "initiator": "PID",
  "payload": { /* type-specific */ },
  "signatures": [
    {
      "signer": "PID",
      "signature": "base64",
      "timestamp": "ISO8601"
    }
  ],
  "state": "string",
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

---

## 4. Message Protocol

### 4.1. Base Message Format

```json
{
  "msg_id": "uuid",
  "msg_type": "string",
  "tx_id": "string | null",
  "from": "PID",
  "to": "PID | null",
  "payload": { /* type-specific */ },
  "signature": "base64(ed25519_sign(canonical_json))"
}
```

### 4.2. Message Types

#### 4.2.1. TrustLine Management

| Type                | Description              |
|---------------------|--------------------------|
| `TRUST_LINE_CREATE` | Create a trust line      |
| `TRUST_LINE_UPDATE` | Update limit/policy      |
| `TRUST_LINE_CLOSE`  | Close a trust line       |

#### 4.2.2. Payments

| Type                  | Description                             |
|-----------------------|-----------------------------------------|
| `PAYMENT_REQUEST`     | Payment request (client → hub)          |
| `PAYMENT_PREPARE`     | Prepare phase (hub → participants)      |
| `PAYMENT_PREPARE_ACK` | Response to prepare                     |
| `PAYMENT_COMMIT`      | Commit                                  |
| `PAYMENT_ABORT`       | Abort                                   |

#### 4.2.3. Clearing

| Type               | Description                |
|--------------------|---------------------------|
| `CLEARING_PROPOSE` | Clearing proposal         |
| `CLEARING_ACCEPT`  | Participant acceptance    |
| `CLEARING_REJECT`  | Participant rejection     |
| `CLEARING_COMMIT`  | Commit clearing           |
| `CLEARING_ABORT`   | Abort clearing            |

#### 4.2.4. Service Messages

| Type      | Description          |
|-----------|----------------------|
| `PING`    | Connectivity check   |
| `PONG`    | PING response        |
| `ERROR`   | Error message        |

### 4.3. Transport

The protocol is transport-agnostic. Recommended options:

| Transport          | Usage                      |
|--------------------|---------------------------|
| **HTTPS + JSON**   | REST API for clients      |
| **WebSocket + JSON** | Real-time notifications |
| **gRPC + Protobuf**  | Inter-hub communication |

---

## 5. Trust Line Operations

### 5.1. TRUST_LINE_CREATE

**Payload:**

```json
{
  "from": "PID_A",
  "to": "PID_B",
  "equivalent": "UAH",
  "limit": 1000.00,
  "policy": {
    "auto_clearing": true,
    "can_be_intermediate": true
  }
}
```

**Requirements:**

- `from` (A) signature is required  
- No existing active TrustLine `(from, to, equivalent)`  
- `limit` > 0  

**Algorithm:**

1. Verify A's signature  
2. Ensure the line is unique  
3. Validate `policy`  
4. Create `TrustLine` record  
5. Create `TRUST_LINE_CREATE (COMMITTED)` transaction  

### 5.2. TRUST_LINE_UPDATE

**Payload:**

```json
{
  "trust_line_id": "uuid",
  "limit": 1500.00,
  "policy": { /* updated fields */ }
}
```

**Requirements:**

- Owner (`from`) signature is required  
- Line exists and `status = active`  
- New `limit` ≥ current `debt[to→from]`  

**Algorithm:**

1. Verify owner signature  
2. Ensure new limit is not below current debt  
3. Update `TrustLine` record  
4. Create `TRUST_LINE_UPDATE (COMMITTED)` transaction  

### 5.3. TRUST_LINE_CLOSE

**Payload:**

```json
{
  "trust_line_id": "uuid"
}
```

**Requirements:**

- Owner signature is required  
- `debt[to→from] = 0` (debt is fully repaid)  

**Algorithm:**

1. Verify owner signature  
2. Ensure there is no outstanding debt  
3. Set `status = closed`  
4. Create `TRUST_LINE_CLOSE (COMMITTED)` transaction  

---

## 6. Payments

### 6.1. Process Overview

```text
                    ┌──────────────────┐
                    │ PAYMENT_REQUEST  │
                    └────────┬─────────┘
                             ▼
                    ┌──────────────────┐
                    │    Routing       │
                    │  (find paths)    │
                    └────────┬─────────┘
                             ▼
              ┌──────────────────────────────┐
              │        PREPARE Phase         │
              │  (reserve on all edges)      │
              └──────────────┬───────────────┘
                             │
              ┌──────────────┴───────────────┐
              │                              │
              ▼                              ▼
       ┌────────────┐                 ┌────────────┐
       │ All OK     │                 │ Any FAIL   │
       └─────┬──────┘                 └─────┬──────┘
             ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │   COMMIT   │                 │   ABORT    │
      │ (apply)    │                 │ (release)  │
      └────────────┘                 └────────────┘
```

### 6.2. PAYMENT_REQUEST

**Client → Hub message:**

```json
{
  "msg_type": "PAYMENT_REQUEST",
  "from": "PID_A",
  "payload": {
    "to": "PID_B",
    "equivalent": "UAH",
    "amount": 100.00,
    "description": "Service payment",
    "constraints": {
      "max_hops": 4,
      "max_paths": 3,
      "timeout_ms": 5000,
      "avoid": ["PID_X"]
    }
  },
  "signature": "..."
}
```

**Constraints:**

| Field        | Description                      | Default |
|--------------|----------------------------------|---------|
| `max_hops`   | Maximum path length              | 6       |
| `max_paths`  | Maximum number of routes         | 3       |
| `timeout_ms` | Transaction timeout              | 5000    |
| `avoid`      | Participants to avoid in routes  | `[]`    |

### 6.3. Routing

#### 6.3.1. Available Credit

For a directed edge `(A→B, E)`:

```text
available_credit(A→B, E) = limit(A→B, E) - debt[B→A, E]
```

Where:

- `limit(A→B, E)` — TrustLine limit from A to B in equivalent E  
- `debt[B→A, E]` — B's current debt to A in equivalent E  

#### 6.3.2. k-Shortest Paths Search

```python
def find_k_paths(graph, source, target, k=3, max_hops=6):
    """
    Modified Yen's algorithm for k-shortest paths
    using available_credit as capacity/weight heuristic.
    """
    paths = []

    # First path: BFS by maximum capacity
    path1 = bfs_max_capacity(graph, source, target, max_hops)
    if path1:
        paths.append(path1)

    # Subsequent paths: iterative edge removal
    for i in range(1, k):
        candidates = []
        for j in range(len(paths[i - 1]) - 1):
            spur_node = paths[i - 1][j]
            # Temporarily remove edge
            graph_wo = graph_without_edge(graph, paths[i - 1][j], paths[i - 1][j + 1])
            # Find alternative path from spur_node
            alt_path = bfs_max_capacity(graph_wo, spur_node, target, max_hops - j)
            if alt_path:
                candidates.append(paths[i - 1][:j] + alt_path)

        if candidates:
            # Choose path with maximum capacity
            paths.append(max(candidates, key=path_capacity))

    return paths
```

#### 6.3.3. Path Capacity

```text
capacity(path) = min(available_credit(edge) for edge in path)
```

#### 6.3.4. Multi-path Split

```python
def split_payment(amount, paths):
    """
    Split a payment across multiple routes.
    """
    result = []
    remaining = amount

    for path in sorted(paths, key=path_capacity, reverse=True):
        cap = path_capacity(path)
        use = min(cap, remaining)
        if use > 0:
            result.append({
                "path": path,
                "amount": use
            })
            remaining -= use

        if remaining <= 0:
            break

    if remaining > 0:
        raise InsufficientCapacity(f"Cannot route {amount}, missing {remaining}")

    return result
```

#### 6.3.5. Max Flow Algorithm (Extended Case)

For large payments or optimal routing, a max-flow algorithm can be used:

```python
def max_flow_routing(graph, source, target, required_amount, equivalent):
    """
    Edmonds–Karp algorithm for maximum flow.

    Finds an optimal flow distribution, minimizing the number of edges used.
    """
    residual = build_residual_graph(graph, equivalent)

    total_flow = Decimal("0")
    flow_assignment = defaultdict(Decimal)

    while total_flow < required_amount:
        # BFS for augmenting path
        path = bfs_find_path(residual, source, target)
        if not path:
            break  # Max flow reached

        # Bottleneck capacity
        bottleneck = min(
            residual[u][v]["capacity"]
            for u, v in zip(path[:-1], path[1:])
        )

        augment = min(bottleneck, required_amount - total_flow)

        # Update residual graph and flow assignment
        for u, v in zip(path[:-1], path[1:]):
            residual[u][v]["capacity"] -= augment
            residual[v][u]["capacity"] += augment  # Reverse edge
            flow_assignment[(u, v)] += augment

        total_flow += augment

    if total_flow < required_amount:
        raise InsufficientCapacity(
            f"Max flow {total_flow} < required {required_amount}"
        )

    # Decompose flow to simple paths
    return decompose_flow_to_paths(flow_assignment, source, target)


def decompose_flow_to_paths(flow_assignment, source, target):
    """
    Decompose edge flow into a set of paths (flow decomposition).

    Returns: list of {path: [...], amount: X}
    """
    paths = []
    remaining_flow = flow_assignment.copy()

    while True:
        path = dfs_find_flow_path(remaining_flow, source, target)
        if not path:
            break

        min_flow = min(
            remaining_flow[(u, v)]
            for u, v in zip(path[:-1], path[1:])
        )

        for u, v in zip(path[:-1], path[1:]):
            remaining_flow[(u, v)] -= min_flow
            if remaining_flow[(u, v)] == 0:
                del remaining_flow[(u, v)]

        paths.append({"path": path, "amount": min_flow})

    return paths
```

**When to use Max Flow:**

| Scenario                  | Algorithm         | Reason                         |
|---------------------------|-------------------|--------------------------------|
| Small payments (< 1000)   | k-shortest paths  | Faster, sufficient             |
| Large payments           | Max Flow          | Optimal distribution           |
| Fragmented network       | Max Flow          | Uses all capacity efficiently  |
| Capacity feasibility test| Max Flow          | Precise "can/cannot" answer    |

#### 6.3.6. Atomicity of Multi-path Payments

**Problem:** In a multi-path payment, some routes may succeed in PREPARE while others fail. We must guarantee atomicity: either all routes commit or none.

**Solution: Group PREPARE**

```python
class MultiPathPaymentCoordinator:
    """
    Coordinator for atomic multi-path payments.

    Guarantees that either all routes commit or all abort.
    """

    async def execute_multipath_payment(
        self,
        tx_id: str,
        routes: list["PaymentRoute"],
        timeout: timedelta
    ) -> "PaymentResult":
        """
        Atomic execution of a multi-path payment.
        """
        # Phase 1: PREPARE all routes in parallel
        prepare_tasks = [
            self.prepare_route(tx_id, route, timeout)
            for route in routes
        ]
        prepare_results = await asyncio.gather(
            *prepare_tasks,
            return_exceptions=True
        )

        all_prepared = all(
            isinstance(r, PrepareSuccess)
            for r in prepare_results
        )

        if all_prepared:
            # Phase 2a: COMMIT all routes
            await self.commit_all_routes(tx_id, routes)
            return PaymentResult(status="COMMITTED", routes=routes)
        else:
            # Phase 2b: ABORT all routes (including successfully prepared)
            await self.abort_all_routes(tx_id, routes)
            failed_route = next(
                r for r in prepare_results
                if isinstance(r, Exception)
            )
            return PaymentResult(
                status="ABORTED",
                reason=str(failed_route)
            )

    async def prepare_route(
        self,
        tx_id: str,
        route: "PaymentRoute",
        timeout: timedelta
    ) -> "PrepareSuccess | Exception":
        """
        PREPARE a single route with a timeout.
        """
        try:
            async with asyncio.timeout(timeout.total_seconds()):
                for participant in route.intermediate_participants:
                    result = await self.send_prepare(
                        tx_id, participant, route.effects_for(participant)
                    )
                    if not result.ok:
                        raise PrepareFailure(participant, result.reason)
                return PrepareSuccess(route)
        except asyncio.TimeoutError:
            raise PrepareFailure(route.path, "timeout")

    async def abort_all_routes(
        self,
        tx_id: str,
        routes: list["PaymentRoute"]
    ) -> None:
        """
        ABORT all routes (idempotent).
        """
        abort_tasks = []
        for route in routes:
            for participant in route.all_participants:
                abort_tasks.append(
                    self.send_abort(tx_id, participant)
                )

        # Ignore errors — ABORT is idempotent
        await asyncio.gather(*abort_tasks, return_exceptions=True)
```

**Multi-path State Diagram:**

```text
                    ┌─────────────────┐
                    │      NEW        │
                    └────────┬────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │   PREPARE_ALL_IN_PROGRESS    │
              │  (all routes in parallel)    │
              └──────────────┬───────────────┘
                             │
              ┌──────────────┴───────────────┐
              │                              │
              ▼                              ▼
       ┌────────────┐                 ┌────────────┐
       │ All OK     │                 │ Any FAIL   │
       └─────┬──────┘                 └─────┬──────┘
             │                              │
             ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │ COMMIT_ALL │                 │ ABORT_ALL  │
      │ (all routes) │                 │ (all routes) │
      └─────┬──────┘                 └─────┬──────┘
            │                              │
            ▼                              ▼
      ┌────────────┐                 ┌────────────┐
      │ COMMITTED  │                 │  ABORTED   │
      └────────────┘                 └────────────┘
```

Note (Hub v0.1 implementation): intermediate `PREPARE_IN_PROGRESS` state is not persisted in DB. After successful `prepare_routes(...)` transaction moves to `PREPARED`, then `COMMIT` executes.

**Atomicity Invariant:**

```text
For any multi-path payment MP:
  (∀ route R ∈ MP: R.state = COMMITTED)
  XOR
  (∀ route R ∈ MP: R.state = ABORTED)
```

### 6.4. PAYMENT Transaction

```json
{
  "tx_id": "uuid",
  "type": "PAYMENT",
  "initiator": "PID_A",
  "payload": {
    "from": "PID_A",
    "to": "PID_B",
    "equivalent": "UAH",
    "total_amount": 100.00,
    "description": "Service payment",
    "routes": [
      {
        "path": ["PID_A", "PID_X", "PID_B"],
        "amount": 60.00
      },
      {
        "path": ["PID_A", "PID_Y", "PID_Z", "PID_B"],
        "amount": 40.00
      }
    ]
  },
  "signatures": [
    /* signatures of affected participants as needed */
  ],
  "state": "NEW"
}
```

### 6.5. PREPARE Phase

#### 6.5.1. PAYMENT_PREPARE Message

Hub sends each route participant:

```json
{
  "msg_type": "PAYMENT_PREPARE",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "payload": {
    "equivalent": "UAH",
    "local_effects": [
      {
        "edge": ["PID_A", "PID_X"],
        "direction": "incoming",
        "delta": 60.00
      },
      {
        "edge": ["PID_X", "PID_B"],
        "direction": "outgoing",
        "delta": 60.00
      }
    ],
    "timeout_at": "ISO8601"
  },
  "signature": "..."
}
```

#### 6.5.2. Participant Validation

```python
def validate_prepare(participant, effects):
    for effect in effects:
        edge = effect["edge"]
        delta = effect["delta"]

        if effect["direction"] == "outgoing":
            # Participant will owe next PID
            next_pid = edge[1]
            trust_line = get_trust_line(next_pid, participant.pid)

            if not trust_line or trust_line.status != "active":
                return FAIL("No active trust line")

            current_debt = get_debt(participant.pid, next_pid)
            new_debt = current_debt + delta

            if new_debt > trust_line.limit:
                return FAIL("Exceeds trust limit")

            if not trust_line.policy.can_be_intermediate:
                return FAIL("Not allowed as intermediate")

        elif effect["direction"] == "incoming":
            # Participant receives debt from previous PID
            prev_pid = edge[0]
            trust_line = get_trust_line(participant.pid, prev_pid)

            if not trust_line or trust_line.status != "active":
                return FAIL("No active trust line")

    return OK()
```

#### 6.5.3. Reservation

On successful validation, the participant:

1. Creates a `prepare_lock` record with `tx_id` and effects  
2. Decreases `available_credit` by reserved amount  
3. Replies with `PAYMENT_PREPARE_ACK (OK)`  

```json
{
  "msg_type": "PAYMENT_PREPARE_ACK",
  "tx_id": "uuid",
  "from": "PID_X",
  "payload": {
    "status": "OK"
  },
  "signature": "..."
}
```

### 6.6. COMMIT Phase

#### 6.6.1. PAYMENT_COMMIT Message

```json
{
  "msg_type": "PAYMENT_COMMIT",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "signature": "..."
}
```

#### 6.6.2. Applying Changes

```python
def apply_commit(participant, tx_id):
    lock = get_prepare_lock(tx_id)
    if not lock:
        return  # Idempotent

    for effect in lock.effects:
        if effect["direction"] == "outgoing":
            # Increase participant's debt
            update_debt(
                debtor=participant.pid,
                creditor=effect["edge"][1],
                delta=effect["delta"]
            )
        elif effect["direction"] == "incoming":
            # Increase debt owed to participant
            update_debt(
                debtor=effect["edge"][0],
                creditor=participant.pid,
                delta=effect["delta"]
            )

    delete_prepare_lock(tx_id)
```

### 6.7. ABORT Phase

```json
{
  "msg_type": "PAYMENT_ABORT",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_X",
  "payload": {
    "reason": "Timeout on PID_Y"
  },
  "signature": "..."
}
```

The participant releases the reservation without modifying debts.

### 6.8. PAYMENT State Machine

```text
        ┌─────┐
        │ NEW │
        └──┬──┘
           │ routing complete
           ▼
      ┌────────┐
      │ ROUTED │
      └───┬────┘
          │ send PREPARE
          ▼
┌─────────────────────┐
│ PREPARE_IN_PROGRESS │
└──────────┬──────────┘
           │
    ┌──────┴──────┐
    │             │
    ▼             ▼
┌─────────┐  ┌─────────┐
│COMMITTED│  │ ABORTED │
└─────────┘  └─────────┘
```

### 6.9. Timeouts

| Stage   | Timeout    | Action on Timeout     |
|---------|------------|-----------------------|
| Routing | 500 ms     | ABORT (no routes)     |
| PREPARE | 3 seconds  | ABORT (timeout)       |
| COMMIT  | 5 seconds  | Retry, then ABORT     |
| Overall | 10 seconds | ABORT                 |

---

## 7. Clearing

### 7.1. Overview

Clearing is automatic offsetting of debts in closed cycles.

### 7.2. Cycle Search

#### 7.2.1. Triggered Search (after each transaction)

```python
def find_cycles_triggered(changed_edges, max_length=4):
    """
    Search for short cycles around changed edges.
    """
    cycles = []

    for edge in changed_edges:
        # Cycles of length 3
        cycles += find_triangles(edge)
        # Cycles of length 4
        cycles += find_quadrangles(edge)

    return deduplicate(cycles)
```

#### 7.2.2. Periodic Search

| Cycle Length | Frequency        | Algorithm      |
|--------------|------------------|----------------|
| 3 nodes      | After each TX    | SQL JOIN       |
| 4 nodes      | After each TX    | SQL JOIN       |
| 5 nodes      | Hourly           | DFS with depth limit |
| 6 nodes      | Daily            | DFS with depth limit |

#### 7.2.3. SQL for Triangles

```sql
SELECT DISTINCT 
    d1.debtor AS a,
    d1.creditor AS b,
    d2.creditor AS c,
    LEAST(d1.amount, d2.amount, d3.amount) AS clear_amount
FROM debts d1
JOIN debts d2 ON d1.creditor = d2.debtor 
             AND d1.equivalent = d2.equivalent
JOIN debts d3 ON d2.creditor = d3.debtor 
             AND d3.creditor = d1.debtor
             AND d2.equivalent = d3.equivalent
WHERE d1.equivalent = :equivalent
  AND LEAST(d1.amount, d2.amount, d3.amount) > :min_amount
ORDER BY clear_amount DESC
LIMIT 100;
```

### 7.3. CLEARING Transaction

```json
{
  "tx_id": "uuid",
  "type": "CLEARING",
  "initiator": "HUB_PID",
  "payload": {
    "equivalent": "UAH",
    "cycle": ["PID_A", "PID_B", "PID_C", "PID_A"],
    "amount": 50.00
  },
  "signatures": [
    /* hub or participants signatures depending on policy */
  ],
  "state": "NEW"
}
```

### 7.4. Consent Modes

#### 7.4.1. Auto-consent (Default)

If all participants in the cycle have `policy.auto_clearing = true`:

- Clearing is applied without explicit confirmations  
- A `CLEARING_NOTICE` is sent for information  

#### 7.4.2. Explicit Consent

If at least one participant has `auto_clearing = false`:

- `CLEARING_PROPOSE` is sent to all participants  
- `CLEARING_ACCEPT` is expected from all of them  
- If any `CLEARING_REJECT` → transaction becomes `REJECTED`  

### 7.5. Clearing Messages

#### CLEARING_PROPOSE

```json
{
  "msg_type": "CLEARING_PROPOSE",
  "tx_id": "uuid",
  "from": "HUB_PID",
  "to": "PID_A",
  "payload": {
    "equivalent": "UAH",
    "cycle": ["PID_A", "PID_B", "PID_C", "PID_A"],
    "amount": 50.00,
    "your_effect": {
      "debt_to_reduce": ["PID_A", "PID_B", 50.00],
      "debt_from_reduce": ["PID_C", "PID_A", 50.00]
    },
    "expires_at": "ISO8601"
  },
  "signature": "..."
}
```

#### CLEARING_ACCEPT

```json
{
  "msg_type": "CLEARING_ACCEPT",
  "tx_id": "uuid",
  "from": "PID_A",
  "payload": {},
  "signature": "..."
}
```

### 7.6. Applying Clearing

```python
def apply_clearing(cycle, amount, equivalent):
    """
    Reduce debts on all cycle edges by `amount`.
    """
    for i in range(len(cycle) - 1):
        debtor = cycle[i]
        creditor = cycle[i + 1]

        current_debt = get_debt(debtor, creditor, equivalent)
        new_debt = current_debt - amount

        if new_debt <= 0:
            delete_debt(debtor, creditor, equivalent)
        else:
            update_debt(debtor, creditor, equivalent, new_debt)
```

### 7.7. CLEARING State Machine

```text
     ┌─────┐
     │ NEW │
     └──┬──┘
        │
   ┌────┴────┐
   │         │
   ▼         ▼
(auto)    (explicit)
   │         │
   │    ┌─────────┐
   │    │PROPOSED │
   │    └────┬────┘
   │         │
   │    ┌────┴────┐
   │    │         │
   │    ▼         ▼
   │ ┌───────┐ ┌────────┐
   │ │WAITING│ │REJECTED│
   │ └───┬───┘ └────────┘
   │     │
   ▼     ▼
┌───────────┐
│ COMMITTED │
└───────────┘
```

---

## 8. Inter-hub Interaction

### 8.1. "Hub as Participant" Principle

Each hub in the federation is a regular protocol participant:

- Has a `PID` and an Ed25519 key pair  
- May open TrustLines with other hubs  
- Participates in payments and clearing as a normal node  

### 8.2. Hub Registration

```json
{
  "pid": "HUB_A_PID",
  "public_key": "...",
  "profile": {
    "type": "hub",
    "name": "Community A Hub",
    "description": "Local Community A",
    "endpoint": "https://hub-a.example.com",
    "supported_equivalents": ["UAH", "HOUR"]
  },
  "status": "active"
}
```

### 8.3. Inter-hub TrustLines

Hub A opens a TrustLine to Hub B:

```json
{
  "from": "HUB_A_PID",
  "to": "HUB_B_PID",
  "equivalent": "UAH",
  "limit": 100000.00,
  "policy": {
    "auto_clearing": true,
    "settlement_schedule": "weekly",
    "max_single_payment": 10000.00
  }
}
```

### 8.4. Routing Between Communities

Payment from A@HubA to B@HubB:

```text
A@HubA → ... → HubA → HubB → ... → B@HubB
```

**Algorithm:**

1. HubA receives request from `A`  
2. HubA detects that `B` belongs to HubB  
3. HubA finds route: `A → ... → HubA`  
4. Adds `HubA → HubB` to the route  
5. HubB finds route: `HubB → ... → B`  
6. Composite route is executed as a single logical transaction  

### 8.5. Inter-hub Payment Protocol

#### 8.5.1. INTER_HUB_PAYMENT_REQUEST

```json
{
  "msg_type": "INTER_HUB_PAYMENT_REQUEST",
  "from": "HUB_A_PID",
  "to": "HUB_B_PID",
  "payload": {
    "tx_id": "uuid",
    "original_sender": "PID_A",
    "final_recipient": "PID_B",
    "equivalent": "UAH",
    "amount": 500.00,
    "incoming_routes": [
      {"path": ["PID_A", "PID_X", "HUB_A_PID"], "amount": 500.00}
    ]
  },
  "signature": "..."
}
```

#### 8.5.2. INTER_HUB_PAYMENT_ACCEPT

```json
{
  "msg_type": "INTER_HUB_PAYMENT_ACCEPT",
  "from": "HUB_B_PID",
  "to": "HUB_A_PID",
  "payload": {
    "tx_id": "uuid",
    "outgoing_routes": [
      {"path": ["HUB_B_PID", "PID_Y", "PID_B"], "amount": 500.00}
    ]
  },
  "signature": "..."
}
```

### 8.6. 2PC Coordination Between Hubs

```text
HubA                              HubB
  │                                 │
  │ INTER_HUB_PAYMENT_REQUEST       │
  │────────────────────────────────►│
  │                                 │
  │◄────────────────────────────────│
  │ INTER_HUB_PAYMENT_ACCEPT        │
  │                                 │
  │      Local PREPARE (both)       │
  │─────────────┬───────────────────│
  │             │                   │
  │ INTER_HUB_PREPARE_OK            │
  │◄────────────┼───────────────────│
  │             │                   │
  │             ▼                   │
  │ INTER_HUB_COMMIT                │
  │────────────────────────────────►│
  │                                 │
  │      Local COMMIT (both)        │
  │                                 │
```

### 8.7. Inter-hub Clearing

Hubs can participate in clearing cycles:

```text
HubA → HubB → HubC → HubA
```

Clearing uses the standard `CLEARING` protocol.

### 8.8. Transport Between Hubs

| Variant  | Protocol         | Usage                      |
|----------|------------------|----------------------------|
| REST     | HTTPS + JSON     | Simple implementation      |
| gRPC     | HTTP/2 + Protobuf| Higher performance         |
| WebSocket| WSS + JSON       | Bi-directional notifications|

---

## 9. Error Handling and Recovery

### 9.1. Idempotency of Operations

**Rule:** Any operation with the same `tx_id` must produce the same result.

```python
def handle_commit(tx_id):
    tx = get_transaction(tx_id)

    if tx.state == "COMMITTED":
        return SUCCESS  # Already applied

    if tx.state == "ABORTED":
        return FAIL  # Already aborted

    # Apply changes
    apply_changes(tx)
    tx.state = "COMMITTED"
    save_transaction(tx)
    return SUCCESS
```

### 9.2. Timeouts and Retries

| Operation        | Timeout | Retries | Action   |
|------------------|---------|---------|----------|
| PREPARE          | 3 sec   | 2       | ABORT    |
| COMMIT           | 5 sec   | 3       | Retry    |
| Inter-hub request| 10 sec  | 2       | ABORT    |

### 9.3. Hub Crash Recovery

#### 9.3.1. On Hub Startup

```python
def recover_on_startup():
    # Find unfinished transactions
    pending = get_transactions_in_state(["PREPARE_IN_PROGRESS", "ROUTED"])

    for tx in pending:
        if tx.created_at < now() - TIMEOUT:
            # Timeout expired — abort
            abort_transaction(tx)
        else:
            # Resume from last known state
            resume_transaction(tx)
```

#### 9.3.2. Cleaning Up Stale Prepare Locks

```python
def cleanup_stale_locks():
    stale = get_prepare_locks_older_than(MAX_LOCK_AGE)
    for lock in stale:
        tx = get_transaction(lock.tx_id)
        if tx.state in ["COMMITTED", "ABORTED"]:
            delete_lock(lock)
        else:
            # Transaction in uncertain state
            mark_for_manual_review(lock)
```

### 9.4. Participant Recovery Protocol

When a participant (client application) crashes mid-operation, a recovery protocol is needed to ensure eventual consistency.

#### 9.4.1. Participant States After Crash

```text
┌─────────────────────────────────────────────────────────────┐
│              Participant Recovery State Machine             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────┐     online      ┌──────────────┐            │
│   │ OFFLINE  │────────────────►│  RECOVERING  │            │
│   │ (crash)  │                 │              │            │
│   └──────────┘                 └──────┬───────┘            │
│                                       │                     │
│                         ┌─────────────┼─────────────┐      │
│                         │             │             │      │
│                         ▼             ▼             ▼      │
│                   ┌─────────┐   ┌─────────┐   ┌─────────┐  │
│                   │COMMITTED│   │ ABORTED │   │UNCERTAIN│  │
│                   │ (sync)  │   │ (sync)  │   │(escalate)│  │
│                   └─────────┘   └─────────┘   └─────────┘  │
│                         │             │             │      │
│                         └─────────────┼─────────────┘      │
│                                       ▼                     │
│                              ┌───────────────┐             │
│                              │ SYNCHRONIZED  │             │
│                              │  (normal op)  │             │
│                              └───────────────┘             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 9.4.2. Recovery Messages

**RECOVERY_QUERY** — ask neighbors about operation state:

```json
{
  "msg_type": "RECOVERY_QUERY",
  "tx_id": "uuid",
  "from": "PID_recovering",
  "to": "PID_neighbor",
  "payload": {
    "my_last_known_state": "PREPARE_ACK_SENT",
    "timestamp": "ISO8601"
  },
  "signature": "..."
}
```

**RECOVERY_RESPONSE** — neighbor answers:

```json
{
  "msg_type": "RECOVERY_RESPONSE",
  "tx_id": "uuid",
  "from": "PID_neighbor",
  "to": "PID_recovering",
  "payload": {
    "tx_state": "COMMITTED | ABORTED | UNKNOWN",
    "my_effects_applied": true,
    "evidence": {
      "commit_signature": "...",
      "commit_timestamp": "ISO8601"
    }
  },
  "signature": "..."
}
```

#### 9.4.3. Recovery Algorithm

```python
class ParticipantRecoveryService:
    """
    Service to restore participant state after a crash.
    """

    async def recover_pending_operations(
        self,
        participant_id: str
    ) -> list["RecoveryResult"]:
        """
        Recover all pending operations for a participant.
        """
        results = []

        # 1. Get all operations in an uncertain state
        pending_locks = await self.get_local_prepare_locks(participant_id)

        for lock in pending_locks:
            result = await self.recover_single_operation(
                participant_id, lock.tx_id, lock
            )
            results.append(result)

        return results

    async def recover_single_operation(
        self,
        participant_id: str,
        tx_id: str,
        local_lock: "PrepareLock"
    ) -> "RecoveryResult":
        """
        Recover a single operation.
        """
        # 2. Identify neighbors for this operation
        neighbors = self.get_neighbors_from_lock(local_lock)

        # 3. Query neighbors about transaction state
        responses = await self.query_neighbors(tx_id, neighbors)

        # 4. Decide based on responses
        decision = self.decide_from_responses(responses)

        if decision == RecoveryDecision.COMMITTED:
            # Apply local effects
            await self.apply_effects(local_lock.effects)
            await self.delete_lock(local_lock)
            return RecoveryResult(tx_id, "COMMITTED")

        elif decision == RecoveryDecision.ABORTED:
            # Release reservation
            await self.release_reservation(local_lock)
            await self.delete_lock(local_lock)
            return RecoveryResult(tx_id, "ABORTED")

        else:  # UNCERTAIN
            # Escalate to hub or manual resolution
            await self.escalate_to_hub(tx_id, responses)
            return RecoveryResult(tx_id, "ESCALATED")

    async def query_neighbors(
        self,
        tx_id: str,
        neighbors: list[str]
    ) -> list["RecoveryResponse"]:
        """
        Query neighbors about the transaction.
        """
        tasks = [
            self.send_recovery_query(tx_id, neighbor)
            for neighbor in neighbors
        ]

        responses = await asyncio.gather(*tasks, return_exceptions=True)

        return [r for r in responses if isinstance(r, RecoveryResponse)]

    def decide_from_responses(
        self,
        responses: list["RecoveryResponse"]
    ) -> "RecoveryDecision":
        """
        Decide outcome based on neighbor responses.

        Rules:
        - If any neighbor confirms COMMITTED → COMMITTED
        - If all neighbors say ABORTED → ABORTED
        - Otherwise → UNCERTAIN
        """
        committed_count = sum(1 for r in responses if r.tx_state == "COMMITTED")
        aborted_count = sum(1 for r in responses if r.tx_state == "ABORTED")

        if committed_count > 0:
            return RecoveryDecision.COMMITTED
        elif aborted_count == len(responses) and len(responses) > 0:
            return RecoveryDecision.ABORTED
        else:
            return RecoveryDecision.UNCERTAIN
```

#### 9.4.4. Recovery Guarantees

| Property     | Guarantee                                                 |
|--------------|-----------------------------------------------------------|
| **Safety**   | Participant does not apply COMMITTED effects to an ABORTED TX |
| **Liveness** | With at least one neighbor reachable, recovery completes |
| **Consistency** | All participants converge to the same TX state        |
| **Idempotency** | Repeated recovery is safe                             |

#### 9.4.5. Recovery Timeouts

| Stage            | Timeout | Action                      |
|------------------|---------|-----------------------------|
| RECOVERY_QUERY   | 5 sec   | Retry or skip neighbor      |
| Overall recovery | 30 sec  | Escalate to hub             |
| Hub resolution   | 5 min   | Manual admin resolution     |

### 9.5. Error Codes

| Code   | Category   | Description                 |
|--------|------------|-----------------------------|
| `E001` | Routing    | Route not found             |
| `E002` | Routing    | Insufficient capacity       |
| `E003` | TrustLine  | TrustLine limit exceeded    |
| `E004` | TrustLine  | TrustLine not active        |
| `E005` | Auth       | Invalid signature           |
| `E006` | Auth       | Insufficient permissions    |
| `E007` | Timeout    | Operation timeout           |
| `E008` | Conflict   | State conflict              |
| `E009` | Validation | Invalid data                |
| `E010` | Internal   | Internal error              |

### 9.6. Error Message Format

```json
{
  "msg_type": "ERROR",
  "tx_id": "uuid | null",
  "payload": {
    "code": "E003",
    "message": "Trust line limit exceeded",
    "details": {
      "trust_line_id": "uuid",
      "limit": 1000.00,
      "requested": 1500.00
    }
  }
}
```

---

## 10. Dispute Resolution

### 10.1. Types of Disputes

| Type                   | Description                                 | Frequency |
|------------------------|---------------------------------------------|-----------|
| **Payment disagreement** | Participant disputes fact or amount      | Rare      |
| **Quality dispute**    | Goods/services mismatch expectations        | Medium    |
| **Technical failure**  | State divergence due to system error        | Rare      |
| **Fraud**              | Intentional malicious actions               | Very rare |

### 10.2. "Disputed Transaction" Status

```json
{
  "tx_id": "uuid",
  "dispute_status": "disputed",
  "dispute_info": {
    "opened_by": "PID_A",
    "opened_at": "ISO8601",
    "reason": "Goods not received",
    "evidence": ["url1", "url2"]
  }
}
```

### 10.3. Resolution Process

```text
┌──────────────────┐
│ Transaction OK   │
└────────┬─────────┘
         │ dispute()
         ▼
┌──────────────────┐
│    DISPUTED      │
└────────┬─────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌────────┐ ┌────────┐
│RESOLVED│ │ESCALATE│
└───┬────┘ └───┬────┘
    │          │
    ▼          ▼
 (close)   (arbitration)
```

### 10.4. Dispute API

#### Open Dispute

```json
{
  "action": "DISPUTE_OPEN",
  "tx_id": "uuid",
  "reason": "string",
  "evidence": ["url"],
  "requested_outcome": "refund | adjustment | investigation"
}
```

#### Respond to Dispute

```json
{
  "action": "DISPUTE_RESPOND",
  "dispute_id": "uuid",
  "response": "string",
  "evidence": ["url"],
  "proposed_resolution": { }
}
```

### 10.5. Roles in Dispute Resolution

| Role        | Rights                                      |
|-------------|---------------------------------------------|
| Participant | Open disputes, provide evidence             |
| Operator    | View logs, request additional information   |
| Arbiter     | Decide outcomes, create compensations       |
| Admin       | Freeze participants, escalate critical cases|

### 10.6. Compensating Transactions

On a confirmed dispute, a compensating transaction is created:

```json
{
  "tx_id": "uuid-compensation",
  "type": "COMPENSATION",
  "initiator": "ARBITER_PID",
  "payload": {
    "original_tx_id": "uuid-original",
    "reason": "Dispute resolution #123",
    "effects": [
      {
        "debtor": "PID_A",
        "creditor": "PID_B",
        "delta": -100.00,
        "equivalent": "UAH"
      }
    ]
  },
  "signatures": [
    { "signer": "ARBITER_PID", "signature": "..." }
  ]
}
```

### 10.7. History Immutability

**Important:** Transactions are never deleted or modified.

- Errors are corrected via **new transactions**  
- Full history is always reconstructible  
- Audits can be performed over the transaction log  

---

## 11. System Integrity Verification

### 11.1. Purpose

System integrity verification ensures:

- **Detection of bugs** in payment and clearing logic  
- **Protection from data corruption** during failures  
- **Auditability** of all operations  
- **Early warnings** about anomalies  

### 11.2. Fundamental Invariants

#### 11.2.1. Zero-Sum Invariant

**Definition:** The sum of all participant balances for each equivalent must be zero.

```text
∀ equivalent E: ∑ net_balance(participant, E) = 0
```

**SQL check:**

```sql
-- Should return 0 for each equivalent
SELECT 
    e.code AS equivalent,
    COALESCE(SUM(
        CASE 
            WHEN d.debtor_id = p.id THEN -d.amount
            WHEN d.creditor_id = p.id THEN d.amount
            ELSE 0
        END
    ), 0) AS net_balance_sum
FROM equivalents e
CROSS JOIN participants p
LEFT JOIN debts d ON d.equivalent_id = e.id 
    AND (d.debtor_id = p.id OR d.creditor_id = p.id)
GROUP BY e.code;
```

**Frequency:** After each transaction + periodically (every 5 minutes)

#### 11.2.2. Trust Limit Invariant

**Definition:** Debt cannot exceed the corresponding TrustLine limit.

```text
∀ (debtor, creditor, equivalent):
  debt[debtor → creditor, E] ≤ trust_line[creditor → debtor, E].limit
```

**SQL check:**

```sql
SELECT 
    d.debtor_id,
    d.creditor_id,
    d.amount AS debt,
    tl.limit AS trust_limit,
    d.amount - tl.limit AS violation_amount
FROM debts d
LEFT JOIN trust_lines tl ON 
    tl.from_participant_id = d.creditor_id 
    AND tl.to_participant_id = d.debtor_id
    AND tl.equivalent_id = d.equivalent_id
    AND tl.status = 'active'
WHERE d.amount > COALESCE(tl.limit, 0);
```

**Frequency:** After each transaction

#### 11.2.3. Clearing Neutrality Invariant

**Definition:** Clearing reduces mutual debts but does **not** change participants' net positions.

```text
∀ participant P in cycle:
  net_position(P, E)_before = net_position(P, E)_after
```

**Verification algorithm:**

```python
def verify_clearing_neutrality(cycle: list[str], amount: Decimal, equivalent: str):
    """
    Verify that clearing does not change participants' net positions.
    """
    # Net positions BEFORE
    positions_before = {}
    for pid in cycle:
        positions_before[pid] = calculate_net_position(pid, equivalent)

    # Apply clearing
    apply_clearing(cycle, amount, equivalent)

    # Net positions AFTER
    positions_after = {}
    for pid in cycle:
        positions_after[pid] = calculate_net_position(pid, equivalent)

    # Check invariant
    for pid in cycle:
        if positions_before[pid] != positions_after[pid]:
            raise IntegrityViolation(
                f"Clearing changed net position of {pid}: "
                f"{positions_before[pid]} → {positions_after[pid]}"
            )

    return True
```

**Frequency:** For every clearing operation

#### 11.2.4. Debt Symmetry Invariant

**Definition:** For a given pair of participants, debt can only exist in one direction.

```text
∀ (A, B, E): NOT (debt[A→B, E] > 0 AND debt[B→A, E] > 0)
```

**SQL check:**

```sql
SELECT 
    d1.debtor_id AS a,
    d1.creditor_id AS b,
    d1.equivalent_id,
    d1.amount AS debt_a_to_b,
    d2.amount AS debt_b_to_a
FROM debts d1
JOIN debts d2 ON 
    d1.debtor_id = d2.creditor_id 
    AND d1.creditor_id = d2.debtor_id
    AND d1.equivalent_id = d2.equivalent_id
WHERE d1.amount > 0 AND d2.amount > 0;
```

**Frequency:** After each transaction

### 11.3. State Checksums

#### 11.3.1. State Checksum

**Format:** SHA-256 hash of canonical representation of all debts.

```python
def compute_state_checksum(equivalent: str) -> str:
    """
    Compute state checksum for a given equivalent.
    """
    debts = db.query("""
        SELECT debtor_id, creditor_id, amount
        FROM debts
        WHERE equivalent_id = (SELECT id FROM equivalents WHERE code = :eq)
        ORDER BY debtor_id, creditor_id
    """, {"eq": equivalent})

    canonical = "|".join([
        f"{d.debtor_id}:{d.creditor_id}:{d.amount}"
        for d in debts
    ])

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

#### 11.3.2. Incremental Checksum

```python
def update_checksum_incremental(
    current_checksum: str,
    operation: str,  # "add" | "remove" | "update"
    debt_record: "DebtRecord",
    previous_amount: Decimal | None = None
) -> str:
    """
    Incrementally update state checksum.
    """
    delta = f"{operation}:{debt_record.debtor_id}:{debt_record.creditor_id}:"

    if operation == "add":
        delta += f"{debt_record.amount}"
    elif operation == "remove":
        delta += f"-{debt_record.amount}"
    elif operation == "update":
        delta += f"{previous_amount}→{debt_record.amount}"

    return hashlib.sha256(
        f"{current_checksum}|{delta}".encode("utf-8")
    ).hexdigest()
```

### 11.4. Audit Trail

#### 11.4.1. Audit Record Structure

```json
{
  "audit_id": "uuid",
  "timestamp": "ISO8601",
  "operation_type": "PAYMENT | CLEARING | TRUST_LINE_*",
  "tx_id": "string",
  "equivalent": "string",
  "state_checksum_before": "sha256",
  "state_checksum_after": "sha256",
  "affected_participants": ["PID1", "PID2"],
  "invariants_checked": {
    "zero_sum": true,
    "trust_limits": true,
    "debt_symmetry": true
  },
  "verification_passed": true
}
```

#### 11.4.2. Audit Log Schema (SQL)

```sql
CREATE TABLE integrity_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    operation_type VARCHAR(50) NOT NULL,
    tx_id VARCHAR(64),
    equivalent_code VARCHAR(16) NOT NULL,
    state_checksum_before VARCHAR(64) NOT NULL,
    state_checksum_after VARCHAR(64) NOT NULL,
    affected_participants JSONB NOT NULL,
    invariants_checked JSONB NOT NULL,
    verification_passed BOOLEAN NOT NULL,
    error_details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_timestamp ON integrity_audit_log(timestamp);
CREATE INDEX idx_audit_tx_id ON integrity_audit_log(tx_id);
CREATE INDEX idx_audit_failures ON integrity_audit_log(verification_passed) 
WHERE verification_passed = false;
```

### 11.5. Recovery Algorithms on Violations

#### 11.5.1. Zero-Sum Violation

```python
async def handle_zero_sum_violation(equivalent: str, imbalance: Decimal):
    """
    Handle zero-sum invariant violation.
    """
    # 1. Lock this equivalent from further operations
    await lock_equivalent(equivalent)

    # 2. Find last valid checkpoint
    last_valid = await find_last_valid_checkpoint(equivalent)

    # 3. Alert administrators
    await alert_admins(
        severity="CRITICAL",
        message=f"Zero-sum violation in {equivalent}: imbalance = {imbalance}",
        last_valid_checkpoint=last_valid
    )

    # 4. Generate report for manual analysis
    report = await generate_integrity_report(equivalent, since=last_valid.timestamp)

    return report
```

#### 11.5.2. Trust Limit Violation

```python
async def handle_trust_limit_violation(
    debtor: str,
    creditor: str,
    equivalent: str,
    debt: Decimal,
    limit: Decimal
):
    """
    Handle debt exceeding trust limit.
    """
    violation_amount = debt - limit

    # 1. Freeze trust line
    await freeze_trust_line(creditor, debtor, equivalent)

    # 2. Log incident
    await log_security_incident(
        type="TRUST_LIMIT_VIOLATION",
        details={
            "debtor": debtor,
            "creditor": creditor,
            "debt": str(debt),
            "limit": str(limit),
            "violation": str(violation_amount)
        }
    )

    # 3. Notify participants
    await notify_participants(
        [debtor, creditor],
        message="Trust line frozen due to integrity violation"
    )
```

### 11.6. Periodic Checks

#### 11.6.1. Schedule

| Check          | Frequency   | Priority  |
|----------------|-------------|-----------|
| Zero-Sum       | Every 5 min | Critical  |
| Trust Limits   | Every 5 min | Critical  |
| Debt Symmetry  | Every 15 min| High      |
| State Checksum | Every hour  | Medium    |
| Full Audit     | Daily       | Low       |

#### 11.6.2. Background Task

```python
async def integrity_check_task():
    """
    Periodic integrity check task.
    """
    while True:
        try:
            for equivalent in await get_active_equivalents():
                report = await run_integrity_checks(equivalent)

                if not report.all_passed:
                    await handle_integrity_failure(report)
                else:
                    await save_checkpoint(equivalent, report.checksum)

            await asyncio.sleep(300)  # 5 minutes

        except Exception as e:
            await alert_admins(severity="ERROR", message=str(e))
            await asyncio.sleep(60)
```

### 11.7. Integrity API

#### 11.7.1. Endpoints

| Method | Endpoint                          | Description                 |
|--------|-----------------------------------|-----------------------------|
| GET    | `/api/v1/integrity/status`       | Current integrity status    |
| GET    | `/api/v1/integrity/checksum/{equivalent}` | State checksum for equivalent |
| POST   | `/api/v1/integrity/verify`       | Run full integrity check    |
| GET    | `/api/v1/integrity/audit-log`    | Audit log                   |

#### 11.7.2. `/integrity/status` Response Format

```json
{
  "status": "healthy | warning | critical",
  "last_check": "ISO8601",
  "equivalents": {
    "UAH": {
      "status": "healthy",
      "checksum": "sha256...",
      "last_verified": "ISO8601",
      "invariants": {
        "zero_sum": { "passed": true, "value": "0.00" },
        "trust_limits": { "passed": true, "violations": 0 },
        "debt_symmetry": { "passed": true, "violations": 0 }
      }
    }
  },
  "alerts": []
}
```

---

## A. Canonical JSON

For signatures, a canonical JSON form is used:

- Keys sorted alphabetically  
- No extra whitespace  
- UTF-8 encoding  
- Numbers without unnecessary trailing zeros  

---

## B. Base58 Alphabet

```text
123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz
```

(Characters `0`, `O`, `I`, `l` are excluded.)

---

## C. Protocol Versioning

Version format: `MAJOR.MINOR`

| Version | Compatibility                |
|---------|-----------------------------|
| 0.x     | Experimental, may break     |
| 1.x     | Stable, backward-compatible |

---

## D. Electronic Money Platform (Future Extension)

> **Status:** Optional future extension. **Out of scope for v0.1.**

GEO can be used as a low-level platform for issuing fiat-backed electronic money.

### D.1. Gateway Concept

A **Gateway** is a special network participant with fiat reserves:

```text
┌────────────────────────────────────────────────────────────┐
│                        GATEWAY                            │
├────────────────────────────────────────────────────────────┤
│  • Has a bank account with fiat reserves                  │
│  • Opens TrustLines for users upon fiat deposits          │
│  • Redeems debts on fiat withdrawal                       │
│  • Must maintain 100% reserve ratio                       │
└────────────────────────────────────────────────────────────┘
```

### D.2. Algorithm

**Deposit (fiat in):**

```text
1. User transfers X units of fiat to Gateway (bank transfer)
2. Gateway creates: TrustLine(Gateway → User, limit = X, equivalent = UAH)
3. User can "spend" up to X inside the GEO network
```

**Redemption (fiat out):**

```text
1. User requests withdrawal of `amount`
2. Gateway checks: debt[Gateway → User] ≥ amount
3. Gateway reduces debt in GEO (DEBT_REDUCTION transaction)
4. Gateway sends fiat to user's bank account
```

**Internal payments:**

```text
User_A → Gateway → User_B (via standard PAYMENT)
```

### D.3. Key Operations

| Operation    | GEO Action             | Banking Action         |
|-------------|------------------------|------------------------|
| **Deposit** | Create/extend TrustLine| Receive fiat           |
| **Payment** | Standard PAYMENT       | —                      |
| **Redemption** | Reduce debt         | Send fiat              |

### D.4. Regulatory Requirements

Under **Directive 2000/46/EC** (Electronic Money Directive):

- Gateway = electronic money issuer  
- Requires financial regulator license  
- Must hold 100% reserves  
- Audit condition: `∑ debts[Gateway→*] ≤ fiat_reserves`  

### D.5. Risks

| Risk                 | Description                 | Mitigation               |
|----------------------|-----------------------------|--------------------------|
| Gateway bankruptcy   | Loss of user funds          | Insurance, regulation    |
| Fraud                | Insufficient reserves       | Audits, transparency     |
| Regulatory           | Activity ban                | Proper licensing         |

### D.6. Full Gateway Protocol (Sketch)

```python
class GatewayService:
    """
    Gateway participant for fiat in/out.

    Any participant may become a Gateway if:
    1. Verification level ≥ 2 (community rules)
    2. Has sufficient reserves
    3. Accepts gateway-specific rules
    """

    async def register_as_gateway(
        self,
        participant_id: str,
        supported_currencies: list[str],
        bank_details: "BankDetails",
        initial_reserve: Decimal
    ) -> "GatewayRegistration":
        """
        Register a participant as Gateway.
        """
        participant = await self.get_participant(participant_id)
        if participant.verification_level < 2:
            raise InsufficientVerification()

        gateway = Gateway(
            pid=participant_id,
            currencies=supported_currencies,
            bank_details=bank_details,
            reserve_balance=initial_reserve,
            status="pending_approval"
        )

        return await self.save_gateway(gateway)

    async def deposit(
        self,
        gateway_pid: str,
        user_pid: str,
        fiat_amount: Decimal,
        fiat_currency: str,
        proof_of_payment: str
    ) -> "DepositResult":
        """
        User deposits fiat → Gateway creates TrustLine.

        1. Verify proof_of_payment (manually or via bank API)
        2. Create/increase TrustLine from Gateway to User
        3. Update Gateway reserves accounting
        """
        gateway = await self.get_gateway(gateway_pid)
        if gateway.status != "active":
            raise GatewayNotActive()

        equivalent = self.currency_to_equivalent(fiat_currency)

        trust_line = await self.get_or_create_trust_line(
            from_pid=gateway_pid,
            to_pid=user_pid,
            equivalent=equivalent
        )

        new_limit = trust_line.limit + fiat_amount
        await self.update_trust_line_limit(trust_line.id, new_limit)

        await self.update_gateway_reserve(
            gateway_pid,
            fiat_currency,
            delta=fiat_amount
        )

        return DepositResult(
            trust_line_id=trust_line.id,
            new_limit=new_limit,
            equivalent=equivalent
        )

    async def withdraw(
        self,
        gateway_pid: str,
        user_pid: str,
        amount: Decimal,
        equivalent: str,
        bank_details: "UserBankDetails"
    ) -> "WithdrawResult":
        """
        Gateway redeems user's GEO balance → sends fiat.

        1. Check that Gateway owes User at least `amount`
        2. Reduce GEO debt
        3. Initiate bank transfer
        """
        debt = await self.get_debt(
            debtor=gateway_pid,
            creditor=user_pid,
            equivalent=equivalent
        )

        if debt.amount < amount:
            raise InsufficientBalance(
                f"Gateway owes {debt.amount}, requested {amount}"
            )

        tx = await self.create_debt_reduction(
            debtor=gateway_pid,
            creditor=user_pid,
            equivalent=equivalent,
            amount=amount,
            reason="fiat_withdrawal"
        )

        fiat_currency = self.equivalent_to_currency(equivalent)
        transfer = await self.initiate_bank_transfer(
            from_account=gateway.bank_details,
            to_account=bank_details,
            amount=amount,
            currency=fiat_currency,
            reference=tx.tx_id
        )

        return WithdrawResult(
            tx_id=tx.tx_id,
            bank_transfer_id=transfer.id,
            status="pending_transfer"
        )

    async def get_gateway_stats(
        self,
        gateway_pid: str
    ) -> "GatewayStats":
        """
        Gateway stats for audit.
        """
        total_liabilities = await self.sum_outgoing_trust_lines(gateway_pid)
        reserves = await self.get_gateway_reserves(gateway_pid)

        reserve_ratio = (
            reserves / total_liabilities
            if total_liabilities > 0 else Decimal("inf")
        )

        return GatewayStats(
            total_liabilities=total_liabilities,
            reserves=reserves,
            reserve_ratio=reserve_ratio,
            is_fully_reserved=reserve_ratio >= 1.0
        )
```

---

## E. Commodity Tokens (Future Extension)

> **Status:** Future extension. **Out of scope for v0.1.**

Commodity tokens are obligations denominated in a quantity of a good, not in money.

### E.1. Concept

```text
┌────────────────────────────────────────────────────────────┐
│                 COMMODITY TOKEN EXAMPLE                    │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Example: "Kolionovo Eggs"                                │
│                                                            │
│  • 1 token = 10 eggs                                      │
│  • Issuer: Farmer X                                       │
│  • Redemption: actual eggs                                │
│                                                            │
│  Differences from monetary obligations:                   │
│  • Denominated in goods, not currency                     │
│  • Redeemed in-kind                                      │
│  • Fiat price may fluctuate                               │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### E.2. Extended Equivalent Model

```python
from enum import Enum
from decimal import Decimal
from datetime import date

class EquivalentType(Enum):
    FIAT_REFERENCE = "fiat"        # Fiat-referenced (UAH, USD)
    TIME_UNIT = "time"             # Hour of work
    COMMODITY_TOKEN = "commodity"  # Commodity token
    ABSTRACT = "abstract"          # Abstract community unit

class CommoditySpec:
    """Specification of a commodity token."""
    commodity_name: str            # "Chicken eggs"
    unit: str                      # "pcs" | "kg" | "l"
    quantity_per_token: Decimal    # e.g., 10 (eggs per 1 token)
    quality_grade: str             # "C1" | "organic"
    producer_pid: str              # PID of producer/issuer

class RedemptionRules:
    """Redemption rules for a commodity token."""
    redemption_locations: list[str]
    min_redemption_amount: Decimal
    advance_notice_hours: int
    expiration_date: date | None
    seasonal_availability: str | None  # e.g., "March–October"

class CommodityEquivalent:
    """Equivalent for a commodity token."""
    code: str                      # "EGG_KOLIONOVO"
    type: EquivalentType = EquivalentType.COMMODITY_TOKEN
    precision: int = 0

    commodity_spec: CommoditySpec
    redemption_rules: RedemptionRules
```

### E.3. Commodity Token Lifecycle

```text
┌─────────────────────────────────────────────────────────────┐
│               COMMODITY TOKEN LIFECYCLE                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. ISSUANCE                                               │
│     Producer creates equivalent EGG_KOLIONOVO              │
│     Producer opens TrustLines to buyers                    │
│     TrustLine limit = max tokens issued                    │
│                                                             │
│  2. CIRCULATION                                            │
│     Buyers receive tokens (pay producer with services)     │
│     Tokens circulate as regular debts                      │
│     Clearing works normally                                │
│                                                             │
│  3. REDEMPTION                                             │
│     Token holder visits farm                               │
│     Producer delivers goods, creates REDEMPTION TX         │
│     Producer's debt decreases                              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### E.4. REDEMPTION Transaction

```json
{
  "tx_id": "uuid",
  "type": "COMMODITY_REDEMPTION",
  "initiator": "FARMER_PID",
  "payload": {
    "equivalent": "EGG_KOLIONOVO",
    "redeemer": "CUSTOMER_PID",
    "amount": 50,
    "commodity_delivered": {
      "quantity": 500,
      "unit": "pcs",
      "delivery_date": "2025-03-15",
      "delivery_location": "Kolionovo Farm"
    }
  },
  "signatures": [
    { "signer": "FARMER_PID", "signature": "..." },
    { "signer": "CUSTOMER_PID", "signature": "..." }
  ],
  "state": "COMMITTED"
}
```

---

## F. Conversion Between Equivalents (Exchange)

> **Status:** Future extension. **Out of scope for v0.1.**

### F.1. Concept

GEO does not convert equivalents automatically, but provides infrastructure for market-based exchange.

```text
┌────────────────────────────────────────────────────────────┐
│                        EXCHANGE                            │
├────────────────────────────────────────────────────────────┤
│  Participant A wants to swap UAH for HOUR                  │
│                                                            │
│  1. A publishes offer: "Sell 100 UAH for 2 HOUR"          │
│  2. B sees the offer and accepts                           │
│  3. System executes atomic trade:                          │
│     - A gets +2 HOUR from B                                │
│     - B gets +100 UAH from A                               │
│                                                            │
│  Price is determined by order book / matching engine       │
└────────────────────────────────────────────────────────────┘
```

### F.2. Exchange Data Model

```python
from decimal import Decimal
from uuid import UUID
from datetime import datetime

class ExchangeOffer:
    """Exchange offer."""
    id: UUID
    creator_pid: str

    # What is sold
    sell_equivalent: str
    sell_amount: Decimal

    # What is requested
    buy_equivalent: str
    buy_amount: Decimal

    # Derived price
    price: Decimal  # buy_amount / sell_amount

    status: str  # "open" | "partial" | "filled" | "cancelled"
    filled_amount: Decimal = Decimal("0")

    expires_at: datetime
    created_at: datetime

class ExchangeMatch:
    """Match between two offers."""
    offer_a: ExchangeOffer
    offer_b: ExchangeOffer

    matched_amount_a: Decimal
    matched_amount_b: Decimal

    execution_price: Decimal
```

### F.3. Exchange Protocol (Sketch)

```python
class ExchangeService:
    async def create_offer(
        self,
        creator_pid: str,
        sell_equivalent: str,
        sell_amount: Decimal,
        buy_equivalent: str,
        buy_amount: Decimal,
        expires_at: datetime
    ) -> ExchangeOffer:
        """Create a new exchange offer."""
        capacity = await self.check_sell_capacity(
            creator_pid, sell_equivalent, sell_amount
        )
        if not capacity.sufficient:
            raise InsufficientCapacity()

        offer = ExchangeOffer(
            creator_pid=creator_pid,
            sell_equivalent=sell_equivalent,
            sell_amount=sell_amount,
            buy_equivalent=buy_equivalent,
            buy_amount=buy_amount,
            price=buy_amount / sell_amount,
            expires_at=expires_at
        )

        matches = await self.find_matches(offer)
        if matches:
            await self.execute_matches(offer, matches)

        return await self.save_offer(offer)

    async def execute_match(
        self,
        offer_a: ExchangeOffer,
        offer_b: ExchangeOffer,
        amount: Decimal
    ) -> "ExchangeTransaction":
        """
        Atomically execute exchange between two offers.

        Creates two linked PAYMENT transactions:
        1. A → B in A's sell_equivalent
        2. B → A in B's sell_equivalent
        """
        amount_a_sells = amount
        amount_b_sells = amount * (offer_a.buy_amount / offer_a.sell_amount)

        async with self.db.transaction():
            tx1 = await self.payment_engine.create_payment(
                from_pid=offer_a.creator_pid,
                to_pid=offer_b.creator_pid,
                equivalent=offer_a.sell_equivalent,
                amount=amount_a_sells,
                description=f"Exchange: {offer_a.id}"
            )

            tx2 = await self.payment_engine.create_payment(
                from_pid=offer_b.creator_pid,
                to_pid=offer_a.creator_pid,
                equivalent=offer_b.sell_equivalent,
                amount=amount_b_sells,
                description=f"Exchange: {offer_b.id}"
            )

            offer_a.filled_amount += amount_a_sells
            offer_b.filled_amount += amount_b_sells

        return ExchangeTransaction(tx1=tx1, tx2=tx2)
```

---

## G. Spam Protection via TrustLines

> **Status:** Future extension. **Out of scope for v0.1.**

TrustLines can be used to filter unwanted communications.

### G.1. Concept

```text
┌────────────────────────────────────────────────────────────┐
│            COMMUNICATION FILTERING VIA TRUST               │
├────────────────────────────────────────────────────────────┤
│  Problem: spam in messages, unsolicited TrustLine requests│
│                                                            │
│  Solution: accept communication only from participants    │
│  within N hops in the trust network                       │
│                                                            │
│  Example (N=2):                                           │
│  • Alice ←trust— Bob ←trust— Charlie                      │
│  • Charlie may contact Alice (2 hops)                     │
│  • Dave (unconnected) cannot                              │
│                                                            │
│  Additional:                                              │
│  • "Cost" of message in trust units                       │
│  • Reputation-based filtering                             │
└────────────────────────────────────────────────────────────┘
```

### G.2. Communication Policy

```python
class CommunicationPolicy:
    """Participant's communication policy."""
    max_trust_distance: int = 3  # max hops in trust graph
    min_sender_reputation: int = 0

    whitelist: list[str] = []
    blacklist: list[str] = []

    require_message_fee: bool = False
    message_fee_equivalent: str | None = None
    message_fee_amount: Decimal | None = None

class SpamFilter:
    async def can_send_message(
        self,
        sender_pid: str,
        recipient_pid: str,
        message_type: str
    ) -> "FilterResult":
        """Check whether sender may contact recipient."""
        policy = await self.get_communication_policy(recipient_pid)

        if sender_pid in policy.blacklist:
            return FilterResult(allowed=False, reason="blacklisted")
        if sender_pid in policy.whitelist:
            return FilterResult(allowed=True)

        distance = await self.calculate_trust_distance(sender_pid, recipient_pid)
        if distance > policy.max_trust_distance:
            return FilterResult(
                allowed=False,
                reason=f"trust_distance {distance} > {policy.max_trust_distance}"
            )

        reputation = await self.get_reputation_score(sender_pid)
        if reputation < policy.min_sender_reputation:
            return FilterResult(
                allowed=False,
                reason=f"reputation {reputation} < {policy.min_sender_reputation}"
            )

        return FilterResult(allowed=True)

    async def calculate_trust_distance(
        self,
        from_pid: str,
        to_pid: str
    ) -> int:
        """
        Distance in the trust graph (BFS).

        Returns the minimum number of TrustLine hops.
        """
        if from_pid == to_pid:
            return 0

        visited = {from_pid}
        queue = [(from_pid, 0)]

        while queue:
            current, distance = queue.pop(0)
            neighbors = await self.get_trust_neighbors(current)

            for neighbor in neighbors:
                if neighbor == to_pid:
                    return distance + 1

                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, distance + 1))

        return float("inf")  # not connected
```

---

## H. Countercyclical Function

> **Status:** Conceptual description; requires empirical validation.

### H.1. Theoretical Basis

Mutual credit networks like GEO or WIR in Switzerland can exhibit **countercyclical behavior**:

```text
┌────────────────────────────────────────────────────────────┐
│                COUNTERCYCLICAL FUNCTION                    │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ECONOMIC DOWNTURN:                                       │
│  • Fiat becomes scarce                                    │
│  • Banks tighten credit                                   │
│  • Businesses struggle to pay each other                  │
│                                                            │
│  GEO IN THIS CONTEXT:                                     │
│  • Mutual credit is independent from banks                │
│  • Participants keep trading via TrustLines               │
│  • Clearing frees "stuck" debts                           │
│  • Liquidity stays inside the community                   │
│                                                            │
│  RESULT:                                                  │
│  • When conventional economy contracts, GEO expands       │
│  • Mitigation of crisis impact for participants           │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### H.2. Countercyclical Metrics

```python
class ContracyclicalMetrics:
    """Metrics for observing countercyclical behavior."""

    async def calculate_metrics(
        self,
        period: "DateRange"
    ) -> "ContracyclicalReport":
        """
        Calculate GEO network metrics for a given period.
        """
        return ContracyclicalReport(
            geo_transaction_volume=await self.sum_transactions(period),
            active_participants=await self.count_active(period),
            trust_line_utilization=await self.avg_utilization(period),
            clearing_volume=await self.sum_clearings(period),
            velocity=await self.calculate_velocity(period),
            correlation_with_gdp=await self.correlate_with_external(
                "gdp_growth", period
            ),
            correlation_with_unemployment=await self.correlate_with_external(
                "unemployment_rate", period
            )
        )

    async def detect_countercyclical_behavior(
        self,
        period: "DateRange"
    ) -> bool:
        """
        Detect whether the network shows countercyclical behavior.

        True if:
        - External economy contracts (GDP↓ or unemployment↑)
        - GEO activity grows (volume↑ and participants↑)
        """
        external = await self.get_external_indicators(period)
        internal = await self.calculate_metrics(period)

        economy_contracting = (
            external.gdp_growth < 0
            or external.unemployment_delta > 0
        )

        geo_expanding = (
            internal.transaction_volume_growth > 0
            and internal.active_participants_growth > 0
        )

        return economy_contracting and geo_expanding
```

### H.3. Strengthening the Effect

To enhance the countercyclical function, communities can:

1. **Inform participants** about GEO's role during crises  
2. **Simplify onboarding** during economic stress  
3. **Increase clearing limits/frequency** when downturn indicators appear  
4. **Publish metrics** showing GEO's stabilizing impact  

---

## Related Documents

- [00-overview.md](00-overview.md) — Project Overview  
- [01-concepts.md](01-concepts.md) — Key Concepts  
- [03-architecture.md](03-architecture.md) — System Architecture  
- [04-api-reference.md](04-api-reference.md) — API Reference
