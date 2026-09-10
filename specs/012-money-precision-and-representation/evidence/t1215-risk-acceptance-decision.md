## A. Decision

The evidence is sufficient to accept the residual risk. I found no artefact establishing an unresolved production P1 or another unconditional blocker.

The ordinary closure route remains unavailable: both external circles returned `READY-TO-CLOSE: NO`, while §15 requires a dated acceptance to proceed ([AGENTS.md](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/AGENTS.md:585)). But the closing ledger records all five T1214 P2 findings as closed and explicitly distinguishes the reviewer’s recommendation from actual acceptance ([t1214-astra-ledger.txt](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/specs/012-money-precision-and-representation/evidence/t1214-astra-ledger.txt:45)).

The remaining gaps are bounded verification debt, not demonstrated current defects. Therefore no additional artefact or measurement is a prerequisite to accepting the risk. Programme 012 may be marked CLOSED only once the new dated record and the status corrections below are committed.

## B. Required acceptance record

The new `specs/BACKLOG.md` record must say:

- Date: `2026-09-10`.
- Authority: decision by the delegated external holder, following the same process-decision delegation already recorded for 012 ([BACKLOG.md](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/specs/BACKLOG.md:602)).
- Exact accepted range: `422ed50^..3aa6b83`, explicitly listing `422ed50`, `afb099d`, `bee9ca3`, `c55feaa`, and `3aa6b83`.
- Final integration snapshot: `ff619d7` on `main`; its tree equals `3aa6b83`.
- Review history: T1213 reviewed `422ed50`; T1214 reviewed `422ed50..c55feaa`; T1214’s five P2 findings were fixed in `3aa6b83`, which received no further external review because the one permitted fix-delta circle was spent.
- Accepted residual risk: the final fixes may still contain a regression, bypass, incomplete domain sample, or false-green verification mechanism.
- The eight explicit limitations from the Verification plan:

  1. `DEFAULT_MAX_AMOUNT_SCALE` has no effective guard.
  2. The float guard matches identifier text and scans only six modules.
  3. The `E+` exponent invariant is weaker than its `E-` counterpart.
  4. `admin-ui/src/utils/decimal.test.ts` does not discriminate the relevant formatter defect.
  5. Six live `Decimal("0.01")` sites remain as T1203 P3 residue.
  6. Both Playwright suites and the Simulator build remain unrun; Admin build and Simulator typecheck were run.
  7. The historical five-run timing range has no retained artefact.
  8. CI for the final `main` snapshot `ff619d7` is unverified from the agent session.

- Local evidence after the last change: backend `1865 passed, 1 failed`, with the failure identified as programme 014’s known `F-014-11`; Postgres `133 passed`; Admin UI `308 passed`; Simulator UI `995 passed`; Admin build succeeded. These are local/orchestrator results, not externally reproduced results ([spec.md](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/specs/012-money-precision-and-representation/spec.md:1677)).
- Standing environment limitation: the home-directory credential store remained reachable. The 2026-08-14 acceptance applies; only the transferred clone lacked credentials.
- Scope: this acceptance lifts the external-review closure requirement only for programme 012 and creates no exception for later programmes.

It must not claim `CLEAN`, `READY-TO-CLOSE: YES`, external validation of the gates, CI success, complete repository-wide testing, Playwright/build coverage that did not occur, comprehensive coverage of all money paths, a credential-free filesystem, or that the reviewer itself accepted the risk. It must also not repeat “no production or test code changed after `8b04e68`.”

## C. Remaining misleading statements

Three current records need reconciliation before the CLOSED status is historically honest:

- The spec header names `d15bf2b` and tasks only through T1213, although the final closure changes reached `main` through `ff619d7` and T1214 is complete ([spec.md](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/specs/012-money-precision-and-representation/spec.md:4)).
- `specs/README.md` still says the repeat external slice has not occurred, contradicted by T1214 ([specs/README.md](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/specs/README.md:35)).
- T1213/T1214 call the clone “credential-free” without the essential filesystem qualification ([spec.md](/C:/Users/admin/AppData/Local/Temp/geov0-riskaccept-1789053673/clone/specs/012-money-precision-and-representation/spec.md:1616)). The ledger supplies that qualification, but the primary narrative should too.

The CI limitation must refer to final snapshot `ff619d7`, not merely the earlier `d15bf2b`. The immutable merge message’s “six” unverified items should not be repeated: the authoritative list now has eight.

VERDICT-012-CLOSURE-RISK: ACCEPT
VERDICT-BLOCKER: NONE
VERDICT-RECORD-SCOPE: 422ed50^..3aa6b83