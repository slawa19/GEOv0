#!/usr/bin/env python
"""Extract a community description from the v2 seed generators.

This is a bridge, and it is meant to die. Today the roster and the trust
topology of a community live inside the generators in this folder; the
simulator scenarios under ``fixtures/simulator/`` carry a second copy of the
same people. The description at ``seeds/communities/<id>/community.json``
becomes the single source, and this script is how the v2 structure got there —
so the move is reproducible rather than asserted.

It carries forward **v2**, not the base generators: v2 changes limits, routing
and clearing policy and connectivity (Greenfield v2 has 523 trustlines against
439 in v1; Riverside v2 has 316 against 222).

It carries forward *structure* only. The invented result is dropped on purpose:
``used`` was ``limit × ((n % 17) + 1) / 20`` with hand-placed 93 % bottlenecks,
``available`` followed from it, and ``created_at`` spread history over 90 days.
Debts are produced by running the seed recipe, not written into a fixture.

Run:
  ./.venv/Scripts/python.exe admin-fixtures/tools/extract_community_description.py

The script is idempotent: re-running it on an unchanged tree rewrites the same
bytes. ``tests/unit/test_p017_t1712_community_descriptions.py`` asserts that the
committed descriptions still equal what the generators produce, so a drift in
either direction is a red test and not a silent divergence.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parents[1]

if str(REPO_ROOT / "seeds" / "communities") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "seeds" / "communities"))

from community_schema import SCHEMA_VERSION, write_community  # noqa: E402


# Group ranges are the roster numbering of the seed documents, and they are the
# implicit rule being made explicit: today group membership is recovered from a
# PID index range (`scripts/generate_simulator_seed_scenarios.py`) or from the
# substring "(Household)" in a display name (`generate_seed_*_v2.py`). After the
# extraction each participant simply says which group it belongs to.
GREENFIELD_GROUPS: list[tuple[str, str, str, range]] = [
    (
        "anchors",
        "Anchors",
        "Кооператив, склад, закупка, рынок, транспорт, пекарня, молочка, теплицы, мастерская, энергия и вода — узлы с высокой связностью, которые держат ликвидность.",
        range(1, 11),
    ),
    (
        "producers",
        "Producers",
        "Фермеры, переработчики и ремесленники: отдают продукцию с отсрочкой, поэтому чаще кредиторы.",
        range(11, 36),
    ),
    (
        "retail",
        "Retail",
        "Магазины, кафе, столовые: покупают у производителей и записывают покупки домохозяйств «на счёт».",
        range(36, 46),
    ),
    (
        "services",
        "Services",
        "Строители, ремонтники, няни, бухгалтеры: работают сейчас, рассчитываются потом — основная экономика HOUR.",
        range(46, 61),
    ),
    (
        "households",
        "Households",
        "Домохозяйства и подработки: чаще должники, с небольшим соседским микрокредитом между собой.",
        range(61, 96),
    ),
    (
        "agents",
        "Agents",
        "Координаторы запуска: связаны с кооперативом в HOUR и UAH, отвечают за доверие и операции.",
        range(96, 101),
    ),
]

RIVERSIDE_GROUPS: list[tuple[str, str, str, range]] = [
    (
        "anchors",
        "Anchors",
        "Кооператив, рыбный рынок с холодильником, портовые склады, общинный центр и марина — взаимная ликвидность городка.",
        range(1, 6),
    ),
    (
        "producers",
        "Producers",
        "Рыбаки, коптильня, рыбоводство, лодочники: отдают улов и изделия с отсрочкой.",
        range(6, 16),
    ),
    (
        "retail",
        "Retail",
        "Рыбный магазин, ресторан, кафе, бакалея, аптека, ларьки: продают домохозяйствам «на счёт».",
        range(16, 24),
    ),
    (
        "services",
        "Services",
        "Лодочный механик, ремонт сетей, лёд, кейтеринг, бухгалтер, медсестра: услуги в HOUR и UAH.",
        range(24, 34),
    ),
    (
        "households",
        "Households",
        "Домохозяйства городка: покупают в розницу, иногда делают предоплату рыбакам (fish-share).",
        range(34, 49),
    ),
    (
        "agents",
        "Agents",
        "Координатор запуска и офицер доверия: точка входа новых участников.",
        range(49, 51),
    ),
]


COMMUNITIES: dict[str, dict[str, Any]] = {
    "greenfield-village-100": {
        "generator": "generate_seed_greenfield_village_100_v2.py",
        "title": "GreenField Village (100 участников)",
        "summary": (
            "Сельская кооперативная экономика: сильные хабы (кооператив, склад, закупка, рынок, "
            "пекарня, молочка), разнообразные производители, точки розницы, где домохозяйства "
            "тратят еженедельно, и службы, создающие заметную экономику HOUR."
        ),
        "groups": GREENFIELD_GROUPS,
        "doc": "docs/ru/seeds/seed-greenfield-village-100.md",
    },
    "riverside-town-50": {
        "generator": "generate_seed_riverside_town_50_v2.py",
        "title": "Riverside Town (50 участников)",
        "summary": (
            "Приречный городок с рыболовецким укладом: рыбаки поставляют улов на коптильню и "
            "рынок, рынок продаёт домохозяйствам, ремонт лодок и сетей идёт в HOUR. Меньший "
            "размер упрощает отладку и чтение циклов клиринга."
        ),
        "groups": RIVERSIDE_GROUPS,
        "doc": "docs/ru/seeds/seed-riverside-town-50.md",
    },
}


_ROLE_RE = re.compile(r"^(.*?)\s*\(([^()]*)\)\s*$")


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load seed module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _split_role(display_name: str) -> tuple[str, str | None]:
    match = _ROLE_RE.match(display_name)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return display_name.strip(), None


def _slug(text: str) -> str:
    text = text.replace("’", "'").replace("&", " and ")
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()


def _pid_index(pid: str) -> int:
    match = re.match(r"^PID_U([0-9]{4})_", str(pid))
    if match is None:
        raise RuntimeError(f"Unexpected pid shape: {pid!r}")
    return int(match.group(1))


def _group_of(index: int, groups: list[tuple[str, str, str, range]]) -> str:
    for group_id, _label, _description, span in groups:
        if index in span:
            return group_id
    raise RuntimeError(f"Participant index {index} falls outside every declared group range")


def build_description(community_id: str) -> dict[str, Any]:
    spec = COMMUNITIES[community_id]
    module = _load_module(TOOLS_DIR / spec["generator"], f"_extract_{_slug(community_id)}")

    participants_objs = module.build_participants()
    trustlines = module.build_trustlines(participants_objs)
    equivalents = list(module.EQUIVALENTS)

    ref_by_pid: dict[str, str] = {}
    participants: list[dict[str, Any]] = []
    for p in participants_objs:
        index = _pid_index(p.pid)
        base_name, role = _split_role(p.display_name)
        ref = _slug(base_name)
        if ref in ref_by_pid.values():
            # Fail loudly rather than invent a suffix: a collision means the
            # roster gained two participants whose names differ only by role,
            # and a human has to pick the symbolic names.
            raise RuntimeError(f"Symbolic ref {ref!r} is not unique in {community_id}")
        ref_by_pid[p.pid] = ref

        entry: dict[str, Any] = {
            "ref": ref,
            "index": index,
            "pid": p.pid,
            "name": p.display_name,
            "type": p.type,
            "group": _group_of(index, spec["groups"]),
            "status": p.status,
        }
        if role:
            entry["role"] = role
        participants.append(entry)

    participants.sort(key=lambda entry: entry["index"])
    index_by_ref = {entry["ref"]: entry["index"] for entry in participants}

    out_trustlines: list[dict[str, Any]] = []
    for t in trustlines:
        policy = t.get("policy") or {}
        out_trustlines.append(
            {
                "equivalent": t["equivalent"],
                "from": ref_by_pid[t["from"]],
                "to": ref_by_pid[t["to"]],
                "limit": str(t["limit"]),
                "status": str(t.get("status") or "active"),
                "policy": {
                    "auto_clearing": bool(policy.get("auto_clearing", False)),
                    "can_be_intermediate": bool(policy.get("can_be_intermediate", False)),
                },
            }
        )

    # Deterministic order. Because `pid` is zero-padded on the roster index,
    # this is the same order the simulator scenario uses for its trustlines
    # (equivalent, from pid, to pid).
    out_trustlines.sort(key=lambda t: (t["equivalent"], index_by_ref[t["from"]], index_by_ref[t["to"]]))

    return {
        "schema_version": SCHEMA_VERSION,
        "community_id": community_id,
        "title": spec["title"],
        "summary": spec["summary"],
        "equivalents": [
            {
                "code": eq["code"],
                "precision": int(eq["precision"]),
                "description": eq["description"],
                "is_active": bool(eq["is_active"]),
            }
            for eq in equivalents
        ],
        "groups": [
            {"id": group_id, "label": label, "description": description}
            for group_id, label, description, _span in spec["groups"]
        ],
        "participants": participants,
        "trustlines": out_trustlines,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--community", choices=sorted(COMMUNITIES), action="append", default=None)
    parser.add_argument(
        "--out-root",
        type=Path,
        default=REPO_ROOT / "seeds" / "communities",
        help="Directory that holds <community_id>/community.json",
    )
    args = parser.parse_args(argv)

    for community_id in args.community or sorted(COMMUNITIES):
        doc = build_description(community_id)
        path = args.out_root / community_id / "community.json"
        write_community(path, doc)
        counts: dict[str, int] = {}
        for t in doc["trustlines"]:
            counts[t["equivalent"]] = counts.get(t["equivalent"], 0) + 1
        by_eq = ", ".join(f"{code} {counts[code]}" for code in sorted(counts))
        print(f"{path}: {len(doc['participants'])} participants, {len(doc['trustlines'])} trustlines ({by_eq})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
