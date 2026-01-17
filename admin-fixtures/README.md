# Admin fixtures pack (v1)

Этот каталог содержит **каноничный набор фикстур и сценариев** для прототипирования админки (вариант B: SPA + моки).

- Версия пакета: `admin-fixtures/v1`
- Основная документация (RU): см. `docs/ru/admin/README.md`

Задумка: любой UI (разные агенты/команды) использует **одни и те же** datasets/scenarios → сравнение UX и архитектуры честное.

## Seed docs & generators

- Seed model for the current demo community: [docs/ru/seeds/seed-greenfield-village-100.md](../docs/ru/seeds/seed-greenfield-village-100.md)
- Authoring guide (how to design community economics + keep fixtures consistent): [docs/ru/seeds/README.md](../docs/ru/seeds/README.md)

## Source of truth

- **Canonical fixtures** for the Admin UI demo are generated via the single entry point `admin-fixtures/tools/generate_fixtures.py` (select a seed like Greenfield 100 / Riverside 50).

## Workflow

1) Generate canonical fixtures (pick one seed):
- `D:/www/Projects/2025/GEOv0-PROJECT/.venv/Scripts/python.exe admin-fixtures/tools/generate_fixtures.py --seed greenfield-village-100`

2) Sync into Admin UI public folder and validate:
- `cd admin-ui`
- `npm run sync:fixtures`
- `npm run validate:fixtures`
