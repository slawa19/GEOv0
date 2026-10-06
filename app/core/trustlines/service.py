from datetime import datetime, timezone
from uuid import UUID
from decimal import Decimal
from typing import List, Literal
from sqlalchemy import event, func, select, and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.money_boundary import MoneyBoundary
from app.utils.exceptions import (
    BadRequestException,
    NotFoundException,
    ForbiddenException,
    ConflictException,
    InvalidSignatureException,
)
from app.core.auth.canonical import canonical_json
from app.core.auth.crypto import verify_signature

from app.db.models.trustline import TrustLine
from app.db.models.participant import Participant
from app.db.models.equivalent import Equivalent
from app.db.models.debt import Debt
from app.db.models.audit_log import (
    TRUST_LINE_CLOSE,
    TRUST_LINE_CLOSE_REQUEST,
    IntegrityAuditLog,
    trust_line_close_completed,
)
from app.db.sqlstate import deliberate_chain, sqlstate
from app.schemas.trustline import TrustLineCloseRequest, TrustLineCreateRequest, TrustLineUpdateRequest
from sqlalchemy import inspect as sa_inspect
from app.utils.validation import (
    parse_money_amount,
    require_money_step,
    validate_equivalent_code,
    validate_trustline_policy,
)
from app.core.payments.router import PaymentRouter

_LIVE_TRUSTLINE_INDEX = "uq_trust_lines_live_from_to_equivalent"

#: 030 S5 (F-030-13, `T3000` item 1): a signed UPDATE/CLOSE is usable while `issued_at` is at most this old and at
#: most `SIGNATURE_MAX_CLOCK_AHEAD_SECONDS` ahead of the server's UTC clock. Constants, not settings.
SIGNATURE_MAX_AGE_SECONDS = 300
SIGNATURE_MAX_CLOCK_AHEAD_SECONDS = 30
TRUSTLINE_STATE_CHANGED = "TRUSTLINE_STATE_CHANGED"


