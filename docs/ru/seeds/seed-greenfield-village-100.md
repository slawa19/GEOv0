# Seed: GreenField Village Community (100 participants)

**Goal**: A realistic village/hromada community seed that can be visualized on `/graph` immediately, and later “animated” by a simulator that generates transactions, trustline changes, and balance drift in real time.

## Core idea
A village-scale cooperative economy with **3–5 strong hubs** (co-op + warehouse/procurement + market + bakery/dairy), a diversified base of **local producers**, **retail/food points** where households spend weekly, and **service providers** that create a meaningful HOUR-economy.

### Constraints (MVP)
- **Equivalents**: `UAH`, `EUR`, `HOUR`
- **Participant types**: `person` and `business` (compatible with current admin-ui filters)
- The network must naturally form **cycles of 3–6 nodes** (visible clearing effects)

### Trustline direction (important)
We treat a trustline as a **risk limit**:
- `from → to` means **from is creditor**, **to is debtor** ("I can let you go negative up to this limit").
- Actual debts are separate edges in the model (`debt[debtor → creditor, E]`), but on `/graph` we are visualizing **trustlines**.

### Intended economic model (UAH-first)
- **Retail credits households**: weekly spending is modeled mostly as `retail → household` in `UAH` (buy now / pay later).
- **Producers credit buyers**: a producer selling goods on deferral is modeled as `producer → (retail | bakery | procurement)` in `UAH`.
- **Anchors provide liquidity**: co-op / warehouse / procurement act as hubs that connect clusters and may provide limited credit to stabilize flows.
- **HOUR economy**: service providers commonly credit households as `service → household` in `HOUR` (work now / settle later), with a smaller number of reverse links to keep reciprocity.
- **People have branching trust**: persons (especially households) also have a small **social trust layer** (microcredit / mutual aid) that creates realistic branching and additional clearing cycles without dominating the economy.
- **EUR is minimized**: keep only a couple of EUR links (e.g., co-op ↔ IT/translator) to demonstrate multi-equivalent support without dominating the graph.

#### Why person-to-person trustlines exist (and should branch)

If the resident subgraph looks like a single chain, it feels artificial. In a real community, `person` nodes get branching trustlines from:

1) **Direct relationships** (work and small sales): odd jobs, repairs, babysitting, garden help.
2) **Social microcredit** with **small limits**:
	- household → household ("neighbor help", "lend until payday")
	- household ↔ micro-workers (small tabs)
	- optional household → producer (prepayment / CSA-style support)

Guardrail: these links have **small limits** and often set `can_be_intermediate=false` so they add realism without turning households into the main liquidity providers.

#### Role sketches (sanity rules)
- **Bakery**: few suppliers, many buyers.
	- Suppliers (incoming trustlines to bakery): a small set of producers (e.g., grain/flour/eggs/honey) + warehouse.
	- Buyers (outgoing trustlines from bakery): households + canteen/school + grocery/market.
	- Rationale: bakery mostly *sells* to many, and only *buys inputs* from a few.
- **Grocery / retail points**: few suppliers, many household buyers.
	- Suppliers (incoming to retail): warehouse / co-op storefront / selected producers.
	- Buyers (outgoing from retail): many households (grocery/market are dominant weekly spend nodes; others are secondary).
- **Procurement desk**: buyer-of-harvest aggregator.
	- Main pattern: `producer → procurement` (deferral for the buyer).
	- Optional: procurement advances a subset of producers (small reverse links).
- **Market hall** (as a proxy for many stalls): acts like a retail aggregator.
	- Many `market → household` (weekly spending), and a few supplier links from producers/bakery/dairy.

## Role audit table (from fixtures)

Interpretation reminder: `UAH out` = how many UAH trustlines this node **extends** (acts as creditor); `UAH in` = how many UAH trustlines **others extend to it** (it can be debtor).

### Group-level degree summary (min / avg / max)

