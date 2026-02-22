from __future__ import annotations

import logging
import math
import random
from decimal import Decimal, ROUND_DOWN
from typing import Any, Callable

from app.core.simulator.scenario_equivalent import effective_equivalent


class RealPaymentPlanner:
    def __init__(
        self,
        *,
        actions_per_tick_max: int,
        amount_cap_limit: Decimal | None,
        logger: logging.Logger,
        action_factory: Callable[[int, str, str, str, str], Any],
    ) -> None:
        self._actions_per_tick_max = int(actions_per_tick_max)
        self._amount_cap_limit = amount_cap_limit
        self._logger = logger
        self._action_factory = action_factory

    def _parse_event_time_ms(self, evt: Any) -> int | None:
        if not isinstance(evt, dict):
            return None
        t = evt.get("time")
        if isinstance(t, int):
            return max(0, int(t))
        # MVP: token-based times are future.
        return None

    def compute_stress_multipliers(
        self,
        *,
        events: Any,
        sim_time_ms: int,
    ) -> tuple[float, dict[str, float], dict[str, float]]:
        """Returns (mult_all, mult_by_group, mult_by_profile) for tx_rate.

        Best-effort: ignores unknown ops/fields/scopes.
        Deterministic: depends only on (events, sim_time_ms).
        """

        mult_all = 1.0
        mult_by_group: dict[str, float] = {}
        mult_by_profile: dict[str, float] = {}

        if not isinstance(events, list) or not events:
            return mult_all, mult_by_group, mult_by_profile

        for evt in events:
            if not isinstance(evt, dict):
                continue
            if str(evt.get("type") or "").strip() != "stress":
                continue
            t0 = self._parse_event_time_ms(evt)
            if t0 is None:
                continue

            duration_ms = 0
            try:
                md = evt.get("metadata")
                if isinstance(md, dict) and md.get("duration_ms") is not None:
                    duration_ms = int(md.get("duration_ms"))
                elif evt.get("duration_ms") is not None:
                    duration_ms = int(evt.get("duration_ms"))
            except Exception:
                duration_ms = 0
            duration_ms = max(0, int(duration_ms))

            if duration_ms <= 0:
                # Interpret as an instantaneous event at t0.
                if int(sim_time_ms) != int(t0):
                    continue
            else:
                if not (int(t0) <= int(sim_time_ms) < int(t0) + int(duration_ms)):
                    continue

            effects = evt.get("effects")
            if not isinstance(effects, list) or not effects:
                continue

            for eff in effects:
                if not isinstance(eff, dict):
                    continue
                if str(eff.get("op") or "").strip() != "mult":
                    continue
                if str(eff.get("field") or "").strip() != "tx_rate":
                    continue
                try:
                    v = float(eff.get("value"))
                except Exception:
                    continue
                if v <= 0:
                    continue
                # Soft clamp to avoid accidental huge multipliers; tx_rate is clamped later anyway.
                v = max(0.0, min(10.0, float(v)))

                scope = str(eff.get("scope") or "all").strip()
                if scope == "all" or not scope:
                    mult_all *= v
                    continue
                if scope.startswith("group:"):
                    g = scope.split(":", 1)[1].strip()
                    if g:
                        mult_by_group[g] = float(mult_by_group.get(g, 1.0)) * v
                    continue
                if scope.startswith("profile:"):
                    p = scope.split(":", 1)[1].strip()
                    if p:
                        mult_by_profile[p] = float(mult_by_profile.get(p, 1.0)) * v
                    continue

        return float(mult_all), mult_by_group, mult_by_profile

    def candidates_from_scenario(self, scenario: dict[str, Any]) -> list[dict[str, Any]]:
        tls = scenario.get("trustlines") or []
        out: list[dict[str, Any]] = []
        for tl in tls:
            status = str(tl.get("status") or "active").strip().lower()
            if status != "active":
                continue

            eq = effective_equivalent(scenario=scenario, payload=(tl or {}))
            frm = str(tl.get("from") or "").strip()
            to = str(tl.get("to") or "").strip()
            if not eq or not frm or not to:
                continue

            try:
                limit = Decimal(str(tl.get("limit")))
            except Exception:
                continue
            if limit <= 0:
                continue

            # TrustLine direction is creditor->debtor. Payment from debtor->creditor.
            # NOTE: capacity-aware filtering is applied in plan_payments()
            # via the debt_snapshot parameter (Phase 1.4).
            out.append(
                {
                    "equivalent": eq,
                    "sender_pid": to,
                    "receiver_pid": frm,
                    "limit": limit,
                }
            )

        out.sort(key=lambda x: (x["equivalent"], x["receiver_pid"], x["sender_pid"]))
        return out

    def pick_amount(
        self,
        rng: random.Random,
        limit: Decimal,
        *,
        amount_model: dict[str, Any] | None = None,
    ) -> str | None:
        cap = limit
        if self._amount_cap_limit is not None:
            cap = min(cap, self._amount_cap_limit)
        if cap <= 0:
            return None

        model_min: Decimal | None = None
        if isinstance(amount_model, dict) and amount_model:
            try:
                m_max = amount_model.get("max")
                if m_max is not None:
                    cap = min(cap, Decimal(str(m_max)))
                m_min = amount_model.get("min")
                if m_min is not None:
                    model_min = Decimal(str(m_min))
            except Exception:
                model_min = None

        if cap <= 0:
            return None
        if model_min is not None and model_min > cap:
            return None

        if isinstance(amount_model, dict) and amount_model:
            low = model_min if model_min is not None else Decimal("0.10")

            # If p50+p90 are provided, prefer a lognormal model for more realistic variability.
            # (p90 was previously ignored; this makes scenarios with p90 behave as intended.)
            try:
                p50_raw = amount_model.get("p50")
                p90_raw = amount_model.get("p90")
                if p50_raw is not None and p90_raw is not None:
                    p50 = Decimal(str(p50_raw))
                    p90 = Decimal(str(p90_raw))

                    if p50 <= 0 or p90 <= 0:
                        raise ValueError("non_positive_percentiles")

                    if p50 < low:
                        p50 = low
                    if p50 > cap:
                        p50 = cap

                    if p90 < p50:
                        p90 = p50
                    if p90 > cap:
                        p90 = cap

                    ratio = float(p90 / p50) if p50 > 0 else 1.0
                    if ratio <= 1.0:
                        raise ValueError("degenerate_p90")

                    z90 = 1.281551565545  # approx Normal(0,1) quantile at 0.90
                    mu = math.log(float(p50))
                    sigma = math.log(ratio) / z90
                    if not (math.isfinite(mu) and math.isfinite(sigma) and sigma > 0):
                        raise ValueError("bad_lognormal_params")

                    raw_f = rng.lognormvariate(mu, sigma)
                    raw = Decimal(str(raw_f))
                else:
                    raise ValueError("missing_percentiles")
            except Exception:
                # Fallback: triangular distribution biased towards p50 (mode).
                try:
                    mode_raw = amount_model.get("p50")
                    mode = (
                        Decimal(str(mode_raw))
                        if mode_raw is not None
                        else (low + cap) / 2
                    )
                    if mode < low:
                        mode = low
                    if mode > cap:
                        mode = cap
                    raw_f = rng.triangular(float(low), float(cap), float(mode))
                    raw = Decimal(str(raw_f))
                except Exception:
                    raw = Decimal(str(0.1 + rng.random() * float(cap)))
        else:
            raw = Decimal(str(0.1 + rng.random() * float(cap)))

        amt = min(raw, cap).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if model_min is not None and amt < model_min:
            amt = model_min.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if amt <= 0:
            return None
        return format(amt, "f")

    def plan_payments(
        self,
        run: Any,
        scenario: dict[str, Any],
        *,
        debt_snapshot: dict[tuple[str, str, str], Decimal] | None = None,
    ) -> list[Any]:
        """Deterministic planner for Real Mode payment actions.

        Important property for SB-NF-04:
        - planning for a given (seed, tick_index, scenario) is deterministic.
        - changing intensity only changes *how many* actions we take from the same
          per-tick ordering (prefix-stable), so it doesn't affect later ticks.

        Parameters
        ----------
        debt_snapshot : dict | None
            Mapping ``(debtor_pid, creditor_pid, eq_code_UPPER) → Decimal``
            with current debt amounts.  When provided, the planner reduces
            trustline limits by the already-used debt to avoid generating
            payments that exceed available capacity (Phase 1.4).
        """

        intensity = max(0.0, min(1.0, float(getattr(run, "intensity_percent", 0)) / 100.0))

        # ── Warm-up ramp (Phase 1.1) ──────────────────────────────────
        # If the scenario defines settings.warmup, linearly ramp intensity
        # from  floor  up to full value over the first *warmup_ticks* ticks.
        # Formula:  ramp_factor = floor + (1 - floor) * (tick_index / warmup_ticks)
        # After warmup_ticks the factor is ≥ 1.0, so intensity stays unchanged.
        warmup_cfg = (scenario.get("settings") or {}).get("warmup") or {}
        warmup_ticks = int(warmup_cfg.get("ticks", 0) or 0)
        if warmup_ticks > 0 and int(getattr(run, "tick_index", 0)) < warmup_ticks:
            # IMPORTANT: allow an explicit floor=0.0 (do not treat as missing).
            floor_raw = warmup_cfg.get("floor", None)
            if floor_raw is None:
                floor_raw = 0.1
            try:
                warmup_floor = float(floor_raw)
            except Exception:
                warmup_floor = 0.1
            warmup_floor = max(0.0, min(1.0, warmup_floor))
            ramp_factor = warmup_floor + (1.0 - warmup_floor) * (
                int(getattr(run, "tick_index", 0)) / warmup_ticks
            )
            intensity = intensity * ramp_factor
        # ── /Warm-up ──────────────────────────────────────────────────

        target_actions = max(1, int(self._actions_per_tick_max * intensity)) if intensity > 0 else 0
        if target_actions <= 0:
            return []

        candidates = self.candidates_from_scenario(scenario)
        if not candidates:
            return []

        profiles_props_by_id: dict[str, dict[str, Any]] = {}
        profiles_full_by_id: dict[str, dict[str, Any]] = {}
        for bp in scenario.get("behaviorProfiles") or []:
            if not isinstance(bp, dict):
                continue
            bp_id = str(bp.get("id") or "").strip()
            if not bp_id:
                continue
            props = bp.get("props")
            profiles_props_by_id[bp_id] = props if isinstance(props, dict) else {}
            profiles_full_by_id[bp_id] = bp

        participant_profile_id_by_pid: dict[str, str] = {}
        participant_group_by_pid: dict[str, str] = {}
        for p in scenario.get("participants") or []:
            if not isinstance(p, dict):
                continue
            pid = str(p.get("id") or p.get("participant_id") or "").strip()
            if not pid:
                continue
            profile_id = str(p.get("behaviorProfileId") or "").strip()
            if profile_id:
                participant_profile_id_by_pid[pid] = profile_id
            group_id = str(p.get("groupId") or "").strip()
            if group_id:
                participant_group_by_pid[pid] = group_id

        # ── Phase 4: profile_by_pid lookup for flow/periodicity/reciprocity ──
        profile_by_pid: dict[str, dict[str, Any]] = {}
        for _pid, _prof_id in participant_profile_id_by_pid.items():
            if _prof_id in profiles_full_by_id:
                profile_by_pid[_pid] = profiles_full_by_id[_prof_id]
        # ── /Phase 4 profile_by_pid ──────────────────────────────────────────

        def _clamp01(v: Any, default: float) -> float:
            try:
                f = float(v)
            except Exception:
                return default
            if f < 0.0:
                return 0.0
            if f > 1.0:
                return 1.0
            return f

        # ── Phase 4: flow config (read once) ─────────────────────────────────
        _flow_cfg: dict[str, Any] = (scenario.get("settings") or {}).get("flow") or {}
        _flow_enabled: bool = bool(_flow_cfg.get("enabled", False))
        _flow_default_affinity: float = _clamp01(
            _flow_cfg.get("default_affinity", 0.7), 0.7
        )
        _flow_reciprocity_bonus: float = _clamp01(
            _flow_cfg.get("reciprocity_bonus", 0.0), 0.0
        )
        # ── /Phase 4 flow config ─────────────────────────────────────────────

        tick_seed = (int(getattr(run, "seed", 0)) * 1_000_003 + int(getattr(run, "tick_index", 0))) & 0xFFFFFFFF
        tick_rng = random.Random(tick_seed)

        order = list(candidates)
        tick_rng.shuffle(order)

        # Active stress multipliers (tx_rate) for this tick.
        mult_all, mult_by_group, mult_by_profile = self.compute_stress_multipliers(
            events=scenario.get("events"),
            sim_time_ms=int(getattr(run, "sim_time_ms", 0)),
        )

        def _norm_weight(weights: Any, key: str) -> float:
            if not isinstance(weights, dict) or not weights:
                return 1.0
            try:
                values = [float(x) for x in weights.values() if float(x) > 0]
                if not values:
                    return 0.0
                max_w = max(values)
                w = float(weights.get(key, 0.0))
                if max_w <= 0 or w <= 0:
                    return 0.0
                return min(1.0, w / max_w)
            except Exception:
                return 1.0

        # Build adjacency (payment direction debtor->creditor) and per-sender/receiver limit hints.
        # NOTE: TrustLine direction is creditor->debtor, but candidates are already inverted to debtor->creditor.
        adjacency_by_eq: dict[str, dict[str, list[tuple[str, Decimal]]]] = {}
        max_outgoing_limit: dict[tuple[str, str], Decimal] = {}
        max_incoming_limit: dict[tuple[str, str], Decimal] = {}
        direct_edge_limit: dict[tuple[str, str, str], Decimal] = {}
        for c in candidates:
            eq = str(c.get("equivalent") or "").strip()
            sender = str(c.get("sender_pid") or "").strip()
            receiver = str(c.get("receiver_pid") or "").strip()
            limit = c.get("limit")
            if not eq or not sender or not receiver:
                continue
            if not isinstance(limit, Decimal):
                continue
            adjacency_by_eq.setdefault(eq, {}).setdefault(sender, []).append(
                (receiver, limit)
            )

            direct_edge_limit[(sender, receiver, eq)] = limit

            k = (sender, eq)
            prev = max_outgoing_limit.get(k)
            if prev is None or limit > prev:
                max_outgoing_limit[k] = limit

            k_in = (receiver, eq)
            prev_in = max_incoming_limit.get(k_in)
            if prev_in is None or limit > prev_in:
                max_incoming_limit[k_in] = limit

        for eq, m in adjacency_by_eq.items():
            for sender, edges in m.items():
                # Deterministic neighbor order.
                edges.sort(key=lambda x: x[0])

        # ── Phase 1.4: pre-aggregate debt snapshot for O(1) lookups ───
        _ZERO = Decimal("0")
        _debt_out_agg: dict[tuple[str, str], Decimal] = {}   # (debtor_pid, eq_upper) → total
        _debt_in_agg: dict[tuple[str, str], Decimal] = {}    # (creditor_pid, eq_upper) → total
        if debt_snapshot:
            for (debtor_pid, creditor_pid, eq_code), amt in debt_snapshot.items():
                k_out = (debtor_pid, eq_code)
                _debt_out_agg[k_out] = _debt_out_agg.get(k_out, _ZERO) + amt
                k_in = (creditor_pid, eq_code)
                _debt_in_agg[k_in] = _debt_in_agg.get(k_in, _ZERO) + amt
        # ── /Phase 1.4 pre-aggregate ──────────────────────────────────

        all_group_ids = sorted({g for g in participant_group_by_pid.values() if g})

        def _pick_group(rng: random.Random, sender_props: dict[str, Any]) -> str | None:
            weights = sender_props.get("recipient_group_weights")
            if not isinstance(weights, dict) or not weights:
                return None
            try:
                items = [(str(k), float(v)) for k, v in weights.items()]
                items = [(k, v) for (k, v) in items if k and v > 0]
                if not items:
                    return None
                total = sum(v for _, v in items)
                if total <= 0:
                    return None
                r = rng.random() * total
                acc = 0.0
                for k, v in items:
                    acc += v
                    if r <= acc:
                        return k
                return items[-1][0]
            except Exception:
                return None

        def _reachable_nodes(
            eq: str, sender: str, *, max_depth: int = 3, max_nodes: int = 200
        ) -> list[str]:
            graph = adjacency_by_eq.get(eq) or {}
            if sender not in graph:
                return []

            visited: set[str] = {sender}
            # (node, depth)
            queue: list[tuple[str, int]] = [(sender, 0)]
            qi = 0
            while qi < len(queue) and len(visited) < max_nodes:
                node, depth = queue[qi]
                qi += 1
                if depth >= max_depth:
                    continue
                for nxt, _lim in graph.get(node) or []:
                    if nxt in visited:
                        continue
                    visited.add(nxt)
                    queue.append((nxt, depth + 1))
                    if len(visited) >= max_nodes:
                        break

            visited.discard(sender)
            return sorted(visited)

        def _choose_receiver(
            *, rng: random.Random, eq: str, sender: str, sender_props: dict[str, Any]
        ) -> str | None:
            reachable = _reachable_nodes(eq, sender)
            if not reachable:
                # Fallback to direct neighbors.
                direct = [
                    pid
                    for (pid, _lim) in (adjacency_by_eq.get(eq) or {}).get(sender, [])
                ]
                reachable = sorted({p for p in direct if p and p != sender})
            if not reachable:
                return None

            # ── Flow Directionality (Phase 4.1) ──────────────────────
            if _flow_enabled:
                sender_group = participant_group_by_pid.get(sender)
                if sender_group:
                    sender_profile = profile_by_pid.get(sender, {})
                    sender_profile_props = sender_profile.get("props") if isinstance(sender_profile.get("props"), dict) else {}
                    flow_chains = sender_profile_props.get("flow_chains", [])
                    if isinstance(flow_chains, list) and flow_chains:
                        affinity = _flow_default_affinity
                        try:
                            _fa = sender_profile_props.get("flow_affinity")
                            if _fa is not None:
                                affinity = float(_fa)
                        except Exception:
                            pass
                        if rng.random() < affinity:
                            target_groups_flow = [
                                chain[1]
                                for chain in flow_chains
                                if isinstance(chain, (list, tuple))
                                and len(chain) >= 2
                                and chain[0] == sender_group
                            ]
                            if target_groups_flow:
                                target_group_flow = rng.choice(target_groups_flow)
                                in_target = [
                                    pid
                                    for pid in reachable
                                    if participant_group_by_pid.get(pid) == target_group_flow
                                ]
                                if in_target:
                                    return rng.choice(in_target)
            # ── /Flow Directionality ─────────────────────────────────

            target_group = _pick_group(rng, sender_props)
            if target_group:
                in_group = [
                    pid
                    for pid in reachable
                    if participant_group_by_pid.get(pid) == target_group
                ]
                if in_group:
                    return rng.choice(in_group)

            # If no group match (or no group weights), try any known group match, then any reachable.
            if all_group_ids:
                rng.shuffle(all_group_ids)
                for g in all_group_ids:
                    in_group = [
                        pid
                        for pid in reachable
                        if participant_group_by_pid.get(pid) == g
                    ]
                    if in_group:
                        return rng.choice(in_group)

            return rng.choice(reachable)

        planned: list[Any] = []
        i = 0
        max_iters = max(1, target_actions) * 50
        while len(planned) < target_actions and i < max_iters:
            c = order[i % len(order)]

            eq = str(c["equivalent"])
            sender_pid = c["sender_pid"]
            sender_profile_id = participant_profile_id_by_pid.get(sender_pid, "")
            sender_props = profiles_props_by_id.get(sender_profile_id, {})

            tx_rate_base = _clamp01(sender_props.get("tx_rate", 1.0), 1.0)
            sender_group = participant_group_by_pid.get(sender_pid, "")
            tx_rate_mult = float(mult_all)
            if sender_group:
                tx_rate_mult *= float(mult_by_group.get(sender_group, 1.0))
            if sender_profile_id:
                tx_rate_mult *= float(mult_by_profile.get(sender_profile_id, 1.0))
            tx_rate = _clamp01(float(tx_rate_base) * float(tx_rate_mult), 1.0)
            eq_weight = _norm_weight(sender_props.get("equivalent_weights"), eq)

            accept_prob = tx_rate * eq_weight
            if accept_prob <= 0.0:
                i += 1
                continue

            action_seed = (tick_seed * 1_000_003 + i) & 0xFFFFFFFF
            action_rng = random.Random(action_seed)

            if action_rng.random() > accept_prob:
                i += 1
                continue

            receiver_pid = _choose_receiver(
                rng=action_rng, eq=eq, sender=sender_pid, sender_props=sender_props
            )
            if receiver_pid is None:
                i += 1
                continue

            # Bound by sender-side outgoing capacity upper bound, plus receiver-side incoming upper bound.
            # For direct neighbors, also bound by the concrete direct edge limit.
            limit = max_outgoing_limit.get((sender_pid, eq), c["limit"])
            recv_cap = max_incoming_limit.get((receiver_pid, eq))
            if recv_cap is not None and recv_cap > 0:
                limit = min(limit, recv_cap)
            direct_cap = direct_edge_limit.get((sender_pid, receiver_pid, eq))
            if direct_cap is not None and direct_cap > 0:
                limit = min(limit, direct_cap)

            # ── Phase 1.4: capacity-aware amounts ─────────────────────
            # Reduce the static limit by already-used debt so generated
            # amounts fit into the remaining trustline capacity.
            if debt_snapshot:
                eq_upper = eq.strip().upper()
                static_limit = limit  # keep for debug logging

                # 1) Sender total outgoing debt → cap max_outgoing_limit
                out_limit_raw = max_outgoing_limit.get((sender_pid, eq), c["limit"])
                out_used = _debt_out_agg.get((sender_pid, eq_upper), _ZERO)
                available_out = max(_ZERO, out_limit_raw - out_used)
                limit = min(limit, available_out)

                # 2) Receiver total incoming debt → cap max_incoming_limit
                recv_limit_raw = max_incoming_limit.get((receiver_pid, eq))
                if recv_limit_raw is not None and recv_limit_raw > 0:
                    in_used = _debt_in_agg.get((receiver_pid, eq_upper), _ZERO)
                    available_in = max(_ZERO, recv_limit_raw - in_used)
                    limit = min(limit, available_in)

                # 3) Direct edge debt → cap direct_edge_limit
                if direct_cap is not None and direct_cap > 0:
                    edge_used = debt_snapshot.get(
                        (sender_pid, receiver_pid, eq_upper), _ZERO
                    )
                    available_direct = max(_ZERO, direct_cap - edge_used)
                    limit = min(limit, available_direct)

                if limit < static_limit and static_limit > 0:
                    ratio = float(limit) / float(static_limit)
                    if ratio < 0.5:
                        self._logger.debug(
                            "capacity_aware: edge %s→%s eq=%s "
                            "static_limit=%s available=%s (%.0f%%)",
                            sender_pid,
                            receiver_pid,
                            eq,
                            static_limit,
                            limit,
                            ratio * 100,
                        )
            # ── /Phase 1.4 ────────────────────────────────────────────

            # ── Reciprocity Bonus (Phase 4.2) ─────────────────────────
            if _flow_reciprocity_bonus > 0 and debt_snapshot:
                _eq_upper_recip = eq.strip().upper()
                _reverse_debt = debt_snapshot.get(
                    (receiver_pid, sender_pid, _eq_upper_recip), _ZERO
                )
                if _reverse_debt > 0:
                    limit = limit * Decimal(str(1.0 + _flow_reciprocity_bonus))
            # ── /Reciprocity Bonus ────────────────────────────────────

            amount_model = None
            raw_amount_model = sender_props.get("amount_model")
            if isinstance(raw_amount_model, dict):
                maybe = raw_amount_model.get(eq)
                if isinstance(maybe, dict):
                    amount_model = maybe

            amount = self.pick_amount(action_rng, limit, amount_model=amount_model)
            if amount is None:
                i += 1
                continue

            # ── Periodicity (Phase 4.3) ───────────────────────────────
            _sender_profile_period = profile_by_pid.get(sender_pid, {})
            _sender_props_period = _sender_profile_period.get("props") if isinstance(_sender_profile_period.get("props"), dict) else {}
            _periodicity_factor = 1.0
            try:
                _pf_raw = _sender_props_period.get("periodicity_factor")
                if _pf_raw is not None:
                    _periodicity_factor = float(_pf_raw)
            except Exception:
                _periodicity_factor = 1.0
            if _periodicity_factor != 1.0:
                try:
                    _amount_val = float(amount)
                except Exception:
                    _amount_val = 0.0
                if _amount_val > 0:
                    _p50_period = 50.0
                    if amount_model and isinstance(amount_model, dict):
                        try:
                            _p50_raw = amount_model.get("p50")
                            if _p50_raw is not None:
                                _p50_period = float(_p50_raw)
                        except Exception:
                            pass
                    if _p50_period > 0:
                        _den = 1.0 + (
                            math.log(max(_amount_val / _p50_period, 0.1))
                            * _periodicity_factor
                        )
                        if _den <= 0:
                            _period_accept = 0.0
                        else:
                            _period_accept = 1.0 / _den
                        _period_accept = max(0.0, min(1.0, _period_accept))
                        if action_rng.random() > _period_accept:
                            i += 1
                            continue
            # ── /Periodicity ──────────────────────────────────────────

            planned.append(
                self._action_factory(
                    # `seq` must be contiguous within a tick for ordered SSE emission.
                    len(planned),
                    eq,
                    sender_pid,
                    receiver_pid,
                    amount,
                )
            )

            i += 1

        return planned
