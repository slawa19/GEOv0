# 002 — Delivery plan

Status: COMPLETE — Phases 0–2 closed 2026-08-11

Contract: [spec.md](spec.md)

Evidence: [phase0-evidence-map.md](phase0-evidence-map.md)

Backlog: [tasks.md](tasks.md)

## Sequencing principles

- Preserve a clean product baseline during Phase 0.
- Change one concurrency owner boundary per reversible commit.
- Use deterministic PostgreSQL barriers; SQLite is supporting evidence only.
- Reproduce and independently review every P1/P2 before accepting it.
- Do not weaken monetary, idempotency or transaction-ownership invariants.
- Do not start a later phase merely because its documentation exists.

## Phase 0 — Evidence-backed specification

Goal: determine whether reverse-direction segment locking is a real residual and
define the smallest safe delivery program.

Deliverables:

1. current/intended/optimal behavior map;
2. payment, clearing, recovery and staged-owner call-site inventory;
3. real PostgreSQL characterization for reverse keys, inverse row order,
   savepoint retry, timeout and cancellation;
4. explicit severity and unverified-path classification;
5. this spec, phased plan, executable tasks and evidence ledger;
6. read-only adversarial review by a separate Codex agent;
7. read-only Claude Code review of the exact frozen governance diff;
8. front-door update only after the phase is accepted.

Exit criteria:

- no product/test/API/schema/UI/fixture change;
- confirmed residual is classified with reproducible evidence;
- any independent P1 is registered without expanding this program;
- links and diff scope pass;
- exact commits and remote status are recorded.

## Phase 1 — Payment serialization owner

Authorized by the owner for Wave 3 on 2026-08-11.

Goal: make payment concurrency use the real reciprocal-debt resource identity and
eliminate incremental staged lock-order deadlocks.

Vertical slice:

1. add failing unit/PG characterizations for reverse direct acquisition,
   inverse multi-segment commit and staged outer transactions;
2. replace the directed concurrency identity with a canonical unordered pair
   identity while retaining directed business/audit data;
3. use the inventoried `commit=False` owners to implement the selected
   equivalent-scoped coarse-owner protocol before the first staged monetary
   mutation; the real tick pre-acquires its complete sorted equivalent set;
4. define mixed-version deployment compatibility: either coordinated quiescence,
   or canonical plus both legacy directional keys in one global order;
5. add real PostgreSQL characterization for the server-selected `40001` or exact
   Debt business-key `23505` after a SERIALIZABLE advisory wait, then rerun
   same-direction bottleneck, same-tx
   transition, idempotency, timeout, cancellation and recovery selectors;
6. update stable payment/decision documentation for the selected mechanism;
7. perform independent adversarial and external review of the frozen product diff.

Exit criteria:

- both inverse start orders complete with bounded, specified outcomes;
- no staged owner depends on savepoint retry to release a pre-savepoint lock;
- monetary and audit assertions prove no partial/double effect;
- no unresolved P1/P2 remains in the payment owner slice.

Rollback: revert the Phase 1 implementation commit(s). Deployment must not mix
old/new key identities without the compatibility mechanism selected in the slice.

## Phase 2 — Clearing interlock and program closure

Owner-authorized for Wave 4 on 2026-08-11; execute after Program 003 closeout.

Goal: close the accepted in-program P2 at the shared payment/clearing resource
boundary and then close the program. The independent clearing ambiguous-commit
P2 is not folded into this phase.

Vertical slice:

1. add a deterministic PG test where clearing reaches its conflict boundary while
   a reverse payment prepares;
2. implement one documented shared serialization/revalidation boundary;
3. assert actual locked clearing amount, debt/version invariants and PrepareLock
   visibility only where required by that boundary;
4. run the exact-head PG payment/clearing matrix and relevant canonical local gate;
5. synchronize stable RU documentation and close the residual ledger;
6. perform final independent and external review.

Exit criteria:

- payment cannot become newly prepared between clearing's decision and mutation;
- payment and clearing preserve debt/trust/version invariants under both schedules;
- no unrelated clearing/recovery cleanup is included;
- no P1/P2 remains in program scope and unverified paths are explicit.

Rollback: revert the clearing-boundary commit independently of Phase 1 where the
selected compatibility protocol permits it; otherwise revert the documented
coherent pair.

## Verification ladder

1. Narrow unit selectors after each micro-batch.
2. Disposable PostgreSQL selectors for advisory locks and concurrent writers.
3. `git diff --check`, link scan, protected-surface and artifact/secret scans.
4. Internal read-only adversarial review.
5. Exact `BASE..HEAD` Claude Code review from a credential-free standalone clone.
6. Published-branch CI observation; never infer CI green from a local command.

## Program stop conditions

- Stop and return to specification if a schema/API/migration dependency appears.
- Stop delivery on a reproduced in-scope P1 until it is resolved or owner-accepted.
- Register an independent P1 under its real owner; do not expand this program.
- Phase 2 completion, not creation of tasks, is the program close signal.