| Group | Count | UAH out | UAH in | HOUR out | HOUR in | EUR out | EUR in |
|---|---:|---:|---:|---:|---:|---:|---:|
| anchors | 10 | 0 / 8.9 / 19 | 0 / 8.8 / 26 | 0 / 0.5 / 5 | 0 / 0.5 / 5 | 0 / 0.2 / 2 | 0 / 0.2 / 2 |
| producers | 25 | 1 / 2.36 / 5 | 0 / 1.12 / 3 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| retail | 10 | 1 / 7.6 / 39 | 2 / 4.1 / 12 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| services | 15 | 1 / 1.13 / 3 | 1 / 1 / 1 | 2 / 2.33 / 3 | 0 / 0.47 / 3 | 0 / 0.13 / 1 | 0 / 0.13 / 1 |
| households | 35 | 0 / 0.46 / 2 | 2 / 2.57 / 4 | 0 / 0.2 / 1 | 1 / 1 / 1 | 0 / 0 / 0 | 0 / 0 / 0 |
| agents | 5 | 1 / 1 / 1 | 0 / 0 / 0 | 1 / 1 / 1 | 1 / 1 / 1 | 0 / 0 / 0 | 0 / 0 / 0 |

### Key nodes snapshot

| # | Participant | Group | Type | UAH out | UAH in | HOUR out | HOUR in | EUR out | EUR in |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 1 | GreenField Cooperative (Co-op) | anchors | business | 19 | 24 | 5 | 5 | 2 | 2 |
| 2 | GreenField Warehouse | anchors | business | 9 | 4 | 0 | 0 | 0 | 0 |
| 3 | GreenField Procurement Desk | anchors | business | 11 | 26 | 0 | 0 | 0 | 0 |
| 4 | Riverside Market Hall | anchors | business | 19 | 13 | 0 | 0 | 0 | 0 |
| 5 | Village Transport Co-op | anchors | business | 6 | 4 | 0 | 0 | 0 | 0 |
| 6 | SunnyHarvest Bakery | anchors | business | 13 | 9 | 0 | 0 | 0 | 0 |
| 7 | MeadowDairy Collective | anchors | business | 12 | 8 | 0 | 0 | 0 | 0 |
| 36 | Riverside Grocery | retail | business | 39 | 12 | 0 | 0 | 0 | 0 |
| 37 | Village Butcher Shop | retail | business | 6 | 6 | 0 | 0 | 0 | 0 |
| 38 | Morning Coffee Corner | retail | business | 5 | 2 | 0 | 0 | 0 | 0 |
| 39 | Family Canteen | retail | business | 6 | 5 | 0 | 0 | 0 | 0 |
| 42 | Pharmacy & Health Kiosk | retail | business | 5 | 2 | 0 | 0 | 0 | 0 |
| 43 | School Cafeteria | retail | business | 5 | 5 | 0 | 0 | 0 | 0 |
| 45 | Cooperative Storefront | retail | business | 5 | 3 | 0 | 0 | 0 | 0 |
| 53 | Mason Wright (IT Support) | services | person | 1 | 1 | 2 | 0 | 1 | 1 |
| 56 | Mia Foster (Childcare) | services | person | 1 | 1 | 2 | 2 | 0 | 0 |
| 57 | Jack Russell (Courier) | services | person | 3 | 1 | 2 | 0 | 0 | 0 |
| 61 | The Adams Family (Household) | households | person | 0 | 3 | 1 | 1 | 0 | 0 |
| 83 | Oliver Price (Odd Jobs) | households | person | 2 | 4 | 0 | 1 | 0 | 0 |
| 96 | Olivia Bennett (Community Coordinator) | agents | person | 1 | 0 | 1 | 1 | 0 | 0 |

## Participants (100)

### 1) Anchors & infrastructure (10, business)
1. GreenField Cooperative (Co-op)
2. GreenField Warehouse
3. GreenField Procurement Desk
4. Riverside Market Hall
5. Village Transport Co-op
6. SunnyHarvest Bakery
7. MeadowDairy Collective
8. Hilltop Greenhouses
9. Oak & Iron Workshop
10. Community Energy & Water Team

### 2) Producers & crafts (25, person)
11. Anton Petrenko (Grain Farmer)
12. Maria Shevchenko (Vegetable Farmer)
13. Oksana Hrytsenko (Berry Farm)
14. Dmytro Kovalenko (Honey & Apiary)
15. Iryna Bondar (Eggs & Poultry)
16. Serhii Melnyk (Goat Cheese)
17. Yulia Tkachenko (Herbal Tea)
18. Viktor Koval (Sunflower Oil Press)
19. Kateryna Marchenko (Flour Milling)
20. Oleh Savchenko (Meat Processing)
21. Nina Romanenko (Canning & Pickles)
22. Taras Klymenko (Carpentry)
23. Alina Moroz (Sewing & Alterations)
24. Bohdan Kravets (Shoemaker)
25. Svitlana Poliakova (Pottery)
26. Denys Lysenko (Welding)
27. Hanna Sydorenko (Tailor Workshop)
28. Roman Zadorozhnyi (Firewood & Timber)
29. Pavlo Rudenko (Farm Equipment Rental)
30. Olena Fedorova (Seedlings & Plants)
31. Maksym Horbach (Compost & Soil)
32. Tetiana Kovalchuk (Bread Supplies)
33. Andrii Boiko (Fishing & Smoked Fish)
34. Larysa Ivashchenko (Handmade Soap)
35. Ihor Chernenko (Metal Parts)

