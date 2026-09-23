"""Seed a database by PERFORMING a community's recipe, not by writing its result down.

Programme 017, `T1711`. The other seeding paths in `scripts/seed_db.py` insert rows: participants,
trust lines and then *debts* and *transactions* invented by a fixture generator. The debts they write
were never produced by a payment, so nothing in the database explains them, the admin screens show a
history that never happened, and the debt journal has to adopt the lot as an opaque `SEED` operation.

This path does the opposite. It reads

* the community description (`seeds/communities/<id>/community.json`) - who is in the community and
  who trusts whom, and
* the recipe (`seeds/communities/<id>/recipe.json`) - the hand-written list of operations, in order,

and then runs those operations through the same services a participant's request runs through:
`ParticipantService`, `TrustLineService`, `PaymentService`, `ClearingService`, and the admin freeze
handler. Every debt in the resulting database is the recorded effect of a journalled operation, which
is why the acceptance below can be a RECONCILIATION rather than a row count.

KEYS ARE GENERATED PER RUN AND NEVER LEAVE MEMORY. `PID = base58(sha256(public_key))`
(`app/core/auth/crypto.py:37-50`), so the `pid` fields of the description are fixture identities and
cannot be the identities in a database. The recipe names both ends of every operation by the symbolic
``ref``; this module keeps the ``ref -> PID`` table for the run and writes that table - and only that
table - to `.local-run/seed-recipe/<community-id>/participants.json`.

WHAT A SECOND RUN DOES, and why it is a refusal rather than a replay. The command id IS the payment's
`tx_id` (`app/schemas/payment.py:38`), so re-sending a command inside one run returns the stored
result instead of moving money twice - that is what makes a transient retry safe (see
`_is_transient`). It does NOT make a second PROCESS idempotent: a new run generates new key pairs, so
its participants have different PIDs, and a payment to a different payee under the same `tx_id` is a
different request, which `PaymentService` answers with a conflict. The seed therefore refuses a
database that is not empty and says what it found. That is also the "refuse on partial
initialization" this task asks for: a run that died halfway leaves rows whose private keys are gone,
so the only honest continuation is a fresh database.

WHAT IT REFUSES TO SEED AT ALL. A description may declare a state that no product operation can
reach. `TrustLineService` writes exactly two statuses - `'active'` at creation
(`app/core/trustlines/service.py:221`) and `'closed'` at close (`:494`) - and nothing anywhere in
`app/` writes `'frozen'` to `trust_lines.status` outside the simulator's own injector
(`app/core/simulator/inject_executor.py:934`). `greenfield-village-100` declares nine frozen trust
lines. Writing that column here would be precisely the domain bypass this programme exists to remove,
so the seed refuses the whole community and names every line, rather than seeding it half-true. The
divergence is recorded in `specs/BACKLOG.md` (2026-09-22) and is the owner's to settle.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
_COMMUNITIES_DIR = _REPO_ROOT / "seeds" / "communities"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# `recipe_schema` / `community_schema` are dependency-free modules that import each other by bare
# name, exactly as `scripts/generate_simulator_seed_scenarios.py:33-34` arranges it.
if str(_COMMUNITIES_DIR) not in sys.path:
    sys.path.insert(0, str(_COMMUNITIES_DIR))

from nacl.signing import SigningKey  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import community_schema  # noqa: E402
import recipe_schema  # noqa: E402

from app.api.v1.admin import admin_create_equivalent, freeze_participant  # noqa: E402
from app.config import Settings, settings  # noqa: E402
from app.core.auth.canonical import canonical_json  # noqa: E402
from app.core.auth.crypto import generate_keypair, get_pid_from_public_key  # noqa: E402
from app.core.clearing.service import ClearingService  # noqa: E402
from app.core.ledger.reconciliation import (  # noqa: E402
    FULL_RECOMPUTATION,
    PASSED,
    open_verification_snapshot,
    take_baseline,
    verify_journal_equals_change,
)
from app.core.participants.service import ParticipantService  # noqa: E402
from app.core.payments.service import (  # noqa: E402
    _RETRYABLE_PAYMENT_SQLSTATES,
    _payment_db_sqlstate,
    PaymentService,
)
from app.core.trustlines.service import TrustLineService  # noqa: E402
from app.db.journal_tables import debt_operation_equivalents, debt_operations  # noqa: E402
from app.db.models import Debt, Equivalent, Participant, TrustLine  # noqa: E402
from app.db.reconciliation_tables import debt_reconciliation_baseline_offsets  # noqa: E402
from app.db.sqlite_transaction_control import sqlite_busy_error_name  # noqa: E402
from app.schemas.admin import (  # noqa: E402
    AdminEquivalentCreateRequest,
    AdminParticipantActionRequest,
)
from app.schemas.participant import ParticipantCreateRequest, ParticipantProfile  # noqa: E402
from app.schemas.payment import PaymentConstraints, PaymentCreateRequest  # noqa: E402
from app.schemas.trustline import TrustLineCreateRequest  # noqa: E402
from app.utils.exceptions import RetryablePaymentConflictException  # noqa: E402


class SeedRefusal(RuntimeError):
    """The seed refused to act. Nothing was written, or what was written is named in the message."""


#: The only `trust_lines.status` a product operation can produce at creation
#: (`app/core/trustlines/service.py:221`). `'closed'` is reachable too, but a description cannot
#: declare it (`seeds/communities/community_schema.py:79`), so it is not listed as seedable.
REACHABLE_TRUSTLINE_STATUSES = frozenset({"active"})

#: A description's participant statuses and the operation that reaches each one.
#: `frozen` is `admin.participants.freeze`, which writes `suspended` (`app/api/v1/admin.py:977`).
REACHABLE_PARTICIPANT_STATUSES = frozenset({"active", "frozen"})

#: Attempts per command, and the delay before each retry. Only a TRANSIENT failure is retried, and a
#: retry re-sends the same command id, which is the payment's `tx_id`: the replay returns the stored
#: result rather than moving money again.
_MAX_ATTEMPTS = 4
_RETRY_BACKOFF_SECONDS = (0.05, 0.20, 0.50)

#: `max_depth` for cycle detection. Every cycle a recipe may name is at least a triangle
#: (`seeds/communities/recipe_schema.py:MIN_CYCLE_LENGTH`); six is the API's own default
#: (`app/api/v1/clearing.py:20`).
_CYCLE_MAX_DEPTH = 6

#: The bottleneck the acceptance looks for: an edge with less than this share of its limit left.
BOTTLENECK_RESIDUAL_SHARE = Decimal("0.10")

#: Every acceptance check this module knows how to run. The runner compares the checks it actually
#: produced against this set, so a check that was skipped is a failure and not a silent pass
#: (`AGENTS.md` §9: a measurement not taken must differ from a measurement of zero).
ACCEPTANCE_CHECKS = (
    "reconciliation_passed",
    "baseline_offsets_are_zero",
    "every_operation_examined",
    "bottleneck_edge_below_threshold",
    "clearing_executed",
    "surviving_cycle_still_clearable",
    "activity_in_every_equivalent",
)


# ==================================================================================================
# What a description declares that no operation can reach
# ==================================================================================================


def unreachable_declared_states(community: dict[str, Any]) -> list[str]:
    """Every declared state of `community` that no product operation can produce.

    Returns one human-readable line per offending declaration, empty when the description is
    seedable. It is a POSITIVE list of what is reachable, so a status nobody thought about lands
    here rather than slipping through.
    """

    offenders: list[str] = []
    for line in community["trustlines"]:
        if line["status"] not in REACHABLE_TRUSTLINE_STATUSES:
            offenders.append(
                f"trustline {line['from']} -> {line['to']} in {line['equivalent']} is declared "
                f"{line['status']!r}; TrustLineService writes only "
                f"{sorted(REACHABLE_TRUSTLINE_STATUSES)} at creation "
                f"(app/core/trustlines/service.py:221)"
            )
    for participant in community["participants"]:
        if participant["status"] not in REACHABLE_PARTICIPANT_STATUSES:
            offenders.append(
                f"participant {participant['ref']} is declared {participant['status']!r}, which no "
                f"seed operation reaches"
            )
    return offenders


# ==================================================================================================
# Guards: where this is allowed to run at all
# ==================================================================================================


async def database_url(session_factory: Callable[[], Any]) -> Any:
    """The URL of the database this factory opens sessions on.

    Read off a real session's bind rather than the factory's keyword arguments: a session bound to a
    connection and a session bound to an engine both answer here, and a factory that is bound to
    nothing raises instead of seeding a database nobody can name.
    """

    async with session_factory() as session:
        bind = session.get_bind()
        engine = getattr(bind, "engine", None)
        if engine is None:
            raise SeedRefusal(
                "the session factory is bound to nothing, so the target database cannot be named; "
                "refusing rather than seeding an unidentified database"
            )
        return engine.url


def assert_target_is_disposable(url: Any, *, allow_scratch_suffix: bool = False) -> None:
    """Refuse any database that is not, by its own name, a disposable local one.

    For PostgreSQL the seed OWNS NO NAME CONTRACT and keeps no copy of one: `geov0_dev_<slug>` is
    decided by `scripts/dev_database.py`, `geov0_test_<slug>` by `scripts/validate_test_database_url.py`.
    The copy this function used to hold - one regex over both - accepted `geov0_test_a__b`, a name
    provisioning reserves for the scratch databases of task `a` and drops when it sweeps them (Codex
    external review of `37fec08..5e687dd`, F5, 2026-09-23).

    `allow_scratch_suffix` means what it means in the test guard, for the same kind of caller: a
    test seeding a clone that provisioning derived as `<tier>__<suffix>`. The supported CLI
    (`scripts/seed_db.py --source recipe`) leaves it off.
    """

    backend = url.get_backend_name()
    if backend in {"postgresql", "postgres"}:
        database = url.database or ""
        rendered = url.render_as_string(hide_password=False)
        if database.startswith("geov0_dev_"):
            from scripts.dev_database import (  # noqa: PLC0415
                UnsafeDevDatabaseError,
                assert_safe_dev_database_url,
            )

            try:
                assert_safe_dev_database_url(rendered)
            except UnsafeDevDatabaseError as refusal:
                raise SeedRefusal(f"not a disposable seed database: {refusal}") from None
            return
        if database.startswith("geov0_test_"):
            from scripts.validate_test_database_url import (  # noqa: PLC0415
                UnsafeTestDatabaseError,
                assert_safe_test_database_url,
            )

            try:
                # "1": the guard's last rule is the opt-in for the harness's destructive schema
                # RESET. The seed resets nothing - it refuses a database that is not empty - so
                # that opt-in is not the question asked here; every NAME rule before it is.
                assert_safe_test_database_url(
                    rendered,
                    allow_destructive_reset="1",
                    repo_root=_REPO_ROOT,
                    required_backend="postgresql",
                    allow_scratch_suffix=allow_scratch_suffix,
                )
            except UnsafeTestDatabaseError as refusal:
                raise SeedRefusal(f"not a disposable seed database: {refusal}") from None
            return
        raise SeedRefusal(
            f"PostgreSQL database {database!r} is not a disposable seed database: only "
            f"geov0_dev_<slug> (scripts/dev_database.py) and geov0_test_<slug> "
            f"(scripts/validate_test_database_url.py) are"
        )
    if backend == "sqlite":
        database = url.database or ""
        if database in {"", ":memory:"}:
            return
        resolved = Path(database)
        if not resolved.is_absolute():
            resolved = Path.cwd() / resolved
        local_run = (_REPO_ROOT / ".local-run").resolve()
        try:
            resolved.resolve().relative_to(local_run)
        except ValueError:
            raise SeedRefusal(
                f"SQLite database {database!r} is outside {local_run}; every runtime artefact of "
                f"this repository lives under .local-run/ (AGENTS.md §12)"
            ) from None
        return
    raise SeedRefusal(f"backend {backend!r} is not a database this seed knows how to dispose of")


def assert_environment_is_safe(env: str | None = None) -> None:
    """`ENV` must be one of the environments the hub itself calls safe."""

    value = env if env is not None else settings.ENV
    # ONE definition of "safe environment", read from `app/config.py` rather than repeated here.
    if value not in Settings._SAFE_ENVS:
        raise SeedRefusal(
            f"ENV={value!r} is not one of {sorted(Settings._SAFE_ENVS)}; this seed writes demo "
            f"money and refuses to run anywhere else"
        )


#: What "empty" means, as (label, entity) pairs. A non-empty count in ANY of them is a refusal.
_EMPTINESS_PROBES: tuple[tuple[str, Any], ...] = (
    ("equivalents", Equivalent),
    ("participants", Participant),
    ("trust_lines", TrustLine),
    ("debts", Debt),
)


async def assert_database_is_empty(session: Any) -> None:
    """Refuse a database that already holds a community, complete or half-written.

    Named counts, not a single boolean: the message has to say WHAT it found, because the operator's
    next move differs between "already seeded" and "a run died in the middle".
    """

    found: list[str] = []
    for label, entity in _EMPTINESS_PROBES:
        count = int((await session.execute(select(func.count()).select_from(entity))).scalar_one())
        if count:
            found.append(f"{label}={count}")
    operations = int(
        (await session.execute(select(func.count()).select_from(debt_operations))).scalar_one()
    )
    if operations:
        found.append(f"debt_operations={operations}")

    if found:
        raise SeedRefusal(
            "the target database is not empty ("
            + ", ".join(found)
            + "). This seed generates a fresh key pair per participant per run, so it cannot "
            "replay onto an existing population: the PIDs would differ and the same command id "
            "would be a different request. Drop the database and run again."
        )


# ==================================================================================================
# Signing: the recipe runs through the signed paths, so the run holds real keys
# ==================================================================================================


@dataclass(frozen=True)
class _Identity:
    """One participant of this run: its symbolic ref, its real PID, and the key that proves it."""

    ref: str
    pid: str
    public_key: str
    #: `None` only in a run rebuilt by `_load_seeded_run`, which re-reads an already seeded
    #: database and signs nothing: the keys of the run that created it are gone by design.
    _signing_key: SigningKey | None
    participant_id: uuid.UUID

    def sign(self, message: bytes) -> str:
        if self._signing_key is None:
            raise SeedRefusal(f"{self.ref} has no signing key in this process; it cannot act")
        return base64.b64encode(self._signing_key.sign(message).signature).decode("utf-8")


def _new_identity(ref: str) -> tuple[str, str, SigningKey]:
    public_key, private_key = generate_keypair()
    return public_key, get_pid_from_public_key(public_key), SigningKey(base64.b64decode(private_key))


def _synthetic_request(path: str) -> Any:
    """A `Request` for the admin handlers, which take one for the audit row and nothing else.

    `_add_audit_entry` reads `X-Request-ID`, `user-agent` and `request.client`
    (`app/api/v1/admin.py:409-413`). A scope without a client makes `request.client` `None`, which
    that helper already handles, so the audit row records this seed as its own user agent and an
    invented request id - which is exactly what it is.
    """

    from starlette.requests import Request  # noqa: PLC0415

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"user-agent", b"scripts/seed_recipe.py")],
            "client": None,
            "server": ("127.0.0.1", 0),
        }
    )


# ==================================================================================================
# Transient versus logical failure
# ==================================================================================================


def _is_transient(exc: BaseException) -> bool:
    """A serialization/deadlock failure, which a retry can win - and nothing else.

    The distinction is the one programme 004 paid for: a swallowed `40001` poisons the transaction
    and the NEXT failure, which is logical, arrives at a retry predicate that no longer recognises
    it. So this asks two narrow questions and answers `False` to everything else:

    * did a payment path already classify it as retryable (`RetryablePaymentConflictException`), or
    * does the exception chain carry one of `_RETRYABLE_PAYMENT_SQLSTATES` (PostgreSQL), or
      SQLite's busy twin?

    The sqlstate reader and the retryable set are IMPORTED from `app/core/payments/service.py` so
    there is one definition of both, including the rule that `__context__` is not followed.
    """

    if isinstance(exc, RetryablePaymentConflictException):
        return True
    if _payment_db_sqlstate(exc) in _RETRYABLE_PAYMENT_SQLSTATES:
        return True
    return sqlite_busy_error_name(exc) is not None


# ==================================================================================================
# The run
# ==================================================================================================


@dataclass
class SeedReport:
    community_id: str
    participants: int = 0
    trustlines: int = 0
    equivalents: int = 0
    commands: dict[str, int] = field(default_factory=dict)
    retries: int = 0
    baselines: dict[str, dict[str, int]] = field(default_factory=dict)
    acceptance: dict[str, dict[str, Any]] = field(default_factory=dict)
    key_table_path: str | None = None
    refs_to_pid: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"community={self.community_id}",
            f"equivalents={self.equivalents}",
            f"participants={self.participants}",
            f"trustlines={self.trustlines}",
            "commands=" + ",".join(f"{op}:{n}" for op, n in sorted(self.commands.items())),
            f"transient_retries={self.retries}",
        ]
        return " ".join(parts)


class _Run:
    """One seeding run: the key table, the session factory, and the report being filled in."""

    def __init__(self, session_factory: Callable[[], Any], community: dict, recipe: dict) -> None:
        self.session_factory = session_factory
        self.community = community
        self.recipe = recipe
        self.identities: dict[str, _Identity] = {}
        self.equivalent_ids: dict[str, uuid.UUID] = {}
        self.community_names = {p["ref"]: p["name"] for p in community["participants"]}
        self.report = SeedReport(community_id=community["community_id"])

    # -- infrastructure ---------------------------------------------------------------------

    def session(self):
        return self.session_factory()

    async def _attempt(self, what: str, body: Callable[[Any], Any]) -> Any:
        """Run `body` in a fresh session, retrying only a transient database conflict.

        A fresh session per attempt on purpose: the failed attempt's transaction is rolled back and
        abandoned, because a transaction that met `40001` cannot be continued.
        """

        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                async with self.session() as session:
                    return await body(session)
            except Exception as exc:  # `Exception`, so cancellation and KeyboardInterrupt pass
                if not _is_transient(exc):
                    raise SeedRefusal(f"{what}: {type(exc).__name__}: {exc}") from exc
                last = exc
                self.report.retries += 1
                if attempt < len(_RETRY_BACKOFF_SECONDS):
                    await asyncio.sleep(_RETRY_BACKOFF_SECONDS[attempt])
        raise SeedRefusal(
            f"{what}: still a transient database conflict after {_MAX_ATTEMPTS} attempts: "
            f"{type(last).__name__}: {last}"
        ) from last

    # -- structure --------------------------------------------------------------------------

    async def create_equivalents(self) -> None:
        for declared in self.community["equivalents"]:

            async def body(session, declared=declared):
                await admin_create_equivalent(
                    body=AdminEquivalentCreateRequest(
                        code=declared["code"],
                        description=declared["description"],
                        precision=declared["precision"],
                        # The description declares no metadata and no symbol, and inventing either
                        # from the code would be reading meaning out of a form (`AGENTS.md` §9).
                        metadata=None,
                        is_active=declared["is_active"],
                        reason=f"seed recipe {self.community['community_id']}",
                    ),
                    request=_synthetic_request("/api/v1/admin/equivalents"),
                    db=session,
                )

            await self._attempt(f"create equivalent {declared['code']}", body)
            self.report.equivalents += 1

        async with self.session() as session:
            rows = (await session.execute(select(Equivalent.code, Equivalent.id))).all()
        self.equivalent_ids = {code: eid for code, eid in rows}

    async def create_participants(self) -> None:
        for declared in self.community["participants"]:
            ref = declared["ref"]
            public_key, pid, signing_key = _new_identity(ref)
            profile = ParticipantProfile(
                **{
                    key: declared[key]
                    for key in ("group", "role")
                    if key in declared and declared[key] is not None
                }
            )
            payload = {
                "display_name": declared["name"],
                "type": declared["type"],
                "public_key": public_key,
                "profile": profile.model_dump(exclude_unset=True),
            }
            signature = base64.b64encode(
                signing_key.sign(canonical_json(payload)).signature
            ).decode("utf-8")

            async def body(session, declared=declared, public_key=public_key, signature=signature, profile=profile):
                created = await ParticipantService(session).create_participant(
                    ParticipantCreateRequest(
                        display_name=declared["name"],
                        type=declared["type"],
                        public_key=public_key,
                        signature=signature,
                        profile=profile,
                    )
                )
                return created.id

            participant_id = await self._attempt(f"create participant {ref}", body)
            self.identities[ref] = _Identity(
                ref=ref,
                pid=pid,
                public_key=public_key,
                _signing_key=signing_key,
                participant_id=participant_id,
            )
            self.report.participants += 1
        self.report.refs_to_pid = {ref: who.pid for ref, who in self.identities.items()}

    async def create_trustlines(self) -> None:
        for declared in self.community["trustlines"]:
            creditor = self.identities[declared["from"]]
            debtor = self.identities[declared["to"]]
            policy = dict(declared["policy"])
            signed = {
                "to": debtor.pid,
                "equivalent": declared["equivalent"],
                "limit": declared["limit"],
                "policy": policy,
            }
            signature = creditor.sign(canonical_json(signed))

            async def body(session, creditor=creditor, declared=declared, policy=policy, signature=signature, debtor=debtor):
                await TrustLineService(session).create(
                    creditor.participant_id,
                    TrustLineCreateRequest(
                        to=debtor.pid,
                        equivalent=declared["equivalent"],
                        limit=declared["limit"],
                        policy=policy,
                        signature=signature,
                    ),
                )

            await self._attempt(
                f"create trustline {declared['from']} -> {declared['to']} in {declared['equivalent']}",
                body,
            )
            self.report.trustlines += 1

    # -- the empty baseline -----------------------------------------------------------------

    async def take_baselines(self) -> None:
        """THE BASELINE COMES BEFORE THE FIRST PAYMENT - the stricter of two sound orderings.

        A baseline records, per edge, `current debt - sum(journal deltas)` as an offset it adopts and
        does not certify (`app/core/ledger/reconciliation.py:999`). Taken afterwards it would NOT
        adopt the seed wholesale, as an earlier version of this docstring claimed: correctly
        journalled payments leave zero offsets, and their operations stay examinable. What it would
        adopt is only a debt the journal fails to explain - which `baseline_offsets_are_zero` would
        still report. So both orderings end in the same verdict for a correct seed.

        Before is chosen because it makes the claim trivial where it is made: taken now - trust
        lines exist, debts do not - the baseline can only be empty, that emptiness is asserted right
        here (`offsets_recorded` and `entries_read` both zero), and every debt the seed goes on to
        write is then checked by `debts == baseline + sum(journal)` alone, with no offset in the sum
        to reason about (Codex external review of `37fec08..5e687dd`, F10, 2026-09-23).
        """

        for code, equivalent_id in sorted(self.equivalent_ids.items()):

            async def body(session, equivalent_id=equivalent_id):
                taken = await take_baseline(session, equivalent_id)
                await session.commit()
                return taken

            # Through the same retry as everything else: `take_baseline` takes the equivalent's
            # owner lock, so it is a writer like the others. A retry after a SUCCESSFUL commit would
            # meet `BaselineAlreadyTaken`, which is not transient and becomes a refusal - the right
            # answer, since nothing re-baselines.
            taken = await self._attempt(f"take the baseline of {code}", body)
            if taken.offsets_recorded or taken.entries_read:
                raise SeedRefusal(
                    f"the baseline of {code} is not empty ({taken.offsets_recorded} offset(s) over "
                    f"{taken.edges_seen} edge(s), {taken.entries_read} journal entries). It was "
                    f"supposed to be taken before any money moved; a baseline that adopts debts "
                    f"makes the reconciliation below prove nothing about them."
                )
            self.report.baselines[code] = {
                "offsets_recorded": taken.offsets_recorded,
                "edges_seen": taken.edges_seen,
                "entries_read": taken.entries_read,
            }

    # -- the commands -----------------------------------------------------------------------

    async def run_commands(self) -> None:
        for command in self.recipe["commands"]:
            op = command["op"]
            handler = {
                "payment": self._payment,
                "freeze": self._freeze,
                "clearing": self._clearing,
            }[op]
            await handler(command)
            self.report.commands[op] = self.report.commands.get(op, 0) + 1

    async def _payment(self, command: dict) -> None:
        payer = self.identities[command["payer"]]
        payee = self.identities[command["payee"]]
        max_hops = recipe_schema.max_hops_for(command)
        constraints = PaymentConstraints(max_hops=max_hops) if max_hops is not None else None

        signed: dict[str, Any] = {
            "tx_id": command["id"],
            "to": payee.pid,
            "equivalent": command["equivalent"],
            "amount": command["amount"],
        }
        if constraints is not None:
            signed["constraints"] = constraints.model_dump(exclude_unset=True)
        signature = payer.sign(canonical_json(signed))

        async def body(session):
            return await PaymentService(session).create_payment(
                payer.participant_id,
                PaymentCreateRequest(
                    tx_id=command["id"],
                    to=payee.pid,
                    equivalent=command["equivalent"],
                    amount=command["amount"],
                    constraints=constraints,
                    signature=signature,
                ),
            )

        result = await self._attempt(f"payment {command['id']}", body)
        if result.status != "COMMITTED":
            raise SeedRefusal(
                f"payment {command['id']} ({command['payer']} -> {command['payee']}, "
                f"{command['amount']} {command['equivalent']}, routing={command['routing']}) "
                f"ended {result.status}: "
                f"{result.error.code if result.error else '?'} "
                f"{result.error.message if result.error else ''}. The recipe expected: "
                f"{command['expect']}"
            )

    async def _freeze(self, command: dict) -> None:
        who = self.identities[command["participant"]]

        async def body(session):
            return await freeze_participant(
                pid=who.pid,
                body=AdminParticipantActionRequest(reason=command["why"][:255]),
                request=_synthetic_request(f"/api/v1/admin/participants/{who.pid}/freeze"),
                db=session,
            )

        result = await self._attempt(f"freeze {command['id']}", body)
        if result["status"] != "suspended":
            raise SeedRefusal(
                f"freeze {command['id']} left {command['participant']} in {result['status']!r}"
            )

    async def _clearing(self, command: dict) -> None:
        expected = self._expected_cycle_edges(command)

        async def body(session):
            service = ClearingService(session)
            cycles = await service.find_cycles(command["equivalent"], max_depth=_CYCLE_MAX_DEPTH)
            match = _match_cycle(cycles, expected)
            if match is None:
                return None, cycles
            if command["mode"] == "assert_clearable":
                return ("asserted", match)
            return ("executed", await service.execute_clearing_with_amount(match))

        outcome = await self._attempt(f"clearing {command['id']}", body)
        if outcome[0] is None:
            raise SeedRefusal(
                f"clearing {command['id']}: the cycle {command['cycle']} in "
                f"{command['equivalent']} is not among the {len(outcome[1])} clearable cycle(s) "
                f"detection found. What detection DID find: {self._render_cycles(outcome[1])}. "
                f"The recipe expected: {command['expect']}"
            )
        if command["mode"] == "assert_clearable":
            smallest = min(Decimal(edge["amount"]) for edge in outcome[1])
            if smallest != Decimal(command["amount"]):
                raise SeedRefusal(
                    f"clearing {command['id']} asserts a surviving cycle of {command['amount']}, "
                    f"but its smallest edge is {smallest}"
                )
            return
        cleared = outcome[1]
        if cleared is None:
            raise SeedRefusal(
                f"clearing {command['id']} was detected but not executed; "
                f"execute_clearing_with_amount returned None"
            )
        if Decimal(cleared) != Decimal(command["amount"]):
            raise SeedRefusal(
                f"clearing {command['id']} cleared {cleared}, the recipe says {command['amount']}"
            )

    def _render_cycles(self, cycles: Sequence[Sequence[dict]]) -> str:
        """Detected cycles in the recipe's own vocabulary, so a mismatch is readable."""

        by_pid = {who.pid: ref for ref, who in self.identities.items()}
        rendered = []
        for cycle in cycles:
            edges = []
            for edge in cycle:
                debtor = by_pid.get(str(edge.get("debtor")), edge.get("debtor"))
                creditor = by_pid.get(str(edge.get("creditor")), edge.get("creditor"))
                edges.append(f"{debtor} owes {creditor} {edge.get('amount')}")
            rendered.append("[" + "; ".join(edges) + "]")
        return ", ".join(rendered) if rendered else "nothing"

    def _expected_cycle_edges(self, command: dict) -> frozenset[tuple[str, str]]:
        """The cycle as (debtor PID, creditor PID) pairs.

        The recipe writes the cycle debtor -> creditor and closes it implicitly
        (`seeds/communities/recipe_schema.py`).

        PIDs, NOT `participants.id`, and this is not a matter of taste. `find_cycles` renders both
        of its detectors' output with `debtor`/`creditor` REPLACED by the participants' PIDs
        (`app/core/clearing/service.py:1258-1270` for the SQL path and the same substitution for the
        DFS), even though the underlying columns are `debts.debtor_id`. Matching on the primary key
        made the one correctly detected cycle look absent - measured 2026-09-22 on the Riverside
        recipe, where detection returned exactly the triangle the recipe names.
        """

        refs = command["cycle"]
        return frozenset(
            (self.identities[debtor].pid, self.identities[creditor].pid)
            for debtor, creditor in zip(refs, refs[1:] + refs[:1])
        )


