"""The run perimeter, as the tick money paths need it."""

from __future__ import annotations

import copy
from typing import Any


def run_perimeter_pids(run) -> "set[str] | None":
    """The run's participants as pids, or None when the run has not been seeded yet.

    2026-08-22 / p010 (`F-010-4`).  The money paths of the tick were unbounded: clearing
    searched the whole equivalent and payments routed through it, so a run could reduce the
    obligations of another run's participants or consume their trust.  That is the same P1
    the Interact Mode routes had (`F-010-3`), on the path that runs by itself.

    Three states, and the difference between the last two is the whole point:

    * `None` — the participant list has not been LOADED yet, so no perimeter can be applied.
      The tick loads it before reaching any money path (`RealTick._open_money_phase` in
      `app/core/simulator/tick.py`, after seeding), so this state is not reachable from
      clearing or payments.
    * an EMPTY set — the list was loaded and the run has nobody in it.  That is a perimeter
      admitting nobody, NOT an absence of one.  Collapsing it into `None` would mean a run
      with an empty scenario clears and routes across the whole equivalent, which is the
      shape of `F-009-1` all over again.
    * a non-empty set — the run.
    """

    participants = getattr(run, "_real_participants", None)
    if participants is None:
        return None
    return {str(pid) for (_uuid, pid) in participants}


# ------------------------------------------------------------------------------------------------ run identities
#
# 044 (`T4405`, the planned design extension of 2026-10-17; Codex `PER-RUN-SCOPED-PIDS`). Two runs whose scenarios
# name the same pid acted on the same participant row, its lines and its debts: the perimeter is the scenario's pid
# list resolved against the shared `participants` table, and the public demo starts every visitor on one scenario.
# A run therefore gets FRESH participants: at creation every participant its scenario declares is given an identity
# of this run, `<run_id>:p<ordinal>`, and every typed reference to a participant in the run's private copy of the
# scenario is rewritten to it. The template is never changed; names stay verbatim; the run keeps the identities for
# its whole life (`/restart` included), because they live in `run._scenario_raw`, which nothing rebuilds.
#
# What is rewritten is a closed list of TYPED participant references (below). Group ids, behaviour profiles,
# captions and any other string are not. A reference to a participant the scenario does not declare is refused when
# the run is created, never resolved against the global table - that resolution is exactly the sharing being closed.
#
# What this is NOT: a tenant boundary in SQL. Routing, clearing and the integrity checks still read the equivalent's
# whole network; the run's money stays within its participants because the perimeter (`run_perimeter_pids`) names
# only this run's rows.

#: `Participant.pid` is `String(64)` (`app/db/models/participant.py`); an identity that would not fit is refused, never
#: truncated (a truncated identity could be another run's).
RUN_IDENTITY_MAX_LENGTH = 64


def run_identity(run_id: str, ordinal: int) -> str:
    """The identity of the `ordinal`-th (1-based) participant a run declares. The one owner of the format."""

    return f"{run_id}:p{ordinal}"


def is_run_identity(run_id: str, pid: object) -> bool:
    """Whether `pid` is an identity bound by the run `run_id` (the seeder's adoption rule)."""

    prefix = f"{run_id}:p"
    return (
        isinstance(pid, str) and pid.startswith(prefix) and pid[len(prefix) :].isdigit()
    )


