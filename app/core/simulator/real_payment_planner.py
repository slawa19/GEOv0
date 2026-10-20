from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from fractions import Fraction
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
        precision: int = 2,
    ) -> str | None:
        step = Decimal(1).scaleb(-int(precision))  # 028 `F-028-32`: the equivalent's step, not always cents
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

        amt = min(raw, cap).quantize(step, rounding=ROUND_DOWN)
        if model_min is not None and amt < model_min:
            amt = model_min.quantize(step, rounding=ROUND_DOWN)
        if amt <= 0:
            return None
        return format(amt, "f")

    def plan_payments(
        self,
        run: Any,
        scenario: dict[str, Any],
        *,
        debt_snapshot: dict[tuple[str, str, str], Decimal] | None = None,
        precision_by_eq: dict[str, int] | None = None,
    ) -> list[Any]:
        """Deterministic planner for Real Mode payment actions.

        Important property for SB-NF-04:
        - planning for a given (seed, tick_index, scenario) is deterministic.
        - changing intensity only changes *how many* actions we take from the same
          per-tick ordering (prefix-stable), so it doesn't affect later ticks.
        - *how many* is the tick's share of a fractional budget (049 decision 10,
          ``_actions_this_tick``): 2 % of 20 actions is one action every 2-3 ticks.

        Parameters
        ----------
        debt_snapshot : dict | None
            Mapping ``(debtor_pid, creditor_pid, eq_code_UPPER) → Decimal``
            with current debt amounts.  When provided, the planner reduces
            trustline limits by the already-used debt to avoid generating
            payments that exceed available capacity (Phase 1.4).

        The body is a sequence of steps; the order of draws from every ``random.Random``
        is part of the contract (a different order is a different plan for the same
        seed), so a step that draws keeps its place relative to the others.
        """

        target_actions = _actions_this_tick(self._actions_per_tick_max, run, scenario)
        if target_actions <= 0:
            return []

        candidates = self.candidates_from_scenario(scenario)
        if not candidates:
            return []

        tick_seed = (int(getattr(run, "seed", 0)) * 1_000_003 + int(getattr(run, "tick_index", 0))) & 0xFFFFFFFF
        tick_rng = random.Random(tick_seed)

        order = list(candidates)
        tick_rng.shuffle(order)

        ctx = _build_plan_context(
            scenario=scenario,
            candidates=candidates,
            tick_seed=tick_seed,
            stress=self.compute_stress_multipliers(
                events=scenario.get("events"),
                sim_time_ms=int(getattr(run, "sim_time_ms", 0)),
            ),
            debt_snapshot=debt_snapshot,
            precision_by_eq=precision_by_eq,
        )

        planned: list[Any] = []
        max_iters = max(1, target_actions) * 50
        for i in range(max_iters):
            if len(planned) >= target_actions:
                break
            action = self._plan_candidate(ctx, order[i % len(order)], i)
            if action is None:
                continue
            eq, sender_pid, receiver_pid, amount = action
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

        return planned

    def _plan_candidate(
        self, ctx: _PlanContext, c: dict[str, Any], i: int
    ) -> tuple[str, str, str, str] | None:
        """One iteration of the planning loop: accept, choose receiver, size, thin by period.

        Returns ``(eq, sender_pid, receiver_pid, amount)`` or ``None`` when this
        candidate yields no action on this iteration.
        """

        eq = str(c["equivalent"])
        sender_pid = c["sender_pid"]
        sender_profile_id = ctx.participant_profile_id_by_pid.get(sender_pid, "")
        sender_props = ctx.profiles_props_by_id.get(sender_profile_id, {})

        accept_prob = _accept_probability(
            ctx,
            sender_pid=sender_pid,
            sender_profile_id=sender_profile_id,
            sender_props=sender_props,
            eq=eq,
        )
        if accept_prob <= 0.0:
            return None

        action_seed = (ctx.tick_seed * 1_000_003 + i) & 0xFFFFFFFF
        action_rng = random.Random(action_seed)

        if action_rng.random() > accept_prob:
            return None

        receiver_pid = _choose_receiver(
            ctx, rng=action_rng, eq=eq, sender=sender_pid, sender_props=sender_props
        )
        if receiver_pid is None:
            return None

        limit = self._capacity_limit(ctx, eq=eq, sender_pid=sender_pid, receiver_pid=receiver_pid)

        amount_model = None
        raw_amount_model = sender_props.get("amount_model")
        if isinstance(raw_amount_model, dict):
            maybe = raw_amount_model.get(eq)
            if isinstance(maybe, dict):
                amount_model = maybe

        amount = self.pick_amount(
            action_rng,
            limit,
            amount_model=amount_model,
            precision=(ctx.precision_by_eq or {}).get(eq.strip().upper(), 2),
        )
        if amount is None:
            return None

        if not _passes_periodicity(
            ctx, action_rng, sender_pid=sender_pid, amount=amount, amount_model=amount_model
        ):
            return None

        return eq, sender_pid, receiver_pid, amount

    def _capacity_limit(
        self,
        ctx: _PlanContext,
        *,
        eq: str,
        sender_pid: str,
        receiver_pid: str,
    ) -> Decimal:
        """Upper bound for the amount: static trustline limits, minus used debt, plus reciprocity."""

        # Bound by sender-side outgoing capacity upper bound, plus receiver-side incoming upper bound.
        # For direct neighbors, also bound by the concrete direct edge limit.
        # `candidates_from_scenario` yields only candidates with a Decimal limit > 0 and
        # `_build_limit_graph` records every one of them under (sender, eq), so the sender-side
        # lookup cannot miss for a candidate taken from `order`.
        out_limit_raw = ctx.max_outgoing_limit[(sender_pid, eq)]
        limit = out_limit_raw
        recv_cap = ctx.max_incoming_limit.get((receiver_pid, eq))
        if recv_cap is not None and recv_cap > 0:
            limit = min(limit, recv_cap)
        direct_cap = ctx.direct_edge_limit.get((sender_pid, receiver_pid, eq))
        if direct_cap is not None and direct_cap > 0:
            limit = min(limit, direct_cap)

        debt_snapshot = ctx.debt_snapshot
        # ── Phase 1.4: capacity-aware amounts ─────────────────────
        # Reduce the static limit by already-used debt so generated
        # amounts fit into the remaining trustline capacity.
        if debt_snapshot:
            eq_upper = eq.strip().upper()
            static_limit = limit  # keep for debug logging

            # 1) Sender total outgoing debt → cap max_outgoing_limit
            out_used = ctx.debt_out_agg.get((sender_pid, eq_upper), _ZERO)
            available_out = max(_ZERO, out_limit_raw - out_used)
            limit = min(limit, available_out)

            # 2) Receiver total incoming debt → cap max_incoming_limit
            recv_limit_raw = ctx.max_incoming_limit.get((receiver_pid, eq))
            if recv_limit_raw is not None and recv_limit_raw > 0:
                in_used = ctx.debt_in_agg.get((receiver_pid, eq_upper), _ZERO)
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
        if ctx.flow_reciprocity_bonus > 0 and debt_snapshot:
            _eq_upper_recip = eq.strip().upper()
            _reverse_debt = debt_snapshot.get(
                (receiver_pid, sender_pid, _eq_upper_recip), _ZERO
            )
            if _reverse_debt > 0:
                limit = limit * Decimal(str(1.0 + ctx.flow_reciprocity_bonus))
        # ── /Reciprocity Bonus ────────────────────────────────────

        return limit


