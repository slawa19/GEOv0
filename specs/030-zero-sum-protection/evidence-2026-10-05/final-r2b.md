The debt journal is substantially protected, but **“the obligations are exactly those required by the protocol” is not yet guaranteed**. In particular, clearing can execute an amount below the equivalent’s accounting step, and reconciliation does not establish correspondence between all committed transaction records and ledger operations. The mathematical zero-sum identity does not detect either defect. (`docs/ru/02-protocol-spec.md:1896`, `app/core/clearing/service.py:1781`, `app/core/ledger/reconciliation.py:444`)

I read source only; I executed no application, tests, SQL, or shell commands. **FACT** below means directly established from source. **INFERENCE** means the predicted consequence of that source, with a reproducer supplied; it is not a claimed execution result. I exclude the supplied internal findings from the new-finding count.

**New findings, ranked**

**R2-1 — P1, class 1: clearing accepts sub-step amounts reachable through the existing importer.**

**FACT — current behavior.** The fixture importer accepts a positive `Decimal` debt without checking the equivalent’s step, then passes it to `NewDebt`. The active clearing snapshot reads storage amounts and converts them into scale-8 atoms without loading precision. The runner uses the resulting atom count directly. Execution checks positivity, cycle structure and `c <= debt`, but does not check accounting-step divisibility. (`scripts/seed_db.py:473`, `scripts/seed_db.py:483`, `app/core/clearing/flow_planner.py:498`, `app/core/clearing/flow_planner.py:522`, `app/core/clearing/runner.py:370`, `app/core/clearing/service.py:110`, `app/core/clearing/service.py:1769`, `app/core/clearing/service.py:1781`)

**Intended behavior.** The accepted 2026-10-04 decision requires clearing amounts to be multiples of the equivalent’s accounting step. The 028 property-test premise covers graphs whose debts are already step-aligned; it does not establish that premise for imported or historical debts. (`specs/028-backlog-rework/spec.md:28`, `specs/028-backlog-rework/spec.md:81`)

**INFERENCE — exact red-first stand.** Import an active precision-2 equivalent, active participants A/B/C, consenting supporting lines with limit `1.00`, and debts A→B, B→C, C→A each `0.015`. Take the baseline, then invoke the normal clearing runner. The expected current result is one occurrence of `0.015`, deleting all three debts. That movement violates the `0.01` step. Journal equality and clearing-v2 recomputation can nevertheless pass: both describe the same sub-step reduction. (`scripts/take_reconciliation_baseline.py:49`, `app/core/clearing/flow_planner.py:419`, `app/core/ledger/book.py:439`, `app/core/ledger/reconciliation.py:729`, `app/core/ledger/reconciliation.py:950`)

**Optimal target.** Validate the declared clearing amount against the locked equivalent’s step before posting; prevent new sub-step imports. Handling existing residues requires the owner decision identified below. This is separate from the already-known absence of a SEED replay rule.

**R2-2 — P2, class 2: competing refusals can deliver an outcome different from the stored outcome.**

**FACT — current behavior.** The separate-session refusal recorder correctly avoids overwriting a concurrent transaction. However, after reading and resolving the winning row, it returns that result only when it is `COMMITTED`; an existing `ABORTED` result becomes `None`. The caller then raises its own original exception. (`app/core/payments/service.py:2840`, `app/core/payments/service.py:2878`, `app/core/payments/service.py:2884`, `app/core/payments/service.py:2511`)

A second branch has the same outcome-resolution problem: a refusal-recording lock timeout becomes `RefusalNotRecorded`, which `_record_refusal` converts to `None`, again allowing the original refusal to escape without establishing the durable outcome. (`app/core/payments/service.py:2859`, `app/core/payments/service.py:2658`, `app/core/payments/service.py:2516`)

**Intended behavior.** The accepted decision says a recorded refusal is retained on replay; retryable contention is distinguished from a terminal refusal. (`specs/028-backlog-rework/spec.md:30`)

**INFERENCE — exact schedule.** Use the supported no-Redis path and identical signed requests with one `tx_id`.

1. A is admitted, times out waiting for a line, rolls back, and pauses immediately before recording its timeout refusal.
2. B starts after A’s rollback, passes identity lookup and computes its route through transit participant T; pause B before participant locking.
3. Admin freezes T and commits.
4. B binds the route, refuses `participant_suspended`, rolls back and records `ABORTED`.
5. Resume A’s recorder.

Expected wrong result: the stored row and later replay report B’s suspension refusal, while A receives its timeout refusal. No double debt effect is claimed. The relevant admission/locking and refusal branches are present in the application. (`app/api/v1/payments.py:95`, `app/core/payments/service.py:1249`, `app/core/payments/service.py:1791`, `app/api/v1/admin.py:919`, `app/core/payments/service.py:2884`)

For the lock-timeout variant, let B retain an uncommitted successful row for the same identity beyond A’s refusal-recording timeout. A can deliver its original refusal before B commits success. (`app/core/payments/service.py:2834`, `app/core/payments/service.py:2860`, `app/core/payments/service.py:2664`)

**Optimal target.** Return the resolved winner for either terminal state. Represent failure to establish the winner as an unresolved outcome, not as successful recording of the original refusal.

**R2-3 — P2, class 2: reconciliation does not establish transaction–operation completeness or terminal-state agreement.**

**FACT — current behavior.** Reconciliation starts from operation envelopes named by membership or journal entries. It does not start from committed transactions. It joins transaction payloads only for `CLEARING v2`, and does not select transaction type or state. The FK ensures that an operation’s referenced transaction exists; it does not require the transaction to be `COMMITTED`, or require every committed transaction to have an operation. (`app/core/ledger/reconciliation.py:444`, `app/core/ledger/reconciliation.py:463`, `app/core/ledger/reconciliation.py:480`, `app/db/journal_tables.py:163`, `app/db/journal_tables.py:200`)

There is an existing path to unmatched committed records: the fixture importer independently inserts transactions after the SEED envelope, accepts terminal PAYMENT states, and permits an empty payload. (`scripts/seed_db.py:493`, `scripts/seed_db.py:520`, `scripts/seed_db.py:527`, `scripts/seed_db.py:533`, `scripts/seed_db.py:586`)

