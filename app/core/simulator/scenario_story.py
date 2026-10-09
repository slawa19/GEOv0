"""036: the story of a scenario - ONE place that reads and validates it.

The upload schema (`fixtures/simulator/scenario.schema.json`) checks the SHAPE of a scenario. What the story means -
whether a participant it names exists, whether an amount is money the ledger could hold, whether the fields an anchor is
matched on are there - and the REST projection (`GET /simulator/scenarios/{id}`) are the same rule, written once here and
used by both, so that what an upload accepts is exactly what the detail can serve and the detail never serves less than
was uploaded (review of `1afe0b09`, findings 1-5).

* `normalize_integral_numbers` - a JSON number that is mathematically an integer (`1000.0`) is stored as the `int` it
  is, once, at ingestion. The schema (draft 2020-12) accepts it as an integer; the projection and the runner read
  integers. Anything not integral stays as it is and the schema refuses it.
* `build_story` - the projection, and an INTEGRITY BOUNDARY (AGENTS.md section 9): an event with no `caption` is not an
  episode and that is not an error; a captioned event that cannot be served as written, an invalid `settings.playback` or
  an invalid `description` is refused with the path of every bad field (`ScenarioStoryInvalid`, 409 - the class the
  stored-scenario refusals already use). Nothing is dropped and no meaning is altered; the source is never touched.
* `story_errors` - the upload check: the projection's errors plus the one that needs the database, a scripted payment
  finer than its equivalent's accounting step (`require_money_step`, never a rounding), when the equivalent resolves.

WHAT THIS DOES NOT SEE. Whether a participant exists in the DATABASE, is active, or was introduced before the moment an
episode names it; whether the equivalent exists or has the precision the upload saw; whether the anchor will ever arrive.
Those are the runtime's (slice B). An equivalent that is not resolved - no `equivalent` on the event and no declared
`baseEquivalent` (the first of `equivalents[]` is NOT a default) - is not stepped.

Paths use the `/` spelling of `SCENARIO_INVALID` (`events/3/caption/en`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping, NamedTuple, Optional

from pydantic import ValidationError

from app.core.simulator.scenario_equivalent import effective_equivalent
from app.schemas.simulator import (
    LocalizedText,
    ScenarioEpisode,
    ScenarioPlayback,
    scenario_amount_is_well_formed,
)
from app.utils.exceptions import BadRequestException, ConflictException
from app.utils.validation import parse_money_amount, require_money_step

SCENARIO_INVALID = "SCENARIO_INVALID"

#: Field names of the REST episode that are spelt differently in the scenario's event.
_EVENT_FIELD = {"time_ms": "time", "kind": "type"}


class ScenarioStoryInvalid(ConflictException):
    """A stored or shipped scenario whose story cannot be served as written (409; the upload twin is the 400 of
    `validate_scenario_or_400`, same `details` shape)."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        paths = ", ".join(e["path"] for e in errors[:5])
        super().__init__(
            f"Scenario story invalid: {paths}",
            details={"simulator_error": SCENARIO_INVALID, "errors": errors[:50]},
        )
        self.errors = errors


class Story(NamedTuple):
    description: Optional[LocalizedText]
    episodes: list[ScenarioEpisode]
    playback: Optional[ScenarioPlayback]


# ------------------------------------------------------------------------------------------------ ingestion


def _integral(value: Any) -> Any:
    return int(value) if isinstance(value, float) and value.is_integer() else value


def normalize_integral_numbers(raw: dict[str, Any]) -> None:
    """Store mathematically integral JSON numbers as `int`, in place: `events[].time`, `events[].anchor.time_ms` and
    `settings.playback.intensity_percent` - the three integer fields of the story."""

    events = raw.get("events")
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        if "time" in event:
            event["time"] = _integral(event["time"])
        anchor = event.get("anchor")
        if isinstance(anchor, dict) and "time_ms" in anchor:
            anchor["time_ms"] = _integral(anchor["time_ms"])
    settings = raw.get("settings")
    playback = settings.get("playback") if isinstance(settings, dict) else None
    if isinstance(playback, dict) and "intensity_percent" in playback:
        playback["intensity_percent"] = _integral(playback["intensity_percent"])