_ZERO = Decimal("0")


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


# The opening window of a launch: ticks 1..LAUNCH_OPENING_TICKS plan at least one action at any intensity above zero.
# `run_lifecycle` creates and restarts a run at `tick_index` 0 and the heartbeat (`runtime_impl._heartbeat_loop`)
# advances it before each tick it runs, so tick 1 is the first tick a launch runs.
LAUNCH_OPENING_TICKS = 5

# 049 decision 10: the visitor's «быстро» step, 15 % (the steps 2 / 5 / 15 % live in the UI only:
# simulator-ui/v2/src/components/TopBar.vue, `TEMPO_STEPS`). The scenario warm-up ramps only the tempo above it.
VISITOR_FAST_STEP_PERCENT = 15


def _actions_this_tick(actions_per_tick_max: int, run: Any, scenario: dict[str, Any]) -> int:
    """049 decision 10: the tick's share of a FRACTIONAL action budget, accumulated between ticks.

    The run's ticks spend the accumulator ``carry += r_t; actions = floor(carry); carry -= actions``, started at 0 at
    ``tick_index`` 0 (a created run; the heartbeat advances it before each tick, so the first tick run is 1), where
    ``r_t`` is ``actions_per_tick_max`` times the tick's intensity: the run's percent ``p`` as
    ``min(p, 15 %) + ramp_t * max(p - 15 %, 0)`` - ``settings.warmup`` ramps only the tempo above the visitor's «быстро»
    step (``VISITOR_FAST_STEP_PERCENT``; 049 T4915, the closing review). At 20 actions, 2 % (``r`` 0.4) is one action
    every 2-3 ticks, 5 % one per tick, 15 % three, 0 % none - from the launch, ramp or not; 30 % on the presets' ramp
    is 3 + 3 * ramp_t, six from the ramp's end. Before 049 the tick planned ``max(1, int(r_t))`` - at least one action
    per tick at any intensity above zero.

    THE CARRY IS DERIVED, NOT STORED. The tick plans ``floor(B(t)) - floor(B(t - 1))``, ``B(t)`` being the budget ticks
    ``0..t`` accrue at the run's CURRENT percent (``_budget_through``); while the percent is unchanged this is exactly
    the accumulator above, ramp included.
    * A replay of the tick's money phase (``money_replay.py``) replans the SAME tick and, with unchanged inputs, plans
      the same count (a percent edited between attempts re-reads ``B`` at the new percent, bounded as below); a stored
      carry would be advanced once per attempt. Planning stays a function of (seed, tick, scenario, percent).
    * Pause: the heartbeat does not advance ``tick_index`` (``runtime_impl._heartbeat_loop``), so nothing accrues.
    * Restart: ``tick_index`` returns to 0 (``run_lifecycle.restart``), so the budget starts from zero; a new run
      starts from zero; nothing outlives the run or crosses to another run (the planner holds no per-run state).
    * Percent change (the admin's field, the visitor's step): ``B`` is re-read at the new percent, and
      ``B(t) - B(t - 1) = r_t``, so the next tick plans ``floor(r_t)`` or ``ceil(r_t)`` at the NEW rate - never more:
      a change cannot release budget saved at the old rate as a burst (the carry takes the new rate's phase).
    Tick 0 is never run by the heartbeat; its share is the carry the first tick starts with (a direct driver may
    plan tick 0 itself: it gets ``floor(r_0)``, ``r_0`` taking the warm-up floor on the tempo above 15 % only).

    THE LAUNCH'S OPENING WINDOW (ticks ``1..LAUNCH_OPENING_TICKS``; a created or restarted run is at ``tick_index`` 0
    and the heartbeat advances it before each tick, so tick 1 is the first one run) plans ``max(1, share)`` at any
    intensity above zero; from tick ``LAUNCH_OPENING_TICKS + 1`` the share is planned unchanged. Spec 043 promises
    a first-time visitor a payment within 15 s of opening (1 s ticks, a few seconds of bootstrap). Before S2a the
    ``max(1, ...)`` above gave EVERY tick an attempt, and that - not the warm-up - kept the promise: with the warm-up
    then applied to the whole tempo the share reached one action only on tick 6 at 5 %, 12 at 2 % (the ramp no longer
    touches the visitor's steps: at 2 % the share is 0, 1, 0, 1, 0 on ticks 1..5). A single action on tick 1
    was not enough (049 T4915, measured): the plan can come out empty under candidate sampling, or its payment be
    rejected (``ROUTING_NO_ROUTE``), about 27 % of opening attempts together. So the window restores «an attempt on
    every tick» only for the opening, and a lost attempt is retried on the next tick: even after four losses in a row
    the fifth tick attempts (about 6-8 s after the page opens). With 27 % lost independently, no attempt of five
    landing is about 0.27 ** 5 = 0.14 % - an estimate: early losses may be correlated (a sparse early graph, the same
    routes); the live first-visit test is the check.
    * At most ``LAUNCH_OPENING_TICKS`` extra actions per launch - exactly the window's ticks whose share is 0 - so the
      slow step is distorted by at most five payments.
    * Derived from ``tick_index``, no state: a replay of a window tick plans the same; a resume past the window
      gets nothing extra; a restart returns to tick 0 and the next launch has its window again.
    * Never more than ``ceil(r_t)`` in a tick: ``max(1, share) <= ceil(r_t)`` for ``r_t > 0``, a percent change
      inside the window included.
    """

    base = _base_intensity(run)
    if base <= 0 or actions_per_tick_max <= 0:
        return 0
    warmup_ticks, warmup_floor = _warmup_of(scenario)
    t = max(0, int(getattr(run, "tick_index", 0) or 0))
    rate_max = int(actions_per_tick_max)
    share = math.floor(rate_max * _budget_through(base, warmup_ticks, warmup_floor, t)) - math.floor(
        rate_max * _budget_through(base, warmup_ticks, warmup_floor, t - 1)
    )
    if 1 <= t <= LAUNCH_OPENING_TICKS:
        return max(1, share)
    return share


