# GEO documentation

This is the front door for repository documentation. For current work, begin with the RU project and domain indexes below; EN and PL trees are translations or historical context unless a file explicitly records a synchronization source and date.

## Authority and precedence

No single artifact proves both intended and observed behavior. Resolve disagreements by separating current behavior, accepted intent, and the optimal target before changing code or prose.

| Question | Primary authority | What other evidence means |
|---|---|---|
| REST paths, request/response fields, and wire serialization | [`api/openapi.yaml`](../api/openapi.yaml) | Markdown explains the schema but does not create a parallel wire contract. |
| Behavior implemented now | Code, runtime observation, and focused tests together | Each is evidence for exercised paths; none alone proves product intent or untested behavior. |
| Accepted product decisions and defaults | Current RU documents, especially [`09-decisions-and-defaults.md`](ru/09-decisions-and-defaults.md), plus the relevant RU domain index | If prose conflicts with observed behavior, record current/intended/optimal before choosing which side changes. |
| Architecture and configuration guidance | [`ru/03-architecture.md`](ru/03-architecture.md) and [`ru/config-reference.md`](ru/config-reference.md), checked against current code and entrypoints | These describe accepted intent; executable defaults still require verification in code/runtime. |
| EN and PL documents | Dated translations of an identified source | Same filenames do not imply parity. Undated translations are informative, not normative. |
| `concept/`, `archive/`, plans, and historical reports | Non-normative context | They may explain rationale or a target, but do not override current contracts or accepted decisions. |

When primary authorities conflict, do not silently force one to match another. Capture:

1. **Current behavior** — code/runtime/test evidence and the exercised path.
2. **Intended behavior** — accepted decision or owner clarification.
3. **Optimal target** — the safest maintainable behavior and migration implications.

## Translation freeze (RU is the working tree) — 2026-08-10

RU is the primary and only maintained documentation tree. **EN and PL are frozen and are not
being updated.** Do not spend effort synchronizing them; do not treat them as evidence of
current behavior. Measured drift as of 2026-08-10:

| Document | RU | EN | PL | Gap |
|---|---|---|---|---|
| `09-decisions-and-defaults` | 22 104 B / 2026-08-09 | 5 600 B / 2026-01-06 | 5 823 B / 2026-01-06 | EN/PL hold ~25% of the RU text — **215 days behind** |
| `03-architecture` | 82 560 B / 2026-08-09 | 69 022 B / 2026-01-10 | 53 115 B / 2026-01-10 | 211 days behind |
| `00-overview` | 12 240 B / 2026-08-07 | 7 614 B / 2026-01-06 | 7 549 B / 2026-01-06 | 213 days behind |
| `02-protocol-spec` | 116 170 B / 2026-02-10 | 87 153 B (−25%) | 61 019 B (−47%) | translations frozen at 2026-01-06 |
| `05-deployment`, `10-testing-framework`, `config-reference` | rewritten shorter in Aug 2026 | **larger than RU** | **larger than RU** | inverted: translations still assert claims RU has withdrawn |

Two structural facts behind the freeze:

- **0 of 28 EN/PL files carry a dated synchronization header.** By the rule in the table below,
  that makes the entire EN/PL tree informative, never normative.
- **~114 of 144 RU documents (79%) were never translated**, including every "current front door"
  listed below (`ru/backend/README.md`, `ru/admin-ui/README.md`, `ru/simulator/README.md`,
  `ru/documentation-rules.md`) and all of `ru/simulator/` (81 files).

Known orphan translations — EN/PL files whose RU counterpart was archived or moved, so the
same-filename correspondence no longer holds. Cleanup candidates, not sources of truth:

- `en/admin-console-minimal-spec.md`, `pl/admin-console-minimal-spec.md` — RU original is archived at `ru/admin-ui/specs/archive/admin-console-minimal-spec.md`
- `en/admin-ui-spec.md`, `pl/admin-ui-spec.md` — RU moved to `ru/admin-ui/specs/admin-ui-spec.md`
- `en/pwa-client-ui-spec.md`, `pl/pwa-client-ui-spec.md` — RU moved to `ru/pwa/specs/pwa-client-ui-spec.md`
- `en/concept/09-behavior-simulator-application.md`, `pl/concept/09-aplikacja-symulator-zachowan.md` — RU content moved to `ru/simulator/backend/GEO-community-simulator-application.md`

Lifting the freeze is a deliberate decision: it means re-translating from RU and stamping each
file with a synchronization source and date.

## Current front doors

- Runtime and local start: [`README.md`](../README.md#getting-started)
- Architecture: [`ru/03-architecture.md`](ru/03-architecture.md)
- REST API: [`api/openapi.yaml`](../api/openapi.yaml)
- Configuration: [`ru/config-reference.md`](ru/config-reference.md)
- Testing: [`README.md`](../README.md#testing-single-entry-point) (it owns the canonical `scripts/verify_local.ps1` command)
- Backend domain: [`ru/backend/README.md`](ru/backend/README.md)
- Admin UI domain: [`ru/admin-ui/README.md`](ru/admin-ui/README.md)
- Simulator domain: [`ru/simulator/README.md`](ru/simulator/README.md)
- Documentation rules: [`ru/documentation-rules.md`](ru/documentation-rules.md)
- Agent rules and gates: [`AGENTS.md`](../AGENTS.md); orchestrator role: [`codex-orchestrator-rule.md`](codex-orchestrator-rule.md); external review (who reviews whom, and how): [`external-review-runbook.md`](external-review-runbook.md)
- Program registry and what to work on next: [`specs/README.md`](../specs/README.md)

The root README and this index are navigation surfaces, not substitute specifications.
