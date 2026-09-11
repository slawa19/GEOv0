# Phase B step 4 design (programme 015) — model constraints, operation envelopes, global flush hook, bounded seed/test contexts

Checked against HEAD f0764c8 (current HEAD 0a9b7b6 only changes inject lock-set pre-read). Contract: specs/015-financial-core-verification/spec.md:575-775. SQLAlchemy 2.0.25 behaviour verified in the installed source.

## 0. Four facts that shape the design
1. before_flush runs before the flush transaction exists (orm/session.py:4339-4341; flush tx begins :4405). An exception there rolls nothing back: the session transaction stays active with Debt changes pending. A swallowed refusal needs a second guard at commit.
2. after_flush runs after the SQL, before history reset (:4411-4413). new/dirty/deleted and attribute history still show pre-flush state, FK columns filled, pending parents inserted. An exception there rolls back the flush boundary (:4453-4455).
3. before_commit also fires on savepoint release (:1217-1219 `if self._parent is None or self.nested`). after_transaction_end fires on every close (:1377). A failed flush inside a savepoint rolls back only that savepoint.
4. AttributeState.history never loads (orm/state.py:1054-1079): expired = no history = the refusal case. Debt has version_id_col (app/db/models/debt.py:24), so UPDATE/DELETE from a stale previous value raises StaleDataError.

## 1. Writer inventory (re-verified; no Core/bulk DML on Debt in app/scripts; tests use 21 delete(Debt) in 17 files for PG cleanup + one update(Debt))
W1 Payment PaymentEngine._apply_flow: update engine.py:1437, delete :1441, insert :1450-1458, flush :1462; netting :1474-1489; per-lock flush :1261. Owner commit._uow :1028-1411; commit=True root commit :1402, retried by _run_uow_with_retry rollback :524/:560; commit=False savepoint :511, outer owned by tick. Identity tx_id. Context opened after TTL-abort branch :1214-1222, around :1224-1388, completed before delete(PrepareLock) :1390-1391.
W2 Clearing _execute_clearing_with_amount: debt.amount -= c clearing/service.py:1980, delete :1982, flush :1986. Postgres work_session AsyncSession(bind=connection) :1573-1577; commit :2051. Identity execution_tx_id (uuid5 over sorted debt ids :163-165,:1690) + CLEARING Transaction :1950-1968. Context around :1976-2050.
W3 Inject stage op_inject_debt insert inject_executor.py:529-536, update :555. Owner real_runner_impl._apply_inject_unit_of_work (locks, stage, explicit flush, commit). Identity (run_id, event_index).
W4/W5 Repairs api/v1/integrity.py (closed by INTEGRITY_REPAIRS_ENABLED).
W6 scripts/seed_db.py Debt :421-429, commit :578. Identity label + source manifest digest + batch id.
W7 scripts/measure_clearing_min_amount_plan.py (outside phase B perimeter; TRUNCATE raw).
N1 closed by T1524. Migrations/TRUNCATE: documented raw-SQL exception.

## 2. Schema: migration 021_debt_journal, declared identically on models, every constraint explicitly named
Money Numeric(20,8) like Debt.amount (BIGINT atoms cannot hold 10^20 atoms of Numeric(20,8) range); atoms only in digest encoding (money_encoding_version=1) and test reference.

debt_operations (envelope): id UUID PK; kind VARCHAR(32) CHECK IN (PAYMENT,CLEARING,INJECT,SEED,INTEGRITY_REPAIR,TEST_FIXTURE); identity VARCHAR(256); tx_id VARCHAR(64) NULL FK transactions.tx_id RESTRICT; intent JSON; intent_digest CHAR(64); schema_version, money_encoding_version, intent_encoding_version SMALLINT; opened_at; state CHECK IN (OPEN,COMPLETED); completed_at, flush_count, effect_count, effect_digest NULL until completion; UNIQUE(kind, identity); UNIQUE(tx_id); CHECK tx_id required iff kind in (PAYMENT, CLEARING); CHECK completion fields all null iff OPEN; partial index on OPEN.
debt_journal_entries: id; operation_id FK RESTRICT; flush_ordinal >=1; equivalent_id, debtor_id, creditor_id FK RESTRICT, debtor<>creditor; effect I/U/D; amount_before, amount_after NUMERIC NULL by shape; delta NUMERIC <>0; UNIQUE(operation_id, flush_ordinal, eq, debtor, creditor); index on edge. No arithmetic CHECK after-before=delta (SQLite REAL affinity); verifier checks it; before/after give per-edge continuity after(n)=before(n+1).
debt_operation_equivalents (per-equivalent ordering slot, written at completion): PK(operation_id, equivalent_id); epoch_no >=0; seq BIGINT >=1; effect_count, effect_digest; UNIQUE(equivalent_id, seq) — seq never resets.
debt_journal_heads: equivalent_id PK FK RESTRICT; epoch_no default 0; last_seq >=0; updated_at.
Epoch in step 4 minimal (epoch_no=0 = pre-baseline, UNVERIFIABLE); step 5 adds epochs table, baseline edges, hash columns on slots. All journal FKs RESTRICT. Downgrade refuses if tables have rows.