def _match_cycle(
    cycles: Sequence[Sequence[dict]], expected: frozenset[tuple[str, str]]
) -> list[dict] | None:
    """The detected cycle whose edges are exactly `expected`, or None.

    Compared as a SET of directed edges rather than as a sequence: detection is free to start a
    cycle at any of its participants, and a rotation is the same cycle.
    """

    for cycle in cycles:
        if len(cycle) != len(expected):
            continue
        edges = frozenset(
            (str(edge.get("debtor")), str(edge.get("creditor"))) for edge in cycle
        )
        if edges == expected:
            return list(cycle)
    return None


# ==================================================================================================
# Acceptance: what has to be TRUE of the database afterwards
# ==================================================================================================


async def _check_reconciliation(session_factory, run: _Run) -> dict[str, dict[str, Any]]:
    """Criteria (a) and (b) per equivalent, plus what the verdict was allowed to see.

    Three of the seven checks are read off one verification, because they are three questions about
    the same verdict: was it `PASSED`, did the baseline it stands on hold nothing, and did criterion
    (b) recompute every operation rather than a part of them.

    WHAT `every_operation_examined` DOES NOT SEE. `operations_examined` is summed over the
    equivalents, so an operation that touched two of them counts twice, while `operations_recorded`
    counts rows. The comparison is an EQUALITY, so that inflation makes the check fail rather than
    pass - the error is on the strict side. It is still not a per-operation join: an unexamined
    operation could in principle be masked by a cross-equivalent one in the same run. No recipe
    command produces a cross-equivalent operation (a payment and a clearing each live in one
    equivalent), so the case does not arise today; if one ever does, this is the check to replace
    with a join rather than to loosen.
    """

    statuses: dict[str, str] = {}
    coverage: dict[str, dict[str, dict[str, int]]] = {}
    limited: dict[str, list[str]] = {}
    examined_total = 0

    for code, equivalent_id in sorted(run.equivalent_ids.items()):
        async with session_factory() as session:
            await open_verification_snapshot(session)
            outcome = await verify_journal_equals_change(session, equivalent_id)
            await session.rollback()
        statuses[code] = outcome.status
        detail = outcome.detail()
        coverage[code] = detail["criterion_b"]["coverage"]
        limited[code] = detail["criterion_b"]["limited"]
        examined_total += int(detail["criterion_b"]["operations_examined"])

    async with session_factory() as session:
        offsets = int(
            (
                await session.execute(
                    select(func.count()).select_from(debt_reconciliation_baseline_offsets)
                )
            ).scalar_one()
        )
        operations = int(
            (await session.execute(select(func.count()).select_from(debt_operations))).scalar_one()
        )

    failing = sorted(code for code, status in statuses.items() if status != PASSED)
    partial = sorted(code for code, levels in coverage.items() if set(levels) - {FULL_RECOMPUTATION})

    return {
        "reconciliation_passed": {
            # `statuses` must be non-empty: "no equivalent failed" is true of a database with no
            # equivalents in it, and a check that passes on an empty database is not a check.
            "passed": bool(statuses) and not failing,
            "statuses": statuses,
            "detail": (
                "no equivalent was verified at all"
                if not statuses
                else (f"equivalents not PASSED: {failing}" if failing else "every equivalent PASSED")
            ),
        },
        "baseline_offsets_are_zero": {
            "passed": offsets == 0,
            "baseline_offset_rows": offsets,
            "detail": (
                "the baseline adopted nothing, so the verdict above covers every debt this seed made"
                if offsets == 0
                else f"{offsets} baseline offset row(s): the baseline adopted debts it cannot certify"
            ),
        },
        "every_operation_examined": {
            "passed": (
                operations > 0 and examined_total == operations and not partial
            ),
            "operations_recorded": operations,
            "operations_examined": examined_total,
            "coverage": coverage,
            "limited": limited,
            "detail": (
                f"{examined_total} of {operations} operation(s) fully recomputed"
                if examined_total == operations and not partial
                else f"{examined_total} of {operations} examined; partial coverage in {partial}"
            ),
        },
    }