def _rfc3339(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(text: object, *, field: str) -> datetime:
    try:
        value = datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        value = None
    if value is None or value.tzinfo is None:
        raise BadRequestException(f"{field} must be an RFC 3339 date-time with a UTC offset",
                                  details={"field": field, "reason": "not_rfc3339"})
    return value


def line_state(trustline: TrustLine) -> dict:
    """The four fields a signed UPDATE/CLOSE binds as `expected`, as the 409 reports them (`limit` as stored; a
    client compares it numerically)."""

    return {"limit": format(trustline.limit, "f"), "policy": dict(trustline.policy or {}),
            "status": str(trustline.status), "close_requested_at": _rfc3339(trustline.close_requested_at)}


def _is_live_trustline_uniqueness_violation(exc: IntegrityError) -> bool:
    """True only for a clash with the live-trustline partial unique index.

    Identity matters: renaming an unrelated IntegrityError into "trustline already exists"
    hands the caller a conflict they can neither understand nor act on.

    Only the DRIVER error is inspected, never `str(exc)`: the latter embeds the INSERT
    statement, whose column list contains `from_participant_id`/`to_participant_id`, so a
    text match against it classifies *every* failing INSERT on this table as a uniqueness
    clash.  An external review demonstrated exactly that.

    A second review then showed the text fallback was still too loose.  It now requires the
    full triple, an explicit uniqueness signal AND the table name, and it walks the whole
    `__cause__` chain rather than one level.

    That text fallback read SQLite's driver message, which carries neither a constraint name
    nor a sqlstate; it left with SQLite (programme 017, stage 3).  A PostgreSQL driver error
    always carries a sqlstate, so one that names no constraint and is not 23505 is not ours.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False

    # Collect the deliberate chain once: drivers wrap differently, nesting depth is not fixed,
    # and the facts we need are spread across several links of it.  Never `__context__`
    # (rule 2026-09-12, `app/db/sqlstate.py`; 024 `T2415.1`): a failure raised while another
    # was being handled would inherit that one's constraint and be renamed into this clash.
    # From `orig`, not `exc`: the wrapper's text embeds the INSERT and its column list.
    chain = list(deliberate_chain(orig))

    for link in chain:
        name = getattr(link, "constraint_name", None)
        if name:
            # The driver told us exactly which constraint failed -- no guessing needed.
            return str(name) == _LIVE_TRUSTLINE_INDEX

    # PostgreSQL without a constraint name: 23505 is unique_violation.  The sqlstate and
    # the table/detail need NOT live on the same link: SQLAlchemy's asyncpg adapter raises
    # a wrapper carrying only `pgcode`/`sqlstate` and chains the real asyncpg error, which
    # is the one holding `table_name` and `detail`
    # (sqlalchemy/dialects/postgresql/asyncpg.py:785-796).  Reading them off the first link
    # with a sqlstate therefore saw an empty table and a message without the DETAIL line,
    # and rejected a genuine live-triple conflict -- a 500 on exactly the path this
    # classifier exists to keep declared.
    if any(
        sqlstate(link, walk=False, bare_code=False) == "23505"
        for link in chain
    ):
        tables = [str(getattr(link, "table_name", "") or "") for link in chain]
        tables = [t for t in tables if t]
        if tables and all(t != "trust_lines" for t in tables):
            return False
        detail = " ".join(
            f"{getattr(link, 'detail', '') or ''} {link}" for link in chain
        )
        return _matches_live_triple(detail)

    return False


def _matches_live_triple(text: str) -> bool:
    """All three columns of the live index must be named, not just two of them."""
    lowered = text.lower()
    return all(
        column in lowered
        for column in ("from_participant_id", "to_participant_id", "equivalent_id")
    )


#: Where the internal trust-line path labels an audit row as part of a caller transaction (programme 021, stage 1;
#: the name predates 024 `T2413.2`, which removed the checkpoints themselves - the key is wire-stable).
#: `affected_participants` is the audit row's open metadata object (`api/openapi.yaml`,
#: `IntegrityAuditLogAffectedParticipants`, `additionalProperties: true`).
CHECKPOINT_SCOPE_KEY = "checkpoint_scope"
CHECKPOINT_SCOPE_CALLER_TRANSACTION = "caller_transaction"

#: The statuses the audit rows written before 030 S3b carry in `initial_status` (the seeder imported a scenario's
#: line with its status; since S3b a closed initial line is a CREATE and a CLOSE). Kept for the wire enum only.
INITIAL_STATUSES = frozenset({"active", "closed"})  # 028 `F-028-29`: no `frozen`


class TrustLineWriteBatch:
    """The trust-line writes of ONE caller transaction: one audit row per operation, the batch owns them.

    Programme 021, stage 1 (spec, "Решения" item 9; `T2100`) made the batch the owner of the trust-line audit of
    a caller transaction; programme 024, step Ш3 (`T2413.2`) removed the full-equivalent integrity checkpoints
    it used to compute before the first and after the last mutation of each equivalent. Every applied operation
    still gets its own `IntegrityAuditLog` row - the record of the operation - staged in `finish()`, after every
    mutation is flushed and before the caller commits. The row says that no check ran in this transaction:
    `verification_passed = null`, empty checksums, `invariants_checked = {}`. What guards a trust-line
    operation are its own refusals, which raise (`execute_update`: a positive limit on a line whose close is
    requested); the whole equivalent is checked by the periodic checkpoint,
    by `POST /integrity/verify` and by the reconciliation.

    A batch of an internal caller labels its rows (`affected_participants.checkpoint_scope =
    "caller_transaction"`): they belong to one caller transaction that may hold several operations of one
    equivalent. A public operation is a batch of one; its rows keep their historical shape.

    NOT A TRANSACTION OWNER. It never commits, rolls back or retries. The caller commits after `finish()`, and on
    any failure - a refusal, an audit row that did not flush - the caller rolls its transaction back BEFORE it
    continues or translates the error (spec, item 7).

    A DETECTOR FOR A FORGOTTEN `finish()`. A batch that has applied operations arms a `before_commit` listener on
    its session: committing such a batch without `finish()` raises instead of making mutations durable without
    their audit rows. A real rollback of the session disarms it. This detects a mistake of an in-process caller;
    it is not a barrier against code that wants to bypass it.
    """

    def __init__(self, session: AsyncSession, *, transaction_scoped: bool) -> None:
        self.session = session
        self._transaction_scoped = transaction_scoped
        self._codes: dict[UUID, str] = {}
        self._operations: list[tuple[str, UUID, dict]] = []
        self._finishing = False
        self._finished = False
        self._discarded = False
        self._armed = False

    @property
    def applied_operations(self) -> int:
        return len(self._operations)

    @property
    def touched_equivalent_ids(self) -> list[UUID]:
        return sorted({eq_id for _op, eq_id, _a in self._operations}, key=str)

    async def _touch(self, equivalent_id: UUID, equivalent_code: str) -> None:
        """Before the first mutation of `equivalent_id` in this batch: remember its code for the audit rows."""

        if self._finishing:
            raise RuntimeError("TrustLineWriteBatch: already finished; open a new batch for new writes")
        self._codes.setdefault(equivalent_id, equivalent_code)

    def _record(self, operation_type: str, equivalent_id: UUID, affected: dict) -> None:
        if equivalent_id not in self._codes:
            raise RuntimeError("TrustLineWriteBatch: an operation was recorded before its equivalent was touched")
        self._operations.append((operation_type, equivalent_id, affected))
        if not self._armed:
            self._armed = True
            event.listen(self.session.sync_session, "before_commit", self._refuse_unfinished)
            event.listen(self.session.sync_session, "after_soft_rollback", self._on_rollback)

    def _refuse_unfinished(self, _session) -> None:
        if self._operations and not self._finished and not self._discarded:
            raise RuntimeError(
                "TrustLineWriteBatch: a commit would make trust-line mutations durable without their "
                "audit rows; call finish() first"
            )

    def _on_rollback(self, _session, previous_transaction) -> None:
        # A rollback of the session's own transaction discards what this batch staged; a savepoint rolled
        # back inside it does not, so the detector stays armed for that one.
        if not getattr(previous_transaction, "nested", False):
            self._discarded = True

    async def finish(self) -> None:
        """Flush the mutations, stage one audit row per operation, flush."""

        if self._finishing:
            raise RuntimeError("TrustLineWriteBatch: finish() called twice")
        if self._discarded:
            raise RuntimeError("TrustLineWriteBatch: its transaction was rolled back; nothing to finish")
        # `_finished` only once everything below succeeded: a finish() that fails half-way (an audit row that
        # did not flush) leaves the detector armed, so a commit that skips the owner's rollback still cannot
        # make the batch durable.
        self._finishing = True
        if not self._operations:
            self._finished = True
            return

        await self.session.flush()
        for equivalent_id in self.touched_equivalent_ids:
            await self._stage_audit_rows(equivalent_id)
        await self.session.flush()
        self._finished = True

    async def _stage_audit_rows(self, equivalent_id: UUID) -> None:
        """The audit rows of one equivalent's operations: the operation's record, no check ran (`T2413.2`)."""

        for operation_type, eq_id, affected in self._operations:
            if eq_id != equivalent_id:
                continue
            affected = dict(affected)
            if self._transaction_scoped:
                affected[CHECKPOINT_SCOPE_KEY] = CHECKPOINT_SCOPE_CALLER_TRANSACTION
            self.session.add(
                IntegrityAuditLog(
                    operation_type=operation_type,
                    tx_id=None,
                    equivalent_code=self._codes[equivalent_id],
                    state_checksum_before="",
                    state_checksum_after="",
                    affected_participants=affected,
                    invariants_checked={},
                    verification_passed=None,
                    error_details=None,
                )
            )


class TrustLineService:
    """Trust-line operations: one implementation of validation, mutation and audit.

    TWO ENTRANCES (programme 021, stage 1; spec "Решения" item 4, `T2100` item 2):

    * the PUBLIC operations `create`/`update`/`close` - one operation per transaction, the participant's Ed25519
      signature ALWAYS required, the service commits and answers;
    * the INTERNAL execution `execute_create`/`execute_update`/`execute_close` - runs inside the CALLER's
      transaction on a `TrustLineWriteBatch`, never commits, retries, publishes SSE or applies in-memory
      effects. Its `require_signature` is keyword-only with no default. Only NAMED trusted in-process callers of
      the simulator pass `False`; the parameter is an internal calling convention, never part of a request schema
      and never derived from a request (guarded by
      `tests/unit/test_p021_unsigned_trust_line_path_is_never_request_controlled.py`).

    What unsigned execution gives up is exactly proof of key possession and binding the request to a signature.
    Everything else holds on both entrances: owner matching, the money door (`parse_money_amount`), live-line
    uniqueness, the debt check of a close, status rules and audit.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    def begin_internal_batch(self) -> TrustLineWriteBatch:
        """A batch for a trusted internal caller: its audit rows are labelled transaction-scoped."""

        return TrustLineWriteBatch(self.session, transaction_scoped=True)

    # ------------------------------------------------------------------ public operations (always signed)

    async def create(self, from_participant_id: UUID, data: TrustLineCreateRequest) -> TrustLine:
        batch = TrustLineWriteBatch(self.session, transaction_scoped=False)
        trustline = await self.execute_create(batch, from_participant_id, data, require_signature=True)
        await batch.finish()
        # `execute_create` found the equivalent BY this code, so it is the row's code.
        equivalent_code = data.equivalent

        # The uniqueness conflict can surface HERE rather than at the flush above: a
        # competing transaction that has not committed yet does not block the INSERT, and
        # PostgreSQL raises only when the winner commits.  Both points must therefore map
        # to the same declared conflict.
        # 2026-08-22 / p009_t905 (`F-009-6`).  Everything the response needs is read and
        # materialised INSIDE the uncommitted transaction, and after the commit this path
        # performs no mandatory database read.  Before, the readback happened after the
        # commit, so a failure there reported a mutation that had already happened as
        # failed -- and the retry it invites is not idempotent.  `RT-009-5` shows the
        # failure is reachable, not theoretical.  With the readback moved before the
        # commit, the same failure now happens while the transaction is still open and
        # honestly undoes the mutation instead of misreporting it.
        await self.session.refresh(trustline)
        response = await self._hydrate_trustline(trustline)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            if not _is_live_trustline_uniqueness_violation(exc):
                raise
            raise ConflictException(
                "Active trustline already exists",
                details={"reason": "CONCURRENT_TRUSTLINE_CREATE"},
            ) from exc

        # In-memory only; `expire_on_commit=False` (`app/db/session.py:78`) keeps the
        # hydrated attributes valid, so serialising the response touches no connection.
        PaymentRouter.invalidate_cache(equivalent_code)
        return response

    async def update(self, trustline_id: UUID, user_id: UUID, data: TrustLineUpdateRequest) -> TrustLine:
        batch = TrustLineWriteBatch(self.session, transaction_scoped=False)
        trustline = await self.execute_update(batch, trustline_id, user_id, data, require_signature=True)
        await batch.finish()

        equivalent_code = (
            await self.session.execute(
                select(Equivalent.code).where(Equivalent.id == trustline.equivalent_id)
            )
        ).scalar_one()
        # See the note in `create`: readback before commit, no mandatory read after it.
        await self.session.refresh(trustline)
        response = await self._hydrate_trustline(trustline)

        await self.session.commit()
        PaymentRouter.invalidate_cache(equivalent_code)
        return response

    async def close(self, trustline_id: UUID, user_id: UUID, data: TrustLineCloseRequest) -> TrustLine:
        """Close, or request the close of, a line; returns its FACTUAL state (026 `T2603.1`): `closed`, or still
        `active` with limit 0 and `close_requested_at` while the debt it supports is owed."""

        batch = TrustLineWriteBatch(self.session, transaction_scoped=False)
        trustline = await self.execute_close(batch, trustline_id, user_id, data, require_signature=True)
        await batch.finish()

        equivalent_code = (
            await self.session.execute(
                select(Equivalent.code).where(Equivalent.id == trustline.equivalent_id)
            )
        ).scalar_one()
        # See the note in `create`: readback before commit, no mandatory read after it.
        await self.session.refresh(trustline)
        response = await self._hydrate_trustline(trustline)
        await self.session.commit()
        PaymentRouter.invalidate_cache(equivalent_code)
        return response

    # ------------------------------------------------------------------ execution in the caller's transaction

    async def execute_create(
        self,
        batch: TrustLineWriteBatch,
        from_participant_id: UUID,
        data: TrustLineCreateRequest,
        *,
        require_signature: bool,
        flush: bool = True,
        lock_timeout_ms: int | None = None,
    ) -> TrustLine:
        """Stage a new ACTIVE trust line in the caller's transaction and record it on `batch`.

        `lock_timeout_ms` bounds the wait on the pair's line locks (`55P03` past it); the inject passes its
        owner's budget (028 F-028-14). The public `create` passes none - its wait stays as before.

        `flush=False` (programme 021, stage 2) leaves the INSERT staged for the caller's next flush instead of
        sending it here. The inject executor needs it: an inject event's effects see each other only through
        the event's own flush points (the session runs with `autoflush=False`). A uniqueness clash
        then surfaces as a raw `IntegrityError` at that later flush - which is what the inject owner already
        classifies - and `batch.finish()` flushes before it stages the audit rows either way.
        """

        if require_signature and (not isinstance(getattr(data, "signature", None), str) or not data.signature):
            raise InvalidSignatureException("Missing signature")

        from_participant = await self.session.get(Participant, from_participant_id)
        if not from_participant:
            raise NotFoundException("Sender not found")

        # Storage-capacity door (012 / F-012-1).  `TrustLine.limit` is Numeric(20, 8) and this
        # service never validated the amount at all -- the schema only bounds it with `ge=0`.
        # Checked BEFORE `verify_signature` and before any write, so a limit the column cannot
        # hold can never become a signed commitment.
        #
        # `data.limit` is the client's own STRING (`TrustLineCreateRequest.limit: str`, as
        # `api/openapi.yaml` has declared all along), for the same reason `request.amount` is
        # one at `POST /payments`: the signature below is taken over it verbatim.  While the
        # schema typed it `Decimal`, pydantic destroyed the client's spelling before this
        # method ran, and whatever we signed was a spelling `str(Decimal)` re-invented -- for
        # `"0.00000001"` that is `"1E-8"`, so the client's signature over its own bytes could
        # never verify and the smallest storable limit was unsignable.  `require_non_negative`
        # is the schema's former `ge=0`, now behind the door with the other money rules.
        limit = parse_money_amount(data.limit, field="limit", require_non_negative=True)

        if require_signature:
            # Signature validation (proof-of-possession + binding of request fields).  `limit` is
            # the client's string verbatim -- see the door note above.
            signed_payload: dict = {
                "to": data.to,
                "equivalent": data.equivalent,
                "limit": data.limit,
            }
            if data.policy is not None:
                signed_payload["policy"] = data.policy

            # `canonical_json` OUTSIDE the try (the `POST /payments` shape, T1210-bis finding B).
            # It refuses floats by design, and `policy` may legitimately carry one: the canon
            # declares `max_hop_usage`/`daily_limit` as `oneOf` string|number, so a JSON number
            # with a fraction arrives here as `float`.  Inside the try that refusal was relabelled
            # "Invalid signature" - a client whose policy the canon blesses got a 401 it could not
            # act on, for a request whose signature was never even checked.  Outside, it surfaces
            # as the honest 400 naming the float.  (That such a policy is UNSIGNABLE at all - the
            # canon admits a number the canonical form cannot carry - is a recorded contract fork,
            # not this call site's to settle.)
            message = canonical_json(signed_payload)
            try:
                verify_signature(from_participant.public_key, message, data.signature)
            except Exception:
                raise InvalidSignatureException("Invalid signature")

        validate_equivalent_code(data.equivalent)
        if data.policy is not None:
            validate_trustline_policy(data.policy)

        # Check existence of 'to' participant (by PID)
        stmt = select(Participant).where(Participant.pid == data.to)
        result = await self.session.execute(stmt)
        to_participant = result.scalar_one_or_none()
        if not to_participant:
            raise NotFoundException("Recipient participant not found")

        # Check if self-trust
        if from_participant_id == to_participant.id:
            raise BadRequestException("Cannot create trustline to self")

        # Check equivalent
        stmt = select(Equivalent).where(Equivalent.code == data.equivalent)
        result = await self.session.execute(stmt)
        equivalent = result.scalar_one_or_none()
        if not equivalent:
            raise NotFoundException(f"Equivalent '{data.equivalent}' not found")

        # 028 `F-028-28` (owner В-1): both ends `FOR SHARE` first (the one order: participants -> lines), the status
        # read by that statement - no line to or from a suspended participant (409 `participant_suspended`).
        await MoneyBoundary(self.session).refuse_suspended_participants(
            [from_participant_id, to_participant.id], timeout_ms=lock_timeout_ms)
        # 027 stage 2 (§15 P1): the pair's lines `FOR UPDATE` first, so a creation waits for a money writer in
        # flight over the pair (which decides only from the lines it locked) - symmetric with every other writer.
        await MoneyBoundary(self.session).lock_pair_lines(
            [(equivalent.id, from_participant_id, to_participant.id)], timeout_ms=lock_timeout_ms)

        # 030 S3b (F-030-19): no new line in an equivalent the operator stopped or the integrity hold stands over -
        # the writers' one order, third lock (participants -> pair lines -> the equivalent row -> debts), `FOR SHARE`
        # to commit. Every entrance of a creation passes here: the public one, the inject's, the seeder's.
        await MoneyBoundary(self.session).refuse_inactive_equivalents([equivalent.id])
        await self._require_step(equivalent.id, limit, timeout_ms=lock_timeout_ms)

        # Only a LIVE line blocks a new one.  This matches the protocol precondition of
        # TRUST_LINE_CREATE — «Не существует активной линии (from, to, equivalent)»
        # (docs/ru/02-protocol-spec.md:333) — and, since migration
        # 019_trust_lines_partial_unique_live, it also matches the database: uniqueness is
        # enforced over `status <> 'closed'` only.
        #
        # Before that migration the constraint was unconditional, so a closed incarnation
        # made the INSERT below fail with a raw IntegrityError -> HTTP 500 on two ordinary
        # user calls (finding F-009-3 / B-A3-004).
        stmt = select(TrustLine).where(
            and_(
                TrustLine.from_participant_id == from_participant_id,
                TrustLine.to_participant_id == to_participant.id,
                TrustLine.equivalent_id == equivalent.id,
                TrustLine.status != 'closed'
            )
        )
        result = await self.session.execute(stmt)
        existing_trustline = result.scalar_one_or_none()
        if existing_trustline:
            raise ConflictException("Active trustline already exists")

        await batch._touch(equivalent.id, equivalent.code)

        # Create TrustLine
        trustline = TrustLine(
            from_participant_id=from_participant_id,
            to_participant_id=to_participant.id,
            equivalent_id=equivalent.id,
            limit=limit,
            policy=data.policy or {},
            status='active'
        )
        # NOTE ON TRANSACTION SHAPE.  The INSERT is deliberately left staged in the
        # caller-owned transaction rather than isolated in a SAVEPOINT.  The fail-closed
        # contract of this service depends on it: if any later step fails (the
        # audit), the exception propagates and the caller's rollback must remove the row.
        # `tests/unit/test_trustline_audit_fail_closed.py` pins exactly that, and an
        # earlier attempt to wrap the flush in a savepoint broke it.
        #
        # The uniqueness race is handled at the commit below instead, which is where the
        # conflict actually surfaces: a competing transaction that has not committed yet
        # does not block this INSERT.
        self.session.add(trustline)
        try:
            if flush:
                await self.session.flush()
        except IntegrityError as exc:
            # The conflict surfaces here when the competing transaction has already
            # committed.  Translate it into a declared conflict WITHOUT rolling back
            # ourselves: the transaction is already aborted, and the caller's own rollback
            # is what removes the staged row -- the same mechanism the fail-closed contract
            # relies on.
            if not _is_live_trustline_uniqueness_violation(exc):
                raise
            raise ConflictException(
                "Active trustline already exists",
                details={"reason": "CONCURRENT_TRUSTLINE_CREATE"},
            ) from exc

        batch._record(
            "TRUST_LINE_CREATE",
            equivalent.id,
            {"from": from_participant.pid, "to": to_participant.pid},
        )
        return trustline

    async def execute_update(
        self,
        batch: TrustLineWriteBatch,
        trustline_id: UUID,
        user_id: UUID,
        data: TrustLineUpdateRequest,
        *,
        require_signature: bool,
    ) -> TrustLine:
        """Change a live line's limit and/or policy in the caller's transaction and record it on `batch`.

        A limit below the line's current debt is ACCEPTED (026 `T2602`, owner 2026-09-29, F-026-1): it changes
        trust, not debt - nothing is written off or moved, `available` goes negative, and the debt above the new
        limit cannot grow (the growth gate of `Book`/`PaymentService`) but can still be repaid.

        THE ROW LOCK (spec 026, fork 5): the line is read `FOR UPDATE` before any decision. It is the only lock
        this path takes (no debt read), so it cannot close a cycle with the money path (lines `FOR UPDATE` in id
        order -> equivalent row `FOR SHARE` -> debts, 027 stage 2): an in-flight payment over the pair makes it
        wait; a payment that starts later waits for it and reads the new limit. A 40001/40P01 here is NOT
        retried: it propagates, the caller rolls the whole transaction back (the public PATCH answers 500
        `E010`), nothing is applied.
        """

        # 028 `F-028-6`: `populate_existing` - the row as the lock returns it. A caller that read the line earlier in
        # this session (the Interact actions do) would otherwise decide on its identity-map copy from before the wait.
        stmt = select(TrustLine).where(TrustLine.id == trustline_id).with_for_update().execution_options(
            populate_existing=True)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise NotFoundException("Trustline not found")

        if trustline.from_participant_id != user_id:
            raise ForbiddenException("Not authorized to update this trustline")

        # TRUST_LINE_UPDATE requires an ACTIVE line (docs/ru/02-protocol-spec.md:355).
        # Before migration 019 a closed row was the only row for its triple, so this was
        # merely a missing check; now a closed incarnation coexists with a live one, and
        # without this guard its id stays patchable forever -- i.e. recorded history could
        # be rewritten after the fact.
        if str(trustline.status) == "closed":
            # `current` since 030 S5: every 409 of the signed routes names the line's state (a retry reads it).
            raise ConflictException(
                "Cannot update a closed trustline",
                details={"reason": "TRUSTLINE_CLOSED", "trustline_id": str(trustline_id),
                         "current": line_state(trustline)},
            )

        user = None
        if require_signature:
            if not isinstance(getattr(data, "signature", None), str) or not data.signature:
                raise InvalidSignatureException("Missing signature")

            user = await self.session.get(Participant, user_id)
            if not user:
                raise NotFoundException("Sender not found")

        # Same storage-capacity door as `create`, before the signature and before the write;
        # `data.limit` is the client's string and the signature covers it verbatim, so the
        # parsed `Decimal` is kept apart from the signed payload -- see the note in `create`.
        new_limit = None
        if data.limit is not None:
            new_limit = parse_money_amount(
                data.limit, field="limit", require_non_negative=True
            )

        if require_signature:
            self._verify_line_operation("TRUST_LINE_UPDATE", trustline, user, data)

        # Every refusal BEFORE the first mutation and before the batch is touched. There is no debt floor
        # any more (026 `T2602`): a limit below `used` is a trust change, see the docstring. A requested close
        # keeps the limit at 0 (026 `T2603.1`, fork 6): a positive limit is a conflict and does not cancel it.
        if trustline.close_requested_at is not None and new_limit is not None and new_limit > 0:
            raise ConflictException(
                "Trustline close is requested; its limit stays 0 until it closes",
                details={"reason": "TRUSTLINE_CLOSE_REQUESTED", "trustline_id": str(trustline_id)},
            )
        if data.policy is not None:
            validate_trustline_policy(data.policy)

        equivalent_code = await self._equivalent_code(trustline.equivalent_id)
        if new_limit is not None:
            await self._require_step(trustline.equivalent_id, new_limit)
        await batch._touch(trustline.equivalent_id, equivalent_code)

        if new_limit is not None:
            trustline.limit = new_limit
        if data.policy is not None:
            # Merge or replace policy? Usually merge or replace. Assuming replace for now or merge top level.
            # Schema says optional dict. Let's update existing dict.
            current_policy = dict(trustline.policy) if trustline.policy else {}
            current_policy.update(data.policy)
            trustline.policy = current_policy

        from_pid, to_pid = await self._pids(trustline)
        batch._record(
            "TRUST_LINE_UPDATE",
            trustline.equivalent_id,
            {
                "from": str(from_pid or trustline.from_participant_id),
                "to": str(to_pid or trustline.to_participant_id),
                "trustline_id": str(trustline_id),
            },
        )
        return trustline

    async def execute_close(
        self,
        batch: TrustLineWriteBatch,
        trustline_id: UUID,
        user_id: UUID,
        data: TrustLineCloseRequest,
        *,
        require_signature: bool,
    ) -> TrustLine:
        """Request the close of a live line, in the caller's transaction, recorded on `batch` (026 `T2603.1`).

        THE RULE (owner В1/В2, 2026-09-29; protocol §5.3): the limit becomes 0 and `close_requested_at` is set.
        Only the debt the line SUPPORTS counts - the debtor (`to`) owing the creditor (`from`); a debt the other
        way belongs to the other line. Zero: the line is `closed` now (completion row `TRUST_LINE_CLOSE`,
        `completed_by = request`). Otherwise it stays `active` with a
        `TRUST_LINE_CLOSE_REQUEST` row, and the money operation that brings that debt to exactly 0 closes it
        (`app/core/ledger/book.py`, `_settle_requested_closes`). Repeating the request while it is pending
        changes nothing and writes no row.

        LOCKS, as `execute_update`: the line `FOR UPDATE` before any decision, the only lock taken; the debt is a
        plain read after it (027 stage 2): every writer of the pair's debt holds this line too, so an in-flight
        one is waited for and read, and a later one waits for this close.
        """

        # 028 `F-028-6`: `populate_existing` - the row as the lock returns it. A caller that read the line earlier in
        # this session (the Interact actions do) would otherwise decide on its identity-map copy from before the wait.
        stmt = select(TrustLine).where(TrustLine.id == trustline_id).with_for_update().execution_options(
            populate_existing=True)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()

        if not trustline:
            raise NotFoundException("Trustline not found")

        if trustline.from_participant_id != user_id:
            raise ForbiddenException("Not authorized to close this trustline")

        # Symmetry with `update()`: a closed row is history.  Closing it again would write a
        # fresh TRUST_LINE_CLOSE audit entry for a line that was
        # closed long ago -- history written after the fact.  Harmless to the state, wrong
        # in the journal.  Found by an independent scan after migration 019 made a closed
        # incarnation coexist with a live one.
        if str(trustline.status) == "closed":
            raise ConflictException(
                "Trustline is already closed",
                details={"reason": "TRUSTLINE_CLOSED", "trustline_id": str(trustline_id),
                         "current": line_state(trustline)},
            )

        if require_signature:
            if not isinstance(getattr(data, "signature", None), str) or not data.signature:
                raise InvalidSignatureException("Missing signature")

            user = await self.session.get(Participant, user_id)
            if not user:
                raise NotFoundException("Sender not found")
            self._verify_line_operation("TRUST_LINE_CLOSE", trustline, user, data)

        supported = await self._get_used_amount(trustline)
        requested_now = trustline.close_requested_at is None
        if not requested_now and supported > 0:
            return trustline  # the request stands: same state, original timestamp, no second row

        equivalent_code = await self._equivalent_code(trustline.equivalent_id)
        await batch._touch(trustline.equivalent_id, equivalent_code)
        if requested_now:
            trustline.limit = Decimal("0")
            trustline.close_requested_at = datetime.now(timezone.utc)
        from_pid, to_pid = await self._pids(trustline)
        parties = (str(from_pid or trustline.from_participant_id), str(to_pid or trustline.to_participant_id))
        if supported > 0:
            batch._record(TRUST_LINE_CLOSE_REQUEST, trustline.equivalent_id,
                          {"from": parties[0], "to": parties[1], "trustline_id": str(trustline_id)})
            return trustline
        trustline.status = "closed"
        batch._record(TRUST_LINE_CLOSE, trustline.equivalent_id,
                      trust_line_close_completed(*parties, str(trustline_id), "request"))
        return trustline

    def _verify_line_operation(self, operation: str, trustline: TrustLine, user: Participant, data) -> None:
        """030 S5 (F-030-13, `T3000` item 1): the signed bytes of an UPDATE/CLOSE are `operation` (the route's), `id`,
        the new `limit`/`policy` as sent, `expected` = {limit, policy, status, close_requested_at} and `issued_at`.
        Checked in this order, before any write and after the row lock: the body carries all of them (the pre-S5
        form over `{id}` is refused, no compatibility), `operation` is the route's, `issued_at` is inside the window
        (server UTC), the signature verifies, and the LOCKED row equals `expected` - limit numerically, policy
        structurally, status and `close_requested_at` exactly. A line that moved on answers 409
        `TRUSTLINE_STATE_CHANGED` with its current state; a client whose intent equals that state is done.

        `canonical_json` stays outside the `try` (the `create` shape): a float in a policy is the payload's 400, not a
        signature failure. Accepted residual: A -> B -> A inside the window re-admits the first A -> B signature, and
        an empty UPDATE is not one-shot.
        """

        if data.operation is None or data.expected is None or data.issued_at is None:
            raise InvalidSignatureException("Signed payload incomplete", details={
                "reason": "signed_payload_incomplete", "required": ["operation", "expected", "issued_at", "signature"]})
        if data.operation != operation:
            raise InvalidSignatureException("Signed operation does not match the route", details={
                "reason": "operation_mismatch", "route_operation": operation, "signed_operation": data.operation})
        age = (datetime.now(timezone.utc) - _parse_rfc3339(data.issued_at, field="issued_at")).total_seconds()
        if age > SIGNATURE_MAX_AGE_SECONDS:
            raise InvalidSignatureException("Signature expired", details={
                "reason": "signature_expired", "issued_at": data.issued_at, "max_age_seconds": SIGNATURE_MAX_AGE_SECONDS})
        if -age > SIGNATURE_MAX_CLOCK_AHEAD_SECONDS:
            raise InvalidSignatureException("Signature issued in the future", details={
                "reason": "signature_issued_in_the_future", "issued_at": data.issued_at,
                "max_clock_ahead_seconds": SIGNATURE_MAX_CLOCK_AHEAD_SECONDS})

        signed_payload: dict = {"operation": operation, "id": str(trustline.id),
                                "expected": data.expected.model_dump(), "issued_at": data.issued_at}
        for field in ("limit", "policy"):
            if getattr(data, field, None) is not None:
                signed_payload[field] = getattr(data, field)
        message = canonical_json(signed_payload)
        try:
            verify_signature(user.public_key, message, data.signature)
        except Exception:
            raise InvalidSignatureException("Invalid signature")

        expected = data.expected
        expected_at = None if expected.close_requested_at is None else _parse_rfc3339(
            expected.close_requested_at, field="expected.close_requested_at")
        if (parse_money_amount(expected.limit, field="expected.limit", require_non_negative=True) != trustline.limit
                or (expected.policy or {}) != (trustline.policy or {})
                or expected.status != str(trustline.status)
                or expected_at != trustline.close_requested_at):
            raise ConflictException("Trustline state differs from the signed expected state", details={
                "reason": TRUSTLINE_STATE_CHANGED, "trustline_id": str(trustline.id), "current": line_state(trustline)})

    async def _require_step(self, equivalent_id: UUID, limit: Decimal, *, timeout_ms: int | None = None) -> None:
        """028 `F-028-23`/`F-028-25` (owner В-4): a limit finer than the equivalent's step is refused, never rounded.
        The step is read under the equivalent row `FOR SHARE`, held to commit, so a PATCH lowering the precision
        either waits for this write and then sees it, or commits first and this write reads the new step."""

        step_of = await MoneyBoundary(self.session).share_equivalent_step(equivalent_id, timeout_ms=timeout_ms)
        code, precision = step_of or ("?", 8)
        require_money_step(limit, precision=precision, equivalent=code, field="limit")

    async def _equivalent_code(self, equivalent_id: UUID) -> str:
        code = (
            await self.session.execute(select(Equivalent.code).where(Equivalent.id == equivalent_id))
        ).scalar_one_or_none()
        return str(code or equivalent_id)

    async def _pids(self, trustline: TrustLine) -> tuple[str | None, str | None]:
        # Resolve PIDs for readability.
        from_pid = (
            await self.session.execute(
                select(Participant.pid).where(
                    Participant.id == trustline.from_participant_id
                )
            )
        ).scalar_one_or_none()
        to_pid = (
            await self.session.execute(
                select(Participant.pid).where(
                    Participant.id == trustline.to_participant_id
                )
            )
        ).scalar_one_or_none()
        return from_pid, to_pid

    async def get_by_participant(
        self,
        participant_id: UUID,
        *,
        direction: str = "all",
        equivalent: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> List[TrustLine]:
        # direction: 'outgoing' (I trust someone) | 'incoming' (someone trusts me) | 'all'
        if status is None:
            query = select(TrustLine).where(TrustLine.status == 'active')
        else:
            query = select(TrustLine).where(TrustLine.status == status)

        if direction == "outgoing":
            query = query.where(TrustLine.from_participant_id == participant_id)
        elif direction == "incoming":
            query = query.where(TrustLine.to_participant_id == participant_id)
        else:
            query = query.where(
                or_(
                    TrustLine.from_participant_id == participant_id,
                    TrustLine.to_participant_id == participant_id,
                )
            )

        if equivalent:
            validate_equivalent_code(equivalent)
            eq = (
                await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent))
            ).scalar_one_or_none()
            if not eq:
                raise NotFoundException(f"Equivalent '{equivalent}' not found")
            query = query.where(TrustLine.equivalent_id == eq.id)

        # `created_at` is not unique -- fixtures write identical values in bulk, and since
        # migration 019 a triple can hold several rows.  Without a unique tie-break the
        # offset/limit pages below may repeat or skip rows between requests.
        query = query.order_by(TrustLine.created_at.desc(), TrustLine.id.asc())

        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)
        
        result = await self.session.execute(query)
        trustlines = result.scalars().all()
        
        hydrated = []
        for tl in trustlines:
            hydrated.append(await self._hydrate_trustline(tl))
        return hydrated

    async def get_one(self, trustline_id: UUID) -> TrustLine:
        stmt = select(TrustLine).where(TrustLine.id == trustline_id)
        result = await self.session.execute(stmt)
        trustline = result.scalar_one_or_none()
        if not trustline:
            raise NotFoundException("Trustline not found")
        return await self._hydrate_trustline(trustline)

    async def list_all(
        self,
        *,
        equivalent: str | None = None,
        creditor_pid: str | None = None,
        debtor_pid: str | None = None,
        status: Literal["active", "closed"] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[TrustLine]:
        query = select(TrustLine)

        if status:
            query = query.where(TrustLine.status == status)

        if creditor_pid:
            creditor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == creditor_pid)
                )
            ).scalar_one_or_none()
            if creditor_id is None:
                return []
            query = query.where(TrustLine.from_participant_id == creditor_id)

        if debtor_pid:
            debtor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == debtor_pid)
                )
            ).scalar_one_or_none()
            if debtor_id is None:
                return []
            query = query.where(TrustLine.to_participant_id == debtor_id)

        if equivalent:
            eq = (
                await self.session.execute(select(Equivalent).where(Equivalent.code == equivalent))
            ).scalar_one_or_none()
            if eq is None:
                return []
            query = query.where(TrustLine.equivalent_id == eq.id)

        # `created_at` is not unique -- fixtures write identical values in bulk, and since
        # migration 019 a triple can hold several rows.  Without a unique tie-break the
        # offset/limit pages below may repeat or skip rows between requests.
        query = query.order_by(TrustLine.created_at.desc(), TrustLine.id.asc())

        if offset is not None:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        result = await self.session.execute(query)
        trustlines = result.scalars().all()
        # 029 F-029-5, matrix row 9: the Admin API (this method's only caller) keeps the stored scale.
        return [await self._hydrate_trustline(tl, in_step=False) for tl in trustlines]

    async def count_all(
        self,
        *,
        equivalent: str | None = None,
        creditor_pid: str | None = None,
        debtor_pid: str | None = None,
        status: Literal["active", "closed"] | None = None,
    ) -> int:
        query = select(func.count()).select_from(TrustLine)

        if status:
            query = query.where(TrustLine.status == status)

        if creditor_pid:
            creditor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == creditor_pid)
                )
            ).scalar_one_or_none()
            if creditor_id is None:
                return 0
            query = query.where(TrustLine.from_participant_id == creditor_id)

        if debtor_pid:
            debtor_id = (
                await self.session.execute(
                    select(Participant.id).where(Participant.pid == debtor_pid)
                )
            ).scalar_one_or_none()
            if debtor_id is None:
                return 0
            query = query.where(TrustLine.to_participant_id == debtor_id)

        if equivalent:
            eq = (
                await self.session.execute(
                    select(Equivalent).where(Equivalent.code == equivalent)
                )
            ).scalar_one_or_none()
            if eq is None:
                return 0
            query = query.where(TrustLine.equivalent_id == eq.id)

        return int((await self.session.execute(query)).scalar_one())

    async def _hydrate_trustline(self, trustline: TrustLine, *, in_step: bool = True) -> TrustLine:
        state = sa_inspect(trustline)

        # Fetch equivalent code (avoid triggering async lazy-load)
        if "equivalent" in state.unloaded or getattr(trustline, "equivalent", None) is None:
            stmt = select(Equivalent).where(Equivalent.id == trustline.equivalent_id)
            result = await self.session.execute(stmt)
            trustline.equivalent = result.scalar_one()

        # Fetch participant PIDs (avoid triggering async lazy-load)
        if "from_participant" in state.unloaded or getattr(trustline, "from_participant", None) is None:
            stmt = select(Participant).where(Participant.id == trustline.from_participant_id)
            result = await self.session.execute(stmt)
            trustline.from_participant = result.scalar_one()

        if "to_participant" in state.unloaded or getattr(trustline, "to_participant", None) is None:
            stmt = select(Participant).where(Participant.id == trustline.to_participant_id)
            result = await self.session.execute(stmt)
            trustline.to_participant = result.scalar_one()

        used = await self._get_used_amount(trustline)
        
        # Attach dynamic properties for Pydantic schema
        # Pydantic model expects: equivalent_code, used, available
        # We can attach them to the object, or return a dict, or let Pydantic extract from methods if we used getter.
        # But since we return the ORM object, we can monkey-patch or use a wrapper.
        # The schema uses aliases.
        # schema.TrustLine: equivalent_code, used, available.
        
        trustline.equivalent_code = trustline.equivalent.code
        # 029 F-029-5: the schema writes limit/used/available in this step; None = the stored scale.
        trustline.equivalent_precision = int(trustline.equivalent.precision) if in_step else None
        trustline.from_pid = trustline.from_participant.pid
        trustline.to_pid = trustline.to_participant.pid
        trustline.from_display_name = trustline.from_participant.display_name
        trustline.to_display_name = trustline.to_participant.display_name
        trustline.used = used
        trustline.available = trustline.limit - used
        
        return trustline

    async def _get_used_amount(self, trustline: TrustLine) -> Decimal:
        # A CLOSED line is history: the debt on this pair belongs to whatever incarnation is
        # live now, not to it.  Reporting the successor's debt as a closed line's `used`
        # would show an operator a foreign amount -- and, with `available = limit - used`,
        # a negative capacity on a line that no longer exists.  A line closes only when the debt it
        # supports is zero (protocol §5.3; 026 `T2603.1`: at the request, or in the money operation that
        # repaid it), so a closed line's own `used` is zero by construction.
        if str(getattr(trustline, "status", "")) == "closed":
            return Decimal("0")

        # used = debt where debtor is 'to' and creditor is 'from'
        stmt = select(Debt.amount).where(
            and_(
                Debt.debtor_id == trustline.to_participant_id,
                Debt.creditor_id == trustline.from_participant_id,
                Debt.equivalent_id == trustline.equivalent_id
            )
        )
        result = await self.session.execute(stmt)
        amount = result.scalar_one_or_none()
        return amount if amount is not None else Decimal('0')
