# Simulator fixtures

This folder contains canonical `scenario.json` examples and the input schema for the simulator.

- `scenario.schema.json` — JSON Schema (MVP).
- `minimal/scenario.json` — smallest valid scenario.
- `golden-7_2-like/scenario.json` — example aligned with the illustrative structure from `docs/ru/simulator/backend/GEO-community-simulator-application.md` (7.2).
- `greenfield-village-100/scenario.json` — seed scenario derived from canonical admin fixtures (`admin-fixtures/v1`).
- `riverside-town-50/scenario.json` — compact seed scenario generated from `admin-fixtures/tools/generate_seed_riverside_town_50.py` logic.
- `negative/` — invalid examples for schema/validator tests.

Regenerate seed scenarios:
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe scripts/generate_simulator_seed_scenarios.py`