**INFERENCE — exact red-first stand.** Import an empty debt dataset plus a `PAYMENT/COMMITTED` transaction whose payload says A paid B `10.00` in E; create the baseline and reconcile E. Expected current result: `PASSED`, with no corresponding PAYMENT operation or debt effect. Repeating with `{}` as payload also lacks an operation-level completeness check. (`scripts/seed_db.py:533`, `app/core/ledger/reconciliation.py:485`, `app/core/ledger/reconciliation.py:965`)

A separate detection mutation is precise: after a valid payment, change only its transaction state to `ABORTED`, keeping the operation and journal unchanged. The model permits that terminal state, and the verifier does not read it; therefore the mismatch is outside its current checks. This is a detection test, **not a claim that a normal payment writer currently makes that mutation**. (`app/db/models/transaction.py:32`, `app/core/ledger/reconciliation.py:463`)

**Optimal target.** Check correspondence in both directions and terminal-state agreement for transactions claiming executable payment/clearing outcomes. Imported historical display records need an explicit classification rather than silently inheriting that guarantee. This differs from checking SEED’s declared debt contents.

**R2-4 — P2, class 2: trust decay performs float subtraction before Decimal conversion.**

**FACT — current behavior.** Scenario configuration retains `decay_rate` as a float; decay computes `Decimal(str(1 - cfg.decay_rate))`, multiplies the monetary limit by it, floors to the accounting step and writes the limit. (`app/core/simulator/models.py:99`, `app/core/simulator/models.py:114`, `app/core/simulator/trust_drift_engine.py:512`, `app/core/simulator/trust_drift_engine.py:592`, `app/core/simulator/trust_drift_engine.py:606`)

**INFERENCE — exact red-first stand.** Use precision 2, current and original limit `100.00`, debt `90.00`, `decay_rate: 0.07`, default overload threshold `0.8`, default minimum ratio `0.3`, and an existing history entry not cleared this tick. Binary-float subtraction produces `0.9299999999999999`; flooring the product predicts a written limit of **`92.99` instead of `93.00`**. Available capacity becomes `2.99` instead of `3.00`. (`app/core/simulator/trust_drift_engine.py:483`, `app/core/simulator/trust_drift_engine.py:507`, `app/core/simulator/trust_drift_engine.py:512`, `app/core/simulator/trust_drift_engine.py:596`)

**Optimal target.** Subtract in Decimal: `Decimal("1") - Decimal(str(cfg.decay_rate))`. This is independent of the already-recorded stale-scenario-limit defect. (`specs/028-backlog-rework/spec.md:499`)

**1. EVERY WRITER — FOUND: the lock protocol has explicit exceptions**

In this inventory, “journaled” means **debt-journaled**, not merely accompanied by an audit record. “No RC guard” means no explicit `require_read_committed` at that writer; it does not assert that its configured session actually uses another isolation level.

