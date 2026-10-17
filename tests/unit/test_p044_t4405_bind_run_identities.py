"""044 `T4405`: a run's private copy of its scenario names only participants of that run.

`bind_run_identities` (`app/core/simulator/run_perimeter.py`) gives every participant the scenario declares an identity
of the run, `<run_id>:p<ordinal>`, and rewrites every TYPED reference to a participant; nothing else. These tests feed it
one scenario carrying every kind of reference and read the copy back. Pure: no database, no runtime.
"""

from __future__ import annotations

import copy

import pytest

from app.core.simulator.run_perimeter import (
    RUN_IDENTITY_MAX_LENGTH,
    bind_run_identities,
    is_run_identity,
    run_identity,
)
from app.core.simulator.scenario_story import ScenarioStoryInvalid

RUN = "run_20261017_101010_ab12cd34"


def _every_reference() -> dict:
    return {
        "schema_version": "scenario/1",
        "scenario_id": "every-reference",
        "equivalents": ["UAH"],
        "participants": [
            {"id": "alice", "name": "Алиса", "type": "person", "groupId": "g1", "behaviorProfileId": "bp"},
            {"id": "bob", "type": "person"},
            {"id": "shop", "name": "Shop", "type": "business"},
        ],
        "groups": [{"id": "g1", "label": "alice"}],
        "behaviorProfiles": [{"id": "bp", "props": {"tx_rate": 1}}],
        "trustlines": [
            {"from": "alice", "to": "bob", "limit": "10", "policy": {"blocked_participants": ["shop"]}},
            {"from": "shop", "to": "alice", "limit": "5"},
        ],
        "events": [
            {"time": 0, "type": "payment", "from": "bob", "to": "alice", "amount": "1",
             "caption": {"ru": "alice платит", "en": "alice pays"},
             "focus": {"pids": ["alice", "bob"], "edges": [{"from": "alice", "to": "bob"}]},
             "anchor": {"event": "tx.updated", "from": "bob", "to": "alice", "amount": "1", "equivalent": "UAH"}},
            {"time": 10, "type": "clearing", "expected_cycle": ["alice", "bob", "shop"]},
            {"time": 20, "type": "inject", "effects": [
                {"op": "add_participant", "participant": {"id": "newbie", "type": "person"},
                 "initial_trustlines": [{"sponsor": "shop", "equivalent": "UAH", "limit": "3"}]},
                {"op": "create_trustline", "from": "newbie", "to": "bob", "equivalent": "UAH", "limit": "2"},
                {"op": "freeze_participant", "participant_id": "bob"},
            ]},
            {"time": 30, "type": "stress", "effects": [{"op": "mult", "field": "tx_rate", "value": 2, "scope": "group:g1"}]},
        ],
    }


def test_every_typed_reference_is_an_identity_of_the_run_and_nothing_else_moves() -> None:
    template = _every_reference()
    pristine = copy.deepcopy(template)

    bound = bind_run_identities(template, run_id=RUN)

    i = {src: run_identity(RUN, n) for n, src in enumerate(["alice", "bob", "shop", "newbie"], start=1)}
    assert [p["id"] for p in bound["participants"]] == [i["alice"], i["bob"], i["shop"]]
    assert [(t["from"], t["to"]) for t in bound["trustlines"]] == [(i["alice"], i["bob"]), (i["shop"], i["alice"])]
    assert bound["trustlines"][0]["policy"]["blocked_participants"] == [i["shop"]]
    pay, clearing, inject, stress = bound["events"]
    assert (pay["from"], pay["to"]) == (i["bob"], i["alice"])
    assert pay["focus"] == {"pids": [i["alice"], i["bob"]], "edges": [{"from": i["alice"], "to": i["bob"]}]}
    assert (pay["anchor"]["from"], pay["anchor"]["to"]) == (i["bob"], i["alice"])
    assert clearing["expected_cycle"] == [i["alice"], i["bob"], i["shop"]]
    add, create, freeze = inject["effects"]
    assert add["participant"]["id"] == i["newbie"]
    assert add["initial_trustlines"][0]["sponsor"] == i["shop"]
    assert (create["from"], create["to"]) == (i["newbie"], i["bob"])
    assert freeze["participant_id"] == i["bob"]
    assert all(is_run_identity(RUN, pid) for pid in i.values())

    # Not participant references: groups, profiles, captions, stress scopes, the participants' group/profile links.
    assert bound["groups"] == pristine["groups"] and bound["behaviorProfiles"] == pristine["behaviorProfiles"]
    assert pay["caption"] == pristine["events"][0]["caption"]
    assert stress == pristine["events"][3]
    assert (bound["participants"][0]["groupId"], bound["participants"][0]["behaviorProfileId"]) == ("g1", "bp")
    # Names verbatim; a missing name becomes the source pid, so a person never reads the identity.
    assert [p["name"] for p in bound["participants"]] == ["Алиса", "bob", "Shop"]
    assert add["participant"]["name"] == "newbie"
    # The template is not touched.
    assert template == pristine


