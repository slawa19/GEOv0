"""R-021-1 (programme 021, stage 2, `T2103`): architecture guard - the simulator writes `trust_lines` only
through `TrustLineService`, with one named exception.

WHAT IT PINS (spec, Problem item 1; "Решения" items 4, 11 and 12). In `app/core/simulator/` and in
`app/api/v1/simulator.py` there is:

1. no `TrustLine(...)` construction;
2. no Core `insert(...)`/`update(...)`/`delete(...)` whose target mentions `TrustLine`;
3. no raw SQL string that writes `trust_lines` (`INSERT INTO`/`UPDATE`/`DELETE FROM` naming the table);
4. no assignment to `.limit`, `.status` or `.policy` except the NAMED ones below - the inject freeze
   (`frozen_tl.status = "frozen"`, a hub operation the protocol does not know, "Решения" item 11) and three
   assignments that are not trust-line rows at all (a participant's status, two snapshot DTOs);
5. no `Participant(...)`/`Equivalent(...)` construction outside the seeder and the inject executor
   ("Решения" item 12).

The other two halves of the spec's R-021-1 row - the internal unsigned path is called only by the named
callers, and no request schema carries the flag - are
`tests/unit/test_p021_unsigned_trust_line_path_is_never_request_controlled.py`, updated by the same stage.

WHAT IT DOES NOT SEE. It reads syntax, not types: an assignment is identified by its exact source text, so a
trust-line row bound to a name already on the allow-list (`p_row`, `link`, `node`, `frozen_tl`) would pass; a
write through `setattr(row, "limit", ...)`, through a helper outside the scanned files, or through SQL built at
run time from fragments is invisible. A green run says the listed syntax is absent, not that no write exists -
the behaviour tests (R-021-2/3/4/6) are what show the writes go through the service. The counter-checks at the
bottom plant each refused form and show it is seen.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.p021_support import require_target, target_xfail_021

REPO = Path(__file__).resolve().parents[2]

SCANNED_DIR = "app/core/simulator"
SCANNED_FILES = ("app/api/v1/simulator.py",)

GUARDED_ATTRIBUTES = {"limit", "status", "policy"}

#: Every assignment to `.limit`/`.status`/`.policy` the scanned code may keep, by module and source text (compared
#: as `ast.unparse` spells it, so quoting and spacing do not matter). Exactly one of them writes a trust-line row:
#: the named freeze exception.
_ALLOWED_ASSIGNMENTS_AS_WRITTEN = {
    # THE NAMED EXCEPTION (spec, "Решения" item 11): freezing a participant freezes its active lines - a hub
    # operation with no protocol counterpart, kept outside the service.
    ("app/core/simulator/inject_executor.py", 'frozen_tl.status = "frozen"'),
    # Not trust-line rows:
    ("app/core/simulator/inject_executor.py", 'p_row.status = "suspended"'),  # a Participant
    ("app/core/simulator/snapshot_builder.py", "link.status = str(status)"),  # a snapshot DTO
    ("app/core/simulator/snapshot_builder.py", "node.status = str(rec.status)"),  # a snapshot DTO
}
ALLOWED_ASSIGNMENTS = {(module, ast.unparse(ast.parse(text))) for module, text in _ALLOWED_ASSIGNMENTS_AS_WRITTEN}

#: Where participants and equivalents may be constructed ("Решения" item 12).
ENTITY_CONSTRUCTORS = {"Participant", "Equivalent"}
ENTITY_MODULES = {"app/core/simulator/real_scenario_seeder.py", "app/core/simulator/inject_executor.py"}

_RAW_TRUST_LINE_WRITE = re.compile(
    r"\b(insert\s+into|update|delete\s+from)\s+\"?trust_lines\b", re.IGNORECASE
)


def _scanned_modules() -> list[tuple[str, str]]:
    paths = sorted((REPO / SCANNED_DIR).rglob("*.py")) + [REPO / f for f in SCANNED_FILES]
    return [(p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8")) for p in paths]


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def writes_in(source: str, module: str) -> tuple[list[str], set[tuple[str, str]]]:
    """(breaches, allowed assignments seen) of one module. `module` is its repo-relative posix path."""

    out: list[str] = []
    allowed_seen: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "TrustLine":
                out.append(f"{module}:{node.lineno}: TrustLine(...)")
            elif name in {"insert", "update", "delete"} and node.args and "TrustLine" in ast.unparse(node.args[0]):
                out.append(f"{module}:{node.lineno}: {name}({ast.unparse(node.args[0])})")
            elif name in ENTITY_CONSTRUCTORS and module not in ENTITY_MODULES:
                out.append(f"{module}:{node.lineno}: {name}(...) outside the seeder and the inject executor")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _RAW_TRUST_LINE_WRITE.search(node.value):
                out.append(f"{module}:{node.lineno}: raw SQL writing trust_lines: {node.value.strip()[:60]!r}")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in GUARDED_ATTRIBUTES:
                    key = (module, ast.unparse(node))
                    if key in ALLOWED_ASSIGNMENTS:
                        allowed_seen.add(key)
                    else:
                        out.append(f"{module}:{node.lineno}: {ast.unparse(node)}")
    return out, allowed_seen


def _scan() -> tuple[list[str], set[tuple[str, str]]]:
    found: list[str] = []
    seen: set[tuple[str, str]] = set()
    for module, source in _scanned_modules():
        breaches, allowed = writes_in(source, module)
        found += breaches
        seen |= allowed
    return found, seen


@target_xfail_021("T2103 (stage 2)", "the five remaining simulator writers of trust_lines move to the service")
def test_the_simulator_writes_trust_lines_only_through_the_service() -> None:
    found, _seen = _scan()
    require_target(not found, "trust-line writes outside the service:\n" + "\n".join(found))


def test_every_named_exception_is_still_where_it_is_named() -> None:
    """Anti-vacuum for the allow-list: an entry whose code is gone is stale and must be removed, not kept as
    a silent licence for whatever reappears under that text."""

    _found, seen = _scan()
    assert seen == ALLOWED_ASSIGNMENTS, f"stale allow-list entries: {sorted(ALLOWED_ASSIGNMENTS - seen)}"


def test_the_scan_reaches_the_scanned_files() -> None:
    modules = dict(_scanned_modules())
    assert "app/core/simulator/inject_executor.py" in modules and "app/api/v1/simulator.py" in modules
    assert "app/core/simulator/real_scenario_seeder.py" in modules


# ---------------------------------------------------------------------------------------- counter-checks


def _breaches(source: str, module: str = "app/core/simulator/inject_executor.py") -> list[str]:
    return writes_in(source, module)[0]


def test_counter_check_a_planted_constructor_is_seen() -> None:
    assert _breaches("session.add(TrustLine(from_participant_id=a, to_participant_id=b))\n")
    assert _breaches("row = models.TrustLine(limit=1)\n", "app/api/v1/simulator.py")


def test_counter_check_a_planted_policy_limit_or_second_status_write_is_seen() -> None:
    assert _breaches("tl.policy = {}\n")
    assert _breaches("tl.limit = new_limit\n", "app/api/v1/simulator.py")
    # The freeze exception covers exactly its own construction: a second status write in the same module,
    # even one that looks like it, is refused.
    assert not _breaches('frozen_tl.status = "frozen"\n')
    assert _breaches('frozen_tl.status = "closed"\n')
    assert _breaches('tl.status = "frozen"\n')
    # And the exception is not portable to another module.
    assert _breaches('frozen_tl.status = "frozen"\n', "app/api/v1/simulator.py")


def test_counter_check_planted_core_and_raw_sql_writes_are_seen() -> None:
    assert _breaches("await s.execute(update(TrustLine).where(TrustLine.id == i).values(limit=1))\n")
    assert _breaches("await s.execute(sa.insert(TrustLine.__table__).values(limit=1))\n")
    assert _breaches('await s.execute(text("UPDATE trust_lines SET status = \'closed\'"))\n')
    assert _breaches('q = """insert into trust_lines (id) values (1)"""\n')
    assert _breaches('q = "DELETE FROM trust_lines WHERE id = :i"\n')
    # Reads are not writes.
    assert not _breaches('q = "SELECT limit FROM trust_lines WHERE id = :i"\n')
    assert not _breaches("select(TrustLine).where(TrustLine.status == 'active')\n")


def test_counter_check_entities_outside_the_seeder_and_the_inject_are_seen() -> None:
    assert _breaches("Participant(pid='x')\n", "app/api/v1/simulator.py")
    assert _breaches("Equivalent(code='X')\n", "app/core/simulator/trust_drift_engine.py")
    assert not _breaches("Participant(pid='x')\n", "app/core/simulator/real_scenario_seeder.py")

