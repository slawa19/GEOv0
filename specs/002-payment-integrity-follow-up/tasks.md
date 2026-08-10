# 002 — Executable tasks

Status legend: `[x]` complete, `[ ]` pending, `[!]` blocked/not authorized.

## Phase 0 — Evidence-backed specification

- [x] P000 Confirm clean accepted baseline and create a dedicated Phase 0 branch.
- [x] P001 Read `AGENTS.md`, the orchestrator rule, program 001 closeout and active
  payment/decision documentation.
- [x] P002 Trace directed segment identity through prepare, prepare-routes, commit,
  abort and recovery callers.
- [x] P003 Trace `commit=True` and `commit=False` transaction ownership, timeout,
  retry and cancellation paths.
- [x] P004 Audit the clearing pair identity and payment/clearing interlock.
- [x] P005 Audit existing unit and PostgreSQL tests for false-green or missing
  reverse-direction coverage.
- [x] P006 Obtain three independent read-only audits with non-overlapping owner
  surfaces and unique task evidence.
- [x] P007 Run the narrow canonical unit selector.
- [x] P008 Provision and verify a unique disposable PostgreSQL database before
  enabling destructive test reset.
- [x] P009 Run the existing targeted PostgreSQL selectors.
- [x] P010 Run an ignored deterministic reverse-direction reproducer for direct
  keys, inverse commit, staged acquisition, timeout and cancellation.
- [x] P011 Classify the payment residual as confirmed P2 and register the
  independent clearing ambiguous-commit P2 without expanding scope.
- [x] P012 Write `spec.md`, `plan.md`, `tasks.md` and the evidence map.
- [x] P013 Run governance checks and commit the initial evidence/specification.
- [x] P014 Assign a separate read-only adversarial reviewer; reproduce every
  reported P1/P2 before editing.
- [x] P015 Commit only confirmed review remediation, if needed.
- [x] P016 Run required Claude Code `/code-review high` against the exact frozen
  governance range from a credential-free standalone clone.
- [ ] P017 Resolve confirmed external findings; allow at most one fix-delta review.
- [ ] P018 Update repository front doors only after Phase 0 acceptance.
- [ ] P019 Run final diff/link/scope/artifact/secret checks.
- [ ] P020 Commit governance closeout, push the branch, verify remote SHA and report
  automatically triggered CI without claiming an unobserved gate.

## Phase 1 — Payment serialization owner

All tasks are pending owner authorization.

- [!] P100 Add a PG test proving reverse directions contend on one resource.
- [!] P101 Add inverse multi-segment tests for both start orders and final monetary,
  state, PrepareLock and audit invariants.
- [!] P102 Add a real staged multi-call deadlock regression test; do not inject a
  synthetic DBAPI error.
- [!] P103 Inventory all staged owners and choose complete-lock-set or coarser-owner
  acquisition with explicit transaction ownership.
- [!] P104 Implement canonical unordered pair identity without changing directed
  business/audit semantics.
- [!] P105 Implement transaction-wide acquisition for staged owners; a retry-count
  increase is forbidden.
- [!] P106 Define and test mixed-version deployment compatibility across service,
  staged, Admin-abort and recovery owners. A bridge must acquire canonical plus
  both legacy directional keys in one global order; otherwise require quiescence.
- [!] P107 Run existing same-direction, same-tx, commit-only `23505`, idempotency,
  timeout, cancellation and recovery selectors.
- [!] P108 Update stable RU payment and decision documentation.
- [!] P109 Complete adversarial/external reviews and publish the exact-head evidence.

## Phase 2 — Clearing interlock and closure

All tasks are pending successful Phase 1 and owner authorization.

- [!] P200 Add deterministic clearing-versus-new-prepare schedules for both orders.
- [!] P201 Select and implement the smallest shared serialization or revalidation
  boundary; avoid unrelated clearing refactoring.
- [!] P202 Assert final debt, versions, trust limits, actual locked clearing amount,
  PrepareLocks and emitted/audit effects relevant to the boundary.
- [!] P203 Run the exact-head payment/clearing PostgreSQL matrix.
- [!] P204 Synchronize stable docs and residual ledger.
- [!] P205 Complete final reviews, rollback evidence, remote verification and
  program closeout.

## Explicitly separate work

The following findings are not tasks in program 002:

- ambiguous durable clearing commit followed by cancellation/acknowledgement loss;
- stale simulator clearing-volume publication;
- trust-drift in-memory mutation before commit;
- cleanup-expired-lock redundant post-abort commit;
- general clearing early-return transaction cleanup.

The first is registered as an independent P2 under the clearing/simulator owner.
It requires a separately approved program rather than silent scope expansion.
