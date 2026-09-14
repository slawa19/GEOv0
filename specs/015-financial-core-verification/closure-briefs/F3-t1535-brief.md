# T1535 — a bare `alembic upgrade head` on a fresh database succeeds: widen the version column in `migrations/env.py`

Repository `D:\www\projects\2025\GEOv0`, branch `claude/012-money-s1`. Read first:

1. `AGENTS.md` §19 and **§19.5** (grep `### 19`; read those sections only). Programme 015 is closing: this is step 3 of
   five. Keep it to the decided line.
2. Row `T1535` in `specs/015-financial-core-verification/spec.md` (grep `^| \`T1535\``), and the narrowing row that
   says "оставить — одна строка чинит документированный путь установки".

## The defect, as recorded

- Alembic creates `alembic_version.version_num` as `VARCHAR(32)`. Revision ids in this repository are longer (up to 46
  characters). A bare `alembic -c migrations/alembic.ini upgrade head` on a fresh PostgreSQL database dies at 010→011
  with `StringDataRightTruncationError` and rolls everything back. Reproduced 2026-09-13.
- The precondition (create or widen `alembic_version.version_num` to `VARCHAR(128)`) lives only in
  `docker/docker-entrypoint.sh` and in the test helper `tests/migrated_schema.py:44-50`. The documentation
  (`docs/en/05-deployment.md` around `:187`, `docs/en/06-contributing.md` around `:202`, and `README.md`) tells a human to
  run the bare command.

## The decision — implement it, do not reopen it

One change in `migrations/env.py`: configure the Alembic context so the version table's column is wide enough
(`version_table_column_type`, per the decision), in **both** `context.configure` calls (offline around `:48`, online
around `:60`).

**Verify the premise first.** Check the installed Alembic version (`.venv`) and its `EnvironmentContext.configure`
signature/documentation for `version_table_column_type`. If the parameter does not exist in the installed version, that
is **stop condition 1** — report the version and what the installed Alembic does offer; do not invent a workaround in
`env.py`.

Note: `version_table_column_type` affects only a version table Alembic **creates**. An existing database whose
`alembic_version` is already `VARCHAR(32)` is not widened by it; the Docker entrypoint and the test helper keep covering
that case. Do not remove either.

## Acceptance

- **Reproduce before the change:** on a fresh, empty PostgreSQL database of your own (`geov0_test_t1535`, at
  `127.0.0.1`), run the bare documented command and show it fails at 010→011 with the truncation error.
- **After the change:** the same bare command on a fresh empty database reaches head (`028_...`), and
  `alembic_version.version_num` is `character varying(128)` (query `information_schema.columns`).
- A test pins it: on a fresh empty PostgreSQL database, a bare `alembic upgrade head` (subprocess, no pre-created
  `alembic_version`) reaches the single head. It must be red without the `env.py` change (mutation: remove the
  parameter; restore byte-exactly, sha256 verified). If an existing test already runs migrations only through
  `tests/migrated_schema.py`'s pre-widening, do not change that helper — add the bare-path test beside it.
- Docs: no change needed if the bare command now works as documented; confirm the three places and say so.

## Stop conditions

1. The parameter does not exist in the installed Alembic.
2. Making the bare path work needs more than the configure argument (e.g. DDL in `env.py`).

## Gates

Check free commit memory before each gate (under 2 GB a red result is not evidence). Full PostgreSQL gate on
`geov0_test_t1535` with `-BackendMarker postgres -BackendSelector tests/integration`, `GEO_TEST_USE_MIGRATED_SCHEMA=1`,
`GEO_TEST_ALLOW_DB_RESET=1`; SQLite `scripts/verify_local.ps1 -TaskSlug t1535 -BackendOnly`; migration single-head
check. Predict counts; poll your runs in the foreground.

No commits, no `specs/` edits. Report in English: Alembic version and parameter evidence, before/after reproduction,
diff, gate counts, mutation result.
