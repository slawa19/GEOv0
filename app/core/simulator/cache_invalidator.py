from __future__ import annotations

import logging
import uuid
from typing import Any

from app.core.payments.router import PaymentRouter
from app.core.simulator.models import RunRecord


def invalidate_routing_cache(*, equivalents: set[str]) -> None:
    for eq in equivalents:
        PaymentRouter._graph_cache.pop(eq, None)


def invalidate_viz_cache(*, run: RunRecord, equivalents: set[str]) -> None:
    for eq in equivalents:
        run._real_viz_by_eq.pop(eq, None)


def invalidate_caches_after_inject(
    *,
    logger: logging.Logger,
    run: RunRecord,
    scenario: dict[str, Any],
    affected_equivalents: set[str],
    new_participants: list[tuple[uuid.UUID, str]],
    new_participants_scenario: list[dict[str, Any]],
    new_trustlines_scenario: list[dict[str, Any]],
    frozen_pids: list[str],
) -> None:
    """Invalidate in-memory caches after a successful inject commit.

    Best-effort: failures here are logged but do not crash the tick.
    """

    try:
        # 1. PaymentRouter graph cache — evict affected equivalents.
        invalidate_routing_cache(equivalents=affected_equivalents)

        # 2. run._real_viz_by_eq — evict so VizPatchHelper is recreated.
        invalidate_viz_cache(run=run, equivalents=affected_equivalents)

        # 3. run._real_participants — append new participants.
        if new_participants and run._real_participants is not None:
            for p_tuple in new_participants:
                run._real_participants.append(p_tuple)

        # 4. scenario["participants"] — append new participant dicts.
        if new_participants_scenario:
            s_participants = scenario.get("participants")
            if isinstance(s_participants, list):
                for p_dict in new_participants_scenario:
                    s_participants.append(p_dict)

        # 5. scenario["trustlines"] — append new trustline dicts.
        if new_trustlines_scenario:
            s_trustlines = scenario.get("trustlines")
            if isinstance(s_trustlines, list):
                for tl_dict in new_trustlines_scenario:
                    s_trustlines.append(tl_dict)

        # 6. run._edges_by_equivalent — add edges for new trustlines.
        if new_trustlines_scenario and run._edges_by_equivalent is not None:
            for tl_dict in new_trustlines_scenario:
                eq = str(tl_dict.get("equivalent") or "").strip()
                src = str(tl_dict.get("from") or "").strip()
                dst = str(tl_dict.get("to") or "").strip()
                if eq and src and dst:
                    run._edges_by_equivalent.setdefault(eq, []).append((src, dst))

        # 7. Frozen participants — update scenario dicts in-place.
        if frozen_pids:
            frozen_set = set(frozen_pids)

            # Update scenario["participants"][i]["status"].
            s_participants = scenario.get("participants")
            if isinstance(s_participants, list):
                for p_dict in s_participants:
                    if isinstance(p_dict, dict):
                        pid = str(p_dict.get("id") or "").strip()
                        if pid in frozen_set:
                            p_dict["status"] = "suspended"

            # Update scenario["trustlines"][i]["status"] for incident edges.
            s_trustlines = scenario.get("trustlines")
            if isinstance(s_trustlines, list):
                for tl_dict in s_trustlines:
                    if isinstance(tl_dict, dict):
                        frm = str(tl_dict.get("from") or "").strip()
                        to = str(tl_dict.get("to") or "").strip()
                        if frm in frozen_set or to in frozen_set:
                            prev = str(tl_dict.get("status") or "active").strip().lower()
                            if prev == "active":
                                tl_dict["status"] = "frozen"

            # Remove frozen edges from run._edges_by_equivalent.
            if run._edges_by_equivalent is not None:
                for eq, edges in run._edges_by_equivalent.items():
                    run._edges_by_equivalent[eq] = [
                        (s, d)
                        for s, d in edges
                        if s not in frozen_set and d not in frozen_set
                    ]

    except Exception:
        logger.warning(
            "simulator.real.inject.cache_invalidation_error",
            exc_info=True,
        )
