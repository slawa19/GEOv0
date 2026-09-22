"""Schema of a seed recipe (`seeds/communities/<community>/recipe.json`).

A community description says *who is in the community and who trusts whom*
(`community_schema.py`). It holds no money, because money is the *result* of
operations. A recipe is that missing half: a short, hand-written list of the
real operations a seeded hub must perform, in the order they must happen.

`T1711` executes it through the same signed domain paths a participant uses —
`ParticipantService`, `TrustLineService`, `PaymentService` and the clearing
service — with key pairs generated per run. Nothing here talks to a database,
and nothing here knows a PID: the recipe names both ends of every operation by
the symbolic ``ref`` of the description, and the executor resolves
``ref`` -> real PID through its own table (a real PID is
``base58(sha256(public_key))``, `app/core/auth/crypto.py:37-50`, so it cannot be
written down in advance).

Three operations, because the seed needs exactly three:

* ``payment`` — ``payer`` pays ``payee`` an ``amount`` in an ``equivalent``.
  **Direction matters and is the trap this repository has already fallen into.**
  A trustline is written creditor -> debtor. A payment S -> R increases S's debt
  to R, so the line that carries it is the one from R to S
  (`app/core/payments/router.py:271-276`). Every rule below about "the line a
  payment needs" means that line and no other.

  ``routing`` says what the recipe is claiming about the route:

  - ``direct`` — the executor sends ``max_hops=1``
    (`app/schemas/payment.py:33`), and the recipe asserts the effect on one
    named line. Without the cap the router is free to reach the same payee by
    another path and the intended edge never forms, so a recipe that builds a
    bottleneck or a clearing cycle has to use it.
  - ``chain`` — the meaning of the command *is* the multi-hop route, so no cap
    is sent, and the validator asserts that no direct line exists between the
    ends. Any route that succeeds is then at least two hops long.
  - ``open`` — the router chooses; the recipe claims only the route-independent
    net effect.

* ``freeze`` — the participant reaches the state the description declares.
  `admin.participants.freeze` writes ``suspended``
  (`app/api/v1/admin.py:977-992`); the UI vocabulary calls that state ``frozen``
  (`app/api/v1/admin.py:135-152`), which is the word the description uses. A
  status in the description is an *intention*; only an operation makes it true,
  and an operation has a moment, which is why the freeze lives in the recipe
  rather than in the loader.

* ``clearing`` — a cycle of debts, named by its participants in debtor ->
  creditor order, which is the order `docs/ru/02-protocol-spec.md:1127-1128`
  uses. The cycle is closed implicitly: the last participant owes the first,
  and the first is **not** repeated at the end. ``mode: "execute"`` runs it;
  ``mode: "assert_clearable"``
  asserts the cycle is there and clearable without running it, which is how a
  recipe leaves a *surviving* cycle behind for the admin screens to show.

WHAT THIS VALIDATOR CHECKS: the form of a recipe and its referential integrity
against one description. Nothing else.

WHAT IT DOES NOT CHECK, AND MUST NOT BE READ AS CHECKING:

* **Whether a route exists.** It models no capacity, no debts, no netting and
  no router. A ``chain`` or ``open`` command that passes here can still find no
  route at runtime; only `T1711`'s run decides that.
* **Whether the recipe is economically sensible.** It reads ``why`` and
  ``expect`` as strings; it has no opinion on whether they are true.
* **The effect of ``chain`` and ``open`` payments on trust limits.** The limit
  check counts ``direct`` commands only, because those are the ones whose route
  the recipe fixes. A chain route may consume the same line at runtime, so the
  check is conservative in the safe direction: it can accept a recipe whose
  chain routes squeeze a line further, never one whose single-hop commands
  overshoot it.
* **Relief from clearing.** The same limit check ignores that an executed
  clearing frees capacity, which makes it stricter than reality, never looser.
* **Whether a frozen participant is used as an intermediate hop.** The recipe
  can forbid naming a frozen participant; it cannot forbid the router from
  routing through one, and a suspended participant is not excluded from routing
  or clearing at the domain level.

This module is dependency-free on purpose, like `community_schema.py`: it is
imported by scripts that run before anything is installed.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "recipe/1"

COMMUNITIES_DIR = Path(__file__).resolve().parent

#: `PaymentCreateRequest.tx_id`, verbatim (`app/schemas/payment.py:38`). The
#: command id IS the tx_id the executor sends, which is what makes the recipe
#: idempotent: re-running it re-sends the same identifiers.
TX_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
TX_ID_MAX_LENGTH = 64

#: `PaymentConstraints.max_hops` bounds (`app/schemas/payment.py:33`).
SINGLE_HOP = 1

_TOP_LEVEL_REQUIRED = ("schema_version", "community_id", "title", "summary", "commands")

_COMMON_KEYS = ("id", "op", "why", "expect")
_KEYS_BY_OP = {
    "payment": _COMMON_KEYS + ("equivalent", "payer", "payee", "amount", "routing"),
    "freeze": _COMMON_KEYS + ("participant",),
    "clearing": _COMMON_KEYS + ("equivalent", "cycle", "amount", "mode"),
}

_ROUTINGS = ("chain", "direct", "open")
_MODES = ("assert_clearable", "execute")

_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")

#: The shortest cycle the clearing service can detect is a triangle
#: (`app/core/clearing/service.py:582`, `find_triangles_sql`).
MIN_CYCLE_LENGTH = 3


class RecipeError(ValueError):
    """A recipe does not agree with the community description it names."""


def _fail(source: str, message: str) -> None:
    raise RecipeError(f"{source}: {message}")


def _require_keys(source: str, what: str, obj: Any, required: tuple[str, ...]) -> None:
    if not isinstance(obj, dict):
        _fail(source, f"{what} must be an object, got {type(obj).__name__}")
    missing = [k for k in required if k not in obj]
    if missing:
        _fail(source, f"{what} is missing keys {missing}")
    extra = sorted(set(obj) - set(required))
    if extra:
        _fail(source, f"{what} has unknown keys {extra}")


def _text(source: str, where: str, value: Any) -> None:
    if not isinstance(value, str) or not value.strip():
        _fail(source, f"{where} must be a non-empty string")


class _Community:
    """The parts of a description a recipe refers to, indexed once."""

    def __init__(self, community: dict[str, Any]) -> None:
        self.community_id = community["community_id"]
        self.precision = {e["code"]: e["precision"] for e in community["equivalents"]}
        self.active_equivalents = {e["code"] for e in community["equivalents"] if e["is_active"]}
        self.status = {p["ref"]: p["status"] for p in community["participants"]}
        self.lines = {
            (t["equivalent"], t["from"], t["to"]): t for t in community["trustlines"]
        }
        self.can_owe: set[tuple[str, str]] = set()
        self.can_be_owed: set[tuple[str, str]] = set()
        for (equivalent, creditor, debtor), line in self.lines.items():
            if line["status"] != "active":
                continue
            self.can_owe.add((equivalent, debtor))
            self.can_be_owed.add((equivalent, creditor))

    def line(self, equivalent: str, creditor: str, debtor: str) -> dict[str, Any] | None:
        line = self.lines.get((equivalent, creditor, debtor))
        return line if line is not None and line["status"] == "active" else None


def _check_amount(source: str, where: str, amount: Any, equivalent: str, index: _Community) -> Decimal:
    if not isinstance(amount, str) or not _DECIMAL_RE.match(amount) or Decimal(amount) <= 0:
        _fail(source, f"{where}.amount {amount!r} is not a plain positive decimal string")
    fractional = len(amount.partition(".")[2])
    precision = index.precision[equivalent]
    if fractional > precision:
        _fail(
            source,
            f"{where}.amount {amount!r} has {fractional} fractional digits, "
            f"more than precision {precision} of {equivalent}",
        )
    return Decimal(amount)


def _check_equivalent(source: str, where: str, code: Any, index: _Community) -> None:
    if code not in index.precision:
        _fail(source, f"{where}.equivalent {code!r} is not a declared equivalent")
    if code not in index.active_equivalents:
        _fail(source, f"{where}.equivalent {code!r} is declared inactive")


def _check_ref(source: str, where: str, ref: Any, index: _Community) -> None:
    if ref not in index.status:
        _fail(source, f"{where} {ref!r} is not a participant ref")


def validate_recipe(recipe: Any, community: Any, *, source: str = "<recipe>") -> dict[str, Any]:
    """Raise :class:`RecipeError` unless ``recipe`` agrees with ``community``.

    ``community`` must already be a valid description; pass it through
    ``community_schema.validate_community`` first (``load_recipe`` does).
    """

    if not isinstance(recipe, dict):
        _fail(source, f"recipe must be an object, got {type(recipe).__name__}")
    if not isinstance(community, dict):
        _fail(source, "community description must be an object")

    missing = [k for k in _TOP_LEVEL_REQUIRED if k not in recipe]
    if missing:
        _fail(source, f"missing top-level keys {missing}")
    extra = sorted(set(recipe) - set(_TOP_LEVEL_REQUIRED))
    if extra:
        _fail(source, f"unknown top-level keys {extra}")

    if recipe["schema_version"] != SCHEMA_VERSION:
        _fail(source, f"schema_version {recipe['schema_version']!r} != {SCHEMA_VERSION!r}")
    for key in ("community_id", "title", "summary"):
        _text(source, key, recipe[key])

    index = _Community(community)
    if recipe["community_id"] != index.community_id:
        _fail(
            source,
            f"recipe names community {recipe['community_id']!r}, but this description "
            f"does not describe it (it describes {index.community_id!r})",
        )

    commands = recipe["commands"]
    if not isinstance(commands, list) or not commands:
        _fail(source, "commands must be a non-empty array")

    ids: set[str] = set()
    # (equivalent, creditor, debtor) -> [(command index, amount)]
    single_hop: dict[tuple[str, str, str], list[tuple[int, Decimal]]] = {}
    frozen_at: dict[str, int] = {}
    executed_clearings: list[int] = []
    asserted_clearings: list[int] = []

    for position, command in enumerate(commands):
        where = f"commands[{position}]"
        if not isinstance(command, dict):
            _fail(source, f"{where} must be an object, got {type(command).__name__}")
        if "op" not in command:
            _fail(source, f"{where} is missing keys ['op']")
        op = command["op"]
        if op not in _KEYS_BY_OP:
            _fail(source, f"{where}.op {op!r} not in {sorted(_KEYS_BY_OP)}")
        _require_keys(source, where, command, _KEYS_BY_OP[op])

        command_id = command["id"]
        if not isinstance(command_id, str) or not TX_ID_RE.match(command_id):
            _fail(source, f"{where}.id {command_id!r} must match {TX_ID_RE.pattern}")
        if len(command_id) > TX_ID_MAX_LENGTH:
            _fail(
                source,
                f"{where}.id {command_id!r} is longer than {TX_ID_MAX_LENGTH} characters, "
                f"which PaymentCreateRequest.tx_id refuses",
            )
        if command_id in ids:
            _fail(source, f"{where}.id {command_id!r} is used twice")
        ids.add(command_id)

        where = f"{where} ({command_id})"
        _text(source, f"{where}.why", command["why"])
        _text(source, f"{where}.expect", command["expect"])

        named: set[str] = set()

        if op == "payment":
            equivalent = command["equivalent"]
            _check_equivalent(source, where, equivalent, index)
            for end in ("payer", "payee"):
                _check_ref(source, f"{where}.{end}", command[end], index)
            payer, payee = command["payer"], command["payee"]
            if payer == payee:
                _fail(source, f"{where} pays {payer!r} to itself")
            named = {payer, payee}

            amount = _check_amount(source, where, command["amount"], equivalent, index)

            routing = command["routing"]
            if routing not in _ROUTINGS:
                _fail(source, f"{where}.routing {routing!r} not in {list(_ROUTINGS)}")

            # A payment moves along the trustline from the PAYEE (creditor) to
            # the PAYER (debtor): `app/core/payments/router.py:271-276`. If the
            # payer has no such line anywhere in this equivalent, there is no
            # first hop for any route, and no constraint can conjure one.
            if (equivalent, payer) not in index.can_owe:
                _fail(
                    source,
                    f"{where}: payer {payer!r} cannot owe anybody in {equivalent} — "
                    f"the description gives it no active trustline where it is the debtor",
                )
            if (equivalent, payee) not in index.can_be_owed:
                _fail(
                    source,
                    f"{where}: nobody can owe payee {payee!r} in {equivalent} — "
                    f"the description gives it no active trustline where it is the creditor",
                )

            direct_line = index.line(equivalent, payee, payer)
            if routing == "direct":
                if direct_line is None:
                    _fail(
                        source,
                        f"{where} is a single-hop payment, but {payee!r} -> {payer!r} "
                        f"has no active trustline in {equivalent}; remember a trustline "
                        f"is written creditor -> debtor, so a payment {payer!r} -> {payee!r} "
                        f"needs the line {payee!r} -> {payer!r}",
                    )
                key = (equivalent, payee, payer)
                single_hop.setdefault(key, []).append((position, amount))
            elif routing == "chain" and direct_line is not None:
                _fail(
                    source,
                    f"{where} is declared a chain payment, but a direct trustline "
                    f"{payee!r} -> {payer!r} exists in {equivalent}, so one hop would do",
                )

        elif op == "freeze":
            participant = command["participant"]
            _check_ref(source, f"{where}.participant", participant, index)
            if index.status[participant] != "frozen":
                _fail(
                    source,
                    f"{where} freezes {participant!r}, but the description declares it "
                    f"active; a recipe reaches the state a description declares, it does "
                    f"not invent a different one",
                )
            if participant in frozen_at:
                _fail(source, f"{where} freezes {participant!r}, already frozen at command {frozen_at[participant]}")
            frozen_at[participant] = position
            named = {participant}

        else:  # clearing
            equivalent = command["equivalent"]
            _check_equivalent(source, where, equivalent, index)

            mode = command["mode"]
            if mode not in _MODES:
                _fail(source, f"{where}.mode {mode!r} not in {list(_MODES)}")

            cycle = command["cycle"]
            if not isinstance(cycle, list) or len(cycle) < MIN_CYCLE_LENGTH:
                _fail(
                    source,
                    f"{where}.cycle must name at least {MIN_CYCLE_LENGTH} participants, "
                    f"the shortest cycle clearing can detect",
                )
            if len(set(cycle)) != len(cycle):
                _fail(source, f"{where}.cycle names the same participant twice")
            for ref in cycle:
                _check_ref(source, f"{where}.cycle", ref, index)
            named = set(cycle)

            declared = _check_amount(source, where, command["amount"], equivalent, index)

            smallest: Decimal | None = None
            for debtor, creditor in zip(cycle, cycle[1:] + cycle[:1]):
                line = index.line(equivalent, creditor, debtor)
                if line is None:
                    _fail(
                        source,
                        f"{where}: the cycle edge {debtor!r} owes {creditor!r} "
                        f"has no active trustline {creditor!r} -> {debtor!r} in {equivalent}; "
                        f"a clearing cycle lives inside one equivalent "
                        f"(app/core/clearing/service.py:1587-1590)",
                    )
                if line["policy"]["auto_clearing"] is not True:
                    _fail(
                        source,
                        f"{where}: the trustline {creditor!r} -> {debtor!r} in {equivalent} "
                        f"has auto_clearing false, and execute_clearing skips a cycle whose "
                        f"every edge has not consented "
                        f"(app/core/clearing/service.py:1905-1922)",
                    )

                builders = [
                    (i, value)
                    for i, value in single_hop.get((equivalent, creditor, debtor), [])
                    if i < position
                ]
                if len(builders) != 1:
                    _fail(
                        source,
                        f"{where}: the cycle edge {debtor!r} owes {creditor!r} is built by "
                        f"{len(builders)} preceding single-hop payments, expected exactly one "
                        f"— the declared cleared amount can only be read off an edge whose "
                        f"debt one command owns",
                    )
                built_at, value = builders[0]
                smallest = value if smallest is None else min(smallest, value)

                if mode == "assert_clearable" and executed_clearings:
                    if built_at < executed_clearings[-1]:
                        _fail(
                            source,
                            f"{where}: the cycle edge {debtor!r} owes {creditor!r} is built at "
                            f"command {built_at}, before the last executed clearing at command "
                            f"{executed_clearings[-1]}; a cycle built earlier is a cycle that "
                            f"clearing may already have eaten, so it cannot be claimed to survive",
                        )

            if smallest is not None and declared != smallest:
                _fail(
                    source,
                    f"{where}.amount {command['amount']!r} is not the smallest debt in the "
                    f"cycle ({smallest}); clearing settles the minimum of the cycle",
                )

            if mode == "execute":
                if asserted_clearings:
                    _fail(
                        source,
                        f"{where} executes a clearing after the surviving cycle was asserted at "
                        f"command {asserted_clearings[-1]}; a surviving cycle must come after "
                        f"every executed clearing, or it does not survive one",
                    )
                executed_clearings.append(position)
            else:
                asserted_clearings.append(position)

        for ref in sorted(named & frozen_at.keys()):
            if op == "freeze":
                continue
            _fail(
                source,
                f"{where} names {ref!r} after it was frozen at command {frozen_at[ref]}; "
                f"operations on a participant belong before its freeze, not after",
            )

    for key, entries in single_hop.items():
        equivalent, creditor, debtor = key
        limit = Decimal(index.lines[key]["limit"])
        total = sum((amount for _, amount in entries), Decimal("0"))
        if total > limit:
            _fail(
                source,
                f"single-hop payments on the trustline {creditor!r} -> {debtor!r} in "
                f"{equivalent} add up to {total}, which exceeds the limit {limit}; "
                f"debt[debtor->creditor] <= trustline[creditor->debtor].limit is a database "
                f"invariant (app/core/invariants.py:95-97)",
            )

    declared_frozen = sorted(ref for ref, status in index.status.items() if status == "frozen")
    for ref in declared_frozen:
        if ref not in frozen_at:
            _fail(
                source,
                f"the description declares {ref!r} frozen, but the recipe has no freeze command "
                f"for it; a declared status nobody reaches is a state the seed never produces",
            )

    return recipe


def max_hops_for(command: dict[str, Any]) -> int | None:
    """The ``PaymentConstraints.max_hops`` a payment command must be sent with.

    ``None`` means "send no cap": for ``chain`` the multi-hop route is the point
    of the command, and for ``open`` the route is the router's to choose.
    """

    return SINGLE_HOP if command.get("routing") == "direct" else None


def load_recipe(community_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Read and validate ``seeds/communities/<community_id>/recipe.json``.

    The caller must have ``seeds/communities`` importable, which every existing
    consumer of `community_schema` already arranges by putting that directory on
    ``sys.path`` (`scripts/generate_simulator_seed_scenarios.py:33-34`,
    `admin-fixtures/tools/extract_community_description.py:41-42`). A caller that
    loads this file by path instead should call :func:`validate_recipe` directly
    with a description it read itself.
    """

    from community_schema import load_community  # noqa: PLC0415

    base = root if root is not None else COMMUNITIES_DIR
    community = load_community(community_id, root=base)
    path = base / community_id / "recipe.json"
    recipe = json.loads(path.read_text(encoding="utf-8"))
    validate_recipe(recipe, community, source=str(path))
    return recipe
