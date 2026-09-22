"""The seed recipe says what happens in the community, and it says it truthfully.

Programme 017, `T1713`. The recipe is data: a hand-written list of thirty-odd
real operations (`payment`, `freeze`, `clearing`) whose ends are the symbolic
``ref`` of `community.json`, not PIDs. `T1711` executes it through
`ParticipantService` / `TrustLineService` / `PaymentService` / the clearing
service with key pairs generated per run. Nothing here touches a database.

Three things are asserted.

1. **The committed recipes agree with the committed descriptions.** Every ref
   resolves, every equivalent is declared and active, every line a command
   assumes exists and points the way the command assumes, every amount fits the
   precision of its equivalent.
2. **The recipes demonstrate what `T1713` asked for**: money in every declared
   equivalent, one executed clearing, a clearable cycle that is built *after*
   it and therefore survives, an edge driven below 10 % of its limit by
   single-hop payments alone, real freezes for exactly the participants the
   description declares frozen, and payments that must travel more than one hop.
3. **The validator refuses a recipe that lies.** A validator that accepts
   everything would make the first two assertions worthless (AGENTS.md §9,
   anti-vacuum), so every rule it owns is shown rejecting a real violation, and
   every rejection is preceded by the unmutated document passing as a control.

What this file does NOT assert, deliberately: that a route exists at runtime for
a ``chain`` or ``open`` payment, that the economics make sense, or that the run
ends in the state each command's ``expect`` describes. Those are claims about a
database and belong to `T1711`'s run, not to a static check of data.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMUNITIES_DIR = REPO_ROOT / "seeds" / "communities"

if str(COMMUNITIES_DIR) not in sys.path:
    sys.path.insert(0, str(COMMUNITIES_DIR))

from community_schema import load_community  # noqa: E402
from recipe_schema import (  # noqa: E402
    RecipeError,
    max_hops_for,
    validate_recipe,
)

COMMUNITY_IDS = ["greenfield-village-100", "riverside-town-50"]

# `T1713`: "порядка 30-50 команд". The band is the contract, not the exact
# count: a recipe below it stops explaining the community, above it stops being
# readable by a human, which was the whole reason a generator was rejected.
MIN_COMMANDS = 30
MAX_COMMANDS = 50

# `T1711` acceptance: "ребро ниже 10 % ёмкости".
BOTTLENECK_FRACTION = Decimal("0.10")


def _read(community_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    community = load_community(community_id, root=COMMUNITIES_DIR)
    recipe = json.loads(
        (COMMUNITIES_DIR / community_id / "recipe.json").read_text(encoding="utf-8")
    )
    return recipe, community


@pytest.fixture(scope="module")
def documents() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {cid: _read(cid) for cid in COMMUNITY_IDS}


def _payments(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in recipe["commands"] if c["op"] == "payment"]


def _clearings(recipe: dict[str, Any], mode: str) -> list[tuple[int, dict[str, Any]]]:
    return [
        (i, c)
        for i, c in enumerate(recipe["commands"])
        if c["op"] == "clearing" and c["mode"] == mode
    ]


# --------------------------------------------------------------------------
# 1. The committed recipes agree with the committed descriptions.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_committed_recipe_is_valid_against_its_description(community_id, documents):
    recipe, community = documents[community_id]
    validate_recipe(copy.deepcopy(recipe), copy.deepcopy(community), source=community_id)
    assert recipe["community_id"] == community_id


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_recipe_is_committed_as_the_repository_writes_json(community_id):
    path = COMMUNITIES_DIR / community_id / "recipe.json"
    raw = path.read_bytes()
    assert b"\r\n" not in raw, "`.gitattributes` keeps every .json at LF in the worktree"
    assert raw.endswith(b"\n")
    text = raw.decode("utf-8")
    assert text == json.dumps(json.loads(text), ensure_ascii=False, indent=2) + "\n"


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_every_command_explains_itself_and_states_an_outcome(community_id, documents):
    recipe, _ = documents[community_id]
    for command in recipe["commands"]:
        # A one-word `why` is a comment, not an economic explanation, and a
        # one-word `expect` is not acceptance. The floor is deliberately low:
        # the point is to catch an empty placeholder, not to grade prose.
        assert len(command["why"]) >= 40, command["id"]
        assert len(command["expect"]) >= 40, command["id"]


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_a_command_id_is_usable_as_a_tx_id(community_id, documents):
    """The identifier a command carries is the one `T1711` gives the payment.

    Idempotency of a payment is keyed by `tx_id`, so the recipe's id has to
    survive `PaymentCreateRequest.tx_id` verbatim (`app/schemas/payment.py:38`).
    """

    from app.schemas.payment import PaymentCreateRequest

    field = PaymentCreateRequest.model_fields["tx_id"]
    pattern = next(m.pattern for m in field.metadata if hasattr(m, "pattern"))
    max_length = next(m.max_length for m in field.metadata if hasattr(m, "max_length"))

    recipe, _ = documents[community_id]
    for command in recipe["commands"]:
        assert re.match(pattern, command["id"]), command["id"]
        assert len(command["id"]) <= max_length, command["id"]


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_routing_mode_maps_to_a_constraint_the_api_really_accepts(community_id, documents):
    """`max_hops=1` is not invented here: it is `PaymentConstraints.max_hops`."""

    from app.schemas.payment import PaymentConstraints

    assert PaymentConstraints(max_hops=1).max_hops == 1
    with pytest.raises(Exception):
        PaymentConstraints(max_hops=0)

    recipe, _ = documents[community_id]
    for command in _payments(recipe):
        hops = max_hops_for(command)
        if command["routing"] == "direct":
            assert hops == 1, command["id"]
            assert PaymentConstraints(max_hops=hops).max_hops == 1
        else:
            # The opposite case is the point of the distinction: where the
            # meaning of the command IS the multi-hop route, a cap of one would
            # make it fail, so the recipe sends no cap at all.
            assert hops is None, command["id"]


# --------------------------------------------------------------------------
# 2. The recipes demonstrate what T1713 asked for.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_the_recipe_is_hand_sized(community_id, documents):
    recipe, _ = documents[community_id]
    assert MIN_COMMANDS <= len(recipe["commands"]) <= MAX_COMMANDS


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_money_moves_in_every_declared_active_equivalent(community_id, documents):
    recipe, community = documents[community_id]
    declared = {e["code"] for e in community["equivalents"] if e["is_active"]}
    used = {c["equivalent"] for c in _payments(recipe)}
    assert declared == used, f"equivalents without a payment: {sorted(declared - used)}"


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_some_payments_must_travel_more_than_one_hop(community_id, documents):
    recipe, community = documents[community_id]
    lines = {(t["equivalent"], t["from"], t["to"]) for t in community["trustlines"]}
    chains = [c for c in _payments(recipe) if c["routing"] == "chain"]
    assert chains, "a recipe with no chain payment does not show what the protocol is for"
    for command in chains:
        key = (command["equivalent"], command["payee"], command["payer"])
        assert key not in lines, (
            f"{command['id']} is declared a chain payment, but a direct trustline "
            f"{command['payee']} -> {command['payer']} exists, so one hop would do"
        )


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_exactly_one_clearing_is_executed_and_a_clearable_cycle_survives_it(
    community_id, documents
):
    recipe, _ = documents[community_id]
    executed = _clearings(recipe, "execute")
    surviving = _clearings(recipe, "assert_clearable")
    assert len(executed) == 1, "T1713: «один исполненный клиринг»"
    assert surviving, "T1713: «выживший клирящийся цикл»"

    last_executed = executed[-1][0]
    for index, _ in surviving:
        assert index > last_executed

    # And the surviving cycle is BUILT after the clearing, not merely asserted
    # after it: a cycle built earlier would have been eaten by the clearing.
    for _, command in surviving:
        cycle = command["cycle"]
        edges = list(zip(cycle, cycle[1:] + cycle[:1]))
        for debtor, creditor in edges:
            builders = [
                i
                for i, c in enumerate(recipe["commands"])
                if c["op"] == "payment"
                and c["routing"] == "direct"
                and c["equivalent"] == command["equivalent"]
                and c["payer"] == debtor
                and c["payee"] == creditor
            ]
            assert builders, f"{command['id']}: edge {debtor} -> {creditor} is built by nobody"
            assert min(builders) > last_executed, (
                f"{command['id']}: edge {debtor} -> {creditor} is built at command "
                f"{min(builders)}, before the clearing at {last_executed}"
            )


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_a_cleared_cycle_lives_inside_one_equivalent(community_id, documents):
    """`app/core/clearing/service.py:1587-1590` refuses anything else."""

    recipe, community = documents[community_id]
    lines = {(t["equivalent"], t["from"], t["to"]): t for t in community["trustlines"]}
    for command in recipe["commands"]:
        if command["op"] != "clearing":
            continue
        cycle = command["cycle"]
        assert len(cycle) >= 3 and len(set(cycle)) == len(cycle), command["id"]
        for debtor, creditor in zip(cycle, cycle[1:] + cycle[:1]):
            line = lines.get((command["equivalent"], creditor, debtor))
            assert line is not None, f"{command['id']}: {creditor} -> {debtor} is not a line"
            assert line["status"] == "active", command["id"]
            # Every edge needs consent, or `execute_clearing` skips the cycle
            # (`app/core/clearing/service.py:1905-1922`).
            assert line["policy"]["auto_clearing"] is True, command["id"]


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_single_hop_payments_alone_drive_one_edge_below_ten_percent(community_id, documents):
    """`T1711` acceptance: «ребро ниже 10 % ёмкости».

    Counted from ``direct`` commands only, because they are the ones whose route
    the recipe fixes. A ``chain`` or ``open`` payment may also consume this line
    at runtime; that can only make the remaining headroom smaller, never larger,
    so the bound below is the conservative one.
    """

    recipe, community = documents[community_id]
    lines = {(t["equivalent"], t["from"], t["to"]): t for t in community["trustlines"]}
    used: dict[tuple[str, str, str], Decimal] = {}
    for command in _payments(recipe):
        if command["routing"] != "direct":
            continue
        key = (command["equivalent"], command["payee"], command["payer"])
        used[key] = used.get(key, Decimal("0")) + Decimal(command["amount"])

    squeezed = [
        (key, amount, Decimal(lines[key]["limit"]))
        for key, amount in used.items()
        if (Decimal(lines[key]["limit"]) - amount) / Decimal(lines[key]["limit"])
        < BOTTLENECK_FRACTION
    ]
    assert squeezed, "no edge is driven below 10 % of its limit by single-hop payments"
    for key, amount, limit in squeezed:
        assert amount <= limit, key


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_every_participant_the_description_calls_frozen_is_frozen_by_an_operation(
    community_id, documents
):
    """A status in the description is intent; the state is reached by an operation.

    `admin.participants.freeze` writes `suspended` (`app/api/v1/admin.py:977-992`);
    the UI vocabulary calls that state `frozen` (`app/api/v1/admin.py:135-152`).
    """

    recipe, community = documents[community_id]
    declared = sorted(p["ref"] for p in community["participants"] if p["status"] == "frozen")
    frozen_by_recipe = [c["participant"] for c in recipe["commands"] if c["op"] == "freeze"]
    assert sorted(frozen_by_recipe) == declared
    assert len(frozen_by_recipe) == len(set(frozen_by_recipe))


@pytest.mark.parametrize("community_id", COMMUNITY_IDS)
def test_nothing_happens_to_a_participant_after_it_is_frozen(community_id, documents):
    recipe, _ = documents[community_id]
    frozen_at: dict[str, int] = {}
    for index, command in enumerate(recipe["commands"]):
        if command["op"] == "freeze":
            frozen_at[command["participant"]] = index
            continue
        named = set()
        if command["op"] == "payment":
            named = {command["payer"], command["payee"]}
        elif command["op"] == "clearing":
            named = set(command["cycle"])
        for ref in named & frozen_at.keys():
            pytest.fail(
                f"{command['id']} names {ref}, frozen at command {frozen_at[ref]}"
            )


# --------------------------------------------------------------------------
# 3. The validator refuses a recipe that lies.
# --------------------------------------------------------------------------


def _first_index(recipe: dict[str, Any], command_id: str) -> int:
    return next(i for i, c in enumerate(recipe["commands"]) if c["id"] == command_id)


def _set_command(command_id: str, key: str, value: Any) -> Callable:
    def mutate(recipe, community):
        recipe["commands"][_first_index(recipe, command_id)][key] = value

    return mutate


def _drop_command(command_id: str) -> Callable:
    def mutate(recipe, community):
        del recipe["commands"][_first_index(recipe, command_id)]

    return mutate


def _set_line_policy(equivalent: str, creditor: str, debtor: str, key: str, value: Any) -> Callable:
    def mutate(recipe, community):
        for line in community["trustlines"]:
            if (line["equivalent"], line["from"], line["to"]) == (equivalent, creditor, debtor):
                line["policy"][key] = value
                return
        raise AssertionError(f"the fixture has no line {creditor} -> {debtor} in {equivalent}")

    return mutate


def _deactivate_equivalent(code: str) -> Callable:
    def mutate(recipe, community):
        for equivalent in community["equivalents"]:
            if equivalent["code"] == code:
                equivalent["is_active"] = False
                return
        raise AssertionError(f"the fixture has no equivalent {code}")

    return mutate


def _move_before(command_id: str, before_id: str) -> Callable:
    def mutate(recipe, community):
        command = recipe["commands"].pop(_first_index(recipe, command_id))
        recipe["commands"].insert(_first_index(recipe, before_id), command)

    return mutate


def _swap_clearing_modes(recipe, community):
    clearings = [c for c in recipe["commands"] if c["op"] == "clearing"]
    for command in clearings:
        command["mode"] = "assert_clearable" if command["mode"] == "execute" else "execute"


def _payment_after_a_freeze(recipe, community):
    recipe["commands"].append(
        {
            "id": "riverside.999.late-payment-to-a-frozen-participant",
            "op": "payment",
            "equivalent": "EUR",
            "payer": "anna_turystka",
            "payee": "riverside_fishing_co_operative",
            "amount": "10.00",
            "routing": "open",
            "why": "Команда, которой в рецепте быть не должно: она обращается к замороженному участнику.",
            "expect": "Валидатор обязан её отвергнуть, а не выполнить.",
        }
    )


def _set_top_level(key: str, value: Any) -> Callable:
    def mutate(recipe, community):
        recipe[key] = value

    return mutate


def _shorten_cycle(command_id: str) -> Callable:
    def mutate(recipe, community):
        command = recipe["commands"][_first_index(recipe, command_id)]
        command["cycle"] = command["cycle"][:2]

    return mutate


def _add_unknown_key(recipe, community):
    recipe["commands"][0]["note"] = "a key nobody reads"


def _duplicate_id(recipe, community):
    recipe["commands"][1]["id"] = recipe["commands"][0]["id"]


def _repeat_in_cycle(command_id: str) -> Callable:
    def mutate(recipe, community):
        command = recipe["commands"][_first_index(recipe, command_id)]
        command["cycle"] = [command["cycle"][0], command["cycle"][1], command["cycle"][0]]

    return mutate


def _duplicate_freeze(recipe, community):
    index = _first_index(recipe, "riverside.026.freeze-pharmacy")
    twin = copy.deepcopy(recipe["commands"][index])
    twin["id"] = twin["id"] + ".again"
    recipe["commands"].append(twin)


def _swap_payment_ends(command_id: str) -> Callable:
    def mutate(recipe, community):
        command = recipe["commands"][_first_index(recipe, command_id)]
        command["payer"], command["payee"] = command["payee"], command["payer"]

    return mutate


MUTATIONS: list[tuple[str, Callable, str]] = [
    (
        "a payment to somebody who is not in the roster",
        _set_command("riverside.001.market-takes-ivan-catch", "payee", "nobody_here"),
        "is not a participant ref",
    ),
    (
        "a payment in an equivalent the description switched off",
        _deactivate_equivalent("EUR"),
        "is declared inactive",
    ),
    (
        "an amount finer than the precision of its equivalent",
        _set_command("riverside.001.market-takes-ivan-catch", "amount", "1180.005"),
        "fractional digits",
    ),
    (
        "an amount that is not a plain decimal string",
        _set_command("riverside.001.market-takes-ivan-catch", "amount", "1.18e3"),
        "not a plain positive decimal string",
    ),
    (
        "a single-hop payment over a line that does not exist",
        _set_command("riverside.001.market-takes-ivan-catch", "payee", "hanna_berehova"),
        "has no active trustline",
    ),
    (
        "a single-hop payment written in the wrong direction",
        _swap_payment_ends("riverside.001.market-takes-ivan-catch"),
        "has no active trustline",
    ),
    (
        "a clearing cycle whose edges live in another equivalent",
        _set_command("riverside.033.clear-market-petro-coop", "equivalent", "HOUR"),
        "has no active trustline",
    ),
    (
        "a clearing cycle over a line that refused auto clearing",
        _set_line_policy(
            "UAH", "petro_rybka", "fish_market_and_cold_storage", "auto_clearing", False
        ),
        "auto_clearing",
    ),
    (
        "a cycle of two participants",
        _shorten_cycle("riverside.033.clear-market-petro-coop"),
        "at least 3 participants",
    ),
    (
        "a declared cleared amount that is not the minimum of the cycle",
        _set_command("riverside.033.clear-market-petro-coop", "amount", "900.00"),
        "is not the smallest debt",
    ),
    (
        "a chain payment that has a direct line after all",
        _set_command(
            "riverside.012.moriak-pays-smokehouse-through-retail",
            "payee",
            "fresh_catch_fish_shop",
        ),
        "a direct trustline",
    ),
    (
        "a freeze of somebody the description calls active",
        _set_command("riverside.023.freeze-tourism-guide", "participant", "ivan_kozak"),
        "the description declares it active",
    ),
    (
        "a participant the description calls frozen and nobody freezes",
        _drop_command("riverside.026.freeze-pharmacy"),
        "has no freeze command",
    ),
    (
        "a payment to a participant that was already frozen",
        _payment_after_a_freeze,
        "after it was frozen",
    ),
    (
        "the surviving cycle declared before the clearing it must outlive",
        _swap_clearing_modes,
        "must come after",
    ),
    (
        "the surviving cycle built before the clearing it must outlive",
        _move_before("riverside.034.caterer-takes-ivan-fish", "riverside.033.clear-market-petro-coop"),
        "before the last executed clearing",
    ),
    (
        "two commands with the same identifier",
        _duplicate_id,
        "is used twice",
    ),
    (
        "an identifier no tx_id would accept",
        _set_command("riverside.001.market-takes-ivan-catch", "id", "riverside 001 bad id"),
        "must match",
    ),
    (
        "single-hop payments that together break through the trust limit",
        _set_command("riverside.029.shop-restock-weekend", "amount", "4000.00"),
        "exceeds the limit",
    ),
    (
        "a payer who cannot owe anybody in that equivalent",
        _set_command("riverside.021.guide-pays-season-charters-eur", "payer", "ivan_kozak"),
        "cannot owe anybody",
    ),
    (
        "an extra command key nobody reads",
        _add_unknown_key,
        "unknown keys",
    ),
    (
        "a recipe of a schema version we do not speak",
        _set_top_level("schema_version", "recipe/999"),
        "schema_version",
    ),
    (
        "a recipe that names a different community",
        _set_top_level("community_id", "greenfield-village-100"),
        "does not describe",
    ),
    (
        "a routing mode the executor would not know what to do with",
        _set_command("riverside.001.market-takes-ivan-catch", "routing", "teleport"),
        "routing",
    ),
    (
        "an operation the executor cannot perform",
        _set_command("riverside.001.market-takes-ivan-catch", "op", "mint"),
        "op",
    ),
    (
        "a payment from somebody to themselves",
        _set_command("riverside.001.market-takes-ivan-catch", "payee", "fish_market_and_cold_storage"),
        "to itself",
    ),
    (
        "a payee nobody in that equivalent can owe",
        _set_command("riverside.021.guide-pays-season-charters-eur", "payee", "ivan_kozak"),
        "nobody can owe payee",
    ),
    (
        "a cycle that names the same participant twice",
        _repeat_in_cycle("riverside.033.clear-market-petro-coop"),
        "the same participant twice",
    ),
    (
        "a cycle edge no single-hop payment builds",
        _set_command("riverside.030.market-takes-petro-week-catch", "routing", "open"),
        "is built by 0 preceding single-hop payments",
    ),
    (
        "a freeze written twice for the same participant",
        _duplicate_freeze,
        "already frozen at command",
    ),
    (
        "an identifier too long for a tx_id",
        _set_command(
            "riverside.001.market-takes-ivan-catch", "id", "riverside.001." + "x" * 60
        ),
        "is longer than 64 characters",
    ),
    (
        "a command that explains nothing",
        _set_command("riverside.001.market-takes-ivan-catch", "why", "  "),
        "why must be a non-empty string",
    ),
    (
        "an extra top-level key nobody reads",
        _set_top_level("debts", []),
        "unknown top-level keys",
    ),
]


@pytest.mark.parametrize("what,mutation,message", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_the_validator_rejects_a_recipe_that_lies(what, mutation, message, documents):
    recipe, community = documents["riverside-town-50"]

    # Anti-vacuum: the unmutated pair must pass, or the rejection below would
    # prove the fixture broken rather than the rule enforced.
    validate_recipe(copy.deepcopy(recipe), copy.deepcopy(community), source="control")

    mutated_recipe = copy.deepcopy(recipe)
    mutated_community = copy.deepcopy(community)
    mutation(mutated_recipe, mutated_community)
    assert (mutated_recipe, mutated_community) != (recipe, community), (
        f"the mutation {what!r} changed nothing"
    )

    with pytest.raises(RecipeError, match=re.escape(message)):
        validate_recipe(mutated_recipe, mutated_community, source=f"mutated:{what}")
