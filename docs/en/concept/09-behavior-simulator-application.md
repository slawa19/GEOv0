# GEO — User Behavior Simulator Application

> Application specification for simulating, testing and visualizing the GEO network

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Main simulator functionality](#2-main-simulator-functionality)
   - 2.1. ["World" configuration](#21-world-configuration-network-generation)
   - 2.2. [Behavioral scenarios](#22-behavioral-scenarios-interaction-models)
   - 2.3. [Load management](#23-load-management)
   - 2.4. [Interaction with core API](#24-interaction-with-real-core-api)
3. [Visualization and analytics](#3-visualization-and-analytics)
   - 3.1. [Trust and debt network graph](#31-trust-and-debt-network-graph)
   - 3.2. [Aggregated metrics](#32-aggregated-metrics-over-time)
   - 3.3. [Protocol efficiency indicators](#33-protocol-efficiency-indicators)
   - 3.4. [Replay mode](#34-replay-mode)
4. [Settings and scenarios for specification](#4-settings-and-scenarios-for-specification)
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
   - 8.2. [Trust line and debt actions](#82-trust-line-and-debt-actions)
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
    - 12.6. [Overall screen states](#126-overall-screen-states)
13. [AI integration into scenario and behavior simulator](#13-ai-integration-into-scenario-and-behavior-simulator)

---

## 1. Introduction

**A separate application that simulates a live community**, loads the GEO core, and visualizes the results. It is simultaneously:

- a traffic generator and behavior scenario engine;
- a load testing tool;
- a laboratory for protocol experiments (routing, clearing, trust policies).

### General idea

The application connects to the hub API as many "virtual users" (participants), creates and manages them, opens trustlines between them, generates payments, initiates/reacts to clearing. Essentially, it is a **community economy simulation** on top of the real GEO core.

**Goals:**

- verify performance and resilience of the implementation;
- verify correctness of logic (especially clearing, limits, idempotency);
- study how different trust topologies and behavior models affect:
  - netting efficiency;
  - debt/credit distribution;
  - average route length and failure frequency;
- discover bugs and bottlenecks before the system reaches real users.

---

## 2. Main Simulator Functionality

### 2.1. "World" Configuration (Network Generation)

> **MVP approach:** Start with a JSON configuration (participant list + trustline list). Add dynamic generation later.

Ability to set network parameters via JSON file:

```json
{
  "participants": [
    { "id": "p1", "name": "Ivan", "type": "person" },
    { "id": "p2", "name": "Coffee Shop", "type": "business" }
  ],
  "trustlines": [
    { "from": "p1", "to": "p2", "limit": 1000 }
  ]
}
```

Configuration parameters:

- number of participants;
- participant types (regular, "central" nodes, hubs, etc.);
- trustline list with limits;
- **single equivalent** (e.g., `UAH`) — sufficient for MVP.

The simulator can:

- **load ready JSON scenarios** (primary mode for MVP);
- automatically generate a network (extended mode, later).

### 2.2. Behavioral Scenarios (Interaction Models)

Models describing how participants behave over time:

- **"random market"** — participants randomly select each other and perform payments;
- **"cluster exchange"** — more intensive exchange within subgroups, rare between them;
- **"client-supplier"** — some participants mostly sell, others mostly buy;
- **stress scenarios:**
  - activity spikes;
  - some participants being disabled (`suspended`);
  - sharp changes in trustline limits.

Models should be configurable:

- transaction intensity (how many payments per second/minute/hour);
- payment amount distribution;
- probability of trustline changes (increasing/decreasing limits, closure).

### 2.3. Load Management

> **MVP approach:** A single "intensity" slider (0–100%) instead of multiple profiles.

Ability to dynamically change:

- total number of active "virtual users";
- **simulation intensity** — one slider, 0–100%:
  - 0% — simulation paused;
  - 25% — quiet day;
  - 50% — normal load;
  - 75% — peak hour;
  - 100% — stress test.

Operation type ratio (for extended mode):

- percentage of payments;
- percentage of trustline changes;
- percentage of explicitly initiated clearings.

### 2.4. Interaction with Real Core API

The simulator must **not** access the DB directly, but use the same interfaces as real clients:

- participant registration via API;
- trustline creation via API;
- sending `PAYMENT_REQUEST` and waiting for the result;
- monitoring transaction states.

This guarantees that we test exactly the protocol layer and logic, not just internal functions.

---

## 3. Visualization and Analytics

A very important part is **visually showing what is happening in the network**.

### 3.1. Trust and Debt Network Graph

- **Nodes** — participants;
- **Edges:**
  - trustlines (limits);
  - current debts on top of them.

Edge color/thickness:

- limit magnitude;
- utilization degree (debt/limit).

Capabilities:

- enable/disable display of trust/debt;
- highlight routes of specific payments;
- show which edges most often participate in clearing.

### 3.2. Aggregated Metrics Over Time

> **MVP approach:** Instead of 10+ metrics, 4–5 key ones are enough.

**Key metrics for MVP:**

| Metric                | Purpose                                   |
|-----------------------|-------------------------------------------|
| Total debt volume     | Understand system scale                   |
| % successful payments | Assess operability                       |
| Average route length  | Assess network efficiency                |
| Clearing volume       | Assess netting performance               |
| Top‑5 bottlenecks     | Find overloaded/risky participants       |

**Extended metrics (after MVP):**

- route length distribution (and maximum);
- failure frequency by reasons:
  - no route;
  - insufficient limit;
  - 2PC timeout;
- time-series graphs, ability to rewind/view history.

### 3.3. Protocol Efficiency Indicators

- **Netting efficiency** — ratio of clearing sum to total sum of all payments;
- **Debt concentration** — which participants become bottlenecks (many debts converge on them);
- **Network stability** — how often the system ends up in states where new payments "don’t go through" due to limit overload on critical edges.

### 3.4. Replay Mode

> ⚠️ **Not for MVP** — requires complex state serialization. Implement after basic functionality.

- Ability to record a scenario (or fragment of simulation);
- Then play it back at different speeds;
- Compare how different protocol/algorithm versions would behave.

---

## 4. Settings and Scenarios for Specification

For formal specifications, the application can support:

- **Simulation core:**
  - scenario engine (simple script language / DSL or configuration file);
  - event generator (tick-based or event-based).

- **Scenario parameters:**
  - network topology generation (number of nodes, density, limit distribution);
  - behavior profiles (see 2.2);
  - event schedule (e.g., change of parameters every N minutes).

- **Experiment reproducibility:**
  - fixed randomness seed;
  - saving configurations and results to be able to repeat or compare runs.

- **Support for multiple GEO backend versions:**
  - ability to run simulations against different core instances (e.g., v0.1, v0.2).

Such application becomes not just a testing tool, but a **research lab** for GEO protocol development.

---

## 5. Visualization Concept

The visual application can have **two main modes**:

1. **"Socio-economic radar"** — overview of the community:
   - who is who,
   - who is connected to whom,
   - how value flows.

2. **"Participant workstation"** — view from the perspective of an individual user (or role):
   - my trustlines;
   - my debts and credits;
   - my actions (payments, limit changes, clearing consent, etc.).

Both modes can be available:

- in a browser (web),
- in a native/cross-platform client.

---

## 6. "Community Map" Screen

### 6.1. Screen Layout

- **Center** — interactive graph:

  - nodes = participants,
  - edges = trust/debt relationships.

- **Left sidebar** — filters and layers:

  - show/hide:
    - trustlines,
    - debts;
  - choose equivalent;
  - basic participant filters.

- **Right sidebar** — details of selected element:

  - if a participant is selected: profile card, aggregates, possible actions;
  - if a connection is selected: trustline and debt details.

- **Top bar** — visualization modes and simulation status:

  - "Network" / "Metrics" / (later) "Replay";
  - connection and simulation status indicators.

### 6.2. Participant Display (Nodes)

Each participant is **icon + label**:

- **Shape/icon** ~ type:

  | Type           | Shape       |
  |----------------|-------------|
  | `person`       | circle      |
  | `business`     | square      |
  | `hub`          | hexagon     |

- **Color** ~ status:

  | Status       | Color   |
  |--------------|---------|
  | `active`     | green   |
  | `suspended`  | yellow  |
  | `left`       | gray    |
  | "risky"      | e.g. red border |

On hover: tooltip (name, role, number of connections). On click: detailed info in right sidebar.

### 6.3. Connection Display (TrustLines and Debts)

Between participants:

- **TrustLines**:
  - thin lines with arrow `from → to`;
  - thickness or saturation ~ limit.

- **Debts**:
  - thicker, more vivid lines;
  - direction: from debtor to creditor;
  - intensity ~ debt amount.

User can:

- toggle trustline / debt layers;
- highlight all connections of a particular node (ego-graph).

---

## 7. Participant Grouping

### 7.1. By Types and Roles

Grouping by:

- type (`person`, `business`, `hub`);
- role (e.g., "coordinator", "supplier", "bulk buyer").

Visually:

- colors/icons;
- ability to filter and highlight groups.

### 7.2. By Trust Clusters

> ⚠️ **MVP approach:** no automatic clustering. Start with manual grouping by types (7.1).

In extended mode:

- automatic cluster detection in graph:

  - tightly connected node groups;
  - local "communities inside community".

Visual:

- nodes in same cluster share background/frame;
- "collapse clusters" mode to super-nodes.

### 7.3. By Financial Indicators

Filters/gradients:

- by net balance (positive/negative);
- by activity (operations per time window).

Enables:

- identifying bottlenecks (high volume and debt);
- spotting high credit risk participants.

---

## 8. User Actions

### 8.1. Participant (Node) Actions

**Regular participant** clicking another node can:

- **open profile** — see public data and trust/debt relations;
- **create/modify trustline** — set limit, choose equivalent, policy;
- **initiate payment** — specify amount and equivalent;
- **view interaction history** — list of `PAYMENT`, `TRUST_LINE_*`, `CLEARING` operations.

**Administrator/coordinator** can additionally:

- change status (`suspended`, `left`);
- view risk flags and anomalies;
- open dispute management tools.

### 8.2. Trust Line and Debt Actions

On edge click, show:

- trustline details `A→B`: limit, policy, created/updated;
- debt details `B→A`: amount, history.

**Regular participant** (if it’s their trustline):

- can change limit/policy;
- can close trustline (subject to protocol rules).

**Administrator**:

- gets more technical data;
- can initiate analytics/corrections (as allowed by governance).

---

## 9. "Payment Routes and Clearing" Mode

For transparency:

- selecting a payment (`tx_id`) highlights its **route**:

  - nodes and edges involved;
  - arrows showing debt flow.

**Clearing:**

- selecting a `CLEARING` transaction:

  - highlights the cycle (e.g. `A → B → C → A`);
  - for each edge: debt "before" and "after" (how much was cleared).

---

## 10. Aggregated Participant List Mode

A table/list view:

- columns:

  - name,
  - type,
  - net balance,
  - incoming/outgoing trustline count,
  - activity/risk indicators.

Actions:

- filtering, sorting;
- click → detail view and highlight on graph;
- bulk operations (for admins): export, notifications, etc.

---

## 11. Recommended Technical Stack

### 11.1. Main Stack (React)

For a web analytical/simulation tool, React offers:

- mature ecosystem;
- lots of ready-made components;
- very good support from AI agents (React+TS is "native language" for many models).

**Proposal:**

| Layer       | Technology            |
|-------------|-----------------------|
| Language    | TypeScript            |
| UI          | React                 |
| UI kit      | MUI (Material UI)     |
| Network graph | `react-force-graph-2d` |
| Charts      | `Recharts`            |
| API         | React Query (TanStack)|
| Bundling    | Vite                  |

Constraints:

- **Graph size** — above ~100 nodes switch to simplified mode (aggregates, lists).
- **Update frequency** — throttle to 2–3 redraws/s for smooth UX.
- **Offline** — work on saved snapshot.

### 11.2. Alternative Stack (Python)

If you want to avoid JS entirely:

**Dash (Plotly Dash):**

- layout + callbacks in Python;
- `dash-cytoscape` for graph;
- `plotly` for charts.

**Streamlit:**

- very simple configuration;
- good for prototypes and ad-hoc analysis;
- for complex graph needs more custom work.

---

## 12. "Community Map" Screen UI Specification

Compact but detailed specification for code generation (React/Flutter).

### 12.1. General Screen Structure

The screen is divided into 4 main areas (AI functionality is integrated so it doesn’t overload the central canvas):

1. **Top Bar** — navigation, modes, simulation status and AI quick actions (explain, suggest stress test, open prompt panel).
2. **Left Sidebar** — filters, layers, scenario/simulation selection and **AI scenario panel**: natural language input, preview of generated scenarios and patches, request history.
3. **Main Canvas** — central graph canvas, with AI overlays on top (bottlenecks, risk areas, stress previews).
4. **Right Sidebar** — detail panel for selection, including an **"AI Insight"** tab with local explanations and recommended actions for selected node/edge/cluster.

```text
CommunityMapPage
├── TopBar
└── Layout
    ├── LeftSidebar
    │   └── AiScenarioPanel
    ├── MainCanvas
    │   └── GraphView
    └── RightSidebar
        └── DetailsPanels (incl. AI Insight)
```

### 12.2. Top Bar

**Component: `TopBar`**

| Element         | Component                 | States                                 |
|-----------------|---------------------------|----------------------------------------|
| Logo            | `AppLogo`                | normal, compact                        |
| View modes      | `ViewModeTabs`           | `activeTab: 'Network' \| 'Metrics'` (⚠️ Replay — not for MVP) |
| Status          | `SimulationStatusIndicator` | `connectionStatus`, `simulationStatus` |
| Simulation ctrl | `SimulationControls`      | Start/Stop/Pause/Resume/Speed          |
| AI quick actions| `AiQuickActions`          | `canExplain`, `canSuggestStress`, `isBusy` |

**`AiQuickActions` — example buttons:**

- `ExplainCurrentView` — ask AI to explain current state of visible graph (`/ai/explain-current-state`).
- `SuggestStressScenarios` — ask AI for 1–3 relevant stress scenarios for current world (`/ai/generate-stress-scenarios`).
- `OpenAiPanel` — expand AI scenario panel in left sidebar and focus text input.

**Hotkeys (UX improvement):**

| Key   | Action                                                     |
|-------|------------------------------------------------------------|
| Space | Pause/resume simulation                                    |
| R     | Reset scenario                                             |
| + / - | Increase/decrease simulation speed                         |
| Esc   | Clear node/edge selection / close modal-like panels       |
| E     | Request AI explanation of current state (`ExplainCurrentView`) |
| T     | Ask AI to suggest stress scenarios (`SuggestStressScenarios`) |

### 12.3. Left Sidebar

**Component: `LeftSidebar`**

Internal structure:

- `QuickStartBanner` — ⭐ UX improvement: on first run suggests loading demo scenario with 10–20 participants.
- `ScenarioSelector` — simulation scenario selection (including AI-generated ones).
- `AiScenarioPanel` — interaction panel with AI "scenario writer".
- `LayerToggles` — layer toggles.
- `FiltersPanel` — participant filters.
- `ExportButton` — graph export (PNG/SVG).

**`AiScenarioPanel` content:**

- `AiPromptInput` — multiline text field:
  - placeholder: "Describe the world, behavior or changes you want to simulate…"
  - buttons:
    - `GenerateScenario` — create new scenario from scratch;
    - `ApplyPatch` — apply changes to current scenario.
- `AiSummaryBox` — brief summary of last AI response:
  - participant/group counts;
  - key behavior profiles and shares;
  - short description of suggested changes/events.
- `AiRequestsHistory` — compact list of last 3–5 requests/responses:
  - click item → expand details (JSON diff, graph preview).
- `AiStatusBar` — indicator:
  - `idle` / `thinking` / `error`;
  - in case of error display short message from AI/validator.

**`AiScenarioPanel` state:**

```ts
ai.state: 'idle' | 'thinking' | 'error';

ai.lastRequest?: {
  text: string;
  type: 'scenario' | 'patch';
  createdAt: string;
};

ai.lastResultSummary?: {
  participantsCount: number;
  groupsCount: number;
  mainProfiles: Array<{ id: string; share: number }>;
  mainNotes: string[];
};
```

**`LayerToggles` state:**

```ts
showTrustLines: boolean;
showDebts: boolean;
showPaymentRoutes: boolean;
showClusters: boolean;
showAiOverlays: boolean; // AI-generated highlights and overlays
```

**`FiltersPanel` state:**

```ts
filter.types: { person: boolean; business: boolean; hub: boolean };
filter.statuses: { active: boolean; suspended: boolean; left: boolean };
filter.equivalentCode: string | 'ANY';
filter.netBalanceRange: [number, number];
filter.activityRange: [number, number];
filter.aiFlags?: {
  bottleneckOnly?: boolean;      // show AI-identified bottlenecks only
  overTrustedOnly?: boolean;     // show "over-trust" participants
  stressAffectedOnly?: boolean;  // participants affected by selected stress scenario
};
```

### 12.4. Main Canvas

**Component: `GraphView`**

Wrapper around library (e.g., `react-force-graph`) with AI overlays:

```ts
type PID = string;

interface AiFlags {
  bottleneck?: boolean;      // bottleneck according to AI
  overTrusted?: boolean;     // "too trusting" node
  stressAffected?: boolean;  // affected by stress scenario
}

interface ParticipantNode {
  id: PID;
  name: string;
  type: 'person' | 'business' | 'hub';
  status: 'active' | 'suspended' | 'left';
  netBalance: number;
  activityScore: number;
  clusterId?: string;
  aiFlags?: AiFlags;
}

interface LinkEdge {
  id: string;
  from: PID;
  to: PID;
  kind: 'trustline' | 'debt';
  equivalent: string;
  limit?: number;    // for trustline
  amount?: number;   // for debt
  utilization?: number;
  aiFlags?: {
    bottleneck?: boolean;
    stressAffected?: boolean;
  };
}

interface AiHighlight {
  nodeIds?: PID[];
  linkIds?: string[];
  reason?: string; // short textual explanation for legend/tooltip
}
```

**Props:**

- `nodes: ParticipantNode[]`
- `links: LinkEdge[]`
- `viewMode: 'trustlines' | 'debts' | 'combined'`
- `highlightedNodeId?: PID`
- `highlightedLinkId?: string`
- `aiHighlight?: AiHighlight` — current AI highlight (bottlenecks, focus area, stress preview)
- `onNodeClick(nodeId: PID)`
- `onLinkClick(linkId: string)`
- `onBackgroundClick()`
- `onAreaSelect?(bounds: { x1: number; y1: number; x2: number; y2: number })` — area selection for local AI explanation

**Node visual states:**

| Type           | Shape   |
|----------------|---------|
| `person`       | circle  |
| `business`     | square  |
| `hub`          | hexagon |

| Status     | Color  |
|------------|--------|
| `active`   | green  |
| `suspended`| yellow |
| `left`     | gray   |

Additional AI flags (on top):

- `bottleneck` — thicker border, "⚠" icon in tooltip;
- `overTrusted` — extra ring or special marker;
- `stressAffected` — semi-transparent fill or pulsing when previewing stress plan.

### 12.5. Right Sidebar

**Component: `RightSidebar`**

Subcomponents:

- `NodeDetailsPanel` — when a node is selected.
- `LinkDetailsPanel` — when a connection is selected.
- `AiInsightPanel` — tab with AI explanation and recommendations for selected element/area.
- `EmptySelectionPanel` — when nothing is selected.

**`NodeDetailsPanel` structure:**

1. Profile header (name, type, status).
2. Key metrics (net balance, trustline count).
3. Connections list (tabs: TrustLines / Debts).
4. Actions (Create TrustLine, Initiate Payment, View History).
5. AI risk indicators (if `aiFlags` present):
   - badges: "Bottleneck", "Over-trust", "Stress-affected".

**`LinkDetailsPanel` structure:**

1. Header (`A → B`, type).
2. Main fields (limit/amount, policy).
3. Change graph (sparkline).
4. Actions (Edit Limit, Edit Policy, Close TrustLine).
5. AI analytic marker (if edge is bottleneck or critical route).

**`AiInsightPanel` structure:**

1. Header (`AI Insight for [node/edge/area]`).
2. Text explanation from AI (`/ai/explain-current-state` for selected context).
3. Key factors:
   - contribution to overall load;
   - role in failures/successes;
   - involvement in stress scenarios.
4. **AI-proposed action plans** (if crisis recommendations exist, see 13.7):
   - list of cards with short descriptions and expected impact;
   - buttons: "Run plan simulation" and "Apply to current scenario".
5. Manual recommendations (if plans are not applied automatically):
   - "Reduce limits on these trustlines";
   - "Add alternative routes around this node";
   - "Reassign behavior for part of participants" (with link to `AiScenarioPanel`).

### 12.6. Overall Screen States

| State                 | Behavior |
|-----------------------|----------|
| Empty/No‑Data         | GraphView shows empty state with hint, `AiScenarioPanel` suggests loading demo scenario or generating one via AI |
| Loading               | Skeletons or loader, TopBar shows `simulationStatus = 'loading'` |
| Error                 | Error banner, `SimulationStatusIndicator = 'error'`; `AiScenarioPanel` can show AI/validation error details |
| AI request in progress| `AiScenarioPanel` and `AiQuickActions` show `thinking`, some buttons disabled until completion |
| AI overlay active     | Graph highlighted via `aiHighlight`, legend/right panel shows which overlay is active (bottlenecks, stress preview, etc.) |
| Action plan selected  | `AiInsightPanel` shows plan details, `MainCanvas` can show preview of patch effects before applying |

---

## Application Improvement Recommendations

> Below are improvements that can be implemented after MVP. Basic simplifications (single equivalent, JSON configuration, simplified UI, etc.) are already embedded into sections above.

### Implementation Priorities

| Phase | Functionality                               | Complexity |
|-------|---------------------------------------------|------------|
| 1     | Participant graph view + basic filters      | Low        |
| 2     | Node/edge details in right panel            | Low        |
| 3     | Simulation launch (random market)           | Medium     |
| 4     | Real-time metrics                           | Medium     |
| 5     | Additional scenarios and behavior profiles  | High       |

### Architectural Improvements (after MVP)

1. **Separate scenario generator from visualizer** — simulator can be a Python CLI tool generating traffic, visualizer — a separate web app reading state via API.
2. **WebSocket for real-time updates** — instead of polling, use WebSocket for immediate updates.
3. **Graph state caching** — keep last state in Redis/memory for fast responses.

### UX Improvements (after MVP)

1. **First-run hints** — short (3–4 step) onboarding explaining trustline, debt, clearing.
2. **"Focus on participant" mode** — when selecting a node, hide unrelated nodes (ego-graph).
3. **Payment animations** — show flow animation along route when a payment occurs.
4. **Snapshot comparison** — compare network state at two points in time (diff-view).

---

## 13. AI Integration into Scenario and Behavior Simulator

> Goal: allow users to describe the world, behavior and "biases" in natural language, and let AI automatically convert this into a strict simulation scenario. Also: provide explanations, stress scenarios, and **concrete crisis mitigation plans**.

### 13.1. Role of AI and Overall Architecture

AI does not replace simulation core, it acts as **a "scenario compiler"**:

- input: human description (prompt);
- output: JSON/DSL scenario with:
  - participants;
  - trustlines;
  - behavior profiles;
  - events and stress scenarios.

**Components:**

1. **Simulation Core** (already described):
   - reads scenario config (JSON/DSL);
   - runs tick-based or event-based simulation;
   - talks to real GEO core API.

2. **Scenario Config Store**:
   - stores scenarios and versions (`scenarioId`, `version`);
   - stores randomness seeds for reproducibility;
   - stores metadata: author, user prompt, AI model.

3. **AI Scenario & Behavior Engine (new service)**:
   - REST/gRPC API:
     - `POST /ai/scenario-from-text` — create scenario from description;
     - `POST /ai/patch-from-text` — generate patch for current scenario;
     - `POST /ai/explain-current-state` — textual explanation of network state and bottlenecks;
     - `POST /ai/generate-stress-scenarios` — set of stress scenarios for given world;
     - `POST /ai/recommend-crisis-actions` — crisis action recommendations based on current state and goals.
   - inside: LLM + post-processing layer to enforce JSON/DSL schema.

4. **UI (React client)**:
   - AI assistant panel:
     - text input;
     - buttons: "Generate scenario", "Apply changes", "Explain state", "Suggest stress test", "Suggest crisis plan";
   - rest of UI as in sections above (graph, filters, metrics, etc.).

### 13.2. Scenario Format (DSL/JSON) for AI

To integrate AI reliably, we need a simple formal scenario format.

Example:

```json
{
  "participants": [
    {
      "id": "p1",
      "name": "Ivan",
      "type": "person",
      "groupId": "A",
      "behaviorProfileId": "normal_consumer"
    },
    {
      "id": "h1",
      "name": "Local Store",
      "type": "hub",
      "groupId": "center",
      "behaviorProfileId": "merchant"
    }
  ],
  "trustlines": [
    { "from": "p1", "to": "h1", "limit": 1000 },
    { "from": "p2", "to": "p3", "limit": 300 }
  ],
  "behaviorProfiles": [
    {
      "id": "normal_consumer",
      "props": {
        "riskTolerance": 0.5,
        "trustPropensity": 0.5,
        "panicSensitivity": 0.3,
        "hoardingTendency": 0.4,
        "localismBias": 0.6
      },
      "rules": [
        {
          "trigger": "each_day",
          "action": "random_payment_within_cluster",
          "params": { "avgAmount": 100, "variance": 0.3 }
        }
      ]
    },
    {
      "id": "panic_prone",
      "extends": "normal_consumer",
      "props": {
        "panicSensitivity": 0.9,
        "hoardingTendency": 0.7
      },
      "rules": [
        {
          "trigger": "metric_drop(network_success_rate,0.8)",
          "action": "reduce_outgoing_payments",
          "params": { "factor": 0.5 }
        }
      ]
    }
  ],
  "groups": [
    { "id": "A", "label": "District A" },
    { "id": "B", "label": "District B" },
    { "id": "center", "label": "Central hubs" }
  ],
  "events": [
    {
      "time": "day_10",
      "type": "stress",
      "description": "Panic in district B",
      "effects": [
        { "targetGroup": "B", "applyBehaviorProfile": "panic_prone" }
      ]
    }
  ]
}
```

AI’s task from text like:

> "200 participants, three districts with strong internal trust and weak between, 5 hub stores, 10% panic-prone, periodic demand spikes for one store…"

is to fill:

- `participants` (count, types, group assignment);
- `trustlines` (strong intra-group, weak inter-group);
- `behaviorProfiles` for "normal", "panic-prone", "hoarder", etc.;
- `events` (e.g. "panic in district B on day 10").

### 13.3. UX Flows with AI

#### 13.3.1. Create Scenario from Scratch (NL → Scenario)

1. In Left Sidebar or Top Bar there is **"AI Scenario Writer"** block:

   - multiline field:
     > "Describe what you want to simulate…"
   - hint example:
     > "Simulate a city with 3 districts, about 200 participants.  
     > Strong trust within districts, weak between.  
     > 5 hub stores, one of them more expensive.  
     > 10% people are panic-prone, 20% are hoarders."

2. User clicks **"Generate scenario"**.

3. UI → `POST /ai/scenario-from-text`:

   ```json
   {
     "prompt": "…user text…",
     "constraints": {
       "maxParticipants": 300,
       "baseEquivalent": "UAH"
     }
   }
   ```

4. AI service:

   - generates scenario (like example above);
   - returns:
     - `scenario` (JSON);
     - `summary` — short summary.

5. UI:

   - displays summary:
     - "Participants: 212 (person: 190, business: 17, hub: 5)"
     - "Groups: A, B, C; strong intra-group trust"
     - "Profiles: normal_consumer (70%), hoarder (20%), panic_prone (10%)"
   - shows graph via `GraphView`;
   - offers **"Run simulation"**.

User does **not** manually tweak dozens of knobs — natural language description is enough for starting.

#### 13.3.2. Interactive Changes During Simulation

While simulation runs, user can:

- change behavior:
  > "Make district C more clannish and add a couple of speculators at the border between districts A and B."

- change structure:
  > "Cut trust limits between districts to 50%, but keep intra-district limits unchanged."

UI sends this to `POST /ai/patch-from-text`, the result is a JSON patch (see 13.4).

---

### 13.4. Patches from Text (Dynamic Scenario Adjustment)

**Goal:** Let users adjust the world using short textual commands, without rewriting entire scenarios.

#### 13.4.1. Patch Format

Logical patch before expansion:

```json
{
  "op": "update",
  "participants": [
    {
      "filter": { "groupId": "C", "type": "person" },
      "set": { "behaviorProfileId": "clan_localist" }
    }
  ],
  "trustlines": [
    {
      "filter": { "fromGroup": "A", "toGroup": "B" },
      "scaleLimitBy": 0.5
    }
  ],
  "behaviorProfiles": [
    {
      "id": "clan_localist",
      "props": {
        "localismBias": 0.95,
        "trustPropensity": 0.3
      },
      "rules": [
        {
          "trigger": "each_day",
          "action": "prefer_payments_within_group",
          "params": { "probability": 0.9 }
        }
      ]
    }
  ]
}
```

AI expands filters into actual entity IDs and stores new scenario version.

#### 13.4.2. Example: Text → Patch

User text:

> "Increase clannishness in district C and reduce trust limits between C and others by 30%."

AI service:

- creates/updates `behaviorProfile` `clan_localist`;
- assigns it to all `participants` with `groupId="C"`;
- scales all trustlines connecting C with others by 0.7.

From user perspective:

- they type one short phrase;
- 1–2 seconds later graph changes (inter-district links thin out, C nodes change styling).

---

### 13.5. Explaining Current State ("Why is this a bottleneck?")

**Goal:** Provide AI commentary on top of existing visualization and metrics.

#### 13.5.1. API

`POST /ai/explain-current-state`:

```json
{
  "snapshot": {
    "nodes": [...],
    "links": [...],
    "metrics": {
      "successRate": 0.76,
      "avgRouteLength": 3.2,
      "totalDebt": 120000,
      "clearingVolume": 45000,
      "bottlenecks": [
        { "participantId": "h1", "reason": "high_centrality_and_debt" },
        { "participantId": "p57", "reason": "over_trusted" }
      ]
    }
  },
  "question": "Why do we have so many failures and where are the bottlenecks?"
}
```

`snapshot` can be:

- aggregated (metrics + top nodes/edges only);
- or focused (single district/cluster).

#### 13.5.2. Example AI Answer

> 1. Participant **h1 (major store)** is a clear bottleneck:  
>    - 34% of all routes go through it,  
>    - it accumulates 41% of total debt,  
>    - many 3+ hop routes pass via this node.  
>    This increases failure probability when its limits are exhausted.  
>
> 2. Participant **p57** shows "over-trust" behavior:  
>    - opened 12 high-limit trustlines,  
>    - is debtor on 9 of them.  
>    This creates local instability risk.  
>
> 3. Payment failures mostly come from:  
>    - insufficient limit on `cluster B ↔ cluster C` edges,  
>    - no alternative routes bypassing `h1`.

UI can show this as:

- AI text report;
- highlight of bottlenecks and problematic edges on graph.

---

### 13.6. Auto-Generation of Stress Scenarios

**Goal:** Offer ready "buttons" for typical shocks to test network resilience, without manual scenario crafting.

#### 13.6.1. Stress Scenario Taxonomy

AI can use a set of patterns:

- **Liquidity shock:**
  - participants suddenly reduce limits;
  - increased hoarding tendency.

- **Panic episode:**
  - sharp trust drop in a cluster;
  - mass trustline closures.

- **Hub failure:**
  - 1–2 major hubs become `suspended`;
  - route redistribution.

- **Credit crunch:**
  - global limit reduction;
  - more "no route/insufficient limit" failures.

#### 13.6.2. API

`POST /ai/generate-stress-scenarios`:

```json
{
  "baseScenario": { "...": "..." },
  "preferences": {
    "maxScenarios": 3,
    "focus": "trust_network_stability"
  }
}
```

Response:

```json
{
  "scenarios": [
    {
      "id": "stress_liquidity_shock_cluster_B",
      "label": "Liquidity shock in cluster B",
      "description": "Sharp limit reduction and increased hoarding in district B.",
      "patch": {
        "events": [
          {
            "time": "day_15",
            "type": "stress",
            "description": "Liquidity shock in cluster B",
            "effects": [
              {
                "targetGroup": "B",
                "applyBehaviorProfile": "hoarder",
                "scaleTrustlinesBy": 0.6
              }
            ]
          }
        ]
      }
    },
    {
      "id": "stress_hub_failure_h1",
      "label": "Failure of main hub h1",
      "description": "Hub h1 disabled for 5 days.",
      "patch": {
        "events": [
          {
            "time": "day_10",
            "type": "stress",
            "description": "Hub h1 suspended",
            "effects": [
              { "targetParticipantId": "h1", "setStatus": "suspended" }
            ]
          },
          {
            "time": "day_15",
            "type": "recovery",
            "description": "Hub h1 back online",
            "effects": [
              { "targetParticipantId": "h1", "setStatus": "active" }
            ]
          }
        ]
      }
    }
  ]
}
```

#### 13.6.3. UX

In UI (e.g., on the "Metrics" view or right panel):

- section "AI-suggested stress scenarios":
  - list of cards:
    - title;
    - short description;
    - buttons: "View patch", "Run stress test".

On selection:

- graph visualizes **before/after** (e.g., ghost edges/nodes);
- user can run simulation with the event at given step.

---

### 13.7. Crisis Action Recommendations

**Goal:** Let AI not only diagnose problems and generate stress scenarios, but also propose **concrete action plans** (patches) to mitigate crisis situations at community or cluster level.

#### 13.7.1. API

`POST /ai/recommend-crisis-actions`:

```json
{
  "snapshot": {
    "nodes": [...],
    "links": [...],
    "metrics": {
      "successRate": 0.62,
      "avgRouteLength": 4.1,
      "totalDebt": 180000,
      "clearingVolume": 30000,
      "bottlenecks": [
        { "participantId": "h1", "reason": "high_centrality_and_debt" },
        { "participantId": "cluster_B", "reason": "liquidity_shortage" }
      ]
    }
  },
  "goals": {
    "targetSuccessRate": 0.8,
    "maxAvgRouteLength": 3.0,
    "timeHorizonDays": 10
  },
  "constraints": {
    "maxChangedParticipants": 50,
    "maxTrustlineChangeFactor": 0.5,
    "allowSuspendHubs": true
  }
}
```

- `snapshot` — current state and metrics (as in 13.5), possibly with active stress scenarios.
- `goals` — target metrics and time horizon.
- `constraints` — operational constraints:
  - how many participants can be affected;
  - max scale of limit changes;
  - whether hubs can be temporarily suspended.

**Response:**

```json
{
  "actionPlans": [
    {
      "id": "plan_diversify_h1",
      "label": "Unload hub h1 and diversify routes",
      "description": "Reduce dependency on the single major hub h1 by strengthening alternative routes and supporting peripheral nodes.",
      "patch": {
        "trustlines": [
          {
            "filter": { "to": "h1", "fromGroupNot": "center" },
            "scaleLimitBy": 0.7
          },
          {
            "filter": { "inCluster": true, "excludeHubs": true },
            "scaleLimitBy": 1.3
          }
        ],
        "behaviorProfiles": [
          {
            "id": "local_connector",
            "props": { "localismBias": 0.8, "trustPropensity": 0.6 },
            "rules": [
              {
                "trigger": "each_day",
                "action": "open_additional_intra_cluster_trustlines",
                "params": { "maxNewLinesPerDay": 2 }
              }
            ]
          }
        ],
        "participants": [
          {
            "filter": { "clusterId": "B", "type": "business" },
            "set": { "behaviorProfileId": "local_connector" }
          }
        ]
      },
      "expectedImpact": {
        "successRate": { "delta": 0.12 },
        "avgRouteLength": { "delta": -0.4 },
        "concentrationIndex": { "delta": -0.25 }
      },
      "rationale": [
        "We reduce load on h1 by lowering limits on its incoming trustlines.",
        "We strengthen intra-cluster connections so payments less often route via the central hub.",
        "We give some organizations a 'local connector' profile to stimulate alternative routes."
      ],
      "simulationPreviewId": "sim_preview_123"
    }
  ]
}
```

- `actionPlans[]` — 2–5 alternative plans:
  - `patch` — in same DSL as other patches;
  - `expectedImpact` — metric deltas;
  - `rationale` — strategy explanation;
  - `simulationPreviewId` — ID of quick what-if run (if executed).

#### 13.7.2. AI Algorithm

In `AI Scenario & Behavior Engine`:

1. Analyze `snapshot`:
   - metrics, bottlenecks, debt concentration;
   - active stress scenarios.
2. Identify crisis type:
   - hub overload;
   - local liquidity shortage;
   - excessive debt concentration;
   - frequent failures from "no route/insufficient limit";
   - panic behavior of certain groups.
3. Build intervention templates:
   - structural: limit redistribution, extra trustlines;
   - behavioral: profile changes (limit over-trust, raise localismBias, strengthen clearing rules);
   - procedural: more frequent clearing, softer throttling of risky agents.
4. Assemble concrete `actionPlans` under `constraints`.
5. (Optional) run short simulations per plan:
   - small horizon (few "days"/ticks);
   - compute metric changes;
   - feed into `expectedImpact` and `simulationPreviewId`.

#### 13.7.3. UX Integration

- In **`AiInsightPanel`** (right sidebar):
  - after "Explain state" user sees both explanation (13.5) and:
    - "AI-proposed action plans" list:
      - each card shows:
        - title;
        - short description;
        - key expected effect (e.g. `+12% successRate`, `-25% debt concentration at h1`);
        - status indicators.
      - buttons:
        - `Simulate plan` — run separate simulation with this patch (new `scenarioId`);
        - `Apply to current simulation` — send patch to backend after explicit confirmation.
- On **`Metrics`** tab:
  - button `Suggest crisis plan`:
    - calls `/ai/recommend-crisis-actions`;
    - displays returned plans as cards;
    - optionally toggles "preview" of patch in `MainCanvas`.

Important: AI **never applies patches automatically** — it only suggests, user decides.

---

### 13.8. Minimal AI Implementation Plan

1. **Define and document scenario schema** (as in 13.2):
   - `participants`, `trustlines`, `behaviorProfiles`, `groups`, `events`.

2. **Extend simulator backend with scenario APIs:**
   - `POST /scenario` — upload full scenario;
   - `POST /scenario/:id/patch` — apply patch;
   - `POST /run` — run simulation by `scenarioId`.

3. **Implement initial AI service:**
   - only `POST /ai/scenario-from-text`;
   - LLM + JSON schema validation;
   - logging: user text, generated scenario, model ID, seed.

4. **Add AI scenario panel to UI:**
   - multiline input;
   - "Generate scenario" button;
   - summary + graph preview before running.

5. **Extend AI service:**
   - add `patch-from-text`;
   - add `explain-current-state`;
   - add `generate-stress-scenarios`;
   - add `recommend-crisis-actions`.

6. **Extend UI:**
   - quick buttons: "Refine behavior", "Explain bottlenecks", "Suggest stress test", "Suggest crisis plan";
   - show AI output as overlays, filters, and explanations;
   - integrate action plans into `AiInsightPanel` and `Metrics` view with safe patch application.

---

## Conclusion

This document describes a full-featured simulator-visualizer for the GEO network. For MVP, it is recommended to start with a basic graph and gradually add functionality as needed.

**Minimum viable product:**

1. Web page with participant graph
2. Right panel with details
3. "Run simulation" button (random market)
4. 3–4 key metrics

This can be implemented in 2–3 weeks using React + `react-force-graph` + MUI.

As a next step beyond MVP, an AI integration layer (section 13) is proposed, which allows:

- defining world configuration and behaviors in natural language,
- automatically generating and modifying scenarios (patches),
- obtaining explanations of current network state and bottlenecks,
- automatically constructing stress scenarios to test protocol resilience,
- and receiving **concrete crisis-mitigation plans** that can be safely tested in simulation and partially translated into real GEO protocol policies if desired.