# ------------------------------------------------------------------------------------------------ projection


def _pydantic_errors(prefix: str, exc: ValidationError) -> list[dict[str, str]]:
    out = []
    for item in exc.errors():
        loc = [str(p) for p in item["loc"]]
        if loc and prefix.startswith("events/") and loc[0] in _EVENT_FIELD:
            loc[0] = _EVENT_FIELD[loc[0]]
        out.append({"path": "/".join([prefix, *loc]), "message": str(item["msg"])})
    return out


def _description(raw: Mapping[str, Any], errors: list[dict[str, str]]) -> Optional[LocalizedText]:
    value = raw.get("description")
    if value is None:
        return None
    if isinstance(value, str):
        # An older scenario's plain string: an untranslated compatibility fallback, served as both languages. A blank
        # string says nothing and is served as no description.
        return LocalizedText(ru=value, en=value) if value.strip() else None
    try:
        return LocalizedText.model_validate(value)
    except ValidationError as exc:
        errors.extend(_pydantic_errors("description", exc))
        return None


def scenario_description_or_none(raw: Mapping[str, Any]) -> Optional[LocalizedText]:
    """For the LIST: one stored scenario with a damaged description must not take the index down. The detail of the same
    scenario refuses (`build_story`), so the damage is never served as if it were a description."""

    return _description(raw, [])


def _episodes(raw: Mapping[str, Any], errors: list[dict[str, str]]) -> list[ScenarioEpisode]:
    out: list[ScenarioEpisode] = []
    events = raw.get("events")
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, dict) or "caption" not in event:
            continue
        payload = {
            "index": index,
            "time_ms": event.get("time"),
            "caption": event["caption"],
            "pause_after": event.get("pause_after", False),
            "kind": event.get("type"),
            "focus": event.get("focus"),
            "anchor": event.get("anchor"),
            "expected_cycle": event.get("expected_cycle"),
        }
        try:
            out.append(ScenarioEpisode.model_validate(payload))
        except ValidationError as exc:
            errors.extend(_pydantic_errors(f"events/{index}", exc))
    return out


def _playback(raw: Mapping[str, Any], errors: list[dict[str, str]]) -> Optional[ScenarioPlayback]:
    settings = raw.get("settings")
    playback = settings.get("playback") if isinstance(settings, dict) else None
    if playback is None:
        return None
    try:
        return ScenarioPlayback.model_validate(playback)
    except ValidationError as exc:
        errors.extend(_pydantic_errors("settings/playback", exc))
        return None


# ------------------------------------------------------------------------------------- references and money


def _declared_pids(raw: Mapping[str, Any]) -> set[str]:
    """The scenario's participants and those an `add_participant` inject introduces (their order in time is B's)."""

    pids: set[str] = set()
    for participant in raw.get("participants") if isinstance(raw.get("participants"), list) else []:
        if isinstance(participant, dict) and isinstance(participant.get("id"), str):
            pids.add(participant["id"])
    for event in raw.get("events") if isinstance(raw.get("events"), list) else []:
        effects = event.get("effects") if isinstance(event, dict) else None
        for effect in effects if isinstance(effects, list) else []:
            new = effect.get("participant") if isinstance(effect, dict) and effect.get("op") == "add_participant" else None
            if isinstance(new, dict) and isinstance(new.get("id"), str):
                pids.add(new["id"])
    return pids


def money_problem(value: Any) -> Optional[str]:
    """Why `value` is not an amount the product's money door would take, or None. The grammar is the scenario schema's
    (`SCENARIO_AMOUNT_PATTERN`); `parse_money_amount` adds what only the value shows - storable in `Numeric(20, 8)`,
    positive."""

    if not scenario_amount_is_well_formed(value):
        return "must be a plain decimal string: digits, at most 18 fraction digits, 50 digits in all"
    try:
        parse_money_amount(value, require_positive=True)
    except BadRequestException as exc:
        return exc.message
    return None


