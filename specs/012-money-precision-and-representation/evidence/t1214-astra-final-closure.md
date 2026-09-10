Reviewed read-only at `c55feaa`, against `422ed50`. I did not rerun the supplied measurements. **Closure is available only through a new, scoped risk acceptance after reconciling the findings below; it is not available under the existing record.**

**C1. The closure record is OVERSTATED.**

- **P2 — “load-bearing” conflates protecting the table with validating behavior.** `specs/012-money-precision-and-representation/spec.md:461` draws its conclusion from three failures after deleting the ten rows. The backend failures follow from two coverage requirements (`tests/unit/test_p012_t1211_money_rendering_conformance.py:83`, `:91`) and the deliberately retained cap-at-eight mutant (`:181`, `:236`). The production conformance test at `:120` simply stops receiving the deleted cases. This demonstrates that deletion is guarded, not independently that those rows are necessary to establish the rule. The stronger, noncircular justification is **legacy reachability**, established by the producer test recorded at `spec.md:1533`. Keep the rows, but use that justification.
- **P2 — the asserted partition does not follow.** `spec.md:473` says nothing falls between the tiers. The canonical default excludes both `slow` and `postgres` (`scripts/verify_local.ps1:127`), while the Postgres tier selects `postgres`. A concrete uncovered case is `tests/integration/test_simulator_adaptive_clearing_effectiveness_ab.py:301`, marked `slow` without `postgres`; `test_simulator_super_smoke.py:18` supplies another. If the recorded run used `not postgres`, its partition concerns an expanded tier, not the canonical default. Record the actual command and collected scope. Moreover, `pytest.ini:5` limits discovery to `tests`; equal selection counts cannot establish repository-wide coverage or execution of skipped tests.

The header’s refusal to call the programme closed is honest (`spec.md:7`).

**C2. The unverified list is INCOMPLETE.**

- **P2 — omitted build and browser surfaces.** The nine selectors at `spec.md:431` do not include either production build or either Playwright suite. Those are separate commands: `admin-ui/package.json:17`, `:23`; `simulator-ui/v2/package.json:13`, `:19`. In particular, Admin’s build includes TypeScript checking, which its test/lint selectors do not replace. The current delta changes `admin-ui/src/api/mockApi.ts:1309`. Part 5 should state the freshness and availability of build/browser evidence explicitly; “CI status unavailable” does not describe which local surfaces were unverified.
- **P2 — the new dataset-discovery guard lacks demonstrated negative controls.** `tests/integration/test_p012_t1212_declared_precision_exceeds_storage_scale_postgres.py:117` discovers only files named `equivalents.json`, with exclusions based on directory components at `:120`; `:287` compares that result with a fixed list. The tree records its normal passing result, but I found no probe demonstrating rejection when an additional eligible dataset appears, or preservation of discovery across the exclusions. A shipped dataset under another filename would evade both sides. This is a limitation of the claimed census, not proof of a currently missed dataset.
- **P3 — some historical measurements remain assertions rather than inspectable results.** `spec.md:1244` claims five runs whose medians ranged from `87.6` to `238.6` ms. The retained query-plan artifact records medians `153.1` and `130.0` at `evidence/t1211-min-amount-query-plan.txt:14`. The generator provides a reproducible method, but that artifact does not establish the claimed five-run range. Mark the historical range as unretained evidence or attach its record.

The omitted slow-only coverage identified in C1 also belongs here.

**C3. Closure of 011 does not itself confer authority.**

**P2 — the authority argument is a rationalisation as written.** The operative prohibition remains “Запрещено всегда” at `spec.md:78`. At `:1621`, the explanation correctly admits that closure creates no permission, then proceeds to edit anyway.

`AGENTS.md:263` requires a coordinated implementation/schema/tests/documentation change; that is a condition on execution, not a transfer of ownership. Mandatory review at `AGENTS.md:559`, risk acceptance at `:585` and `:690`, and closure reconciliation at `docs/codex-orchestrator-rule.md:243` do not supply that transfer either. New authority is addressed explicitly at `docs/codex-orchestrator-rule.md:228`.

