# GEO — User Behavior Simulator Application

> Application specification for simulating, testing and visualizing GEO network

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Main simulator functionality](#2-main-simulator-functionality)
   - 2.1. ["World" configuration](#21-world-configuration-network-generation)
   - 2.2. [Behavioral scenarios](#22-behavioral-scenarios-interaction-models)
   - 2.3. [Load management](#23-load-management)
   - 2.4. [Core API interaction](#24-interaction-with-real-core-api)
3. [Visualization and analytics](#3-visualization-and-analytics)
   - 3.1. [Trust network and debt graph](#31-trust-network-and-debt-graph)
   - 3.2. [Aggregated metrics](#32-aggregated-metrics-over-time)
   - 3.3. [Protocol efficiency indicators](#33-protocol-efficiency-indicators)
   - 3.4. [Replay mode](#34-replay-mode)
4. [Settings and scenarios for specs](#4-settings-and-scenarios-for-specs)
5. [Visualization concept](#5-visualization-concept)
6. ["Community Map" screen](#6-community-map-screen)
   - 6.1. [Screen layout](#61-screen-layout)
   - 6.2. [Participant display](#62-participant-node-display)
   - 6.3. [Connection display](#63-connection-display-trustlines-and-debts)
7. [Participant grouping](#7-participant-grouping)
   - 7.1. [By types and roles](#71-by-types-and-roles)
   - 7.2. [By trust clusters](#72-by-trust-clusters)
   - 7.3. [By financial indicators](#73-by-financial-indicators)
8. [User actions](#8-user-actions)
   - 8.1. [Participant actions](#81-participant-node-actions)
   - 8.2. [Trust line actions](#82-trust-line-and-debt-actions)
9. ["Payment routes and clearing" mode](#9-payment-routes-and-clearing-mode)
10. [Aggregated participant list mode](#10-aggregated-participant-list-mode)
11. [Recommended technical stack](#11-recommended-technical-stack)
    - 11.1. [Main stack (React)](#111-main-stack-react)
    - 11.2. [Alternative (Python)](#112-alternative-stack-python)
12. ["Community Map" screen UI specification](#12-community-map-screen-ui-specification)
    - 12.1. [General structure](#121-general-screen-structure)
    - 12.2. [Top Bar](#122-top-bar)
    - 12.3. [Left Sidebar](#123-left-sidebar)
    - 12.4. [Main Canvas](#124-main-canvas)
    - 12.5. [Right Sidebar](#125-right-sidebar)
    - 12.6. [Screen states](#126-overall-screen-states)

---

## 1. Introduction

**Separate application that simulates live community** and loads GEO core, and also visualizes results. This is simultaneously:

- traffic generator and behavior scenario tool;
- load testing instrument;
- laboratory for protocol experiments (routing, clearing, trust policies).

### General Idea

Application connects to hub's API as multiple "virtual users" (participants), creates and manages them, opens trustlines between them, generates payments, initiates/reacts to clearing. Essentially, this is **community economy simulation** on top of real GEO core.

**Goals:**

- check implementation performance and stability;
- verify logic correctness (especially clearing, limit restrictions, idempotency);
- research how different trust topologies and behavior models affect:
  - netting efficiency;
  - debt/credit distribution;
  - average route length and refusal frequency;
- find bugs and bottlenecks before system reaches real users.

---

## 2. Main Simulator Functionality

### 2.1. "World" Configuration (Network Generation)

> **MVP approach:** Start with JSON configuration (participant list + trustline list). Add dynamic generation later.

Ability to set network parameters via JSON file:

```json
{
  "participants": [
    { "id": "p1", "name": "Ivan", "type": "person" },
    { "id": "p2", "name": "Coffee Shop", "type": "organization" }
  ],
  "trustlines": [
    { "from": "p1", "to": "p2", "limit": 1000 }
  ]
}
```

Configuration parameters:

- participant count;
- participant types (regular, "central" nodes, hubs etc.);
- trustline list with limit specification;
- **one equivalent** (e.g., `UAH`) — sufficient for MVP.

Simulator can:

- **load ready JSON scenarios** (main mode for MVP);
- automatically generate network (extended mode, add later).

### 2.2. Behavioral Scenarios (Interaction Models)

Models describing how participants behave over time:

- **"random market"** — participants randomly select each other and make payments;
- **"cluster exchange"** — more intensive exchange within subgroups, rare between them;
- **"client-supplier"** — some participants mainly sell, others mainly buy;
- **stress scenarios**:
  - activity spike;
  - part of participants disconnecting (suspended);
  - sharp trustline limit changes.

Models should be configurable:

- transaction intensity (how many payments per second/minute/hour);
- payment amount distribution;
- trustline change probability (increase/decrease limits, closure).

### 2.3. Load Management

> **MVP approach:** One "intensity" slider (0–100%) instead of multiple profiles.

Ability to dynamically change:

- total number of active "virtual users";
- **simulation intensity** — one slider 0–100%:
  - 0% — simulation paused;
  - 25% — quiet day;
  - 50% — normal load;
  - 75% — peak hour;
  - 100% — stress test.

Operation type ratio (for extended mode):

- what percentage — payments;
- how much — trustline changes;
- how much — artificially initiated clearings.

### 2.4. Interaction with Real Core API

Simulator shouldn't "climb into DB directly" but use same interfaces as real clients:

- participant registration through API;
- trustline creation through API;
- sending `PAYMENT_REQUEST` and awaiting result;
- monitoring transaction states.

This guarantees we test exactly protocol layer and logic, not just internal functions.

---

## 3. Visualization and Analytics

Very important part — **visual display of what's happening in network**.

### 3.1. Trust Network and Debt Graph

- **Nodes** — participants;
- **Edges**:
  - trustlines (limits);
  - current debts on top;
- **Edge color/thickness**:
  - limit magnitude;
  - utilization degree (debt/limit);
- **Capability**:
  - enable/disable trust/debt display;
  - highlight specific payment routes;
  - see which edges most often participate in clearing.

### 3.2. Aggregated Metrics Over Time

> **MVP approach:** Instead of 10+ metrics, 4–5 key ones sufficient.

**Key metrics for MVP:**

| Metric | Purpose |
|--------|---------|
| Total debt volume | Understand system scale |
| % successful payments | Assess workability |
| Average route length | Assess network efficiency |
| Clearing volume | Assess netting work |
| Top-5 "bottlenecks" | Find overloaded participants |

**Extended metrics (after MVP):**

- Route length distribution (maximum);
- Refusal frequency by reasons:
  - no route;
  - insufficient limit;
  - 2PC timeout.
- Time series graphs, ability to rewind/view history.

### 3.3. Protocol Efficiency Indicators

- **Netting efficiency** — ratio of clearing sum to sum of all conducted payments;
- **Debt concentration** — which participants become "bottlenecks" (many debts converge on them);
- **Network stability** — how often system reaches states when new payments "don't go through" due to certain edge overload.

### 3.4. Replay Mode

> ⚠️ **Not for MVP** — requires complex state serialization. Implement after basic functionality.

- Ability to record scenario (or simulation fragment);
- Then play it back at different speeds;
- Compare how different protocol/algorithm versions would behave.

---

## 4. Settings and Scenarios for Specs

In perspective specs for such application can include:

- **Simulation core:**
  - scenario engine (script language or configuration files);
  - event generator (tick-based or event-based).

- **Scenario settings:**
  - network generation parameters;
  - participant behavior profiles;
  - event scheduler (when what changes).

- **Replica and reproducibility support:**
  - fixed seeds for random number generators;
  - configuration and run result saving.

- **Different GEO backend version support:**
  - ability to run simulation against different core instances (e.g., v0.1, v0.2).

Such application will become not only testing tool, but **research laboratory** for GEO protocol development.

---

## 5. Visualization Concept

Visual part of application can have **two main modes**:

1. **"Social-economic radar"** — community overview representation:
   - who is who,
   - who is connected to whom,
   - how value flows.

2. **"Participant workstation"** — focus on user themselves:
   - my connections (trustlines),
   - my debts and credits,
   - my actions (pay, change limit, agree to clearing etc.).

Both modes can be available in browser (web interface) and native/cross-platform client.

---

## 6. "Community Map" Screen

### 6.1. Screen Layout

**Center** — interactive graph:

- **nodes** — participants;
- **edges** — trust connections and/or debt between them.

**Left** — filters and layers:

- enable/disable:
  - trust lines (limits);
  - debts (actual obligations);
- select equivalent (UAH, HOUR, LOCAL_UNIT etc.);
- filter by participant type.

**Right** — information panel about selected element:

- if participant selected: brief profile card, aggregated indicators, available actions;
- if edge selected: trustline and debt details between participants.

**Top** — visualization mode panel:

- "Trust Network" (TrustLines);
- "Debt Network" (Debts);
- "Payment Routes";
- "Clusters".

### 6.2. Participant Display (Nodes)

Each participant on graph is **icon + label**:

- **Shape/icon** encodes type:
  - circle — individual;
  - square — organization;
  - hexagon — hub.

- **Node color** means state:
  - green — active (`active`);
  - yellow — limited/under surveillance (`suspended`);
  - gray — left (`left`) or disabled;
  - red border — participant often figures in disputes or has anomalous indicators.

On cursor hover **mini-tooltip** pops up: name, short status, trust connection count.

On node click right panel opens with detailed information.

### 6.3. Connection Display (trustlines and debts)

Edges drawn between participants:

- **TrustLines**:
  - thin lines, color showing trust direction;
  - arrow from `from` to `to`;
  - saturation/thickness — limit magnitude.

- **Debts**:
  - thicker or highlighted lines;
  - direction — from debtor to creditor;
  - color intensity — debt size.

User can:

- enable "trustlines only" or "debts only" mode;
- interactively "highlight" specific participant's connections.

---

## 7. Participant Grouping

### 7.1. By Types and Roles

Grouping by:

- type (`person`, `organization`, `hub`);
- role in community (e.g., "coordinator", "service provider", "major buyer").

Implementation: color codes or different icons; ability to highlight one group.

### 7.2. By Trust Clusters

> ⚠️ **MVP approach:** Disable automatic clustering for MVP. Show only manual grouping by participant type (see 7.1).

In extended mode based on graph analysis can automatically highlight **clusters**:

- participant groups having many mutual trust lines;
- local "communities within community" (neighborhoods, professional groups).

Visually:

- nodes of same cluster highlighted with same background/frame;
- "collapse clusters" mode: each cluster shown as one large node.

### 7.3. By Financial Indicators

Filters and color gradients:

- **by net balance**: positive (creditor) — one color; negative (debtor) — another;
- **by activity**: operation frequency over last N days.

This allows:

- seeing "bottlenecks" (participants through whom too many operations pass);
- identify potential risks (heavily indebted).

---

## 8. User Actions

### 8.1. Participant (Node) Actions

**Regular participant**, selecting another participant on graph, can:

- **Open profile panel** — view public information, see their relationships with them;
- **Create/modify trust line** — set limit, choose equivalent, change policy;
- **Initiate payment** — specify amount and equivalent, see preliminary route estimate;
- **View interaction history** — payment list, trustline changes.

**Administrator/coordinator** when selecting participant can additionally:

- **Change participant status** (`suspended`, `left`);
- **View risks and anomalies**;
- **Go to dispute resolution tools**.

### 8.2. Trust Line and Debt Actions

On edge click shows:

- **trustline details** `A→B`: current limit, policy, creation/modification date;
- **debt details** `B→A`: current amount, change history.

**Regular participant** (if it's their trustline) can: change limit, change policy, close trust line.

**Administrator** can: see additional technical data, run analytical tools.

---

## 9. "Payment Routes and Clearing" Mode

For protocol work transparency and debugging:

- user selects specific payment (by `tx_id`);
- system **highlights route**: nodes and edges involved; arrows show how debts redistributed.

**Clearing cycle display mode**:

- when selecting `CLEARING` transaction: cycle `A → B → C → A` highlighted;
- on each edge shows: how much debt was before clearing, how much after, what part "collapsed".

---

## 10. Aggregated Participant List Mode

Besides graphic view need **table-card** form of participant list.

Table/list can contain for each participant:

- name/title;
- type;
- net balance;
- incoming/outgoing trustline count;
- activity and reliability indicators.

**Possible actions:**

- search and filtering;
- click on row — transition to participant card and/or highlighting them on graph;
- bulk operations (for admins): data export, notification sending.

---

## 11. Recommended Technical Stack

### 11.1. Main Stack (React)

For separate analytical/simulation web tool React gives almost ideal balance:

- huge community;
- ocean of ready solutions;
- AI agents best at generating exactly React+TS code.

**Recommended stack:**

| Component | Technology | Note |
|-----------|------------|------|
| Language | TypeScript | — |
| UI framework | React | — |
| UI kit | MUI (Material UI) | — |
| Graph (network) | `react-force-graph-2d` | ⚠️ Use 2D, not 3D — simpler and sufficient for MVP |
| Charts | `Recharts` | Declarative, simple |
| API work | React Query (TanStack Query) | — |
| Build | Vite | — |

**Technical limitations:**

- **Limit graph size** — at >100 nodes switch to aggregated view. Force-directed layout works poorly with >500 nodes.
- **Throttle updates** — during active simulation don't redraw graph more than 2–3 times per second.
- **Offline mode** — ability to load state snapshot and analyze without backend connection.

**Why this is "minimal coding":**

- Practically all visual components will be **composition of ready libraries**;
- Real "manual" code — mainly **glue**: API requests, data transformation, filtering logic.

### 11.2. Alternative Stack (Python)

If want to completely avoid JS world:

**Dash (Plotly Dash):**

- Python code describes layout and callbacks;
- has `dash-cytoscape` for graph visualization;
- Plotly graphs for metrics;
- easily deployed.

**Streamlit:**

- even simpler syntax;
- many examples;
- for complex graph visualization may need more workarounds.

This is especially good if main audience — developers/researchers.

---

## 12. "Community Map" Screen UI Specification

Compact but detailed specification for code generation (React/Flutter).

### 12.1. General Screen Structure

Screen divided into 4 main areas:

1. **Top Bar** — upper navigation and mode panel.
2. **Left Sidebar** — filters, layers, scenario/simulation selection.
3. **Main Canvas** — central canvas with participant graph.
4. **Right Sidebar** — detailed panel of selected element.

```
CommunityMapPage
├── TopBar
└── Layout
    ├── LeftSidebar
    ├── MainCanvas
    │   └── GraphView
    └── RightSidebar
```

### 12.2. Top Bar

**Component: `TopBar`**

| Element | Component | States |
|---------|-----------|--------|
| Logo | `AppLogo` | normal, compact |
| Modes | `ViewModeTabs` | `activeTab: 'Network' \| 'Metrics'` (⚠️ Replay — not for MVP) |
| Status | `SimulationStatusIndicator` | `connectionStatus`, `simulationStatus` |
| Control | `SimulationControls` | Start/Stop/Pause/Resume/Speed |

**Hotkeys (UX improvement):**

| Key | Action |
|-----|--------|
| `Space` | Pause/resume simulation |
| `R` | Reset (simulation reset) |
| `+` / `-` | Increase/decrease speed |
| `Esc` | Remove selection from node/edge |

### 12.3. Left Sidebar

**Component: `LeftSidebar`**

Internal structure:

- `QuickStartBanner` — ⭐ UX improvement: on first launch suggests loading demo scenario with 10–20 participants
- `ScenarioSelector` — simulation scenario selection
- `LayerToggles` — layer toggles
- `FiltersPanel` — participant filters
- `ExportButton` — graph export to PNG/SVG

**`LayerToggles` — states:**

```ts
showTrustLines: boolean
showDebts: boolean
showPaymentRoutes: boolean
showClusters: boolean
```

**`FiltersPanel` — states:**

```ts
filter.types: { person: boolean; organization: boolean; hub: boolean }
filter.statuses: { active: boolean; suspended: boolean; left: boolean }
filter.equivalentCode: string | 'ANY'
filter.netBalanceRange: [number, number]
filter.activityRange: [number, number]
```

### 12.4. Main Canvas

**Component: `GraphView`**

Wrapper over library (e.g., `react-force-graph`):

```ts
type PID = string;

interface ParticipantNode {
  id: PID;
  name: string;
  type: 'person' | 'organization' | 'hub';
  status: 'active' | 'suspended' | 'left';
  netBalance: number;
  activityScore: number;
  clusterId?: string;
}

interface LinkEdge {
  id: string;
  from: PID;
  to: PID;
  kind: 'trustline' | 'debt';
  equivalent: string;
  limit?: number;     // for trustline
  amount?: number;    // for debt
  utilization?: number;
}
```

**Props:**

- `nodes: ParticipantNode[]`
- `links: LinkEdge[]`
- `viewMode: 'trustlines' | 'debts' | 'combined'`
- `highlightedNodeId?: PID`
- `highlightedLinkId?: string`
- `onNodeClick(nodeId: PID)`
- `onLinkClick(linkId: string)`
- `onBackgroundClick()`

**Node visual states:**

| Type | Shape |
|------|-------|
| `person` | circle |
| `organization` | square |
| `hub` | hexagon |

| Status | Color |
|--------|-------|
| `active` | green |
| `suspended` | yellow |
| `left` | gray |

### 12.5. Right Sidebar

**Component: `RightSidebar`**

Subcomponents:

- `NodeDetailsPanel` — if participant selected
- `LinkDetailsPanel` — if connection selected
- `EmptySelectionPanel` — if nothing selected

**`NodeDetailsPanel` — structure:**

1. Profile card (name, type, status)
2. Brief metrics (net balance, trustline count)
3. Connection list (tabs: TrustLines / Debts)
4. Actions (Create TrustLine, Initiate Payment, View History)

**`LinkDetailsPanel` — structure:**

1. Header (`A → B`, type)
2. Main fields (limit/amount, policy)
3. Change graph (sparkline)
4. Actions (Edit Limit, Edit Policy, Close TrustLine)

### 12.6. Overall Screen States

| State | Behavior |
|-------|----------|
| Empty/No-Data | GraphView shows empty state with hint |
| Loading | Skeleton elements or loader in center |
| Error | Banner at screen top, SimulationStatusIndicator in `error` state |

---

## Application Improvement Recommendations

> Below — additional improvements that can be implemented after MVP. Basic simplifications (one equivalent, JSON configuration, simplified UI etc.) already embedded in corresponding document sections.

### Implementation Priorities

| Phase | Functionality | Complexity |
|-------|---------------|------------|
| 1 | Participant graph (viewing) + basic filters | Low |
| 2 | Node/edge details in right panel | Low |
| 3 | Simulation launch (random market) | Medium |
| 4 | Real-time metrics | Medium |
| 5 | Additional scenarios and profiles | High |

### Architectural improvements (after MVP)

1. **Separate scenario generator from visualization** — simulator can be Python CLI utility that generates traffic. Visualizer — separate web application that reads state through API.

2. **WebSocket for real-time updates** — instead of polling use WebSocket for instant graph updates on changes.

3. **Graph state caching** — store last network state in Redis/memory for quick visualizer query response.

### UX improvements (after MVP)

1. **First run hints** — short onboarding (3–4 steps) explaining what trustline, debt, clearing are.

2. **"Focus on participant" mode** — when selecting node automatically hide unconnected nodes (ego-graph mode).

3. **Payment animation** — when executing payment show "flow" animation along route.

4. **Snapshot comparison** — ability to compare network state at two time points (diff-view).

---

## Conclusion

This document describes full-featured simulator-visualizer for GEO network. For MVP recommended to start with basic graph and gradually add functionality as needed.

**Minimum viable product:**

1. Web page with participant graph
2. Right panel with details
3. "Run simulation" button (random market)
4. 3–4 key metrics

This can be implemented in 2–3 weeks using React + react-force-graph + MUI.