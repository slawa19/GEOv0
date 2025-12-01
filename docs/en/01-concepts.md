# GEO Protocol: Key Concepts

**Version:** 0.1  
**Date:** November 2025

---

## Contents

1. [Participant](#1-participant)
2. [Equivalent](#2-equivalent)
3. [TrustLine](#3-trustline)
4. [Debt](#4-debt)
5. [Payment](#5-payment)
6. [Clearing](#6-clearing)
7. [Transaction](#7-transaction)
8. [Hub and Coordination](#8-hub-and-coordination)

---

## 1. Participant

A **Participant** is any entity that can participate in the GEO economy:
- Individual
- Organization or business
- Hub (community node — also a participant at the federation level)

### Identity

Each participant is cryptographically identified:

```
PID = base58(sha256(public_key))
```

- **public_key** — Ed25519 public key (32 bytes)
- **PID** — derived participant identifier

### Participant properties

| Field              | Description                                   |
|--------------------|-----------------------------------------------|
| `pid`              | Unique identifier (derived from pubkey)        |
| `public_key`       | Ed25519 public key                            |
| `display_name`     | Human-readable name                           |
| `profile`          | Metadata (type, contacts, description)        |
| `status`           | `active`, `suspended`, `left`, `deleted`      |
| `verification_level`| Level of verification (community-dependent)   |

### Ownership principle

- The private key is **never transmitted** to the server
- All critical operations are **signed** by the key owner
- The hub only stores public keys and verifies signatures

---

## 2. Equivalent

An **Equivalent** is a unit of account in which obligations are measured.

### Examples of equivalents

| Code    | Description              | Precision |
|---------|-------------------------|-----------|
| `UAH`   | Ukrainian Hryvnia       | 2         |
| `USD`   | US Dollar               | 2         |
| `HOUR`  | Hour of work            | 2         |
| `HOUR_DEV` | Developer hour       | 2         |
| `kWh`   | Kilowatt-hour           | 3         |
| `LOCAL` | Local community unit    | 2         |

### Equivalent properties

| Field        | Description                           |
|--------------|---------------------------------------|
| `code`       | Unique string code                    |
| `precision`  | Number of digits after the decimal    |
| `description`| Human-readable description            |
| `metadata`   | Additional data (type, binding)       |

### Important

- There is **no default currency** in the system
- All operations explicitly specify the equivalent
- GEO does **not convert** between equivalents automatically
- A participant may have trust lines in different equivalents

---

## 3. TrustLine

A **TrustLine** is a declaration by one participant of readiness to take on risk related to another participant.

### Semantics

```
TrustLine: A → B (limit=1000, equivalent=UAH)
```

Means: **"A allows B to owe them up to 1000 UAH"**

### Key properties

| Property      | Description                                   |
|---------------|-----------------------------------------------|
| **Direction** | A→B ≠ B→A (these are different lines)         |
| **Unilateral**| Created by the owner (A) without B's consent  |
| **Limited**   | Sets a maximum debt B may owe A               |
| **Specific**  | Bound to a specific equivalent                |

### TrustLine structure

| Field        | Description                      |
|--------------|----------------------------------|
| `from`       | PID giving trust (creditor)      |
| `to`         | PID trusted (potential debtor)   |
| `equivalent` | Equivalent                       |
| `limit`      | Max debt amount                  |
| `policy`     | Policies (autoclearing, route constraints) |
| `status`     | `active`, `frozen`, `closed`     |

### Basic invariant

```
debt[B→A, E] ≤ limit(A→B, E)
```

B's debt to A cannot exceed the set limit.

### Example

```
Alice creates TrustLine: Alice → Bob (limit=500, UAH)

This means:
- Bob may owe Alice up to 500 UAH
- Alice accepts the risk of non-repayment
- Bob can use this trust to make purchases
```

---

## 4. Debt

**Debt** is the actual obligation of one participant to another.

### Semantics

```
Debt: X → Y (amount=300, equivalent=UAH)
```

Means: **"X owes Y 300 UAH"**

### Debt structure

| Field        | Description               |
|--------------|--------------------------|
| `debtor`     | Debtor's PID             |
| `creditor`   | Creditor's PID           |
| `equivalent` | Equivalent               |
| `amount`     | Current debt amount (>0) |

### Relation to TrustLine

- TrustLine sets the **ceiling** for possible debt
- Debt is the **actual state**
- One TrustLine may "service" multiple transactions

### Aggregation

- For each triplet `(debtor, creditor, equivalent)` one **aggregated record** is stored
- The protocol does not log each individual deal — only the net debt
- History is recorded in transactions

---

## 5. Payment

A **Payment** is a value transfer from the payer to the recipient through the trust network.

### How payment works

```
A wants to pay C 100 UAH
Direct TrustLine A→C is absent

But there's a chain:
A → B (limit=200) → C (limit=150)

Payment goes via the chain:
1. A owes B +100
2. B owes C +100
3. C receives "payment" from A
```

### Available Credit

For each directed edge:

```
available_credit(A→B, E) = limit(A→B, E) - debt[B→A, E]
```

This is the maximum that can be "transferred" via the edge.

### Routing

1. **Path search:** BFS with depth limit (up to 6 links)
2. **Capacity calculation:** min(available_credit) along the path
3. **Multi-path:** split the payment into 2–3 routes if needed

### Multi-path example

```
A → C for 100 UAH

Paths found:
- A → X → C (capacity 60)
- A → Y → Z → C (capacity 50)

Payment split:
- 60 via path 1
- 40 via path 2
```

---

## 6. Clearing

**Clearing** is the automatic offset of debts in a closed cycle.

### Cycle example

```
A owes B: 100
B owes C: 100
C owes A: 100

This is a closed cycle. Economically — nobody owes anyone anything.

Clearing reduces all debts by min(100, 100, 100) = 100:
A owes B: 0
B owes C: 0
C owes A: 0
```

### How it works

1. **Cycle search:** Algorithm finds closed debt chains
2. **Sum calculation:** S = min(debt on each cycle edge)
3. **Apply:** All debts reduced by S

### Cycle length

| Length | Search frequency        | Complexity   |
|--------|-------------------------|-------------|
| 3 nodes| After every transaction | Low         |
| 4 nodes| After every transaction | Low         |
| 5 nodes| Periodically (hourly)   | Medium      |
| 6 nodes| Periodically (daily)    | High        |

### Why is clearing needed

- **Frees up limits:** TrustLines become available again after clearing
- **Reduces risk:** Less “hanging” debt in the system
- **Boosts liquidity:** More possible payment routes

---

## 7. Transaction

A **Transaction** is an atomic unit of change in the system.

### Types of transactions

| Type                | Description                 |
|---------------------|----------------------------|
| `TRUST_LINE_CREATE` | Create a trust line        |
| `TRUST_LINE_UPDATE` | Change limit or policy     |
| `TRUST_LINE_CLOSE`  | Close the trust line       |
| `PAYMENT`           | Payment through the network|
| `CLEARING`          | Cycle clearing             |

### Transaction structure

| Field        | Description                     |
|--------------|---------------------------------|
| `tx_id`      | Unique ID (UUID or hash)        |
| `type`       | Transaction type                |
| `initiator`  | PID of the initiator            |
| `payload`    | Typed operation data            |
| `signatures` | Participant signatures          |
| `state`      | Current state                   |
| `created_at` | Creation time                   |

### PAYMENT states

```
NEW → ROUTED → PREPARE_IN_PROGRESS → COMMITTED
                                   ↘ ABORTED
```

### CLEARING states

```
NEW → PROPOSED → WAITING_CONFIRMATIONS → COMMITTED
                                       ↘ REJECTED
```

### Idempotence

- Repeated `COMMIT` on a finished transaction is safe
- `tx_id` ensures there are no duplicates

---

## 8. Hub and Coordination

### Role of the Hub

The hub serves as **coordinator**, not a bank:

| Function      | Description                                       |
|---------------|---------------------------------------------------|
| **Storage**   | State of participants, TrustLines, debts          |
| **Coordination** | PREPARE/COMMIT phases of transactions         |
| **Routing**   | Path discovery for payments                       |
| **Clearing**  | Cycle discovery and execution                     |
| **API**       | Access for clients                                |

### What the Hub does NOT do

- Does not store participants' private keys
- Does not perform operations without owner signature
- Does not “own” participants’ funds
- Does not unilaterally decide balances

### Transaction Coordination (2PC)

**Two-phase commit** ensures atomicity:

```
Phase 1: PREPARE
- Hub sends all participants a reservation request
- Each checks conditions and reserves resources
- All respond OK or FAIL

Phase 2: COMMIT or ABORT
- If all OK → COMMIT (apply changes)
- If any FAIL → ABORT (release reserves)
```

### Hub Administration

The hub is managed by a **community operator** (cooperative, NGO, etc):

| Role      | Rights                                        |
|-----------|-----------------------------------------------|
| `admin`   | Config, roles, addons management              |
| `operator`| Monitoring, user help, dispute handling       |
| `auditor` | Read-only access to logs and reports          |

### Hub Federation

Hubs from different communities can unite:

```
┌───────────────┐         ┌───────────────┐
│  Community A  │         │  Community B  │
│  ┌─────────┐  │ Trust-  │  ┌─────────┐  │
│  │ Hub A   │◄─┼─Line────┼─►│ Hub B   │  │
│  └────┬────┘  │         │  └────┬────┘  │
│  [users A]    │         │  [users B]    │
└───────────────┘         └───────────────┘

Hub A and Hub B — regular protocol participants
TrustLines are open between them
Payments between communities are routed via the hubs
```

---

---

## 9. Economic Model

### 9.1. Implicit Demurrage

Unlike explicit demurrage (tax on money storage), GEO creates **implicit demurrage** via the system’s structure.

**What is demurrage?**

Demurrage is a mechanism that makes storing money “expensive”. In traditional systems, it might be an explicit tax (e.g., -2%/month on balances). In GEO, demurrage is **implicit** — it arises naturally from the system’s structure.

**Why can’t you “accumulate wealth” in GEO?**

There’s no real money in GEO — only obligations (debts) between participants. “Wealth” in GEO means others owe you. But for others to owe you, someone must open a TrustLine to you. To receive TrustLines, you must be an active participant — buying, selling, opening TrustLines for others.

**The hidden demurrage mechanism works like this:**

1. **To get paid** — you need incoming TrustLines (credit lines to you)
2. **To receive TrustLines** — you must be known, reliable, useful to the network
3. **To be useful** — you must also open TrustLines (accept risk), participate in clearing, act as intermediary
4. **Opening a TrustLine = risk** — you may lose up to the limit if the debtor fails to repay
5. **Result:** The more you want to "accumulate," the more risk you must accept

**Comparison with traditional money:**

| Aspect          | Traditional Money   | GEO                        |
|-----------------|--------------------|----------------------------|
| Accumulation    | Unlimited saving   | Limited by TrustLines to you|
| Accumulator risk| Only inflation      | Risk of non-repayment      |
| Spending incentive | Only inflation   | Incoming payment block at high balance |
| Passive income  | Interest on deposit | None — only active participation |

#### Hidden demurrage mechanism

```
┌────────────────────────────────────────────────────────────┐
│              Hidden Demurrage in GEO                      │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Increasing TrustLine = increasing personal risk           │
│                                                            │
│  If I open a TrustLine for 1000 UAH:                       │
│  • I accept the risk of non-repayment up to 1000 UAH       │
│  • It is the "price" for being part of the network         │
│  • More trust = more risk = hidden "cost"                  │
│                                                            │
│  Result:                                                   │
│  • Natural limit on "wealth" accumulation                  │
│  • Incentive to balance positions                          │
│  • Circulation instead of hoarding                         │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 9.2. Incentives for Zero Balance

Optimal strategy for a participant is to **aim for a zero balance**.

#### Consequences of a positive balance (others owe you a lot)

| Effect                | Description                                     |
|-----------------------|-------------------------------------------------|
| **Liquidity block**   | TrustLines to you are fully used — no one can pay you |
| **Reduced usefulness**| You "drop out" as a payment receiver            |
| **Reputation effects**| Seen as a "hoarder"                             |
| **Non-repayment risk**| Accumulated debts might not be repaid           |

#### Consequences of a negative balance (you owe a lot)

| Effect                  | Description                                    |
|-------------------------|------------------------------------------------|
| **Purchase restriction**| No free credit for new purchases               |
| **TrustLine withdrawal risk** | Creditors may close lines               |
| **Reputation loss**     | Reduced trust from others                      |
| **Repayment pressure**  | Pressure to provide goods/services             |

#### Optimal state — around zero

```
┌────────────────────────────────────────────────────────────┐
│                  Balance around zero                       │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  ✓ Max flexibility for buying AND selling                  │
│  ✓ Good reputation in the community                        │
│  ✓ Stable relationships with partners                      │
│  ✓ Minimal risks                                           │
│  ✓ Active in clearing                                      │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 9.3. Economic Health Metrics

To assess a participant’s economic health, four key metrics are used:

1. **Net balance** — difference between what is owed to the participant and what they owe. Ideal is around zero.

2. **Deviation from zero** — absolute net balance. Lower is better.

3. **Incoming TrustLines utilization** — what percent of limits open TO the participant are used (debts to them). If 100%, no one can pay them.

4. **Outgoing TrustLines utilization** — what percent of limits opened FROM participant are used (their debts). If 100%, cannot buy more.

**Health score calculation (0-100):**
- Start with 100 points
- Subtract penalty for deviation from zero (up to 50 points)
- Subtract penalty for utilization above 70% (up to 30 points)

```python
class EconomicHealthMetrics:
    """Participant economic health metrics"""
    # Net balance (owed to you - you owe)
    net_balance: Decimal
    # Deviation from zero (0 = ideal)
    balance_deviation: Decimal = abs(net_balance)
    # Incoming TrustLines utilization (%)
    incoming_utilization: Decimal  # debt_to_me / total_incoming_limit
    # Outgoing TrustLines utilization (%)
    outgoing_utilization: Decimal  # my_debt / total_outgoing_limit
    # Balance health (0-100)
    @property
    def health_score(self) -> int:
        # Penalty for deviation from zero
        balance_penalty = min(self.balance_deviation / 1000, 50)
        # Penalty for high utilization
        utilization_penalty = max(
            self.incoming_utilization - 0.7,
            self.outgoing_utilization - 0.7,
            0
        ) * 100
        return max(0, 100 - balance_penalty - utilization_penalty)
```

### 9.4. Economic Behavior Evaluation Algorithm

Economic behavior analysis evaluates how much the participant follows the optimal strategy — aiming for a zero balance.

**Analysis algorithm:**

1. **Gather history** — get all balance entries for the participant over a period (default 30 days)
2. **Calculate average balance** — far from zero is a bad sign
3. **Calculate volatility** — large stable balance worse than oscillation near zero
4. **Determine trend** — is participant moving toward or away from zero
5. **Count clearing participation** — active clearing gets bonuses

**Final score calculation (0-100):**
- Base 100 points
- Penalty up to 30 points for large average balance
- Penalty of 20 points for trend away from zero, or bonus of 10 for trend to zero
- Bonus up to 20 for clearing participation

```python
class EconomicBehaviorAnalyzer:
    """Participant economic behavior analyzer"""

    async def analyze_participant(
        self,
        pid: str,
        period_days: int = 30
    ) -> BehaviorAnalysis:
        """
        Analyze economic behavior:
        Evaluates whether participant follows optimal strategy
        (zero balance aim).
        """
        # Get balance history
        balance_history = await self.get_balance_history(pid, period_days)
        # Average balance for the period
        avg_balance = sum(b.net_balance for b in balance_history) / len(balance_history)
        # Balance volatility
        balance_volatility = statistics.stdev(
            b.net_balance for b in balance_history
        )
        # Trend (toward or away from zero?)
        trend = self.calculate_trend(balance_history)
        # Clearing frequency
        clearing_participation = await self.count_clearings(pid, period_days)
        return BehaviorAnalysis(
            pid=pid,
            avg_balance=avg_balance,
            balance_volatility=balance_volatility,
            trend_direction=trend,  # "toward_zero" | "away_from_zero" | "stable"
            clearing_participation=clearing_participation,
            behavior_score=self.calculate_score(avg_balance, trend, clearing_participation)
        )

    def calculate_score(
        self,
        avg_balance: Decimal,
        trend: str,
        clearing_count: int
    ) -> int:
        """
        Behavior score (0-100).
        High score = good economic behavior.
        """
        score = 100
        # Penalty for large avg balance
        score -= min(abs(avg_balance) / 100, 30)
        # Penalty for moving away from zero
        if trend == "away_from_zero":
            score -= 20
        elif trend == "toward_zero":
            score += 10
        # Bonus for clearing participation
        score += min(clearing_count * 2, 20)
        return max(0, min(100, score))
```

---

## 10. Reputation System

### 10.1. Purpose

Reputation in GEO reflects the **reliability and usefulness** of a participant.

### 10.2. Reputation components

A participant’s reputation score aggregates four metric groups:

**1. Basic trust metrics:**
- Total TrustLine limits open TO participant (how much they are trusted monetarily)
- Count of different participants trusting them (trust breadth)

**2. Activity metrics:**
- Number of payments sent and received
- Percentage of successful payments (not aborted)

**3. Economic behavior metrics:**
- Clearing participation (helping the network)
- Zero-balance proximity (healthy behavior)

**4. Network contribution metrics:**
- How often acted as intermediary in others’ payments
- Volume “passed through” the participant (value for the network)

**5. Verification and tenure:**
- Verification level (0-3)
- Membership duration in the community

```python
class ReputationScore:
    """Participant reputation score"""
    # === Basic metrics ===
    # Total incoming TrustLines (how much others trust)
    trust_received: Decimal
    # Number of TrustLine openers
    trustees_count: int
    # === Activity metrics ===
    # Number of outgoing successful payments
    payments_sent: int
    # Number of incoming payments
    payments_received: int
    # Success rate (% COMMITTED of all initiated)
    payment_success_rate: Decimal
    # === Economic behavior metrics ===
    # Clearing participations
    clearing_participation: int
    # Average balance deviation
    avg_balance_deviation: Decimal
    # === Network contribution ===
    # Intermediary in payments
    intermediary_count: int
    # Volume “passed through”
    intermediary_volume: Decimal
    # === Verification ===
    verification_level: int
    # Community membership start
    member_since: datetime
```

### 10.3. Reputation calculation algorithm

Reputation score is a number from 0 to 100, calculated as a weighted sum of normalized metrics.

**Component weights:**
- **Trust received (20%)** — total amount others trust you
- **Trustees count (10%)** — number of different people trusting you
- **Payment success (15%)** — percent of successful payments
- **Clearing participation (10%)** — clearing frequency
- **Balance health (15%)** — proximity to zero balance
- **Network contribution (15%)** — intermediary in payments
- **Verification (10%)** — identity confirmation level
- **Tenure (5%)** — community membership duration

**Metric normalization:**
- Each metric is normalized 0-100
- For money, log scale (1000 = ~50, 10000 = ~80)
- For counts, linear to expected (e.g., 50 trustees = 100)
- For tenure, 1 year = 100 points

**Final calculation:**
```
score = Σ (normalized[i] × weight[i])
```

```python
class ReputationService:
    """Reputation calculation service"""

    # Component weights
    WEIGHTS = {
        "trust_received": 0.20,       # 20%
        "trustees_count": 0.10,       # 10%
        "payment_success": 0.15,      # 15%
        "clearing_participation": 0.10, # 10%
        "balance_health": 0.15,       # 15%
        "network_contribution": 0.15, # 15%
        "verification": 0.10,         # 10%
        "tenure": 0.05                # 5%
    }

    async def calculate_reputation(
        self,
        pid: str
    ) -> ReputationResult:
        """
        Calculate reputation score (0-100).
        """
        metrics = await self.gather_metrics(pid)

        # Normalize each metric to 0-100
        normalized = {
            "trust_received": self.normalize_trust(metrics.trust_received),
            "trustees_count": self.normalize_count(metrics.trustees_count, max_expected=50),
            "payment_success": metrics.payment_success_rate * 100,
            "clearing_participation": self.normalize_count(metrics.clearing_participation, max_expected=100),
            "balance_health": 100 - min(metrics.avg_balance_deviation / 10, 100),
            "network_contribution": self.normalize_contribution(metrics.intermediary_volume),
            "verification": metrics.verification_level * 33.33,
            "tenure": self.normalize_tenure(metrics.member_since)
        }

        # Weighted sum
        total_score = sum(
            normalized[key] * weight
            for key, weight in self.WEIGHTS.items()
        )

        return ReputationResult(
            pid=pid,
            score=round(total_score),
            breakdown=normalized,
            calculated_at=datetime.utcnow()
        )

    def normalize_trust(self, trust_received: Decimal) -> Decimal:
        """
        Normalize trust received.
        Log scale (1000 UAH = ~50, 10000 = ~80, 100000 = ~100)
        """
        if trust_received <= 0:
            return Decimal("0")
        import math
        return min(100, Decimal(math.log10(float(trust_received) + 1) * 25))

    def normalize_contribution(self, volume: Decimal) -> Decimal:
        """Normalize intermediary contribution"""
        if volume <= 0:
            return Decimal("0")
        import math
        return min(100, Decimal(math.log10(float(volume) + 1) * 20))

    def normalize_tenure(self, member_since: datetime) -> Decimal:
        """Normalize membership duration"""
        days = (datetime.utcnow() - member_since).days
        # 1 year = 100
        return min(100, Decimal(days / 365 * 100))
```

### 10.4. Using Reputation

| Context                   | Usage                                               |
|---------------------------|----------------------------------------------------|
| **TrustLine opening**     | Recommends limit based on recipient's reputation   |
| **Routing**               | Prefer paths through highly reputable intermediaries|
| **Spam filtering**        | Minimum reputation to contact participant          |
| **Dispute resolution**    | Consider reputation in arbitration                 |
| **Gateway**               | Minimum reputation for Gateway registration        |

### 10.5. Reputation API

```python
# Endpoint: GET /api/v1/participants/{pid}/reputation

{
  "pid": "5HueCGU8rMjx...",
  "score": 78,
  "level": "trusted",  # "new" | "basic" | "trusted" | "established" | "pillar"
  "breakdown": {
    "trust_received": 85,
    "trustees_count": 60,
    "payment_success": 95,
    "clearing_participation": 70,
    "balance_health": 80,
    "network_contribution": 65,
    "verification": 66,
    "tenure": 40
  },
  "badges": ["active_trader", "good_intermediary", "clearing_champion"],
  "calculated_at": "2025-11-30T12:00:00Z"
}
```

### 10.6. Reputation Levels

| Level         | Score | Description                             |
|---------------|-------|-----------------------------------------|
| `new`         | 0-20  | New participant, limited capabilities   |
| `basic`       | 21-40 | Basic, standard capabilities            |
| `trusted`     | 41-60 | Trusted, extended limits                |
| `established` | 61-80 | Established, can be Gateway             |
| `pillar`      | 81-100| Community pillar, max privileges        |

---

## Glossary

| Term           | Definition                                 |
|----------------|--------------------------------------------|
| **PID**        | Participant ID — unique participant ID     |
| **TrustLine**  | Credit line from A to B                    |
| **Debt**       | Actual obligation                          |
| **Equivalent** | Unit of account                            |
| **Available Credit** | Remaining limit minus debt           |
| **Clearing**   | Debt offset in a cycle                     |
| **2PC**        | Two-Phase Commit                           |
| **Hub**        | Community node — coordinator & storage     |
| **Demurrage**  | "Cost" for storing funds                   |
| **Reputation** | Participant reliability metric             |

---

## Related Documents

- [00-overview.md](00-overview.md) — Project Overview
- [02-protocol-spec.md](02-protocol-spec.md) — Complete Protocol Specification
- [03-architecture.md](03-architecture.md) — System Architecture