| State and mutation sites | Locks, isolation and guards |
|---|---|
| **Debts: amount changes**, through `_set_amount`; **insertions** for payment, inject and initial debt; **deletions** for repayment/netting/clearing | Physical sites are `book.py:230`, `:377`, `:511`, `:543`, `:368`, `:413`, `:418`, `:452`. All are inside Book operations on the application paths; the database trigger journals actual stored OLD/NEW values. Book itself does not acquire the complete money-boundary lock set. (`app/core/ledger/book.py:578`, `migrations/versions/029_debt_journal_by_the_database.py:95`) |
| **Payment debt writer**, reached through public payment, internal payment and staged payment | All reach `execute`, its RC guard, participant/status locks, ordered pair-line locks, equivalent stop/hold check and Book. Staged ownership adds perimeter locks before individual executions. (`app/core/payments/service.py:898`, `:959`, `:1003`, `:1142`, `:1791`, `:1792`, `:1941`, `:1951`) |
| **Clearing debt writer**, through occurrence execution | RC guard; participant SHARE/status check; ordered pair lines; equivalent stop/hold SHARE; ordered debt UPDATE locks; Book. (`app/core/clearing/service.py:1659`, `:1681`, `:1692`, `:1705`, `:1711`, `:1980`) |
| **Inject debt writer** | Event owner checks RC, prelocks participants—including exclusive freeze targets—then lines and equivalent stops/holds. Each debt effect rechecks participant status and locks its pair before Book posting. (`app/core/simulator/real_runner_impl.py:456`, `:465`, `:472`, `:495`; `app/core/simulator/inject_executor.py:595`, `:606`, `:631`) |
| **Initial debt imports / benchmark setup** | `seed_db` and clearing benchmark use SEED/NewDebt. They do **not** use the participant → pair → stop/hold protocol or its RC guard. Book takes equivalent SHARE at SEED completion and refuses an existing baseline; debt changes are journaled. (`scripts/seed_db.py:437`, `:483`; `scripts/measure_clearing_min_amount_plan.py:206`, `:257`; `app/core/ledger/book.py:875`) |
| **Trust-line create** | Public/internal execution constructs the row after participant SHARE/status checks, pair-line locking and equivalent-step SHARE. No explicit RC check or equivalent stop/hold refusal in this service path. Audited, not debt-journaled. (`app/core/trustlines/service.py:468`, `:472`, `:475`, `:502`, `:202`) |
| **Trust-line limit/policy update** | Locks the individual line UPDATE with `populate_existing`; checks owner and closed/requested-close state; checks step under equivalent SHARE. Does not lock participants or apply money stop/hold; no RC guard here. (`app/core/trustlines/service.py:569`, `:577`, `:585`, `:627`, `:637`, `:641`, `:647`) |
| **Trust-line close request / immediate close** | Individual line UPDATE with refresh, owner check, current supported-debt read; writes zero limit, close-request timestamp and, when appropriate, `closed`. No RC guard or participant/stop/hold check here. (`app/core/trustlines/service.py:687`, `:695`, `:726`, `:734`, `:735`, `:742`) |
| **Automatic requested-close settlement** | Book updates line status after examining the operation’s actual journal transitions. It relies on the caller’s pair locks; it does not acquire them itself. (`app/core/ledger/book.py:738`, `:766`, `:780`, `:791`) |
| **Trust growth/decay** | Locks lines individually, outside the global line order; checks RC before mutation; delegates updates to TrustLineService. No participant freeze or equivalent stop/hold gate for these limit edits. (`app/core/simulator/trust_drift_engine.py:373`, `:411`, `:415`, `:557`, `:604`, `:606`) |
| **Scenario initial trust lines** | `import_initial` inserts directly through the service’s import branch, without participant/pair locks or stop/hold checks. Seeder locks equivalents SHARE and refreshes precision; import is audited. (`app/core/trustlines/service.py:788`, `:815`; `app/core/simulator/real_scenario_seeder.py:227`, `:294`, `:316`) |
| **Script initial trust lines** | Direct constructors in both seed modes and two benchmarks bypass TrustLineService and the money-boundary protocol. Benchmark setup uses dedicated newly created databases. (`scripts/seed_db.py:188`, `:415`; `scripts/measure_clearing_min_amount_plan.py:214`, `:233`; `scripts/measure_p021_trust_line_batches.py:224`, `:248`) |
| **Equivalent creation** | Admin creates the equivalent and baseline together. Scenario seeder and direct seed/benchmark constructors do not use that admin path. (`app/api/v1/admin.py:1163`, `:1178`; `app/core/simulator/real_scenario_seeder.py:163`; `scripts/seed_db.py:133`, `:320`; `scripts/measure_clearing_min_amount_plan.py:246`; `scripts/measure_p021_trust_line_batches.py:236`) |
| **Equivalent precision / active flag** | Admin takes NO KEY UPDATE, checks existing lines/debts/journal before lowering precision, then assigns fields. No separate RC guard. (`app/api/v1/admin.py:1219`, `:1261`, `:1286`, `:1290`) |
| **Equivalent integrity hold** | Reaction uses a fresh REPEATABLE READ transaction, reconfirms failure, then conditionally UPDATEs an unheld equivalent. Admin clear takes equivalent UPDATE and latest-result SHARE, then conditionally clears the pointer. (`app/core/ledger/reconciliation.py:1234`, `:1241`, `:1264`; `app/api/v1/admin.py:1389`, `:1403`, `:1422`) |
| **Equivalent deletion** | Admin UPDATE-locks the equivalent, requires inactive/unused, deletes its empty baseline header, then deletes the equivalent. Offsets and debt/journal FKs prevent removal of accounting evidence; reconciliation reports cascade on equivalent deletion. (`app/api/v1/admin.py:1492`, `:1502`, `:1509`, `:1527`, `:1531`; `app/db/reconciliation_tables.py:105`, `:161`; `app/db/models/debt.py:29`) |
| **Participant status changes** | Admin freeze/unfreeze/ban/unban use the common locked status writer. Inject freeze uses UPDATE and writes immediately; mixed-event targets are prelocked exclusively. No debt journal entry is expected for status alone. (`app/api/v1/admin.py:912`, `:919`, `:946`, `:966`, `:986`, `:1006`; `app/core/simulator/inject_executor.py:1004`, `:1025`; `app/core/simulator/real_runner_impl.py:465`) |
| **Participant initial status** | Registration, scenario seeding, inject-add and script/benchmark constructors create initial rows. Profile update changes only display name/profile, not status. (`app/core/participants/service.py:62`, `:191`; `app/core/simulator/real_scenario_seeder.py:195`; `app/core/simulator/inject_executor.py:702`; `scripts/seed_db.py:146`, `:349`; `scripts/measure_p021_trust_line_batches.py:239`) |
| **Transactions** | Payment inserts COMMITTED in the money transaction; staged and separate-session refusal paths insert ABORTED with conflict-do-nothing. Clearing inserts NEW and changes it to COMMITTED in its transaction. Fixture importer inserts records independently. Admin “abort” does not mutate transaction state. (`app/core/payments/service.py:1678`, `:2173`, `:2840`; `app/core/clearing/service.py:1952`, `:2042`; `scripts/seed_db.py:527`; `app/api/v1/admin.py:1111`) |
| **Operations / memberships / journal entries** | Book inserts OPEN, inserts memberships and completes the envelope. Debt triggers alone insert journal rows; guards reject journal editing and open-envelope commit. No independent money-boundary checks are added by these metadata writers. (`app/core/ledger/book.py:1054`, `:904`, `:911`; `migrations/versions/029_debt_journal_by_the_database.py:129`, `:208`, `:251`) |
| **Baseline headers / offsets** | Admin creation and baseline CLI call `take_baseline`: equivalent NO KEY UPDATE, baseline-existence check, then header/offset inserts. No explicit RC guard inside this function. Header deletion exists only in the admin equivalent-delete path found above. (`scripts/take_reconciliation_baseline.py:49`; `app/core/ledger/reconciliation.py:1157`, `:1175`, `:1181`; `app/api/v1/admin.py:1527`) |
| **Reconciliation results** | `record_outcome` updates timestamps/latest markers or inserts a transition; scheduled verification and confirmed-failure reaction call it. These are evidence writers, not journaled money writers. (`app/core/ledger/reconciliation.py:1073`, `:1081`, `:1086`, `:1268`, `:1393`) |

Migration-specific mutations are also exceptions to the online protocol: 029 installs the triggers and its downgrade backfills `flush_count`; 035 rewrites `frozen` lines to `active`; 036 clears clearing initiators, with a reconstructing downgrade. 030–034 otherwise change schema/preconditions rather than posting debt movements. (`migrations/versions/029_debt_journal_by_the_database.py:379`, `:425`; `migrations/versions/030_payment_rows_are_terminal.py:60`; `migrations/versions/031_drop_prepare_locks.py:60`; `migrations/versions/033_trust_line_close_requested_at.py:34`; `migrations/versions/034_simulator_run_seed_bigint.py:25`; `migrations/versions/035_trust_line_status_without_frozen.py:30`; `migrations/versions/036_clearing_records_no_initiator.py:42`, `:49`)