async def _check_bottleneck(session_factory, run: _Run) -> dict[str, Any]:
    """At least one live trust line with less than `BOTTLENECK_RESIDUAL_SHARE` of its limit left.

    `used` on a line creditor -> debtor is the debt the debtor owes the creditor, which is the
    direction `app/core/trustlines/service.py:759-778` reads it in.
    """

    async with session_factory() as session:
        lines = (
            await session.execute(
                select(
                    TrustLine.from_participant_id,
                    TrustLine.to_participant_id,
                    TrustLine.equivalent_id,
                    TrustLine.limit,
                ).where(TrustLine.status == "active")
            )
        ).all()
        debts = {
            (debtor, creditor, equivalent): Decimal(str(amount))
            for debtor, creditor, equivalent, amount in (
                await session.execute(
                    select(Debt.debtor_id, Debt.creditor_id, Debt.equivalent_id, Debt.amount)
                )
            ).all()
        }

    by_participant_id = {who.participant_id: ref for ref, who in run.identities.items()}
    by_equivalent_id = {eid: code for code, eid in run.equivalent_ids.items()}

    tightest: tuple[Decimal, str] | None = None
    for creditor, debtor, equivalent, limit_value in lines:
        limit = Decimal(str(limit_value))
        if limit <= 0:
            continue
        used = debts.get((debtor, creditor, equivalent), Decimal("0"))
        share = (limit - used) / limit
        if tightest is None or share < tightest[0]:
            tightest = (
                share,
                f"line {by_participant_id.get(creditor, creditor)} -> "
                f"{by_participant_id.get(debtor, debtor)} in "
                f"{by_equivalent_id.get(equivalent, equivalent)}: used {used} of {limit}",
            )

    if tightest is None:
        return {
            "passed": False,
            "detail": "no active trust line with a positive limit was found at all",
        }
    return {
        "passed": tightest[0] < BOTTLENECK_RESIDUAL_SHARE,
        "tightest_residual_share": str(tightest[0].quantize(Decimal("0.0001"))),
        "threshold": str(BOTTLENECK_RESIDUAL_SHARE),
        "detail": tightest[1],
    }