There **is a potentially sufficient independent basis**: `spec.md:1587` records owner delegation after presenting the conflict. If that delegation covered the canon edit, amend the operative owner surface with the dated, bounded exception and cite that delegation. No repeat permission is necessary merely to document authority already granted. A backlog question about future ownership cannot substitute for the present exception.

**C4. The 015 hand-off is SOUND, with an important calculation distinction.**

The anchors support a real discrepancy for the same participant and equivalent:

- Metrics computes `DOWN(credits × scale) − DOWN(debts × scale)` (`app/core/admin/metrics.py:52`, `:384`, `:388`, `:392`).
- The graph API computes `HALF_UP((credits − debts) × scale)` (`app/api/v1/admin.py:1917`, `:1925`; the other copy is at `:1535`).

These represent the same underlying net position, but **metrics does not round the net itself**. With precision 1, credits `0.06`, debts `0`, the outputs are respectively `0` and `1` atom: rounding mode alone is enough to distinguish them. With credits `0.11`, debts `0.09`, metrics produces `1` atom while rounding the net produces `0`, even using DOWN. That second difference is the already recorded order-of-operations issue.

The finding acknowledges that second issue at `specs/015-financial-core-verification/spec.md:213`. Preserve the explicit formulas so fixing the mode is not mistaken for fixing both causes.

Recording this before authorization is appropriate: `:217` requires an explicit scope decision before starting, and `T1521` remains `[!]` at `:364`. The lapsed restriction was expressly conditional on merger (`:8`), not programme closure. This is proposed scope, not silently authorized implementation.

**C5. The sentinels remain WEAK.**

- **P2 — a plausible wrong formatter passes the whole table.** The cases in `api/money-rendering-conformance.json:72` contain no nonzero integer ending in zero. A formatter that first applies `text.rstrip("0").rstrip(".") or "0"` to the entire decimal string, then pads the fraction to the declared minimum, satisfies the current rows but renders **`"120"` at precision 0 as `"12"`**. This is a general integer/fraction boundary error, not a literal tailored to one test. It does not establish a defect in today’s production formatter; it refutes the table’s claimed discrimination.
- **P2 — a sentinel can pass without its named property.** The “negative that is stripped” predicate at `tests/unit/test_p012_t1211_money_rendering_conformance.py:93` checks only lexical scale. The existing `"-0.05"` at precision 0 (`api/money-rendering-conformance.json:276`) satisfies it although no digit is stripped. Removing `"-12.300"` at precision 1 leaves that sentinel green without a negative trailing-zero stripping case. The other listed mutants still have positive stripping cases or other sign cases to distinguish them.

The shared table remains useful. Its assertions establish particular examples and particular mutant distinctions, not completeness of the domain or correctness of every named coverage class.

**C6. Closure requires a NEW risk-acceptance record.**

The existing acceptance enumerates seven commits (`specs/BACKLOG.md:616`) and states that production/test code did not change after `8b04e68` (`:630`). It cannot cover `422ed50` and `afb099d`. The current header explicitly recognizes this (`spec.md:9`).

There is no new demonstrated production P1 in this review requiring an unconditional block. The permissible route is:

1. Reconcile the overstated evidence claims, incomplete unverified list, and operative authority exception.
2. Disposition the sentinel findings as fixes or explicitly accepted verification debt.
3. Record a **new dated owner acceptance**, or an acceptance under demonstrable delegated authority, in `specs/BACKLOG.md`, identifying the current closure snapshot, uncovered delta, this review, outstanding verification limits, and unavailable CI status.

My recommendation of that route is **not itself acceptance of the risk**. The earlier acceptance and both historical `READY-TO-CLOSE: NO` results must remain scoped to their recorded revisions.

VERDICT-CLOSURE-RECORD: OVERSTATED
VERDICT-UNVERIFIED-LIST: INCOMPLETE
VERDICT-AUTHORITY: RATIONALISATION
VERDICT-015-HANDOFF: SOUND
VERDICT-SENTINELS: WEAK
VERDICT-012-CLOSURE: RISK-ACCEPT