`seed_recipe` delegates to services/admin handlers; it is not another physical debt writer. Docker invokes migrations; its seed invocation is commented out. Database reset/drop tools destroy an entire dedicated development database outside the row protocol. These must not be confused with online accounting operations. (`scripts/seed_recipe.py:470`, `:663`, `:690`; `docker/docker-entrypoint.sh:26`, `:29`; `scripts/dev_database.py:327`, `:359`, `:387`)

**2. LOCK PROTOCOL UNDER READ COMMITTED**

**a. Trust drift — FOUND, already documented; not counted again.** Growth/decay acquire individual lines in traversal order. A concrete deadlock schedule is: drift locks L2; payment locks L1 then waits for L2; drift next requests L1. Growth rolls back on failure; decay’s caller rolls back before continuing. I found no basis to label that deadlock itself an accounting loss. The stale cached limit used by decay is already recorded in 028. (`app/core/simulator/trust_drift_engine.py:341`, `:373`, `:296`, `:498`, `:557`; `app/core/simulator/tick.py:1325`; `specs/028-backlog-rework/spec.md:232`, `:499`)

**b. Staged phase — NOT FOUND: new committed overspend beyond the supplied findings.** It prelocks the perimeter, serializes actions sharing the session, and each action still binds its actual pair locks. A conflict propagates to whole-phase replay on a fresh session; it is not swallowed as a successful partial phase. (`app/core/simulator/tick.py:496`, `:507`; `app/core/simulator/real_payments_executor.py:427`, `:506`; `app/core/simulator/money_replay.py:126`, `:516`)

An unmeasured late-line schedule remains useful: phase S holds L2; create and commit previously absent L1; API writer A locks L1 and waits for L2; S’s later payment requests L1. The settling assertion is whole-phase rollback/replay on `40P01`, with no durable prefix or duplicate effect—not “no deadlock can happen.” (`app/core/money_boundary.py:28`, `app/core/payments/service.py:1792`, `app/core/simulator/money_replay.py:13`)

**c. Participant freeze — NOT FOUND: a payment committing after an already-committed freeze while bypassing the status guard.** If freeze commits first, the locking status read rejects the route. If the payment acquires SHARE first, freeze must wait, allowing the payment to linearize before freeze. Transit participants are included. Inject prelocks freeze targets exclusively rather than upgrading a shared lock midway. (`app/core/money_boundary.py:131`, `:150`; `app/core/payments/service.py:1791`; `app/api/v1/admin.py:912`; `app/core/simulator/real_runner_impl.py:465`)

**d. Equivalent PATCH/DELETE — NOT FOUND on the inspected ordinary writers.** The conflicting equivalent locks protect deactivation/deletion; precision lowering refuses any existing line, debt or journal entry. Initial import exceptions are listed in form 1 and must not be inferred to have these guards. (`app/api/v1/admin.py:1219`, `:1264`, `:1492`; `app/core/money_boundary.py:250`; `app/core/trustlines/service.py:837`)

**e. Hold starvation — NOT ESTABLISHED.** The reaction UPDATE conflicts with writers’ SHARE locks, but source inspection does not establish queue fairness or a maximum reaction delay. (`app/core/ledger/reconciliation.py:1241`, `app/core/money_boundary.py:250`)

Exact stand: park W0 holding equivalent SHARE; let H finish confirmed FAILED verification and queue its UPDATE; start successive ordinary writers W1…Wn while retaining overlap; release W0. Observe lock admission and hold commit. A wrong result would be later writers continually bypassing queued H so that money continues without the hold becoming durable. I make no claim that PostgreSQL actually exhibits that result.

**f. Late/reopened line and absent live line — NOT FOUND: an unlocked late row used by ordinary payment capacity.** Binding consumes the rows returned by the locking statement, not a later unrestricted line query. A pair with no active line has zero payment capacity; clearing requires a supporting eligible line. SEED/NewDebt remains the bootstrap exception. (`app/core/money_boundary.py:158`, `app/core/payments/service.py:1877`, `app/core/payments/capacity.py:28`, `app/core/clearing/service.py:951`, `app/core/ledger/book.py:539`)

**g. Statement snapshots — NOT FOUND on the bound ordinary pair paths.** Capacity and reverse-debt reads occur after pair locks; growth checking reads current debt and supporting limit in one joined statement. Those guarantees depend on every competing writer respecting the same pair protocol; bootstrap/import and direct Book invocation do not independently establish it. (`app/core/payments/service.py:1792`, `:1880`; `app/core/invariants.py:86`; `app/core/ledger/book.py:866`)

**h. Identity-map staleness — NOT FOUND as a demonstrated application loss.** Locking participant/line reads use columns, and trust-line edits explicitly refresh ORM state. Book debt reads and clearing’s ORM debt lock do not use `populate_existing`; ordinary payment retries and clearing occurrences use fresh sessions, while staged payments retain their perimeter locks. (`app/core/money_boundary.py:131`, `:158`; `app/core/trustlines/service.py:569`; `app/core/ledger/book.py:326`; `app/core/clearing/service.py:1711`; `app/core/clearing/runner.py:304`; `app/core/payments/service.py:2247`)

The exact unresolved stand is a caller-owned session retaining a loaded Debt across a competing commit, followed by execution in that same session. The required assertion is either a fresh value or a version-conflict rollback; a successful overwrite based on the retained value would establish the defect. I did not establish a production caller that permits that schedule.

**3. BOOK AND THE TRIGGER — NOT FOUND: a new journal-loss or partial-envelope defect**