async def _check_clearing_executed(session_factory) -> dict[str, Any]:
    async with session_factory() as session:
        executed = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(debt_operations)
                    .where(debt_operations.c.kind == "CLEARING")
                )
            ).scalar_one()
        )
    return {
        "passed": executed > 0,
        "clearing_operations": executed,
        "detail": f"{executed} CLEARING operation(s) in the journal",
    }


async def _check_surviving_cycle(session_factory, run: _Run) -> dict[str, Any]:
    """Every `assert_clearable` cycle of the recipe is STILL detectable now, at the end."""

    asserted = [
        command
        for command in run.recipe["commands"]
        if command["op"] == "clearing" and command["mode"] == "assert_clearable"
    ]
    if not asserted:
        return {
            "passed": False,
            "detail": "the recipe asserts no surviving cycle, so nothing survives to be shown",
        }

    missing: list[str] = []
    found: list[str] = []
    for command in asserted:
        expected = run._expected_cycle_edges(command)
        async with session_factory() as session:
            cycles = await ClearingService(session).find_cycles(
                command["equivalent"], max_depth=_CYCLE_MAX_DEPTH
            )
        (found if _match_cycle(cycles, expected) is not None else missing).append(command["id"])

    return {
        "passed": not missing,
        "surviving": found,
        "missing": missing,
        "detail": f"{len(found)} of {len(asserted)} asserted cycle(s) still clearable",
    }


