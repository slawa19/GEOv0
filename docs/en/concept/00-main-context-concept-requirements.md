# GEO: Free Exchange Economy  
**Project Summary Context for Further Development and Implementation Discussion**

This document summarizes:

- official GEO texts (Economy of Free Exchange, Decentralized Credit Network, protocol target parameters);
- correspondence with Dima Chizhevsky;
- Comparison with Ripple, clearing issues, sustainability, etc.

Goal — to have one "reference text" that can be relied upon when designing and discussing technical implementation of GEO or related systems.


---

## 1. Problems: What's Wrong with Current Monetary-Credit System

The original GEO documents highlight three basic problems:

1. **Interest on loans**
   - Interest is the lower profitability threshold for any project.  
     The higher it is, the fewer projects become economically feasible.
   - Credit as a resource is expensive, especially in developing countries.
   - Classical banking model makes credit a paid and scarce service, although in fact *mutual credit* between economic agents constantly exists anyway (through payment deferral, receivables, etc.).

2. **Centralization**
   - Money issuance and management are concentrated in the hands of:
     - central banks and financial regulators;
     - network of commercial banks.
   - Consequences:
     - subjectivity and politicization of decisions;
     - "Cantillon effect" and wealth redistribution due to inflation;
     - risk of bailing out individual players at others' expense;
     - privacy vulnerability: complete transaction observability and account blocking capability.

3. **Inflation / deflation**
   - Inflation:
     - used as a hidden "tax" on all money holders;
     - devalues savings and redistributes wealth in favor of the issuer.
   - Deflation:
     - in developed economies the main problem is exactly this;
     - growth of money's purchasing power slows investments and consumption.

**GEO conclusion:**  
modern money system adequately solves the exchange problem, but has built-in and hard-to-eliminate side effects. It makes sense to extract the *exchange and mutual credit* function into a separate protocol, not relying on money as an object of accumulation and speculation.


---

## 2. Basic GEO Idea

**GEO** is:

- **open decentralized credit network protocol**;
- in which:
  - each participant can **issue their own debt obligations**;
  - participants open **trust lines** (credit limits) to each other;
  - obligations are used as **medium of exchange**, but not as universal money;
  - network automatically performs **clearing (netting) of debts** in closed cycles.

Key principles:

1. **Interest-free mutual credit**
   - There is no loan interest between participants.
   - Debt is expressed in goods/services (or chosen equivalent), not in "interest on money".

2. **No own currency**
   - GEO has **no native token** like XRP or BTC.
   - Only **participant obligations** are in circulation, denominated in arbitrary equivalents (fiat, goods, hour of work, kWh, etc.).

3. **Decentralization and risk localization**
   - No central issuer or global transaction register.
   - Credit risk is localized at the level of **trust lines**, which participants voluntarily open to each other.

4. **Privacy by default**
   - No common DB of all transactions.
   - Full details of specific operation are known only to **payment path participants** (and then — predominantly in their part).

5. **Automatic clearing**
   - Network actively searches for closed debt cycles (3–6 nodes) and collapses them, reducing total debt without real "monetary" transfers.


---

## 3. Basic Entities and Definitions

### 3.1. Participant (network node)

- Individual person, company, cooperative, service, etc.
- Identified in system by **node ID** (pseudonym).
- May have additional identification means (social network profile, KYC in overlay service, etc.).

### 3.2. Equivalent

- Unit of account in which obligation and limit value is measured:
  - fiat (UAH, USD);
  - goods (kg of wheat, kWh);
  - service (hour of work of specific specialization);
  - basket or index.
- For one counterparty PARTICIPANT_X you can open **several trust lines in different equivalents**, but no more than one per equivalent.

### 3.3. Trust line

- Directed relationship:  
  "A trusts B up to limit \(L\) in equivalent E".
- Semantics:
  - B can **receive goods/services from A on credit** up to amount \(L\);
  - In return A receives **B's obligations** (promise to give back their goods/services for this amount).
- Important:
  - trust line sets **maximum risk of A relative to B** for given equivalent;
  - current **line balance** — how much of the limit is already used.

### 3.4. Obligation

- Record "X owes Y amount \(S\) in equivalent E".
- Can:
  - arise when receiving goods/services on credit;
  - be transferred to third parties (assignment of claims).

By nature closer to:

- **bonds / commodity bills** (obligation to deliver goods),
- than to **money** as universal asset:

  - **not** a measure of value (the chosen equivalent is);
  - **poorly suited for accumulation** (no interest, specific issuer default risk).

### 3.5. Participant balance