- Direction reversal reduces/deletes the reverse debt, creates/increases the residual forward debt, and flushes; trigger entries record each actual transition. Zero debts are deleted, and storage checks reject persistent zero/self-debt rows. (`app/core/ledger/book.py:358`, `:368`, `:377`, `:393`; `migrations/versions/029_debt_journal_by_the_database.py:129`; `app/db/models/debt.py:68`, `:72`)
- A shared multipath hop is reserved cumulatively and posted sequentially. Payment-v2 replay applies the same ordered flows and compares their resulting edge deltas. This protects repeated legitimate flow use; it does not solve the already-known missing request-to-route binding. (`app/core/payments/service.py:1817`, `:1979`; `app/core/ledger/reconciliation.py:823`)
- Self-payment is refused at payment entry. Current clearing occurrences require unique debt IDs and a simple directed cycle with unique debtors. A manually supplied repeated-node payment route falls under the previously identified route-validation gap, not a new finding here. (`app/core/payments/service.py:1181`; `app/core/clearing/service.py:103`, `:1773`)
- Book rejects unstorable effect values; the debt column/check enforces positive finite scale-8 storage bounded by `999999999999.99999999`. This storage guarantee is distinct from the missing clearing step check in R2-1. (`app/core/ledger/book.py:211`, `:593`; `app/db/models/debt.py:34`, `:68`)
- Body failure rolls back Book’s savepoint. Failure to roll that back invalidates/terminates the connection rather than allowing a later commit. A deferred database trigger independently rejects an OPEN operation at commit. (`app/core/ledger/book.py:1073`, `:1081`, `:972`, `:949`; `migrations/versions/029_debt_journal_by_the_database.py:251`, `:307`)
- Journal rows belong to the same transaction/savepoints as debt DML. Sequence gaps are permitted; completion counts rows rather than ordinal differences. The operation GUC is transaction-local and cleared before releasing the normal savepoint. I found no session-level GUC assignment here. (`app/core/ledger/book.py:929`, `:935`, `:916`; `migrations/versions/029_debt_journal_by_the_database.py:129`)

**4. CLEARING — FOUND: R2-1; NOT FOUND: a new scope, consent or duplicate-occurrence defect**

The snapshot is advisory. Execution rechecks current participant status, locked supporting-line consent, equivalent stop/hold, debt existence, declared equivalent, simple closed-cycle structure, perimeter membership and `c <= current amount`; it then verifies neutrality over the locked cycle pairs. (`app/core/clearing/service.py:1681`, `:1692`, `:1705`, `:1722`, `:1752`, `:1769`, `:1773`, `:1782`, `:1799`, `:2033`)

Thus an E1 descriptor cannot execute against E2 debt rows, and a scoped invocation cannot mutate participants outside its supplied perimeter. The code checks containment in the supplied perimeter, not a cryptographic identity of the original planning perimeter. The runner supplies the same scope to snapshot and execution. (`app/core/clearing/service.py:1752`, `:1769`; `app/core/clearing/runner.py:355`, `:377`)

The renewable lease stops new occurrences after loss; it intentionally allows the in-flight occurrence to finish. Without Redis, the lease is not distributed. Even then, overlapping runners must pass the database rechecks; lease uniqueness is not the accounting barrier. (`app/utils/distributed_lock.py:147`, `:259`; `app/core/clearing/runner.py:366`; `app/core/clearing/service.py:1782`)

Occurrence identity is derived from plan/equivalent/ordinal; replay validates the stored descriptor and amount. Unknown commit resolution reads that identity on a fresh session, and an unresolved outcome is not blindly retried. Cancellation after established commit carries the committed occurrence to the runner. (`app/core/clearing/service.py:114`, `:306`, `:414`, `:2051`, `:2116`; `app/core/clearing/runner.py:309`)

**5. RECONCILIATION RULES — FOUND: R2-3; existing limitations excluded from the count**

| Kind/version | What criterion (b) establishes |
|---|---|
| PAYMENT v2 | Replays declared flows from explicit prestate for both directions, then compares exact edge deltas. It does not bind those flows to the independently stored requested payer/payee/amount. (`app/core/ledger/reconciliation.py:795`, `:818`, `:823`) |
| PAYMENT v1 | Only checks that journal edges belong to declared flow pairs; it does not recompute amounts. (`app/core/ledger/reconciliation.py:829`) |
| CLEARING v1 | Checks edge uniqueness, balanced incidence and declared minimum, then recomputes reductions. Balanced incidence is weaker than “one simple cycle.” (`app/core/ledger/reconciliation.py:620`, `:627`, `:630`) |
| CLEARING v2 | Checks occurrence identity/descriptor, equivalent, unique simple cycle, positive atom amount bounded by prestate, transaction payload agreement, exact reductions and zero-row deletion semantics. It does not check accounting-step divisibility or transaction terminal state. (`app/core/ledger/reconciliation.py:659`, `:717`, `:725`, `:729`, `:740`, `:756`) |
| INJECT v1 | Per-equivalent bounds on positive increases, step, edge count and total amount—not full replay. Already identified internally. (`app/core/ledger/reconciliation.py:849`, `:865`, `:889`, `:893`) |
| SEED / TEST_FIXTURE | Explicitly NOT_EXAMINED; this bypass occurs before version dispatch. Already identified internally. (`app/core/ledger/reconciliation.py:920`) |
| Unknown examined kind/version | Produces `b_version_unsupported`; no permissive default rule. (`app/core/ledger/reconciliation.py:925`) |

For “payment amount X, journal Y,” **summing every journal delta is not a valid payment-amount check**: a multihop payment changes several edges, and reverse repayment changes signs. The appropriate independent check is the requested endpoint-position delta plus transit neutrality, bound to the request. Current v2 replay verifies the supplied flows instead. (`app/core/ledger/reconciliation.py:797`, `:823`; `app/core/money_boundary.py:350`)

The operation FK prevents a COMPLETED payment/clearing envelope referencing an absent transaction under normal constraints. It does not prevent an ABORTED transaction with an operation, or the reverse orphan-transaction case. (`app/db/journal_tables.py:163`, `:200`; `app/core/ledger/reconciliation.py:463`)

Baseline capture locks the equivalent before reading debt/journal offsets; it adopts unexplained opening amounts rather than certifying them. The documented procedure remains a quiet cutover. (`app/core/ledger/reconciliation.py:1141`, `:1157`, `:1165`)