async def _check_activity_per_equivalent(session_factory, run: _Run) -> dict[str, Any]:
    """Every declared active equivalent carries at least one journalled operation of its own."""

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    debt_operation_equivalents.c.equivalent_id,
                    func.count(),
                ).group_by(debt_operation_equivalents.c.equivalent_id)
            )
        ).all()
    by_id = {equivalent_id: int(count) for equivalent_id, count in rows}

    active = sorted(
        declared["code"] for declared in run.community["equivalents"] if declared["is_active"]
    )
    # ITERATED OVER WHAT THE DESCRIPTION DECLARES, not over what the database happens to hold. The
    # first version walked `run.equivalent_ids` and filtered it by the declared set, so a declared
    # equivalent that was never created simply did not appear - and "no idle equivalent" was true
    # because the equivalent itself was missing. That is the shape `AGENTS.md` §9 calls a rule that
    # passes by having nothing to look at.
    per_code = {
        code: by_id.get(run.equivalent_ids.get(code), 0) if code in run.equivalent_ids else 0
        for code in active
    }
    idle = sorted(code for code, count in per_code.items() if count == 0)
    absent = sorted(code for code in active if code not in run.equivalent_ids)
    return {
        "passed": bool(active) and not idle,
        "operations_per_equivalent": per_code,
        "absent_equivalents": absent,
        "detail": (
            "the description declares no active equivalent at all"
            if not active
            else (
                "every active equivalent saw money"
                if not idle
                else f"idle equivalents: {idle}" + (f" (absent from the database: {absent})" if absent else "")
            )
        ),
    }