## 3. Operation context API (app/core/ledger/journal.py; listeners installed from app/db/models/__init__.py)
DebtOperationKind enum; DebtJournalError(RuntimeError) — never GeoException (payments/service.py:990 turns 4xx GeoException into REJECTED); subclasses ContextMissing, NestingRefused, KeyMutationRefused, PreviousAmountUnavailable, AmountNotStorable, BulkWriteRefused, OperationIncomplete.
`async with debt_operation(session, *, kind, identity, intent, tx_id=None, scope_equivalent_ids=None, intent_equivalent_ids=()) as handle`
Open: require in_transaction; refuse pending Debt in new/dirty/deleted; refuse OPEN/POISONED op in the current transaction chain (no nesting; sequential completed ops allowed); session.flush(); insert envelope via Core on session.connection() (state OPEN, intent digest over canonical_json app/core/auth/canonical.py:86); record in session.info["geo.debt_journal"] bound to get_nested_transaction() or get_transaction().
Complete (normal exit): flush; re-check OPEN and bound tx in chain; read entries from DB (authoritative — descendant savepoint rollbacks simply absent), sort, digest per equivalent; for each equivalent in touched ∪ intent_equivalent_ids sorted: head row insert-if-missing (last_seq=1) else UPDATE last_seq=last_seq+1 WHERE last_seq=:observed rowcount 1; insert slot; UPDATE envelope COMPLETED WHERE state=OPEN rowcount 1.
Exception in block: mark POISONED (no I/O, cancellation-safe), re-raise; rollback is the owner's.
Listeners on Session class: after_transaction_end drops records bound to that trans (and root poison on root end) => rollback/commit/savepoint rollback/release/close all clear the op, retries never reuse. before_commit: refuse if an OPEN/POISONED op is bound to the committing innermost tx or root poisoned; descendant savepoint release under an OPEN ancestor op allowed (_apply_flow :1429, nested abort).
Poisoning: every hook refusal poisons the ROOT before raising, so the transaction can never commit even if an `except Exception` swallows it.
Interplay: payment retry commit=True rollback clears op; commit=False op bound to savepoint :511; _apply_flow StaleDataError retry :1492-1505 rolls back only a descendant savepoint (op survives, ordinal gaps); invariant-violation path commit=True rollback :1297 then abort(commit=True) commits with no op; clearing commit errors -> rollback clears; tests/conftest.py db_session on Postgres (join_transaction_mode=create_savepoint, restart listener :275-278): op binds to fixture savepoint, class-level after_transaction_end runs before the fixture's instance listener.

## 4. The hook
before_flush (validation+computation, no SQL): refuse partial flush with pending Debt (objects not None); Debt in new/dirty/deleted; no single OPEN op in chain -> ContextMissing + poison. Insert: key from FK columns (None = refuse), after = history.added[0], effect I. Dirty: any change on id/debtor_id/creditor_id/equivalent_id/debtor/creditor/equivalent -> KeyMutationRefused; amount unchanged -> skip; before = history.deleted[0], empty -> PreviousAmountUnavailable; effect U. Deleted: key unchanged; before = history.deleted[0] if amount modified this flush (payment sets 0 then deletes engine.py:1437-1441; clearing :1980-1982) else history.unchanged[0]; neither -> refuse; effect D. "Previous" = before this flush (history reset every flush; netting flush :1489 sees value from flush :1462). Storability against the column's declared precision/scale (Debt.__table__.c.amount.type), not validation.MONEY_MAX_SCALE. Aggregate per key; D and I for one key in one flush refused; key outside scope refused. Stash rows in flush_context.attributes, stamp flush ordinal.
after_flush: insert stashed entries via Core on session.connection() (RESTRICT FKs to parents pending in same flush; failure rolls back flush boundary). Deviation from contract wording "before_flush writes the journal": registration in before_flush, write in after_flush of the same flush (F1).
do_orm_execute: statement.is_dml targeting table debts (ORM and Core Debt.__table__ through Session.execute) -> BulkWriteRefused + poison; same guard for the 4 journal tables. Not intercepted and documented: text(), connection.execute, legacy bulk_*. Architecture test scans app/ and scripts/ for Debt.__table__, bulk_*, text DML on debts. ORM dirty/deleted DebtOperation/DebtJournalEntry refused in before_flush.