def _base_intensity(run: Any) -> Fraction:
    """The run's percent as an exact fraction in [0, 1] (``intensity_percent`` is an integer on the wire)."""

    percent = max(0.0, min(100.0, float(getattr(run, "intensity_percent", 0) or 0)))
    return Fraction(percent).limit_denominator(1_000_000) / 100


def _warmup_of(scenario: dict[str, Any]) -> tuple[int, Fraction]:
    """``settings.warmup`` (Phase 1.1): ``(ticks, floor)``; ticks <= 0 means no ramp.

    The ramp factor at tick ``k < ticks`` is ``floor + (1 - floor) * k / ticks``; from ``ticks`` on it is 1. It scales
    only the tempo above ``VISITOR_FAST_STEP_PERCENT`` (``_budget_through``).
    """

    warmup_cfg = (scenario.get("settings") or {}).get("warmup") or {}
    warmup_ticks = int(warmup_cfg.get("ticks", 0) or 0)
    # IMPORTANT: allow an explicit floor=0.0 (do not treat as missing).
    floor_raw = warmup_cfg.get("floor", None)
    if floor_raw is None:
        floor_raw = 0.1
    try:
        warmup_floor = float(floor_raw)
    except Exception:
        warmup_floor = 0.1
    warmup_floor = max(0.0, min(1.0, warmup_floor))
    return warmup_ticks, Fraction(warmup_floor).limit_denominator(1_000_000)


