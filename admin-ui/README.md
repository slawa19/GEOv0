# Vue 3 + TypeScript + Vite

This template should help get you started developing with Vue 3 and TypeScript in Vite. The template uses Vue 3 `<script setup>` SFCs, check out the [script setup docs](https://v3.vuejs.org/api/sfc-script-setup.html#sfc-script-setup) to learn more.

Learn more about the recommended Project Setup and IDE Support in the [Vue Docs TypeScript Guide](https://vuejs.org/guide/typescript/overview.html#project-setup).

## Fixtures

The dev server reads JSON fixtures from `admin-ui/public/admin-fixtures/...`.

- `npm run sync:fixtures` copies canonical fixtures from `../admin-fixtures` into `public/`.
- `npm run validate:fixtures` checks that canonical+public fixtures are parseable, `_meta.json` matches, `seed_id` is allow-listed, and participant/trustline fields follow the deterministic constraints (including supported participant types `person|business|hub`).

`npm run dev` runs `sync:fixtures` + `validate:fixtures` automatically via `predev`.