A held E1 does not suppress verification of E2: the scheduler enumerates equivalents separately, including inactive/held ones. However, processing is sequential, so a long or blocked E1 reaction delays later equivalents. Whole-history journal reads and operation replay grow with history; the 300-second interval starts after the previous run finishes. No runtime-cost bound was established. (`app/core/ledger/reconciliation.py:355`, `:444`, `:1368`, `:1384`, `:1405`; `app/core/maintenance_jobs.py:146`)

**Additional uncounted hypothesis: stale PASSED can authorize clearing a newer hold.** Publication ordering is explicitly not enforced; the clear endpoint checks latest PASSED and a different result ID, not snapshot freshness. (`app/core/ledger/reconciliation.py:1030`, `:1055`; `app/api/v1/admin.py:1406`)

Exact stand: verifier A captures a valid snapshot and pauses before publication; introduce a criterion-(b) failure; verifier B confirms it and commits a hold; A publishes its older PASSED; invoke admin hold-clear. Expected wrong result if the schedule is reachable: the hold clears while a fresh verification remains FAILED. Overlap requires multiple processes without the integrity lock, or exceeding its nonrenewed TTL; this is not established for the configured single-worker deployment. (`app/core/ledger/reconciliation.py:1038`, `:1049`; `app/core/maintenance_jobs.py:92`)

**6. EQUIVALENT INDEPENDENCE — NOT FOUND: a new cross-equivalent monetary sum in the inspected paths**

The inspected accounting aggregates retain the equivalent dimension: participant trust statistics group by code/precision; balance summaries retain per-equivalent entries; admin balance/counterparty/rank queries group or filter by equivalent; unscoped admin liquidity does not execute monetary sums or rankings. (`app/core/participants/service.py:116`, `:128`; `app/core/balance/service.py:103`, `:233`; `app/core/admin/metrics.py:200`, `:298`, `:371`; `app/api/v1/admin.py:793`, `:859`)

Simulator debt snapshots include equivalent in grouping and keys; snapshot/edge-patch queries filter it; tick totals and inject caps are per equivalent. Routing graph/topology caches and clearing lease keys are equivalent-scoped. (`app/core/simulator/real_debt_snapshot_loader.py:69`, `:79`; `app/core/simulator/snapshot_builder.py:136`; `app/core/simulator/edge_patch_builder.py:102`; `app/core/simulator/tick.py:1416`; `app/core/simulator/inject_executor.py:587`; `app/core/payments/router.py:42`, `:221`; `app/core/clearing/runner.py:100`)

There **is operational coupling**: participant status is global; staged transactions can lock multiple equivalents; a mixed inject event checks all named debt equivalents before staging, and a held one refuses the entire event. These are not arithmetic conversions or sums, but should not be described as complete failure-domain independence. (`app/core/simulator/tick.py:496`; `app/core/simulator/real_runner_impl.py:472`, `:495`, `:593`; `specs/028-backlog-rework/spec.md:40`)

**7. PRECISION / STEP — FOUND: R2-1 and R2-4**

Ordinary payment binding validates flow amounts against the step; routing floors capacity to it; trust-line create/update and simulator inject validate amounts; scenario initial lines validate after refreshed equivalent SHARE locks. Precision lowering is refused when the equivalent has existing accounting data. (`app/core/payments/service.py:1799`; `app/core/payments/router.py:343`; `app/core/trustlines/service.py:837`; `app/core/simulator/inject_executor.py:581`; `app/core/simulator/real_scenario_seeder.py:227`, `:294`; `app/api/v1/admin.py:1261`)

Floats used only for visualization widths or logging ratios are not themselves debt writers. The trust-decay multiplication differs because its result is written as a limit. (`app/core/simulator/edge_patch_builder.py:110`, `:117`; `app/core/simulator/real_payment_planner.py:643`; `app/core/simulator/trust_drift_engine.py:606`)

Legacy sub-step storage remains possible; preserving exact scale-8 values on output does not enforce the accounting step. (`app/db/models/debt.py:34`; `app/core/clearing/service.py:1866`; `scripts/seed_db.py:473`)

**8. IDEMPOTENCY / OUTCOMES — FOUND: R2-2; NOT FOUND: a new double-posting path**

| Exit | Source behavior |
|---|---|
| Existing payment identity | Checks type, initiator, fingerprint and scoped routes before returning the stored result. (`app/core/payments/service.py:789`) |
| Failure before admission | No definitive-refusal row is required by the admission predicate. (`app/core/payments/service.py:491`) |
| Admitted ordinary refusal | Rolls back and records ABORTED separately; concurrent winner handling contains R2-2. (`app/core/payments/service.py:2467`, `:2511`) |
| Retryable `40001`/`40P01` | Whole-attempt retry; exhaustion stays retryable and does not record a terminal ABORTED result. (`app/db/sqlstate.py:33`; `app/core/payments/service.py:2491`) |
| Server-confirmed COMMIT rejection | Classifies the server result and retries or records refusal as appropriate. (`app/core/payments/service.py:2532`) |
| Lost connection / timeout / cancellation around COMMIT | Reads identity freshly; an absent row is explicitly not treated as proof of rollback; no blind retry or terminalization. An unresolved timeout still reports E007, which does not establish whether money committed. (`app/core/payments/service.py:2551`, `:2587`, `:2594`) |
| Cancellation after established payment commit | Keeps the cancellation signal; applies own post-commit effects only when the durable row belongs to this attempt. A cancelled caller cannot be promised a delivered success response. (`app/core/payments/service.py:2574`, `:2585`) |
| Staged phase unknown outcome | Resolves the phase’s written identities; unresolved outcome is not a transient replay. (`app/core/simulator/money_replay.py:41`, `:92`) |
| Clearing unknown outcome / cancellation | Drains commit, resolves occurrence identity, avoids blind replay, and explicitly carries committed-after-cancellation progress. (`app/core/clearing/service.py:460`, `:2051`, `:2102`, `:2116`) |

The supplied same-identity stand exercises concurrent **success/success**, not competing definitive refusals. Its passing result therefore does not settle R2-2. (`tests/integration/test_p029_adv_concurrency_postgres.py:528`, `:542`)

