# Admin UI (Vue 3 + TypeScript + Vite)

Recommended way to run the Admin UI in this repo (Windows): use the repo runner script.

```powershell
.\scripts\run_local.ps1 start
```

It starts the backend and Admin UI, manages ports/PIDs under `.local-run/`, and writes `admin-ui/.env.local` with the backend URL.

## Fixtures

The dev server reads JSON fixtures from `admin-ui/public/admin-fixtures/...`.

- `npm run sync:fixtures` copies canonical fixtures from `../admin-fixtures` into `public/`.
- `npm run validate:fixtures` checks that canonical+public fixtures are parseable, `_meta.json` matches, `seed_id` is allow-listed, and participant/trustline fields follow the deterministic constraints (including supported participant types `person|business|hub`).

`npm run dev` runs `sync:fixtures` + `validate:fixtures` automatically via `predev`.
