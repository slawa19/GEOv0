"""Schema of a community description (`seeds/communities/<id>/community.json`).

A community description says *who is in the community and who trusts whom*.
It is the single roster. The simulator scenario generator
(`scripts/generate_simulator_seed_scenarios.py`) reads it today, and the seed
recipe of programme 017 (`T1711`, `T1713`, not yet built) is meant to read the
same file, so that nobody keeps a second copy of the same people by hand.

The format is small and owned by the seed tools. It is deliberately **not** the
simulator scenario format: that one mixes structure with ticks, warm-up and
stress, and it accepts numeric limits and arbitrary policy objects
(`fixtures/simulator/scenario.schema.json`: ``limit`` is
``integer | number | string``, ``policy`` is ``additionalProperties: true``).
Money here is a decimal string, never a float (AGENTS.md §8).

What a description holds:

* ``equivalents`` — code, precision, description, activity flag. Precision is
  part of the contract, not a default.
* ``groups`` — the economic roles of the community, declared once.
* ``participants`` — a symbolic ``ref`` (what a recipe writes), the roster
  ``index`` (the numbering of the seed document), the fixture ``pid``, the
  display name, the type, the explicit ``group`` and the demo ``status``.
  Group membership is written down; it is never inferred from a substring of
  the name or from a position in the roster.
* ``trustlines`` — creditor ``from`` → debtor ``to`` by symbolic ref, the
  equivalent, the limit as a decimal string, the demo status and the domain
  policy (``auto_clearing``, ``can_be_intermediate``).

What a description does **not** hold: debts, balances, `used`/`available`,
transactions or timestamps. Those are the *result* of operations and are
produced by running the recipe, not written by hand.

``pid`` is a fixture identity. It keys the committed simulator scenarios under
``fixtures/simulator/`` and the Admin UI prototype fixtures. It is not the PID a
participant gets in a database: there ``PID = base58(sha256(public_key))`` of a
key pair generated per run (`app/core/auth/crypto.py:37-50`), and the recipe
resolves
``ref`` → real PID through its own table.

This module is dependency-free on purpose: it is imported by scripts that must
run before anything is installed.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "community/1"

COMMUNITIES_DIR = Path(__file__).resolve().parent

_TOP_LEVEL_REQUIRED = (
    "schema_version",
    "community_id",
    "title",
    "summary",
    "equivalents",
    "groups",
    "participants",
    "trustlines",
)

_EQUIVALENT_KEYS_REQUIRED = ("code", "precision", "description", "is_active")
_GROUP_KEYS_REQUIRED = ("id", "label", "description")
_PARTICIPANT_KEYS_REQUIRED = ("ref", "index", "pid", "name", "type", "group", "status")
_PARTICIPANT_KEYS_OPTIONAL = ("role",)
_TRUSTLINE_KEYS_REQUIRED = ("equivalent", "from", "to", "limit", "status", "policy")

# Participant types the simulator scenario schema accepts.
_PARTICIPANT_TYPES = frozenset({"person", "business", "hub"})
# Demo statuses. A description may declare a frozen participant or a frozen
# line, but only the participant is reachable: the recipe freezes a participant
# through the admin freeze operation, and no service or API operation freezes a
# trust line (only the simulator's own injector writes that status,
# `app/core/simulator/inject_executor.py`). A description declaring a frozen line is valid
# here and refused by the seed before its first write
# (`scripts/seed_recipe.py::unreachable_declared_states`) - greenfield-village-100
# is refused for exactly that.
_PARTICIPANT_STATUSES = frozenset({"active", "frozen"})
_TRUSTLINE_STATUSES = frozenset({"active", "frozen"})

_POLICY_KEYS = ("auto_clearing", "can_be_intermediate")

_REF_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_GROUP_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_EQUIVALENT_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PID_RE = re.compile(r"^PID_U[0-9]{4}_[0-9a-f]{8}$")
_DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")

# Declared precision is bounded by the storage scale of the money columns.
MAX_PRECISION = 18


class CommunityError(ValueError):
    """A community description violates the schema."""


def _fail(source: str, message: str) -> None:
    raise CommunityError(f"{source}: {message}")


def _require_keys(source: str, what: str, obj: Any, required: tuple[str, ...], optional: tuple[str, ...] = ()) -> None:
    if not isinstance(obj, dict):
        _fail(source, f"{what} must be an object, got {type(obj).__name__}")
    missing = [k for k in required if k not in obj]
    if missing:
        _fail(source, f"{what} is missing keys {missing}")
    extra = sorted(set(obj) - set(required) - set(optional))
    if extra:
        _fail(source, f"{what} has unknown keys {extra}")


def _check_equivalents(source: str, doc: dict[str, Any]) -> dict[str, int]:
    equivalents = doc["equivalents"]
    if not isinstance(equivalents, list) or not equivalents:
        _fail(source, "equivalents must be a non-empty array")

    precision_by_code: dict[str, int] = {}
    for i, eq in enumerate(equivalents):
        where = f"equivalents[{i}]"
        _require_keys(source, where, eq, _EQUIVALENT_KEYS_REQUIRED)

        code = eq["code"]
        if not isinstance(code, str) or not _EQUIVALENT_CODE_RE.match(code):
            _fail(source, f"{where}.code {code!r} must match {_EQUIVALENT_CODE_RE.pattern}")
        if code in precision_by_code:
            _fail(source, f"{where}.code {code!r} is declared twice")

        precision = eq["precision"]
        if not isinstance(precision, int) or isinstance(precision, bool):
            _fail(source, f"{where}.precision must be an integer, got {precision!r}")
        if not 0 <= precision <= MAX_PRECISION:
            _fail(source, f"{where}.precision {precision} is outside 0..{MAX_PRECISION}")

        if not isinstance(eq["description"], str) or not eq["description"].strip():
            _fail(source, f"{where}.description must be a non-empty string")
        if not isinstance(eq["is_active"], bool):
            _fail(source, f"{where}.is_active must be a boolean")

        precision_by_code[code] = precision

    if not any(eq["is_active"] for eq in equivalents):
        _fail(source, "no equivalent is active")
    return precision_by_code


def _check_groups(source: str, doc: dict[str, Any]) -> set[str]:
    groups = doc["groups"]
    if not isinstance(groups, list) or not groups:
        _fail(source, "groups must be a non-empty array")

    ids: set[str] = set()
    for i, group in enumerate(groups):
        where = f"groups[{i}]"
        _require_keys(source, where, group, _GROUP_KEYS_REQUIRED)
        gid = group["id"]
        if not isinstance(gid, str) or not _GROUP_ID_RE.match(gid):
            _fail(source, f"{where}.id {gid!r} must match {_GROUP_ID_RE.pattern}")
        if gid in ids:
            _fail(source, f"{where}.id {gid!r} is declared twice")
        for key in ("label", "description"):
            if not isinstance(group[key], str) or not group[key].strip():
                _fail(source, f"{where}.{key} must be a non-empty string")
        ids.add(gid)
    return ids


def _check_participants(source: str, doc: dict[str, Any], group_ids: set[str]) -> set[str]:
    participants = doc["participants"]
    if not isinstance(participants, list) or not participants:
        _fail(source, "participants must be a non-empty array")

    refs: set[str] = set()
    pids: set[str] = set()
    indices: set[int] = set()
    used_groups: set[str] = set()

    for i, p in enumerate(participants):
        where = f"participants[{i}]"
        _require_keys(source, where, p, _PARTICIPANT_KEYS_REQUIRED, _PARTICIPANT_KEYS_OPTIONAL)

        ref = p["ref"]
        if not isinstance(ref, str) or not _REF_RE.match(ref):
            _fail(source, f"{where}.ref {ref!r} must match {_REF_RE.pattern}")
        if ref in refs:
            _fail(source, f"{where}.ref {ref!r} is used twice")
        refs.add(ref)

        index = p["index"]
        if not isinstance(index, int) or isinstance(index, bool) or index < 1:
            _fail(source, f"{where}.index must be a positive integer, got {index!r}")
        if index in indices:
            _fail(source, f"{where}.index {index} is used twice")
        indices.add(index)

        pid = p["pid"]
        if not isinstance(pid, str) or not _PID_RE.match(pid):
            _fail(source, f"{where}.pid {pid!r} must match {_PID_RE.pattern}")
        if pid in pids:
            _fail(source, f"{where}.pid {pid!r} is used twice")
        pids.add(pid)

        if not isinstance(p["name"], str) or not p["name"].strip():
            _fail(source, f"{where}.name must be a non-empty string")
        if p["type"] not in _PARTICIPANT_TYPES:
            _fail(source, f"{where}.type {p['type']!r} not in {sorted(_PARTICIPANT_TYPES)}")
        if p["status"] not in _PARTICIPANT_STATUSES:
            _fail(source, f"{where}.status {p['status']!r} not in {sorted(_PARTICIPANT_STATUSES)}")
        if p["group"] not in group_ids:
            _fail(source, f"{where}.group {p['group']!r} is not a declared group")
        used_groups.add(p["group"])
        if "role" in p and (not isinstance(p["role"], str) or not p["role"].strip()):
            _fail(source, f"{where}.role must be a non-empty string when present")

    expected = set(range(1, len(participants) + 1))
    if indices != expected:
        missing = sorted(expected - indices)[:5]
        _fail(source, f"participant indices must be 1..{len(participants)} without gaps (missing {missing})")

    empty = sorted(group_ids - used_groups)
    if empty:
        _fail(source, f"groups {empty} have no participants")
    return refs


def _check_trustlines(
    source: str,
    doc: dict[str, Any],
    refs: set[str],
    precision_by_code: dict[str, int],
    active_codes: set[str],
) -> None:
    trustlines = doc["trustlines"]
    if not isinstance(trustlines, list) or not trustlines:
        _fail(source, "trustlines must be a non-empty array")

    seen: set[tuple[str, str, str]] = set()
    for i, t in enumerate(trustlines):
        where = f"trustlines[{i}]"
        _require_keys(source, where, t, _TRUSTLINE_KEYS_REQUIRED)

        eq = t["equivalent"]
        if eq not in precision_by_code:
            _fail(source, f"{where}.equivalent {eq!r} is not a declared equivalent")
        if eq not in active_codes:
            _fail(source, f"{where}.equivalent {eq!r} is declared inactive")

        for end in ("from", "to"):
            if t[end] not in refs:
                _fail(source, f"{where}.{end} {t[end]!r} is not a participant ref")
        if t["from"] == t["to"]:
            _fail(source, f"{where} is a self-loop on {t['from']!r}")

        key = (eq, t["from"], t["to"])
        if key in seen:
            _fail(source, f"{where} duplicates trustline {key}")
        seen.add(key)

        limit = t["limit"]
        if not isinstance(limit, str):
            _fail(source, f"{where}.limit must be a decimal string, got {type(limit).__name__}")
        if not _DECIMAL_RE.match(limit):
            _fail(source, f"{where}.limit {limit!r} is not a plain non-negative decimal string")
        try:
            value = Decimal(limit)
        except InvalidOperation:  # pragma: no cover - guarded by the regex above
            _fail(source, f"{where}.limit {limit!r} is not a decimal")
        if value <= 0:
            _fail(source, f"{where}.limit {limit!r} must be positive")
        fractional = len(limit.partition(".")[2])
        if fractional > precision_by_code[eq]:
            _fail(
                source,
                f"{where}.limit {limit!r} has {fractional} fractional digits, "
                f"more than precision {precision_by_code[eq]} of {eq}",
            )

        if t["status"] not in _TRUSTLINE_STATUSES:
            _fail(source, f"{where}.status {t['status']!r} not in {sorted(_TRUSTLINE_STATUSES)}")

        _require_keys(source, f"{where}.policy", t["policy"], _POLICY_KEYS)
        for key_name in _POLICY_KEYS:
            if not isinstance(t["policy"][key_name], bool):
                _fail(source, f"{where}.policy.{key_name} must be a boolean")


def validate_community(doc: Any, *, source: str = "<community>") -> dict[str, Any]:
    """Raise :class:`CommunityError` unless ``doc`` is a valid description."""

    if not isinstance(doc, dict):
        _fail(source, f"community description must be an object, got {type(doc).__name__}")

    missing = [k for k in _TOP_LEVEL_REQUIRED if k not in doc]
    if missing:
        _fail(source, f"missing top-level keys {missing}")
    extra = sorted(set(doc) - set(_TOP_LEVEL_REQUIRED))
    if extra:
        _fail(source, f"unknown top-level keys {extra}")

    if doc["schema_version"] != SCHEMA_VERSION:
        _fail(source, f"schema_version {doc['schema_version']!r} != {SCHEMA_VERSION!r}")
    for key in ("community_id", "title", "summary"):
        if not isinstance(doc[key], str) or not doc[key].strip():
            _fail(source, f"{key} must be a non-empty string")

    precision_by_code = _check_equivalents(source, doc)
    active_codes = {eq["code"] for eq in doc["equivalents"] if eq["is_active"]}
    group_ids = _check_groups(source, doc)
    refs = _check_participants(source, doc, group_ids)
    _check_trustlines(source, doc, refs, precision_by_code, active_codes)
    return doc


def load_community(community_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Read and validate ``seeds/communities/<community_id>/community.json``."""

    base = root if root is not None else COMMUNITIES_DIR
    path = base / community_id / "community.json"
    return load_community_file(path)


def load_community_file(path: Path) -> dict[str, Any]:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_community(doc, source=str(path))
    return doc


def write_community(path: Path, doc: dict[str, Any]) -> None:
    """Validate, then write with the repository's fixed JSON shape."""

    validate_community(doc, source=str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    # LF regardless of platform: `.gitattributes` keeps every `.json` at LF in
    # the worktree, and a CRLF write would leave the tree dirty after each run.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