def _budget_through(base: Fraction, warmup_ticks: int, warmup_floor: Fraction, t: int) -> Fraction:
    """Sum of the intensities of ticks ``0..t`` at ``base`` (0 for ``t < 0``), exact.

    The intensity of tick ``k`` is ``min(base, fast) + ramp_k * max(base - fast, 0)``, ``fast`` being the visitor's
    «быстро» step (``VISITOR_FAST_STEP_PERCENT``, 049 decision 10): the warm-up ramps only the part of the tempo above
    it, so the visitor's steps 2 / 5 / 15 % run at their plain rate from the launch and an operator tempo above 15 %
    keeps the ramp on its excess (049 T4915, the closing review: with the ramp on the WHOLE tempo «медленно» chosen at
    launch planned its first actions after the window on ticks 12 and 19, then 5, 4, 4, 4, 3 ... ticks apart until the
    ramp's end at about 50 s, instead of 2-3). The ramp factor of tick ``k < ticks`` is ``floor + (1 - floor) * k / ticks``, from
    ``ticks`` on 1; its sum over ticks ``0..t`` is ``n * floor + (1 - floor) * n (n - 1) / (2 ticks)`` for the ``n``
    ramped ones plus 1 per tick after them. So ``B(t) = (t + 1) * min(base, fast) + max(base - fast, 0) * R(t)``.
    """

    if t < 0:
        return Fraction(0)
    if warmup_ticks > 0:
        last_ramped = min(t, warmup_ticks - 1)
        n = last_ramped + 1
        ramp_sum = n * warmup_floor + (1 - warmup_floor) * Fraction(n * (n - 1), 2 * warmup_ticks) + (t - last_ramped)
    else:
        ramp_sum = Fraction(t + 1)
    fast = Fraction(VISITOR_FAST_STEP_PERCENT, 100)
    return (t + 1) * min(base, fast) + max(base - fast, Fraction(0)) * ramp_sum