### 3) Retail & food (10, business)
36. Riverside Grocery
37. Village Butcher Shop
38. Morning Coffee Corner
39. Family Canteen
40. Farmers’ Corner Shop
41. HomeGoods Mini-Mart
42. Pharmacy & Health Kiosk
43. School Cafeteria
44. Weekend Street Food Stall
45. Cooperative Storefront

### 4) Services (15, person)
46. Alex Turner (General Builder)
47. Ethan Brooks (Electrician)
48. Liam Carter (Plumber)
49. Noah Bennett (Mechanic)
50. Chloe Evans (Bicycle Repair)
51. Grace Collins (Accountant)
52. Ava Reed (Legal Advisor)
53. Mason Wright (IT Support)
54. Ella Morgan (Nurse)
55. Lucas Hayes (Tutor)
56. Mia Foster (Childcare)
57. Jack Russell (Courier)
58. Amelia Scott (Hairdresser)
59. Henry Ward (Photographer)
60. Sophia Kim (Translator)

### 5) Households (35, person)
61. The Adams Family (Household)
62. The Baker Family (Household)
63. The Carter Family (Household)
64. The Davis Family (Household)
65. The Edwards Family (Household)
66. The Foster Family (Household)
67. The Garcia Family (Household)
68. The Harris Family (Household)
69. The Ivanov Family (Household)
70. The Johnson Family (Household)
71. The King Family (Household)
72. The Lewis Family (Household)
73. The Miller Family (Household)
74. The Nelson Family (Household)
75. The Owens Family (Household)
76. The Parker Family (Household)
77. The Quinn Family (Household)
78. The Robinson Family (Household)
79. The Smith Family (Household)
80. The Taylor Family (Household)
81. The Walker Family (Household)
82. The Young Family (Household)
83. Oliver Price (Odd Jobs)
84. Emily Price (Home Baking)
85. Daniel Hughes (Garden Help)
86. Lily Hughes (Babysitting)
87. Benjamin Lee (Repairs)
88. Isabella Lee (Sewing Help)
89. James Hill (Driving Help)
90. Charlotte Hill (Cooking Help)
91. William Green (Harvest Help)
92. Harper Green (Crafts)
93. Michael Wood (Handyman)
94. Evelyn Wood (Care Visits)
95. Samuel Clark (Errands)

### 6) Launch agents (5, person)
96. Olivia Bennett (Community Coordinator)
97. Daniel Stone (Trustline Officer)
98. Emma Wright (Dispute Mediator)
99. Ryan Cooper (Operations Lead)
100. Sophia Turner (Tech Steward)

## How the graph should look
- **Dense hub cluster** around the cooperative + warehouse/procurement + market + bakery/dairy.
- **Sub-clusters**: producers cluster (procurement + bakery + grocery), services/HOUR cluster (households + repair/build/childcare).
- **Bridges** between clusters (courier/transport, IT/translator, retail).

## Simulation goal (next step)
We want a deterministic simulator that:
- generates **transactions** (volume + frequency by role and cadence)
- adjusts **trustlines** (open/close/raise/lower limits based on behavior/risk)
- evolves **balances** and highlights emerging **clearing cycles**

### Suggested event stream for realtime UI
Emit events such as:
- `tx_created`, `tx_committed`
- `balance_updated`
- `trustline_opened`, `trustline_updated`, `trustline_closed`
- `participant_joined`

### Visualization tools (minimal stack change)
- Keep **Vue 3 + Element Plus + Cytoscape** for `/graph`.
- Add **FastAPI WebSocket** endpoint for streaming simulation events to the admin-ui.
- For charts on `/metrics`: start with tables + light SVG sparklines; if needed add **Apache ECharts** (one justified dependency, good Vue support).
