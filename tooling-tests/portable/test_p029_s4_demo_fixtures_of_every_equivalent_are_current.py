"""The committed Simulator demo snapshots of EVERY equivalent are in the form the generator writes.

Programme 029, stage S4, F-029-20. `scripts/sync_demo_fixtures.ps1` regenerated only UAH, so the EUR and
HOUR snapshots stayed in an older form: a signed `net_balance_atoms` ("-150") next to a separate
`net_sign`, and trust lines with `status: frozen`, a value 028 withdrew from the contract (F-028-29).
The generator wrote the magnitude and kept the sign in `net_sign`, so a leading minus in a committed file
meant the file was not produced by it.

2026-10-07, 032 S4 (022 `T2207`): the generator and `scripts/sync_demo_fixtures.ps1` are deleted; the
snapshots and playlists are now static versioned assets. This check stays as the schema guard of those
assets, and the list of equivalents comes from the community descriptions
(`seeds/communities/*/community.json`), because the canonical Admin pack it used to read is deleted too.

What this checks, and what it does not:

- every equivalent named by a community description (`seeds/communities/*/community.json`) has a
  non-empty demo snapshot, and no JSON file of its directory carries a `net_balance_atoms` with a leading
  `-` or a trust line (an object with `source` and `target`) in status `frozen`. Participant nodes may be
  `frozen`: that is a participant status, not a line status;
- the scan itself is checked first on a synthetic snapshot that holds both defects, otherwise a green run
  would only prove the scan is blind (AGENTS.md section 9, anti-vacuum);
- it does not compare bytes: the assets are no longer regenerated, so a change to them is a reviewed diff
  of a versioned file;
- it does not check that the event playlists (`events/*.json`) are valid SSE events of the wire contract:
  that is held by the offline workbench's playlist tests (042 `T4205`,
  `simulator-ui/v2/src/composables/usePlaylistWorkbench.test.ts`, which parse every committed playlist with
  the live SSE normaliser);
- 2026-10-18, 039 `T3935`: a non-empty playlist `events/<id>.json` is played on its own start snapshot
  `events/<id>-snapshot.json` (recorded from a live run, `scripts/record_workbench_playlists.py`), so the check
  of a snapshot is extended deliberately to those: each must exist, name the equivalent of its folder and have
  nodes, and it is scanned for the two defects like every JSON file of the folder. An empty playlist needs no
  start snapshot;
- it does not check the financial coherence of the recorded patches (that a payment moves exactly its amount on
  every leg, that a clearing moves no balance): that is held, against each playlist's own start snapshot, by
  `simulator-ui/v2/src/composables/usePlaylistWorkbench.test.ts` (039 `T3935`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = _REPO_ROOT / "simulator-ui" / "v2" / "public" / "simulator-fixtures" / "v1"
_COMMUNITIES = _REPO_ROOT / "seeds" / "communities"


def _defects(node: Any, where: str) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        net = node.get("net_balance_atoms")
        if isinstance(net, str) and net.startswith("-"):
            found.append(f"{where}: signed net_balance_atoms {net!r}")
        if node.get("status") == "frozen" and "source" in node and "target" in node:
            found.append(f"{where}: trust line {node['source']} -> {node['target']} in status 'frozen'")
        for key, value in node.items():
            found.extend(_defects(value, f"{where}/{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_defects(value, f"{where}[{index}]"))
    return found


def _start_snapshot_problems(code: str, events: Path) -> tuple[list[str], int]:
    """039 T3935: every non-empty playlist of `events` has its start snapshot, of `code`, with nodes; and how many were read."""

    found: list[str] = []
    judged = 0
    for playlist in sorted(events.glob("*.json")):
        if playlist.name.endswith("-snapshot.json") or not json.loads(playlist.read_text(encoding="utf-8")):
            continue
        start_path = playlist.with_name(f"{playlist.stem}-snapshot.json")
        if not start_path.is_file():
            found.append(f"{code}: events/{playlist.name} has no start snapshot events/{start_path.name}")
            continue
        start = json.loads(start_path.read_text(encoding="utf-8"))
        judged += 1
        if start.get("equivalent") != code or not start.get("nodes"):
            found.append(f"{code}: events/{start_path.name} is not a {code} snapshot with nodes")
    return found, judged


def test_demo_snapshots_of_every_equivalent_have_no_signed_net_and_no_frozen_line(tmp_path: Path) -> None:
    # Counter-check of the start-snapshot rule (039 T3935) on a synthetic folder: a playlist without its snapshot and one
    # with a snapshot of another equivalent are named; an empty playlist needs none.
    (tmp_path / "a.json").write_text('[{"type": "tx.updated"}]', encoding="utf-8")
    (tmp_path / "b.json").write_text('[{"type": "tx.updated"}]', encoding="utf-8")
    (tmp_path / "b-snapshot.json").write_text('{"equivalent": "EUR", "nodes": [{"id": "A"}]}', encoding="utf-8")
    (tmp_path / "c.json").write_text("[]", encoding="utf-8")
    synthetic_found, synthetic_judged = _start_snapshot_problems("UAH", tmp_path)
    assert (len(synthetic_found), synthetic_judged) == (2, 1), "the start-snapshot rule no longer sees its defects"
    synthetic = {
        "nodes": [{"id": "A", "status": "frozen", "net_balance_atoms": "-5"}],
        "links": [{"source": "A", "target": "B", "status": "frozen"}],
    }
    assert len(_defects(synthetic, "synthetic")) == 2, "the scan no longer sees the defects it exists to find"

    descriptions = sorted(_COMMUNITIES.glob("*/community.json"))
    assert descriptions, "no community description found: nothing would be checked"
    codes = sorted(
        {str(e["code"]) for path in descriptions for e in json.loads(path.read_text(encoding="utf-8"))["equivalents"]}
    )
    assert codes, "the community descriptions name no equivalent: nothing would be checked"

    problems: list[str] = []
    starts_judged = 0
    for code in codes:
        snapshot_path = _FIXTURES / code / "snapshot.json"
        if not snapshot_path.is_file():
            problems.append(f"{code}: no demo snapshot (a static asset per equivalent of the community descriptions)")
            continue
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not snapshot.get("nodes"):
            problems.append(f"{code}: snapshot has no nodes")
        found, judged = _start_snapshot_problems(code, _FIXTURES / code / "events")
        problems.extend(found)
        starts_judged += judged
        for path in sorted((_FIXTURES / code).rglob("*.json")):
            problems.extend(_defects(json.loads(path.read_text(encoding="utf-8")), f"{code}/{path.relative_to(_FIXTURES / code).as_posix()}"))

    # Name every kind of defect of every equivalent with its count and two samples: a list cut at N lines would
    # show the first defect class only.
    by_kind: dict[tuple[str, str], list[str]] = {}
    for problem in problems:
        kind = (
            "frozen trust line"
            if "in status 'frozen'" in problem
            else "signed net_balance_atoms"
            if "signed net_balance_atoms" in problem
            else "other"
        )
        by_kind.setdefault((problem.split("/", 1)[0].split(":", 1)[0], kind), []).append(problem)
    summary = [f"{code} {kind} x{len(items)}: {'; '.join(items[:2])}" for (code, kind), items in sorted(by_kind.items())]
    assert not problems, "\n".join(summary)
    # Anti-vacuum (039 T3935): the start snapshots of the recorded playlists were judged (UAH: payments and clearing).
    assert starts_judged >= 2, f"only {starts_judged} start snapshot(s) of a recorded playlist judged"
