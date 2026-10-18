
This folder contains meta docs for simulator fixtures.
Fixtures live under: public/simulator-fixtures/v1/<EQ>/...

For demo-fast-mock v2, the active EQ is UAH.

<EQ>/snapshot.json is the offline source's snapshot (the scene of the offline screen, the QA stand and the visual
baselines). It is a static asset of 029 S4 and is not touched by the recordings below.

Event playlists (<EQ>/events/demo-tx.json, demo-clearing.json) are played by the offline effects workbench (042 T4205).
Since 2026-10-18 (spec 039, T3935) they are RECORDED from a live run, not written by hand, and each non-empty playlist
<id>.json carries its own start snapshot <id>-snapshot.json: the run's graph just before the playlist's first event.
The workbench shows that snapshot while the playlist plays and returns to <EQ>/snapshot.json when the playback ends.
The money of every recording is checked against its own start snapshot by
src/composables/usePlaylistWorkbench.test.ts (every leg moves the debt by the amount, balances -amount / +amount / 0,
a clearing moves no balance, cleared_amount = the sum over its cycles).

How UAH was recorded (2026-10-18, run run_20261018_171301_af036628):
- scenario greenfield-village-100-realistic-v2 (fixtures/simulator/), real mode, intensity 0, paused at once: no
  automatic payment and no automatic clearing during the recording;
- 20 Interact payments of the fixed plan in scripts/record_workbench_playlists.py (PLAN: payer, receiver, amount),
  then one Interact clearing;
- demo-tx-snapshot.json: GET /api/v1/simulator/runs/{id}/graph/snapshot?equivalent=UAH before the first payment
  (all debts zero); demo-tx.json: the 20 tx.updated events as the SSE stream sent them;
- demo-clearing-snapshot.json: the snapshot after the last payment; demo-clearing.json: the clearing.done event
  (3 cycles, 247.95; two cycles share the line p1 -> p2).
Nothing recorded is edited: ids (<run_id>:p<N>, programme 044), event ids, timestamps and numbers are the server's.

How to re-record (any machine with PostgreSQL; nothing of the run is kept in the repository):
  1. a migrated empty database and a backend with Interact on and no automatic clearing, e.g. (PowerShell):
       $env:ENV='dev'; $env:DATABASE_URL='postgresql+asyncpg://geo:geo@127.0.0.1:5432/geov0_dev_<slug>'
       .venv\Scripts\python.exe scripts\dev_database.py create
       .venv\Scripts\python.exe -m alembic -c migrations/alembic.ini upgrade head
       $env:ADMIN_TOKEN='<a token>'; $env:JWT_SECRET='<a secret>'; $env:SIMULATOR_ACTIONS_ENABLE='1'
       $env:SIMULATOR_CLEARING_EVERY_N_TICKS='100000'; $env:SIMULATOR_STATE_DIR='.local-run/<slug>/simulator'
       .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port <port>
  2. $env:GEO_ADMIN_TOKEN='<the same token>'
     .venv\Scripts\python.exe scripts\record_workbench_playlists.py --base-url http://127.0.0.1:<port> `
         --out simulator-ui/v2/public/simulator-fixtures/v1/UAH/events
  3. npm --prefix simulator-ui/v2 run test:unit (the consistency guard judges the new files).
What is reproducible: the scenario, the plan (payers, receivers, amounts) and the clearing request. What is not: the
run id and so every participant id, the timestamps, and the routes - the engine may choose another route of the same
length between recordings (seen on 2026-10-18), so the line numbers and the clearing's cycles and amount can differ.

EUR and HOUR: their playlists are [] (not offered) since 2026-10-18: no shipped scenario carries those equivalents, and
a custom uploaded scenario would not be the product as it is. Their snapshot.json files stay (the fixture guard
tooling-tests/portable/test_p029_s4_demo_fixtures_of_every_equivalent_are_current.py needs one per equivalent).