## 5. Tests migration
169 real ORM Debt constructions in 53 test files (largest: test_clearing_additional_cases.py 40, test_clearing_sql_cycle_detection.py 17, test_invariants.py 14).
tests/debt_setup.py: `debt_fixture_setup(session, *, label)` (kind TEST_FIXTURE, identity nodeid:label:uuid4) and `add_debts(session, debts, *, label="setup")`; closes before test body continues; app code inside is refused (nesting); pending Debt at an app op's open refused. No global test mode/default context/skip flag.
Mechanical: AST codemod add/add_all(Debt) -> add_debts; direct mutation/deletion after setup wrapped in debt_fixture_setup(label="corrupt:..."): test_invariants.py:235,240,271,274; test_integrity_checkpoints.py:129; test_apply_flow_retry_on_stale.py:69; test_clearing_commit_replay_postgres.py:480; test_post_tick_audit_drift_runner_integration.py:250-275. Raw external writer in the B3 40001 test -> Core update(Debt.__table__) on a Connection. Tests driving writer internals (_apply_flow in test_apply_flow_retry_on_stale.py:78, test_debt_symmetry.py:60; stage_inject_event in test_p015_inject_transaction_ownership.py) open the REAL kind as the owner would (F7). 21 PG cleanups -> purge_ledger(conn, ...) via Core in FK order. SQLite per-test reset picks new tables automatically.

## 6. Step 2 counterexamples, to be written first (red for the stated reason; API imported inside test bodies; named mutation each; non-vacuity)
C1 Debt insert/update/delete with no op -> ContextMissing, nothing persisted, commit still raises until rollback.
C2 bulk DML on Debt through Session refused; controls select(Debt) and delete(PrepareLock) pass; documented limitation: connection.execute(text) not intercepted.
C3 key field / relationship change refused, no UPDATE.
C4 one op insert 10, update 7, update 12, delete -> (I,+10),(U,-3),(U,+5),(D,-12); set-0-then-delete records -previous.
C5 mutual D[a,b]=10, D[b,a]=7; payment a->b 5 via engine.commit -> per-edge sums equal fresh-read final minus initial; nothing doubled.
C6 wrong writer, honest journal: _apply_flow patched to route a->c; clearing reduces by c-1 atom -> journal equals actual state change; intent equals prepared flows parsed from PrepareLock.effects before commit; test-local int-atom algebra shows effects contradict intent.
C7 rollback then reopen same identity; savepoint-bound op rolled back while outer commits; descendant savepoint rollback inside open op.
C8 PG payment fake 40001 first commit; real concurrent 40001 on inject -> one envelope per tx_id, entries only from attempt 2, head +1 once.
C9 after commit/rollback/release, Debt write without new op refused; op on session A, write on session B refused.
C10 nesting refused; engine.commit inside debt_fixture_setup refused; sequential ops distinct seqs.
C11 commit/release with OPEN op -> OperationIncomplete; exception in block then commit; forced hook refusal inside clearing -> nothing durable.
C12 expired amount then delete -> PreviousAmountUnavailable; scale 9 or >=1e12 -> AmountNotStorable (PG control: without the hook PG would round).
C13 duplicate (kind, identity) IntegrityError; clearing replay and already-committed payment add no envelope.
C14 PG listener on DELETE FROM prepare_locks: envelope exists with intent flows equal to the locks; also for clearing work_session.
C15 subprocess import app.db.models.debt only -> listeners installed.
C16 PG two payments same equivalent, sequential and concurrent -> seq 1 and 2, unique, no gap.
C17 delete participant/equivalent with journal rows refused; route 409.
C18 version_id_col dependency (mutation-only).

## 7. Forks, with the architect's recommendation
F1 refusal/computation in before_flush, rows written in after_flush of the same flush — within "HOOK: D"? Rec: yes.
F2 Numeric(20,8) vs integer atoms. Rec: Numeric; atoms only in digest/reference. Caveat SQLite REAL affinity for large values.
F3 store before/after as well as delta. Rec: yes.
F4 epochs table in step 4 or 5. Rec: step 5.
F5 per-equivalent seq (head CAS at completion) in step 4. Rec: step 4; risk: more 40001 under SERIALIZABLE vs COMMIT_RETRY_ATTEMPTS=3 (config.py:121).
F6 separate TEST_FIXTURE kind in production CHECK. Rec: separate.
F7 tests calling _apply_flow/stage_inject_event directly open the real kind. Rec: yes.
F8 repairs: instrument (INTEGRITY_REPAIR) with flag closed, or leave blocked. Rec: instrument.
F9 measurement script outside perimeter: amend boundary (SEED op) or record blocked. Rec: amend.
F10 use envelope uniqueness to change inject at-most-once policy. Rec: not in step 4 (re-apply after restart fails on unique key, logged db error).
F11 storability refusal becomes a third barrier before the RT-012-1 counter-check. Rec: assert refusal, then widen the hook's check too.
F12 RESTRICT journal FKs make equivalents/participants with history undeletable. Rec: accept (409 exists).
F13 envelope for inject events with no Debt effect. Rec: always write.
F14 root-level poison even if a savepoint later rolls back the offending change. Rec: yes.
F15 flush ordinal gaps after descendant savepoint rollback. Rec: keep gaps.
F16 scope_equivalent_ids refusal. Rec: include.
F17 seed identity label:manifest_digest:batch_uuid. Rec: yes.