async def run_acceptance(session_factory, run: _Run) -> dict[str, dict[str, Any]]:
    checks: dict[str, dict[str, Any]] = {}
    checks.update(await _check_reconciliation(session_factory, run))
    checks["bottleneck_edge_below_threshold"] = await _check_bottleneck(session_factory, run)
    checks["clearing_executed"] = await _check_clearing_executed(session_factory)
    checks["surviving_cycle_still_clearable"] = await _check_surviving_cycle(session_factory, run)
    checks["activity_in_every_equivalent"] = await _check_activity_per_equivalent(
        session_factory, run
    )

    assert_every_check_reported(checks)
    return checks


def assert_every_check_reported(checks: dict[str, Any]) -> None:
    """A check that did not run is not a check that passed (`AGENTS.md` §9).

    Both directions, because both are ways for the acceptance to stop meaning what it says: a
    declared check with no verdict would read as a pass, and an undeclared one would be a verdict
    nobody promised - most likely a renamed check whose old name is still being looked for
    elsewhere.
    """

    missing = sorted(set(ACCEPTANCE_CHECKS) - set(checks))
    if missing:
        raise SeedRefusal(f"acceptance did not produce a verdict for {missing}")
    unknown = sorted(set(checks) - set(ACCEPTANCE_CHECKS))
    if unknown:
        raise SeedRefusal(f"acceptance produced unknown check(s) {unknown}")