def _reference_and_money_errors(raw: Mapping[str, Any], errors: list[dict[str, str]]) -> None:
    declared = _declared_pids(raw)

    def known(path: str, pid: Any) -> None:
        if isinstance(pid, str) and pid and pid not in declared:
            errors.append({"path": path, "message": f"participant {pid} is neither a participant of the scenario nor "
                                                    "introduced by an add_participant inject"})

    events = raw.get("events")
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, dict):
            continue
        base = f"events/{index}"
        if event.get("type") == "payment":
            for key in ("from", "to"):
                if not isinstance(event.get(key), str) or not event.get(key):
                    errors.append({"path": f"{base}/{key}", "message": "a payment names its sender and receiver"})
                else:
                    known(f"{base}/{key}", event[key])
            problem = money_problem(event.get("amount"))
            if problem:
                errors.append({"path": f"{base}/amount", "message": f"amount {problem}"})
        anchor = event.get("anchor")
        if isinstance(anchor, dict):
            known(f"{base}/anchor/from", anchor.get("from"))
            known(f"{base}/anchor/to", anchor.get("to"))
            if anchor.get("amount") is not None:
                problem = money_problem(anchor["amount"])
                if problem:
                    errors.append({"path": f"{base}/anchor/amount", "message": f"amount {problem}"})
        focus = event.get("focus")
        if isinstance(focus, dict):
            for i, pid in enumerate(focus.get("pids") if isinstance(focus.get("pids"), list) else []):
                known(f"{base}/focus/pids/{i}", pid)
            for i, edge in enumerate(focus.get("edges") if isinstance(focus.get("edges"), list) else []):
                if isinstance(edge, dict):
                    known(f"{base}/focus/edges/{i}/from", edge.get("from"))
                    known(f"{base}/focus/edges/{i}/to", edge.get("to"))
        cycle = event.get("expected_cycle")
        for i, pid in enumerate(cycle if isinstance(cycle, list) else []):
            known(f"{base}/expected_cycle/{i}", pid)


def _distinct(errors: list[dict[str, str]]) -> list[dict[str, str]]:
    """One entry per path, the first message: the projection and the raw checks can both name the same bad field."""

    seen: set[str] = set()
    out = []
    for error in errors:
        if error["path"] not in seen:
            seen.add(error["path"])
            out.append(error)
    return out


def _collect(raw: Mapping[str, Any]) -> tuple[Story, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    description = _description(raw, errors)
    episodes = _episodes(raw, errors)
    playback = _playback(raw, errors)
    _reference_and_money_errors(raw, errors)
    return Story(description, episodes, playback), _distinct(errors)


def build_story(raw: Mapping[str, Any]) -> Story:
    """The story of a scenario for the REST detail; raises `ScenarioStoryInvalid` naming every bad path."""

    story, errors = _collect(raw)
    if errors:
        raise ScenarioStoryInvalid(errors)
    return story


# --------------------------------------------------------------------------------------------------- upload


def payment_equivalents(raw: Mapping[str, Any]) -> set[str]:
    """The equivalent codes the scripted payments of `raw` resolve to (explicit, or the declared default)."""

    codes: set[str] = set()
    events = raw.get("events") if isinstance(raw, Mapping) else None
    for event in events if isinstance(events, list) else []:
        if isinstance(event, dict) and event.get("type") == "payment":
            code = effective_equivalent(raw, event)
            if code:
                codes.add(code)
    return codes


def story_errors(
    raw: Mapping[str, Any], *, equivalent_precisions: Optional[Mapping[str, int]] = None
) -> list[dict[str, str]]:
    """Every problem of the story of `raw` at upload. `equivalent_precisions` maps the codes the caller could look up
    to their precision (= the accounting step); a payment whose resolved equivalent is in it must be a multiple of that
    step - a value rule, so `"1.500"` is fine at precision 2 - and is never rounded. A code not in it is not stepped."""

    _, errors = _collect(raw)
    precisions = {str(k).upper(): int(v) for k, v in (equivalent_precisions or {}).items()}
    events = raw.get("events")
    for index, event in enumerate(events if isinstance(events, list) else []):
        if not isinstance(event, dict) or event.get("type") != "payment" or money_problem(event.get("amount")):
            continue
        code = effective_equivalent(raw, event)
        if code in precisions:
            try:
                require_money_step(Decimal(event["amount"]), precision=precisions[code], equivalent=code)
            except BadRequestException as exc:
                errors.append({"path": f"events/{index}/amount", "message": exc.message})
    return _distinct(errors)