- **Net position** of participant within network and equivalent:
  \[
  \text{Balance}_E(U) = \sum\text{others' obligations to U} - \sum \text{U's obligations to others}
  \]
- **Net balance** is important, not total volume of issued/received obligations.

### 3.6. Available credit / payment flow

- For node pair \(A\) and \(B\) GEO determines **maximum possible amount** that A can "pay" B:
  - considering all direct and transitive trust lines;
  - considering already used limits.
- This is essentially the problem of finding maximum flow in directed graph with edge constraints.

Example:

- D trusts B for 1000;
- D trusts A for 1000;
- A trusts B for 1000.

As a result **flow from B to D** can be up to 2000 due to combination of direct and transitive paths.


---

## 4. GEO Network Architecture

### 4.1. Trust graph

- Network — directed graph:
  - vertices — participants;
  - edges — trust lines with limits and current balances.
- Each participant has *local* list of:
  - outgoing trust lines (whom they trust);
  - incoming (who trusts them).

### 4.2. No global registry

Fundamental difference from blockchain systems:

- **No common ledger** of all transactions that everyone stores and validates.
- Only stored:

  - trust line states,
  - aggregated obligation amounts,
  - local logs at participants involved in operations.

Consequences:

- high scalability (everyone processes only "their" part of graph);
- better privacy (no single log of all actions);
- more complex global audit (can't "scroll blockchain from genesis").

### 4.3. Local consensus instead of global

Instead of "one consensus for entire network" (as in PoW/PoS):

- **each transaction arranges consensus only between participants it affects**:
  - payer;
  - recipient;
  - intermediate nodes on payment paths.

This is essentially a distributed version of two-phase commit (2PC) on graph path.


---

## 5. GEO Transaction Mechanics

### 5.1. Operation types

1. **Trust line management**
   - open/change/close trust line A → B in equivalent E;
   - recorded at both participants (initiator and trust recipient).

2. **Purchasing goods/services on credit**
   - initiator requests payment (specify: counterparty, amount, equivalent);
   - network selects paths and creates chain of mutual obligations.

3. **Assignment of obligations**
   - creditor transfers their claim right to third party;
   - this is the basis for transitive payments and clearing.

4. **Clearing operations**
   - special transactions on closed debt cycles (see below).


### 5.2. Payment routing (path-finding)

When paying A → Z:

1. Client A forms request:
   - recipient Z ID;
   - amount and equivalent;
   - optional constraints (acceptable path length, minimum trust to intermediaries).

2. GEO routing module:
   - searches for trust chains from A to Z up to 6 links long;
   - calculates available flow for each path (minimum of limits minus already occupied);
   - combines multiple paths to achieve needed amount.

3. Result: set of payment paths indicating which volume goes through which path.

### 5.3. Confirmation protocol (local consensus)

For each selected path combination:

1. **Preparation phase (prepare)**
   - each chain participant receives request:
     - "are you ready to change balance on line with neighbor by ΔS within transaction T?"
   - participant:
     - checks local conditions (limit, current load, their risk rules);
     - temporarily **reserves** this volume;
     - responds `OK` or `FAIL`.

2. **Commit phase**
   - if all on all paths responded `OK`:
     - initiator sends `COMMIT(T)` command;
     - each node updates their balance on corresponding line and transaction log.
   - if at least one `FAIL` or no response within reasonable timeout:
     - `ROLLBACK(T)` is sent;
     - all participants free reserves, no balance changes remain.

**Properties:**

- **Atomicity:** no partially processed transactions.
- **Isolation:** parallel transactions see each other only through reservation mechanism, preventing collisions.
- **Consistency:** as result of successful transaction all participants see the same change on their trust lines.

### 5.4. Who knows what about transaction

- Full route (A → … → Z) is known to:
  - initiator;
  - routing component (if separated).
- Each intermediate node knows:
  - from whom to whom they "transferred risk" on line;
  - but may **not know** final recipient and initiator (if not required by upper-level protocol).
- Only recorded in node log:
  - their own changes,
  - neighboring node ID on line,
  - transaction ID / signatures.

No global log of "who bought what from whom" exists.


---

## 6. Clearing (debt netting)

### 6.1. Clearing task

Cycles constantly arise in network:

\[
A \to B \to C \to ... \to A
\]

Essentially, all cycle participants could **mutually zero part of obligations**, without changing the fact that someone consumed or supplied goods.

**Clearing goals:**

- reduce total nominal debt in network;
- reduce number of edges with non-zero debt;
- improve network "liquidity" and stability.

### 6.2. Clearing mechanics in GEO

From correspondence:

- cycles of **3, 4, 5 and 6 participants** are cleared;
- cycles of 3–4 nodes are searched after each operation;
- cycles of 5–6 links — once a day (due to resource intensity).

General principle:

1. **Cycle search**
   - based on current obligation graph (not just trust limits);
   - uses specialized algorithm (`Cycle3`, `Cycle4`, `Cycle5`, `Cycle6` classes in GEO sources).

2. **Determining possible clearing amount**
   - let cycle debts be:
     \[
     A \to B = a,\; B \to C = b,\; ...,\; X \to A = x
     \]
   - then maximum netting amount:
     \[
     S = \min(a, b, ..., x)
     \]

3. **Forming clearing transaction**
   - virtual payment of type "A pays A through cycle (A → B → C → … → A) for amount S" is created;
   - for all cycle edges:
     - debt is reduced by S;
   - local logs are updated at all cycle participants.

4. **Participant confirmation**
   - as in regular transaction, all participants must agree;
   - if someone refuses — cycle is not cleared (or smaller amount is tried).

### 6.3. Example on 5 nodes (A, B, C, D, E)

Initial debt structure (after series of purchases and one clearing):

- A → B : 20
- B → C : 20
- C → D : 10
- D → E : 10
- E → A : 0

**Clearing for 40** already happened (see our previous discussion). If E → A was 40, 5-node cycle would allow reducing all debts by 40.

### 6.4. Who initiates clearing

- Cycles can be **found both by nodes themselves**, analyzing local subgraphs, and by:
  - dedicated services/hubs,
  - that receive aggregated data (without specific transaction details).
- Clearing transaction can be initiated by:
  - any cycle participant;
  - or service, on behalf of one of participants (by pre-agreed rules).

Important:  
**no forced clearing** — debt reduction through cycle is only possible with all participants' consent, although economically it's always beneficial to them.

---

## 7. Security, Trust and Risks

### 7.1. Credit risk localization

- Unlike banking system:
  - no "third party" concentrating risks and making decisions for everyone;
  - each participant decides themselves **whom and how much to trust**.
- Risk is determined by:
  - trust limits set by participant;
  - graph structure (indirect risks in transitive operations).

GEO philosophy:  
protocol **doesn't insure against bad counterparty choice** — it only limits damage scale to trust limit.

### 7.2. Double spending

"GEO Target Parameters" document emphasizes:

- Topology construction method and local consensus **exclude double spending** regardless of individual nodes' computational power.
- Mechanism:
  - reservation on trust lines in `prepare` phase;
  - commitment refusal on any conflict;
  - no common balance that can be "faked" with 51% attack, etc.

### 7.3. Resilience to failures and node "death"

**Temporary node dropout:**

- All operations where it participated and managed to complete are already recorded at others.
- New transactions cannot use paths through this node.
- Rest of network continues working.

**Permanent node disappearance (example with D in cycle A–B–C–D–E):**

- Neighbors remain with last agreed obligations:
  - C owes D — debt becomes practically bad;
  - D owes E — E loses claim to D.
- This is **credit loss of its counterparties**, not protocol breakdown.
- Graph simply "thins": edges disappear, liquidity decreases, but other participants work.

**Mass failures:**

- Network may split into several connected components:
  - within each — everything still works;
  - between components — temporarily no routes.
- No single point of failure like central server or global blockchain.


---

## 8. Privacy and Data

### 8.1. Public data

According to GEO specification publicly available are:

- node ID;
- total volume of **incoming** trust lines;
- total volume of **outgoing** trust lines;
- total volume of node obligations to counterparties;
- total volume of counterparty obligations to node;
- net obligation balance.

This enables:

- assessing relative "reliability" and participant size;
- building reputation mechanisms at protocol/overlay level.

### 8.2. Private data

- Specific transaction history on line A–B:
  - stored only at A and B (and possible intermediary services by their choice).
- In transitive operation:
  - intermediate node sees **only their segment** (e.g., change between B and C), but not entire path and not all amounts in other segments.

### 8.3. Convenience compromise (services like GEOpay)

- Overlays like GEOpay:
  - facilitate interaction (profiles, social networks, UI);
  - but worsen privacy as service server knows more.
- User chooses themselves:
  - work directly through protocol;
  - or use convenience at cost of some privacy.


---

## 9. GEO Comparison with Other Systems

### 9.1. Classical banking system

| Characteristic            | Banks                       | GEO                                        |
|---------------------------|-----------------------------|--------------------------------------------|
| Issuance                  | Central and commercial      | Each participant issues their obligations  |
| Interest                  | Yes                         | No (interest-free mutual credit)           |
| Centralization           | High                        | Absent, p2p graph                          |
| Transaction accounting   | Centralized DBs             | Local logs, no common registry             |
| Third party risk         | High                        | No central intermediary                    |
| Privacy                  | Low (banks and gov agencies)| Higher: no common DB of all operations     |

### 9.2. Ripple / Stellar (XRPL, Stellar Network)

Common features:

- trust lines;
- trust transitivity;
- ability to use IOUs (obligations) as medium of exchange.

Key GEO differences:

1. **No internal currency**
   - Ripple/Stellar: have XRP/XLM, mandatory for operation (registration, fees).
   - GEO: has no "GEO coin"; no protocol fee.

2. **Accounting and consensus**
   - Ripple/Stellar:
     - global distributed ledger of all transactions;
     - consensus among validators (UNL, SCP, etc.).
   - GEO:
     - no common ledger;
     - local consensus only between specific operation participants.

3. **Clearing**
   - Ripple/Stellar:
     - clearing as side result of specific payment transaction;
     - no separate process for finding and collapsing debt cycles.
   - GEO:
     - clearing is **key function**;
     - network purposefully searches for 3–6 node cycles and reduces debts.

4. **Money philosophy**
   - Ripple/Stellar:
     - oriented toward fast settlements in money (fiat, crypto, tokens).
   - GEO:
     - oriented toward credit barter and free exchange without money as independent asset.

### 9.3. Lightning Network and similar solutions

- Lightning:
  - facilitates **internal currency** transfers (e.g., BTC) off blockchain;
  - requires **depositing funds** in channel;
  - channels work in "trustless" mode, but with capital lockup.

- GEO:
  - doesn't require pre-freezing assets;
  - obligation issuance flexibly adapts to needs (endogenous);
  - closer to WIR, LETS and other mutual credit systems, but in p2p network form.


---

## 10. Target Parameters and GEO Protocol Requirements

Per "GEO Target Parameters" document:

### 10.1. General properties

1. **Computational load segmentation**
   - nodes spend resources only on transactions they directly participate in;
   - no common processing of all network operations.

2. **Local 100% consensus**
   - transaction must be confirmed by *all* operation participants;
   - refusal by one participant whose balance is affected makes operation impossible.

3. **High throughput**
   - no network-wide TPS upper bound;
   - performance grows with resource scaling by participants (similar to Internet).

4. **No double spending**
   - topology and transaction algorithm completely eliminate this problem.

5. **Mobile platform focus**
   - protocol must be resource-light;
   - ability to run client on modern smartphones with reasonable power consumption.

### 10.2. Payment algorithm requirements (ACID-like)

1. **Eventual atomicity**
   - transaction:
     - either fully recorded at all participants,
     - or fully cancelled;
   - no partial states (except temporary gaps during network segmentation) should exist.

2. **Consistency**
   - each participant's local state:
     - consistent with previous transaction history;
     - resilient to temporary network gaps and subsequent recovery.

3. **Isolation**
   - parallel execution of other transactions doesn't lead to data integrity violations.

### 10.3. Time parameters (guideline level)

For operation with:

- up to 200 payment paths;
- average path length of 6 participants;
- moderate probability of one node participating in multiple paths;

under given network and hardware conditions:

- **average execution time for 1000 payment operation — no more than 2 minutes** for one participant.

For algorithm predicting possible spending (available flow calculation):

- for network up to 100M participants and average connectivity of 24 counterparties:
  - **average processing time for 1000 requests — up to 6 sec** to get at least 85% of maximum flow.

These figures set guidelines for future implementation and optimization.


---

## 11. GEO Application Scenarios

### 11.1. Credit barter and local economies

- Local communities, cooperatives, small business clusters:
  - participants exchange goods/services "on credit";
  - repayment goes in natural form (work, goods, services of other network members);
  - GEO serves role of:
    - mutual obligation accounting,
    - trust chain finding,
    - automatic clearing.

### 11.2. Internal accounting system of cooperative (example with 50 participants)

- All participants are in one community (cooperative).
- Two configurations possible (we discussed):

1. **Minimal mutual trust + central cooperative node**
   - Cooperative acts as hub:
     - participants open trust lines primarily to cooperative;
     - cooperative has lines to key suppliers, warehouses, etc.
   - Internal settlements:
     - go through GEO graph;
     - clearing reduces mutual debts between participants and cooperative.

2. **Small trust limits between participants**
   - Each participant can give small trust line to limited number of people/departments;
   - this builds network where:
     - one can buy on credit from fairly wide circle of people;
     - risk of each limit is small (units or tens of conventional units).

### 11.3. Electronic money platform

- GEO can serve as low-level platform for:
  - electronic money issuance (node "gateways" backed by fiat);
  - fast and cheap p2p transfers.
- In this mode part of network obligations:
  - represents **monetary obligations** (promise to issue fiat on presentation),
  - essentially — electronic money per 2000/46/EC directive.

### 11.4. IoT and micropayments

- Absence of fees and high scalability make GEO:
  - candidate base for micropayments in **Internet of Things**;
  - e.g., accounting consumption/supply of energy, computing power, resource access.

### 11.5. Loyalty, reputation, spam limiting

- Same trust mechanism can be used **not in money**:
  - reputation system;
  - message filter (spam limitation by spending "communication credit");
  - loyalty and bonus systems.


---

## 12. Organizational aspects for real communities (brief)

For practical implementation of GEO-like system in community (e.g., cooperative) important are:

1. **Technical hygiene**
   - backup of keys and local DBs;
   - server duplication for key nodes (warehouse, common fund);
   - node availability monitoring.

2. **Recovery mechanisms**
   - social recovery / multisig (2–3 trusted persons can help restore access when device is lost);
   - "participant disappearance" regulation (after what time debts are considered bad, etc.).

3. **Economic trust policy**
   - starting limits for newcomers;
   - risk concentration limitations (don't hold too large limit on one counterparty);
   - special status of infrastructure nodes (more transparency, possibly — deposits).

4. **Culture and education**
   - explaining to participants that:
     - trust line = their own risk;
     - "minus" = promise of future work/goods, not magical "minus money";
   - introducing simple rules (don't take more than monthly contribution, etc.).

These issues go beyond the protocol itself, but are critically important for real GEO economy sustainability.


---

## 13. Further development directions and open questions

Below — list of topics logical to discuss further, based on this context.

### 13.1. Implementation technical architecture

- Network layer:
  - p2p-over-IP, NAT-traversal, mobile clients;
  - FDNS / decentralized node catalog.
- Storage:
  - local DB format (trust lines, logs);
  - encryption and backup mechanisms.
- Routing and available flow computation:
  - path finding algorithms (k-shortest paths, max-flow in local subgraphs);
  - result caching (granularity, reuse for new requests).
- Payment protocol:
  - `prepare/commit/rollback` message format;
  - conflict and timeout handling;
  - recovery after network segmentation.
- Clearing module:
  - cycle search API and triggers;
  - frequency and priority policy for clearing;
  - user interface interaction.

### 13.2. UX and user "mental model"

- How to formulate in interface:
  - trust lines (so user understands risk);
  - available credit/flow;
  - balance and clearing state.
- What "safeguards" to build into UI level:
  - default limits;
  - warnings on sharp trust growth to new participant;
  - visual relationship graphs.

### 13.3. Security and correctness formalization

- Formal proofs:
  - absence of double spending;
  - transaction atomicity under partial network segmentation;
  - clearing algorithm correctness (preserving participant net positions).
- Attack analysis:
  - Sybil attacks (mass node generation to influence routes);
  - attempts to inflate obligations without real goods/services backing;
  - DoS attacks on key network segments.

### 13.4. Legal and regulatory aspects

- When do GEO obligations fall under regulation as:
  - electronic money;
  - securities;
  - credit agreements;
  - local currency?
- How to build legal wrapper for:
  - cooperatives;
  - GEO service provider platforms;
  - fiat gateways (if any).

### 13.5. Economic models based on GEO

- Trust and limit rule tuning for:
  - minimizing defaults;
  - maximizing network liquidity;
  - supporting countercyclicality (as in WIR).
- Experiments/simulations:
  - modeling networks with different connectivity and trust strategies;
  - analyzing resilience to defaults and node disappearance;
  - clearing frequency impact on stability and liquidity.

---

## 14. Conclusion

GEO is an attempt to:

- extract **mutual credit and exchange function** from shadow of bank money;
- formalize it as **open p2p protocol**;
- remove:
  - loan interest,
  - centralized issuance,
  - global transaction control;
- and replace them with:
  - **network trust**,
  - **distributed obligation accounting**,
  - **automatic clearing**.

The above document fixes current understanding level and sets framework within which one can:

- design protocol and its implementations;
- discuss specific algorithms, architectures and UX solutions;
- model economic behavior and community rules.

Next one can proceed either to **specific implementation design** (language, architecture, modules), or to **scenario development for specific community/product**, based on this foundation.