@dataclass
class _PlanContext:
    """Everything one ``plan_payments`` call derives from the scenario before its loop.

    ``all_group_ids`` is deliberately a single mutable list: ``_choose_receiver``
    shuffles it in place, so the order one iteration leaves is the starting order of
    the next. Copying it per iteration would change the plan for the same seed.
    """

    tick_seed: int
    profiles_props_by_id: dict[str, dict[str, Any]]
    participant_profile_id_by_pid: dict[str, str]
    participant_group_by_pid: dict[str, str]
    profile_by_pid: dict[str, dict[str, Any]]
    all_group_ids: list[str]
    flow_enabled: bool
    flow_default_affinity: float
    flow_reciprocity_bonus: float
    mult_all: float
    mult_by_group: dict[str, float]
    mult_by_profile: dict[str, float]
    adjacency_by_eq: dict[str, dict[str, list[tuple[str, Decimal]]]]
    max_outgoing_limit: dict[tuple[str, str], Decimal]
    max_incoming_limit: dict[tuple[str, str], Decimal]
    direct_edge_limit: dict[tuple[str, str, str], Decimal]
    debt_snapshot: dict[tuple[str, str, str], Decimal] | None
    debt_out_agg: dict[tuple[str, str], Decimal]
    debt_in_agg: dict[tuple[str, str], Decimal]
    precision_by_eq: dict[str, int] | None


def _build_plan_context(
    *,
    scenario: dict[str, Any],
    candidates: list[dict[str, Any]],
    tick_seed: int,
    stress: tuple[float, dict[str, float], dict[str, float]],
    debt_snapshot: dict[tuple[str, str, str], Decimal] | None,
    precision_by_eq: dict[str, int] | None,
) -> _PlanContext:
    profiles_props_by_id, profiles_full_by_id = _index_profiles(scenario)
    participant_profile_id_by_pid, participant_group_by_pid = _index_participants(scenario)

    # ── Phase 4: profile_by_pid lookup for flow/periodicity/reciprocity ──
    profile_by_pid: dict[str, dict[str, Any]] = {}
    for _pid, _prof_id in participant_profile_id_by_pid.items():
        if _prof_id in profiles_full_by_id:
            profile_by_pid[_pid] = profiles_full_by_id[_prof_id]
    # ── /Phase 4 profile_by_pid ──────────────────────────────────────────

    # ── Phase 4: flow config (read once) ─────────────────────────────────
    flow_cfg: dict[str, Any] = (scenario.get("settings") or {}).get("flow") or {}
    flow_enabled = bool(flow_cfg.get("enabled", False))
    flow_default_affinity = _clamp01(flow_cfg.get("default_affinity", 0.7), 0.7)
    flow_reciprocity_bonus = _clamp01(flow_cfg.get("reciprocity_bonus", 0.0), 0.0)
    # ── /Phase 4 flow config ─────────────────────────────────────────────

    # Active stress multipliers (tx_rate) for this tick.
    mult_all, mult_by_group, mult_by_profile = stress

    adjacency_by_eq, max_outgoing_limit, max_incoming_limit, direct_edge_limit = _build_limit_graph(candidates)
    debt_out_agg, debt_in_agg = _aggregate_debts(debt_snapshot)

    return _PlanContext(
        tick_seed=tick_seed,
        profiles_props_by_id=profiles_props_by_id,
        participant_profile_id_by_pid=participant_profile_id_by_pid,
        participant_group_by_pid=participant_group_by_pid,
        profile_by_pid=profile_by_pid,
        all_group_ids=sorted({g for g in participant_group_by_pid.values() if g}),
        flow_enabled=flow_enabled,
        flow_default_affinity=flow_default_affinity,
        flow_reciprocity_bonus=flow_reciprocity_bonus,
        mult_all=mult_all,
        mult_by_group=mult_by_group,
        mult_by_profile=mult_by_profile,
        adjacency_by_eq=adjacency_by_eq,
        max_outgoing_limit=max_outgoing_limit,
        max_incoming_limit=max_incoming_limit,
        direct_edge_limit=direct_edge_limit,
        debt_snapshot=debt_snapshot,
        debt_out_agg=debt_out_agg,
        debt_in_agg=debt_in_agg,
        precision_by_eq=precision_by_eq,
    )


