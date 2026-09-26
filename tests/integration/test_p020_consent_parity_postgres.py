"""Programme 020 stage 2, review P2-2: the eligible-edge relation admits consent EXACTLY as production parses it.

THE GAP (Codex §15 review of stage 2, 2026-09-25, P2-2). The experimental relation parsed
`policy.auto_clearing` in SQL and trimmed a fixed ASCII whitespace set (`E' \\t\\n\\r\\f\\v'`), while the
production parser `ClearingService._policy_flag` uses Python `str.strip()`, which removes every character
with `str.isspace()`. So `{"auto_clearing": "\\u00a0false\\u00a0"}` is REFUSED consent by production and was
ADMITTED by the relation. The trust-line schema accepts arbitrary policy values, so the value is storable.
Admission must be exact BEFORE enumeration and limiting: an execution-time refusal cannot give back the
place in the top 100 that an ineligible cycle took.

The stand: one edge per Python whitespace character outside the SQL trim set (23 of them), each carrying
`"<ws>false<ws>"`; positive controls carrying `"<ws>true<ws>"` / `"<ws>yes"` (production CONSENTS - the fix
must not refuse them); negative controls `False` and `"false"` (refused by both). Then a triangle whose one
line carries `"\\u00a0false\\u00a0"`: the DFS must not return it, while an otherwise identical consenting
triangle is returned.
"""

from __future__ import annotations

import pytest

from app.core.clearing.service import ClearingService
from scripts.p020_experimental_detectors import detect_dfs, load_eligible_edges
from tests.p020_support import Edge, debt_uuid, participant_uuid, require_target, ring, seed_graph


_SQL_TRIM = " \t\n\r\f\v"
_PY_ONLY_WHITESPACE = [c for c in map(chr, range(0x110000)) if c.isspace() and c not in _SQL_TRIM]


def _consents(value) -> bool:
    return ClearingService._policy_flag({"auto_clearing": value}, "auto_clearing", default=True)


def _stand():
    edges, refused, admitted = [], set(), set()
    n = 0

    def add(value):
        nonlocal n
        e = Edge(debt_uuid(0x30, n), f"w{n:03d}a", f"w{n:03d}b", "5", consent=value)
        n += 1
        edges.append(e)
        (admitted if _consents(value) else refused).add(e.debt_id)

    for c in _PY_ONLY_WHITESPACE:
        add(f"{c}false{c}")
    for c in (" ", "　", " "):
        add(f"{c}true{c}")
        add(f"{c}yes")
    add(False)
    add("false")
    add(True)
    return edges, refused, admitted


@pytest.mark.asyncio
async def test_the_relation_admits_consent_exactly_as_the_production_parser(db_session) -> None:
    edges, refused, admitted = _stand()
    # Controls: the stand says what it claims. Every whitespace-wrapped "false" is REFUSED by production,
    # the whitespace-wrapped "true"/"yes" are ADMITTED, and there are 23 characters SQL trim does not strip.
    assert len(_PY_ONLY_WHITESPACE) == 23 and " " in _PY_ONLY_WHITESPACE
    assert len(refused) == 23 + 2 and len(admitted) == 6 + 1

    eq = await seed_graph(db_session, "PZW", edges)
    loaded = {row[0] for row in await load_eligible_edges(db_session, eq.id)}

    assert admitted <= loaded, f"the relation refuses consenting lines: {sorted(map(str, admitted - loaded))}"
    wrongly = loaded & refused
    require_target(
        not wrongly,
        f"the relation admits {len(wrongly)} line(s) production refuses consent: "
        f"{sorted(repr(e.consent) for e in edges if e.debt_id in wrongly)}",
    )


@pytest.mark.asyncio
async def test_a_cycle_through_a_refusing_line_does_not_take_a_place(db_session) -> None:
    bad = ring(["zba", "zbb", "zbc"], ["9"] * 3, [debt_uuid(0x31, k) for k in range(3)])
    bad = [Edge(bad[0].debt_id, bad[0].debtor, bad[0].creditor, "9", consent=" false ")] + bad[1:]
    good = ring(["zga", "zgb", "zgc"], ["1"] * 3, [debt_uuid(0x32, k) for k in range(3)])
    eq = await seed_graph(db_session, "PZW", bad + good)

    assert not _consents(" false ")  # control: production refuses it
    found = await detect_dfs(db_session, eq.id, 6, limit=1)
    identities = [c.identity for c in found]
    good_identity = tuple(sorted(str(e.debt_id) for e in good))
    # With limit 1 the refused (larger) triangle, if admitted, takes the only place.
    require_target(
        identities == [good_identity],
        f"limit 1: expected the consenting triangle, got {identities}",
    )


@pytest.mark.asyncio
async def test_a_scoped_load_applies_the_same_parser(db_session) -> None:
    edges, refused, admitted = _stand()
    eq = await seed_graph(db_session, "PZW", edges)
    scope = {participant_uuid(p) for e in edges for p in (e.debtor, e.creditor)}
    loaded = {row[0] for row in await load_eligible_edges(db_session, eq.id, scope_ids=scope)}
    assert admitted <= loaded
    require_target(not (loaded & refused), "the scoped relation admits refused consent")