**9. AUTHORIZATION CONSISTENCY — FOUND: intentionally different entry authorization; no additional authorization finding counted**

| Operation | Public path | Other state-changing paths |
|---|---|---|
| Trust-line create/update/close | Active participant bearer token; creditor ownership and participant signature in service wrappers. (`app/api/v1/trustlines.py:24`, `:74`, `:84`; `app/core/trustlines/service.py:296`, `:334`, `:355`) | Interact uses simulator actor/run/perimeter checks and calls execution without participant signature; drift/inject use internal execution; initial imports use import semantics. (`app/api/v1/simulator.py:1263`, `:1268`, `:1353`, `:1487`, `:1570`; `app/core/trustlines/service.py:752`) |
| Payment | Active bearer participant plus signed payment request. (`app/api/v1/payments.py:64`; `app/core/payments/service.py:1214`) | Interact and staged simulator use internal unsigned commands, with run perimeter and money-boundary checks. Recipe payments sign their requests. (`app/api/v1/simulator.py:1619`, `:1624`, `:1678`; `app/core/payments/service.py:1003`; `scripts/seed_recipe.py:660`) |
| Clearing | Execution relies on current line consent and monetary guards, not participant signatures on each occurrence. (`app/core/clearing/service.py:1799`) | Awaited, periodic and simulator runners feed the same occurrence execution. (`app/core/clearing/runner.py:304`, `:428`, `:472`) |
| Freeze/unfreeze/ban | Admin router dependency, then common status writer. (`app/api/v1/admin.py:114`, `:899`) | Inject freeze is scenario-driven and checks simulated-participant identity; recipe freeze directly invokes the admin handler as a trusted local script. (`app/core/simulator/inject_executor.py:1015`; `scripts/seed_recipe.py:690`) |
| Deactivate/delete/step change/hold clear | Admin router dependency plus the state checks inventoried above. (`app/api/v1/admin.py:114`, `:1209`, `:1352`, `:1480`) | Initial constructors bypass HTTP authorization; they are local/bootstrap writers, not authenticated REST requests. (`scripts/seed_db.py:133`; `app/core/simulator/real_scenario_seeder.py:163`) |

Admin authorization is a matching token, with an explicit dev-mode trusted-IP alternative—not an independently enforced role hierarchy. Simulator actors may be admin, active participant JWT or validated anonymous session cookie, with cookie-origin checks. (`app/api/deps.py:188`, `:198`, `:258`, `:316`)

**INFERENCE from the owner’s installation decision:** simulator authority may intentionally differ, but accounting semantics must remain the same. Separate production installation does not make simulator debts exempt from step, hold, freeze or journal requirements. Existing internal execution already shares several of those guards, so repairing the remaining gaps should extend that common boundary rather than create a second accounting model. (`specs/028-backlog-rework/spec.md:28`, `:40`; `app/core/payments/service.py:1033`; `app/core/simulator/real_runner_impl.py:495`)

**10. WHAT A TEST CANNOT SEE — FOUND: bounded structural guards, not completeness proofs**

- **Book-only debt guard:** misses raw SQL/`text()`/COPY, Core DML through table objects, dynamic `setattr`, bulk/merge paths, rebinding/re-exported aliases and roots outside `app/` and `scripts/`. The database trigger still captures ordinary debt DML that those source shapes conceal; it does not prove the operation followed the protocol or acquired its locks. (`tests/unit/test_p018_only_book_writes_debts.py:23`, `:61`; `migrations/versions/029_debt_journal_by_the_database.py:95`)
- **Simulator trust-line guard:** scans only simulator Python and its API file. It **does** catch literal unqualified raw SQL and expressions containing `TrustLine`; saying it misses all `text()` writes would be wrong. It misses, for example, `UPDATE public.trust_lines`, dynamically assembled names, aliased table objects, `setattr`, and `close_requested_at` assignments because that attribute is absent from its guarded set. (`tests/unit/test_p021_simulator_writes_trust_lines_only_through_the_service.py:39`, `:42`, `:58`, `:87`, `:91`, `:97`)
- **No-second-dialect guard:** checks selected Python syntax and literals, not actual runtime isolation or every launcher/configuration surface; dynamically constructed names and non-Python sources are explicit blind spots. (`tests/unit/test_p017_no_second_dialect.py:18`, `:65`)

The supplied concurrency harness parks a real writer and observes blockers, and its unlocked-line control demonstrates sensitivity to the tested limit race. That is meaningful evidence for those schedules, not proof covering all writer entrypoints or all transaction outcomes. (`tests/integration/test_p029_adv_concurrency_postgres.py:145`, `:170`, `:550`, `:566`)

**Advice for the implementation spec — eight ordered changes**

These are recommendations, not claims of implementation. Each stand below is expected to expose a current gap; none was executed in this review.