def _index_profiles(
    scenario: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
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
    return profiles_props_by_id, profiles_full_by_id


def _index_participants(scenario: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
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
    return participant_profile_id_by_pid, participant_group_by_pid


def _build_limit_graph(
    candidates: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, list[tuple[str, Decimal]]]],
    dict[tuple[str, str], Decimal],
    dict[tuple[str, str], Decimal],
    dict[tuple[str, str, str], Decimal],
]:
    """Adjacency (payment direction debtor->creditor) and per-sender/receiver limit hints.

    NOTE: TrustLine direction is creditor->debtor, but candidates are already inverted to
    debtor->creditor. Every candidate carries a non-empty stripped ``equivalent``,
    ``sender_pid``, ``receiver_pid`` and a ``Decimal`` ``limit > 0``
    (``candidates_from_scenario`` drops the rest), so there is no per-candidate
    re-validation here.
    """

    adjacency_by_eq: dict[str, dict[str, list[tuple[str, Decimal]]]] = {}
    max_outgoing_limit: dict[tuple[str, str], Decimal] = {}
    max_incoming_limit: dict[tuple[str, str], Decimal] = {}
    direct_edge_limit: dict[tuple[str, str, str], Decimal] = {}
    for c in candidates:
        eq = c["equivalent"]
        sender = c["sender_pid"]
        receiver = c["receiver_pid"]
        limit = c["limit"]
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

    return adjacency_by_eq, max_outgoing_limit, max_incoming_limit, direct_edge_limit


def _aggregate_debts(
    debt_snapshot: dict[tuple[str, str, str], Decimal] | None,
) -> tuple[dict[tuple[str, str], Decimal], dict[tuple[str, str], Decimal]]:
    """Phase 1.4: pre-aggregate the debt snapshot for O(1) lookups."""

    debt_out_agg: dict[tuple[str, str], Decimal] = {}  # (debtor_pid, eq_upper) → total
    debt_in_agg: dict[tuple[str, str], Decimal] = {}  # (creditor_pid, eq_upper) → total
    if debt_snapshot:
        for (debtor_pid, creditor_pid, eq_code), amt in debt_snapshot.items():
            k_out = (debtor_pid, eq_code)
            debt_out_agg[k_out] = debt_out_agg.get(k_out, _ZERO) + amt
            k_in = (creditor_pid, eq_code)
            debt_in_agg[k_in] = debt_in_agg.get(k_in, _ZERO) + amt
    return debt_out_agg, debt_in_agg


