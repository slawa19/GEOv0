Общий контекст (вставлять в начало каждого запроса)
CONTEXT (GEO): game-like tactical map UI (not admin dashboard). Deep space dark background. Trust network graph: 35–50 nodes, 2–4 links per node. Node types: business = emerald square, person = blue circle. Trustlines: thin slate, semi-transparent, no numbers/labels on edges. Selection highlights only incident links; other links dim. Balance glow only on focused node: credit cyan, debt orange, near-zero neutral white/slate. Minimal HUD: bottom panel buttons “Single Tx” and “Run Clearing”. Clean, readable, conservative, subtle bloom/glow, small particles only; no minimap, no camera controls UI, no admin dashboard aesthetics.

Скриншот A — общий вид сети (idle)
PROMPT A:
Game UI screenshot, 16:9 1920×1080. Use CONTEXT (GEO). Idle state: show full network centered, soft conservative bloom on nodes, trustlines very faint. No sparks, no active tx/clearing. Bottom HUD panel with buttons “Single Tx” and “Run Clearing”.

Скриншот B — выбранный узел + карточка участника
PROMPT B:
Game UI screenshot, 16:9. Use CONTEXT (GEO). Select one node center-right: keep its base type color/shape, add cyan glow (credit). Highlight only incident trustlines brighter; all other links almost invisible. Add glassmorphism info card near node: icon(type), Name: “Aurora Market”, Balance: “+1250 GC”. Bottom HUD with “Single Tx”, “Run Clearing”.

Скриншот C — Tx по одной линии
PROMPT C:
Game UI screenshot, 16:9. Use CONTEXT (GEO). Active transaction A→B on a single trustline: that line becomes bright shimmering. One spark (white core + cyan trail) moves A to B. On arrival B flashes briefly (not explosive). Floating label near B rises/fades: “125 GC”. Rest of network subdued. Bottom HUD with “Single Tx”, “Run Clearing”.

Скриншот D — Tx multi-hop (A→B→C→D)
PROMPT D:
Game UI screenshot, 16:9. Use CONTEXT (GEO). Multi-hop route of 4 nodes A→B→C→D (3 edges). Route edges moderately highlighted; currently active edge is bright shimmering. Single spark moves step-by-step A→B then B→C then C→D. D flashes briefly on arrival. Floating label near D rises/fades: “125 GC”. Optional top status line: “Nodes 40 | Links 90 | Active Tx 1”. No edge numbers.

Скриншот E — clearing на одном ребре (локальный)
PROMPT E:
Game UI screenshot, 16:9. Use CONTEXT (GEO). Clearing/netting on one trustline: two sparks move toward each other on same line (cyan = credit, orange = debt). Line bright shimmering during motion. Sparks meet at midpoint: compact flash + tiny particle dust (no explosion). Floating midpoint label rises/fades: “300 GC”. After event line returns to faint idle. Bottom HUD: “Single Tx”; “Run Clearing” disabled.

Скриншот F — cycle clearing 5 узлов (A-B-C-D-E-A)
PROMPT F:
Game UI screenshot, 16:9. Use CONTEXT (GEO). Highlight a 5-node cycle A-B-C-D-E-A; dim non-cycle links. On each cycle edge, two sparks move toward each other (cyan credit, orange debt), edges bright shimmering; compact flashes + tiny dust at midpoints. One floating label near cycle center rises/fades: “300 GC”. After event cycle returns to faint idle. Bottom HUD: “Single Tx”; “Run Clearing” disabled. Optional top status: “Clearing: running”.


Лучший выбор
DALL·E 3 HD — обычно самый надежный по читаемому тексту, аккуратной верстке, “дизайнерскому” виду мокапов.
ideogram 2.0 / ideogram 2.0 Turbo — один из лучших именно для UI с текстом (кнопки, лейблы, табы), часто точнее по надписям, чем диффузионные.
SDXL v1.0 — сильный универсал для концептов UI и стилизации; текст/мелкие надписи часто “плывут”, но композиции и элементы получаются хорошо.
Когда нужен максимум “визуала”, а не текста
FLUX 1.1 [pro] / Ultra — отлично для современного polished-арта, материалов, освещения, эффектов; мелкий текст и пиксель-перфект может быть слабее, чем у DALL·E/Ideogram.
Я бы реже выбирал для UI
SD 3.0 Medium, SD 3.5 Large / Large Turbo — могут быть хороши, но для “чистой” типографики и ровной сетки чаще менее предсказуемы, чем DALL·E/Ideogram (зато иногда хороши для отдельных UI-ассетов без текста).
Практическая рекомендация
Экраны/мокапы с кнопками и надписями: Ideogram 2.0 (Turbo) или DALL·E 3 HD
Стилизация/концепт интерфейса без критичного текста: FLUX 1.1 Ultra или SDXL