# ==================================================================================================
# The ref -> PID table
# ==================================================================================================


def key_table_path(community_id: str, *, root: Path | None = None) -> Path:
    base = root if root is not None else (_REPO_ROOT / ".local-run" / "seed-recipe")
    return base / community_id / "participants.json"


def write_key_table(run: _Run, *, root: Path | None = None) -> Path:
    """Write the run's `ref -> PID` table, and nothing else.

    THE PRIVATE KEYS ARE NOT WRITTEN, here or anywhere: they exist for the length of the process and
    are what make the seed's operations real signatures rather than an internal bypass.

    No retention policy is declared for this file because it is not a growing family: one fixed path
    per community, overwritten by the next run of that community (`AGENTS.md` §12 asks for a TTL
    where artefacts accumulate).
    """

    path = key_table_path(run.community["community_id"], root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "community_id": run.community["community_id"],
        "recipe_title": run.recipe["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Symbolic ref -> the PID this run generated. PID = base58(sha256(public_key)) of a key "
            "pair that existed only in the seeding process; the private keys are gone."
        ),
        "participants": {
            ref: {"pid": who.pid, "name": run.community_names[ref]}
            for ref, who in sorted(run.identities.items())
        },
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ==================================================================================================
# Entry point
# ==================================================================================================


#: What `dev_database.py ready` requires on every start, out of `ACCEPTANCE_CHECKS`.
#:
#: The acceptance asks "did the seed finish correctly", once, right after the seed. A start asks "is
#: this database fit to run on", every time - including after the product has been USED, and using
#: it changes the demonstration state that `bottleneck_edge_below_threshold`,
#: `surviving_cycle_still_clearable` and `activity_in_every_equivalent` describe: `/clearing/auto`
#: clears the surviving cycle, payments move the bottleneck. Readiness that re-ran all seven refused
#: a correct database and advised resetting it (Codex external review of `37fec08..5e687dd`, F1,
#: 2026-09-23). What a start needs is the reconciliation: the money the journal explains is the
#: money in `debts`, which a legitimate operation keeps true. The other two reconciliation checks
#: are properties of the SEED's verdict (the baseline adopted nothing; every operation of the seed
#: was fully recomputed) and are not kept either. The second would go red on a sound database the
#: moment the simulator's injector writes an `INJECT` operation, which reconciliation examines only
#: as a subset (`app/core/ledger/reconciliation.py:162`) - a limit the verdict records about
#: itself, not a defect of the database.
READINESS_CHECKS = ("reconciliation_passed",)


async def _load_seeded_run(
    session_factory: Callable[[], Any],
    *,
    community_id: str,
    refs_to_pid: dict[str, str],
    communities_root: Path | None,
) -> _Run:
    """Rebuild a run over an ALREADY seeded database from its `ref -> PID` table, signing nothing.

    Refuses when a participant the table names is not in the database: that is a different or
    unfinished population, not the one the table describes.
    """

    root = communities_root if communities_root is not None else _COMMUNITIES_DIR
    community = community_schema.load_community(community_id, root=root)
    recipe = recipe_schema.load_recipe(community_id, root=root)
    run = _Run(session_factory, community, recipe)

    async with session_factory() as session:
        run.equivalent_ids = {
            code: eid for code, eid in (await session.execute(select(Equivalent.code, Equivalent.id))).all()
        }
        rows = (
            await session.execute(
                select(Participant.pid, Participant.id).where(
                    Participant.pid.in_(sorted(refs_to_pid.values()))
                )
            )
        ).all()
    id_by_pid = {pid: participant_id for pid, participant_id in rows}

    missing = sorted(ref for ref, pid in refs_to_pid.items() if pid not in id_by_pid)
    if missing:
        raise SeedRefusal(
            f"{len(missing)} participant(s) of {community_id} named by the ref -> PID table are not "
            f"in this database: {missing[:10]}"
        )
    run.identities = {
        ref: _Identity(
            ref=ref,
            pid=pid,
            public_key="",
            _signing_key=None,
            participant_id=id_by_pid[pid],
        )
        for ref, pid in refs_to_pid.items()
    }
    return run


async def reverify(
    session_factory: Callable[[], Any],
    *,
    community_id: str,
    refs_to_pid: dict[str, str],
    communities_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the whole ACCEPTANCE again over an already seeded database, without seeding anything.

    All seven checks, so this answers "is this still the state the seed left" - which is what the
    counter-checks in `tests/integration/test_p017_t1711_seed_recipe_postgres.py` need, and which a
    database the product has since used is allowed to fail. It is NOT the start gate; that is
    `check_ready_to_start`.
    """

    run = await _load_seeded_run(
        session_factory,
        community_id=community_id,
        refs_to_pid=refs_to_pid,
        communities_root=communities_root,
    )
    return await run_acceptance(session_factory, run)


async def check_ready_to_start(
    session_factory: Callable[[], Any],
    *,
    community_id: str,
    refs_to_pid: dict[str, str],
    communities_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """The `READINESS_CHECKS` over a seeded database: its population is there and it reconciles."""

    run = await _load_seeded_run(
        session_factory,
        community_id=community_id,
        refs_to_pid=refs_to_pid,
        communities_root=communities_root,
    )
    reconciliation = await _check_reconciliation(session_factory, run)
    return {name: reconciliation[name] for name in READINESS_CHECKS}


async def seed_community(
    session_factory: Callable[[], Any],
    *,
    community_id: str,
    communities_root: Path | None = None,
    key_table_root: Path | None = None,
    env: str | None = None,
    allow_scratch_suffix: bool = False,
) -> SeedReport:
    """Seed one community by running its recipe. Raises `SeedRefusal` and writes nothing further."""

    root = communities_root if communities_root is not None else _COMMUNITIES_DIR
    try:
        community = community_schema.load_community(community_id, root=root)
        recipe = recipe_schema.load_recipe(community_id, root=root)
    except FileNotFoundError as missing:
        # `scripts/seed_db.py --community` also offers the two `-v2` fixture pack ids, which have
        # no description and no recipe. A traceback would read as a defect; this reads as the
        # wrong argument, and says which communities can be seeded this way.
        seedable = sorted(
            child.name for child in root.iterdir() if (child / "recipe.json").is_file()
        ) if root.is_dir() else []
        raise SeedRefusal(
            f"{community_id} has no description and recipe under {root}: {missing}. "
            f"Communities that can be seeded by recipe: {seedable}"
        ) from missing

    offenders = unreachable_declared_states(community)
    if offenders:
        raise SeedRefusal(
            f"{community_id} declares {len(offenders)} state(s) no product operation reaches, so "
            f"seeding it would mean writing a domain column directly - which is the bypass "
            f"programme 017 exists to remove. Recorded in specs/BACKLOG.md (2026-09-22) and the "
            f"owner's to settle. Offending declarations:\n  - " + "\n  - ".join(offenders)
        )

    assert_environment_is_safe(env)
    url = await database_url(session_factory)
    assert_target_is_disposable(url, allow_scratch_suffix=allow_scratch_suffix)
    try:
        async with session_factory() as session:
            await assert_database_is_empty(session)
    except SeedRefusal:
        raise
    except Exception as exc:
        # A database that is absent, unreachable or unmigrated is a refusal with a name, not a
        # driver traceback. The URL is rendered with the password hidden (`AGENTS.md` §12).
        raise SeedRefusal(
            f"cannot read the target database {url.render_as_string(hide_password=True)}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    run = _Run(session_factory, community, recipe)

    await run.create_equivalents()
    await run.create_participants()
    run.report.key_table_path = str(write_key_table(run, root=key_table_root))
    await run.create_trustlines()
    await run.take_baselines()
    await run.run_commands()

    run.report.acceptance = await run_acceptance(session_factory, run)
    failed = sorted(name for name, check in run.report.acceptance.items() if not check["passed"])
    if failed:
        lines = "\n  - ".join(
            f"{name}: {run.report.acceptance[name]['detail']}" for name in failed
        )
        raise SeedRefusal(
            f"the recipe ran, but the seeded database does not satisfy "
            f"{len(failed)} acceptance check(s):\n  - {lines}"
        )
    return run.report