def _pid_of(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def bind_run_identities(template: dict, *, run_id: str) -> dict:
    """The run's private copy of `template` with every declared participant under an identity of `run_id`.

    Declared participants, in order: `participants[]`, then the participants of `add_participant` injects in event
    order (their ordinal is fixed at creation, so an inject that fires late or twice names the same identity). A
    participant without a name gets its source pid as its name, so a person never reads the identity.

    Rewritten references: `trustlines[].from/to`, `trustlines[].policy.blocked_participants[]`, the payment events'
    `from/to`, `anchor.from/to`, `focus.pids[]`, `focus.edges[].from/to`, `expected_cycle[]`, and in inject effects
    `participant.id`, `initial_trustlines[].sponsor`, `create_trustline` `from/to` and `freeze_participant`
    `participant_id`. Raises `ScenarioStoryInvalid` (409 SCENARIO_INVALID, the path of every bad field) for a
    reference to an undeclared participant or an identity longer than `RUN_IDENTITY_MAX_LENGTH`.
    """

    from app.core.simulator.scenario_story import ScenarioStoryInvalid

    raw = copy.deepcopy(template)
    mapping: dict[str, str] = {}
    errors: list[dict[str, str]] = []

    def declare(path: str, holder: dict) -> None:
        pid = _pid_of(holder.get("id"))
        if not pid:
            return
        if pid not in mapping:
            mapping[pid] = run_identity(run_id, len(mapping) + 1)
            if len(mapping[pid]) > RUN_IDENTITY_MAX_LENGTH:
                errors.append(
                    {
                        "path": path,
                        "message": f"the run identity of participant {pid} is longer than "
                        f"{RUN_IDENTITY_MAX_LENGTH} characters",
                    }
                )
        if not (isinstance(holder.get("name"), str) and holder["name"].strip()):
            holder["name"] = pid
        holder["id"] = mapping[pid]

    def ref(path: str, holder: Any, key: Any) -> None:
        value = holder[key]
        if value is None:
            return
        pid = _pid_of(value)
        if not pid:
            return
        if pid not in mapping:
            errors.append(
                {
                    "path": path,
                    "message": f"participant {pid} is not declared by the scenario",
                }
            )
            return
        holder[key] = mapping[pid]

    def ref_key(path: str, holder: dict, key: str) -> None:
        if key in holder:
            ref(path, holder, key)

    def ref_list(path: str, holder: dict, key: str) -> None:
        items = holder.get(key)
        for i in range(len(items) if isinstance(items, list) else 0):
            ref(f"{path}/{i}", items, i)

    participants = raw.get("participants")
    for i, p in enumerate(participants if isinstance(participants, list) else []):
        if isinstance(p, dict):
            declare(f"participants/{i}/id", p)
    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    for i, event in enumerate(events):
        effects = event.get("effects") if isinstance(event, dict) else None
        for j, eff in enumerate(effects if isinstance(effects, list) else []):
            if (
                isinstance(eff, dict)
                and eff.get("op") == "add_participant"
                and isinstance(eff.get("participant"), dict)
            ):
                declare(f"events/{i}/effects/{j}/participant/id", eff["participant"])

    trustlines = raw.get("trustlines")
    for i, tl in enumerate(trustlines if isinstance(trustlines, list) else []):
        if not isinstance(tl, dict):
            continue
        ref_key(f"trustlines/{i}/from", tl, "from")
        ref_key(f"trustlines/{i}/to", tl, "to")
        if isinstance(tl.get("policy"), dict):
            ref_list(
                f"trustlines/{i}/policy/blocked_participants",
                tl["policy"],
                "blocked_participants",
            )

    for i, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        base = f"events/{i}"
        if event.get("type") == "payment":
            ref_key(f"{base}/from", event, "from")
            ref_key(f"{base}/to", event, "to")
        if isinstance(event.get("anchor"), dict):
            ref_key(f"{base}/anchor/from", event["anchor"], "from")
            ref_key(f"{base}/anchor/to", event["anchor"], "to")
        focus = event.get("focus")
        if isinstance(focus, dict):
            ref_list(f"{base}/focus/pids", focus, "pids")
            edges = focus.get("edges")
            for k, edge in enumerate(edges if isinstance(edges, list) else []):
                if isinstance(edge, dict):
                    ref_key(f"{base}/focus/edges/{k}/from", edge, "from")
                    ref_key(f"{base}/focus/edges/{k}/to", edge, "to")
        ref_list(f"{base}/expected_cycle", event, "expected_cycle")
        effects = event.get("effects")
        for j, eff in enumerate(effects if isinstance(effects, list) else []):
            if not isinstance(eff, dict):
                continue
            op, at = eff.get("op"), f"{base}/effects/{j}"
            if op == "add_participant":
                initial = eff.get("initial_trustlines")
                for k, itl in enumerate(initial if isinstance(initial, list) else []):
                    if isinstance(itl, dict):
                        ref_key(f"{at}/initial_trustlines/{k}/sponsor", itl, "sponsor")
            elif op == "create_trustline":
                ref_key(f"{at}/from", eff, "from")
                ref_key(f"{at}/to", eff, "to")
            elif op == "freeze_participant":
                ref_key(f"{at}/participant_id", eff, "participant_id")

    if errors:
        raise ScenarioStoryInvalid(errors)
    return raw
