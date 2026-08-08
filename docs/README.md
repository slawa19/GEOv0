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

The root README and this index are navigation surfaces, not substitute specifications.