| Order | Observable loss closed | Cheapest mechanism and honest cost | Red-first stand |
|---|---|---|---|
| **1** | A correctly journaled operation can still implement the wrong requested payment. | Bind requested equivalent/payer/payee/amount to flows at the execution boundary; check endpoint deltas and transit neutrality. Extend replay to the same independently stored request. **Medium:** core check, intent-version compatibility and focused tests; no new ledger framework. (`app/core/payments/service.py:1752`, `:1951`; `app/core/ledger/reconciliation.py:795`) | Wrong endpoint, missing route portion, duplicated portion; assert refusal before durable debt changes. This closes the supplied internal finding. |
| **2** | A money operation can remain outside complete verification coverage. | Enforce baseline availability for ordinary movement, create it through every equivalent-creation path, and enforce “initial-only kinds before baseline” at the database completion boundary. **Medium:** one narrow trigger/check plus bootstrap sequencing. (`app/core/ledger/book.py:875`; `app/core/simulator/real_scenario_seeder.py:163`; `app/core/ledger/reconciliation.py:969`) | Ordinary payment on a simulator-created unbaselined equivalent; post-baseline initial-kind DML inside an otherwise valid envelope. |
| **3** | Detection can claim success without proving all declared outcomes. | Exact accepted-effect replay for INJECT; explicit initial-state verification; bidirectional transaction/operation correspondence and state agreement. **Medium–high:** historical/import policy is the main cost, not arithmetic. (`app/core/ledger/reconciliation.py:444`, `:849`, `:920`; `scripts/seed_db.py:527`) | R2-3, omitted INJECT effect, wrong injected edge with the same total, and unmatched transaction state. |
| **4** | Confirmed divergence or failed verification can fail to stop movement or leave misleading health. | Separate reconciliation scheduling from checkpoint success; persist current verification failure/freshness; make the equivalent’s stop decision explicit at the existing money boundary. Reverify under the clear operation’s exclusion lock before releasing a hold. **Medium:** lifecycle/error-state design; no extra monitoring subsystem. (`app/core/maintenance_jobs.py:98`; `app/core/ledger/reconciliation.py:1395`, `:1405`; `app/api/v1/admin.py:1406`) | Inject checkpoint failure and verifier failure after a prior PASSED; assert movement/health follow the decided policy. Also run the stale-publication/hold-clear schedule above. |
| **5** | Sub-step obligations can be extinguished contrary to the accounting contract. | Reject non-step clearing `c` at execution; validate new import amounts; make the planner step-aware where legacy data is retained. **Small immediate guard; medium legacy handling.** (`app/core/clearing/service.py:1781`; `scripts/seed_db.py:473`) | R2-1’s `0.015` triangle at precision 2. |
| **6** | A bypassing caller can miss stop/freeze/transaction ownership requirements. | Put each existing mutation’s authoritative guard in one callable core location and make entrypoints delegate. Consolidate staged ownership only where it removes a demonstrated divergent branch. **Medium:** careful migration of callers and cancellation behavior. (`app/core/money_boundary.py:131`, `:250`; `app/core/payments/service.py:1003`; `app/api/v1/admin.py:899`) | Call every supported entrypoint with held equivalent/frozen transit participant; assert the same refusal and no debt mutation. |
| **7** | A client receives a refusal different from the durable outcome. | Return either terminal winner from refusal collision resolution; give unrecorded/unresolved outcomes a distinct result. **Small–medium:** shared resolver plus concurrent-outcome tests. (`app/core/payments/service.py:2658`, `:2884`) | Both schedules in R2-2. |
| **8** | A configured decay rate silently removes an extra accounting unit of capacity. | Perform rate arithmetic in Decimal before multiplication. **Small:** local edit and one numeric regression case. (`app/core/simulator/trust_drift_engine.py:512`) | R2-4: require `93.00`, not `92.99`. |

I would **not** turn these findings into a second double-entry balance store, an outbox, cryptographic journal chaining, universal SERIALIZABLE execution, or a generalized authorization/transaction framework. The supplied material contains findings rather than concrete internal implementation proposals, so I cannot honestly attribute those proposals to the internal reviewers. The applicable test is the repository’s own: observable loss, cheapest sufficient mechanism, and proportionate cost. (`AGENTS.md:708`, `AGENTS.md:783`; `app/core/money_boundary.py:8`)

Likewise, do not rebuild the clearing optimizer merely because it uses storage atoms: first add the execution guard and the actual off-step counterexample. Do not rewrite structural tests into a Python data-flow analyzer when a database invariant can enforce the relevant state. (`specs/028-backlog-rework/spec.md:81`; `tests/unit/test_p018_only_book_writes_debts.py:38`)

**Three owner decisions are needed**

1. **Existing sub-step obligations:** refuse further affected operations until an explicit correction, or permit only step-aligned reductions while retaining exact residues. Silent rounding would change obligations and is not an acceptable technical default. (`specs/028-backlog-rework/spec.md:28`; `app/db/models/debt.py:34`)
2. **Recovery after a genuine historical protocol failure:** what makes a repaired equivalent usable when the immutable history still correctly records the earlier bad operation? Explicit adjudication/repair acceptance and a controlled new verification baseline have materially different meanings. Merely clearing the pointer does not answer this. (`app/core/ledger/reconciliation.py:909`, `:1159`; `app/api/v1/admin.py:1406`)
3. **Imported historical transaction records:** are they executable payment/clearing claims requiring matching ledger evidence, or historical presentation data that must be visibly outside that guarantee and replay identity space? The current importer does not encode that distinction. (`scripts/seed_db.py:493`, `:533`; `app/core/payments/service.py:789`)

Exact Decimal arithmetic, terminal-winner resolution, lock ordering, and enforcing an already-decided accounting step are technical decisions; they do not require another product fork. (`specs/028-backlog-rework/spec.md:28`, `:30`, `:40`)

**What I could not establish**

- I did not independently verify HEAD/history or execute the supplied stands; their reported results remain owner-supplied evidence. I inspected their scheduling and assertions.
- I did not establish hold-update starvation, a live stale-ORM overwrite, the staged late-line deadlock’s actual driver classification, or the stale-PASSED hold-clear schedule in a supported deployment.
- I did not establish a runtime upper bound for full-history reconciliation, multi-worker cache behavior, or production database/trigger configuration.
- Source searches and the writer inventory are not a proof against arbitrary dynamic SQL. The database trigger is the stronger debt-DML boundary, but it proves recording, not protocol correctness. (`migrations/versions/029_debt_journal_by_the_database.py:95`; `tests/unit/test_p018_only_book_writes_debts.py:23`)
- The final verdicts concern the requested end-to-end guarantee. They do not negate the supplied successful pair-lock schedules; the unresolved coverage and reaction paths prevent extending those results to every writer. (`app/core/ledger/reconciliation.py:920`, `:1395`; `app/core/clearing/service.py:1781`)

VERDICT-ZERO-SUM-ON-WRITE: GAPS
VERDICT-ZERO-SUM-CONCURRENT: GAPS
VERDICT-DETECTION-AND-REACTION: BROKEN
NEW-FINDINGS-COUNT: 4
CLASS-1-COUNT: 1
P1-COUNT: 1
P2-COUNT: 3
P3-COUNT: 0
OWNER-DECISIONS-NEEDED: 3