def _accept_probability(
    ctx: _PlanContext,
    *,
    sender_pid: str,
    sender_profile_id: str,
    sender_props: dict[str, Any],
    eq: str,
) -> float:
    tx_rate_base = _clamp01(sender_props.get("tx_rate", 1.0), 1.0)
    sender_group = ctx.participant_group_by_pid.get(sender_pid, "")
    tx_rate_mult = float(ctx.mult_all)
    if sender_group:
        tx_rate_mult *= float(ctx.mult_by_group.get(sender_group, 1.0))
    if sender_profile_id:
        tx_rate_mult *= float(ctx.mult_by_profile.get(sender_profile_id, 1.0))
    tx_rate = _clamp01(float(tx_rate_base) * float(tx_rate_mult), 1.0)
    eq_weight = _norm_weight(sender_props.get("equivalent_weights"), eq)
    return tx_rate * eq_weight


def _reachable_nodes(
    ctx: _PlanContext, eq: str, sender: str, *, max_depth: int = 3, max_nodes: int = 200
) -> list[str]:
    graph = ctx.adjacency_by_eq.get(eq) or {}
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
    ctx: _PlanContext,
    *,
    rng: random.Random,
    eq: str,
    sender: str,
    sender_props: dict[str, Any],
) -> str | None:
    participant_group_by_pid = ctx.participant_group_by_pid
    reachable = _reachable_nodes(ctx, eq, sender)
    if not reachable:
        # Fallback to direct neighbors.
        direct = [
            pid
            for (pid, _lim) in (ctx.adjacency_by_eq.get(eq) or {}).get(sender, [])
        ]
        reachable = sorted({p for p in direct if p and p != sender})
    if not reachable:
        return None

    # ── Flow Directionality (Phase 4.1) ──────────────────────
    if ctx.flow_enabled:
        sender_group = participant_group_by_pid.get(sender)
        if sender_group:
            sender_profile = ctx.profile_by_pid.get(sender, {})
            sender_profile_props = sender_profile.get("props") if isinstance(sender_profile.get("props"), dict) else {}
            flow_chains = sender_profile_props.get("flow_chains", [])
            if isinstance(flow_chains, list) and flow_chains:
                affinity = ctx.flow_default_affinity
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
    if ctx.all_group_ids:
        # In place on purpose: see `_PlanContext`.
        rng.shuffle(ctx.all_group_ids)
        for g in ctx.all_group_ids:
            in_group = [
                pid
                for pid in reachable
                if participant_group_by_pid.get(pid) == g
            ]
            if in_group:
                return rng.choice(in_group)

    return rng.choice(reachable)


def _passes_periodicity(
    ctx: _PlanContext,
    rng: random.Random,
    *,
    sender_pid: str,
    amount: str,
    amount_model: dict[str, Any] | None,
) -> bool:
    """Periodicity (Phase 4.3): thin the amounts that deviate from the sender's p50.

    Draws from ``rng`` only when the sender's ``periodicity_factor`` is not 1.0, the amount
    is positive and the p50 is positive; otherwise the action passes without a draw.
    """

    _sender_profile_period = ctx.profile_by_pid.get(sender_pid, {})
    _sender_props_period = _sender_profile_period.get("props") if isinstance(_sender_profile_period.get("props"), dict) else {}
    _periodicity_factor = 1.0
    try:
        _pf_raw = _sender_props_period.get("periodicity_factor")
        if _pf_raw is not None:
            _periodicity_factor = float(_pf_raw)
    except Exception:
        _periodicity_factor = 1.0
    if _periodicity_factor == 1.0:
        return True
    try:
        _amount_val = float(amount)
    except Exception:
        _amount_val = 0.0
    if _amount_val <= 0:
        return True
    _p50_period = 50.0
    if amount_model and isinstance(amount_model, dict):
        try:
            _p50_raw = amount_model.get("p50")
            if _p50_raw is not None:
                _p50_period = float(_p50_raw)
        except Exception:
            pass
    if _p50_period <= 0:
        return True
    _den = 1.0 + (
        math.log(max(_amount_val / _p50_period, 0.1))
        * _periodicity_factor
    )
    if _den <= 0:
        _period_accept = 0.0
    else:
        _period_accept = 1.0 / _den
    _period_accept = max(0.0, min(1.0, _period_accept))
    return not (rng.random() > _period_accept)