def test_two_runs_of_one_scenario_share_no_identity_and_one_run_binds_the_same_way_twice() -> None:
    a = bind_run_identities(_every_reference(), run_id=RUN)
    b = bind_run_identities(_every_reference(), run_id=RUN.replace("ab12cd34", "ab12cd35"))
    ids = lambda raw: {p["id"] for p in raw["participants"]}  # noqa: E731
    assert ids(a).isdisjoint(ids(b)), (ids(a), ids(b))
    # Counter-check: the binding is a function of the run, not of the order runs were made in.
    assert bind_run_identities(_every_reference(), run_id=RUN) == a


@pytest.mark.parametrize(
    ("path", "mutate"),
    [
        ("trustlines/1/to", lambda raw: raw["trustlines"][1].__setitem__("to", "stranger")),
        ("trustlines/0/policy/blocked_participants/0",
         lambda raw: raw["trustlines"][0]["policy"].__setitem__("blocked_participants", ["stranger"])),
        ("events/2/effects/0/initial_trustlines/0/sponsor",
         lambda raw: raw["events"][2]["effects"][0]["initial_trustlines"][0].__setitem__("sponsor", "stranger")),
        ("events/2/effects/1/from", lambda raw: raw["events"][2]["effects"][1].__setitem__("from", "stranger")),
        ("events/2/effects/2/participant_id",
         lambda raw: raw["events"][2]["effects"][2].__setitem__("participant_id", "stranger")),
    ],
)
def test_a_reference_to_an_undeclared_participant_refuses_the_run(path, mutate) -> None:
    """Before T4405 these resolved against the GLOBAL participants table (the seeder skipped a line to an unknown end;
    an inject looked a stranger up by pid and adopted any simulator row): now the run is refused, naming the field."""

    raw = _every_reference()
    mutate(raw)
    with pytest.raises(ScenarioStoryInvalid) as refused:
        bind_run_identities(raw, run_id=RUN)
    assert [e["path"] for e in refused.value.errors] == [path], refused.value.errors


def test_an_identity_that_does_not_fit_the_pid_column_refuses_the_run() -> None:
    long_run = "run_" + "x" * (RUN_IDENTITY_MAX_LENGTH - len("run_") - len(":p1") + 1)
    with pytest.raises(ScenarioStoryInvalid) as refused:
        bind_run_identities(_every_reference(), run_id=long_run)
    assert refused.value.errors[0]["path"] == "participants/0/id"
    # Counter-check: one character shorter fits.
    fits = bind_run_identities(_every_reference(), run_id=long_run[:-1])
    assert len(fits["participants"][0]["id"]) == RUN_IDENTITY_MAX_LENGTH


def test_is_run_identity_is_the_run_s_own_namespace_only() -> None:
    assert is_run_identity(RUN, f"{RUN}:p12")
    for other in ("alice", f"{RUN}:alice", f"{RUN}x:p1", f"{RUN}:p", f"{RUN[:-1]}:p1", None):
        assert not is_run_identity(RUN, other), other
