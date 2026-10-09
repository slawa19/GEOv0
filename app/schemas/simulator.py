from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictInt,
    constr,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.config import ConfigDict


SIMULATOR_API_VERSION = "simulator-api/1"


class SimulatorVizSize(BaseModel):
    w: float
    h: float

    model_config = ConfigDict(extra="forbid")


class SimulatorGraphNode(BaseModel):
    id: str
    name: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None

    links_count: Optional[int] = None
    net_balance_atoms: Optional[str] = None
    net_sign: Optional[Literal[-1, 0, 1]] = None
    # Signed major-units string (backend-authoritative), e.g. "-123.45".
    net_balance: Optional[str] = None

    viz_color_key: Optional[str] = None
    viz_shape_key: Optional[str] = None
    viz_size: Optional[SimulatorVizSize] = None
    viz_badge_key: Optional[str] = None

    model_config = ConfigDict(extra="allow")


NumberOrString = Union[float, str]


class SimulatorGraphLink(BaseModel):
    source: str
    target: str

    id: Optional[str] = None

    trust_limit: Optional[NumberOrString] = None
    used: Optional[NumberOrString] = None
    available: Optional[NumberOrString] = None

    status: Optional[str] = None
    # 026 `T2603.2`: set while the creditor's close request waits for the supported debt to reach 0 (limit 0).
    close_requested_at: Optional[datetime] = None

    viz_color_key: Optional[str] = None
    viz_width_key: Optional[str] = None
    viz_alpha_key: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class SimulatorPaletteEntry(BaseModel):
    color: str
    label: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorGraphLimits(BaseModel):
    max_nodes: Optional[int] = None
    max_links: Optional[int] = None
    max_particles: Optional[int] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorGraphSnapshot(BaseModel):
    equivalent: str
    generated_at: datetime

    nodes: List[SimulatorGraphNode]
    links: List[SimulatorGraphLink]

    palette: Optional[Dict[str, SimulatorPaletteEntry]] = None
    limits: Optional[SimulatorGraphLimits] = None

    model_config = ConfigDict(extra="allow")


class SimulatorEventEdgeRef(BaseModel):
    from_: str = Field(alias="from")
    to: str

    # Use 'from' (not 'from_') when serializing to JSON — frontend expects this key
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

class SimulatorEventEdgeStyle(BaseModel):
    viz_width_key: Optional[str] = None
    viz_alpha_key: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class SimulatorTxUpdatedEventEdge(BaseModel):
    from_: str = Field(alias="from")
    to: str
    style: Optional[SimulatorEventEdgeStyle] = None

    # Use 'from' (not 'from_') when serializing to JSON — frontend expects this key
    model_config = ConfigDict(extra="allow", populate_by_name=True, serialize_by_alias=True)


class SimulatorTxUpdatedNodeBadge(BaseModel):
    id: str
    viz_badge_key: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class SimulatorTxUpdatedEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["tx.updated"]
    equivalent: str

    # Optional explicit endpoints (do not replace routed edges; provided for UI convenience).
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None

    # Backend-authoritative transaction amount in major units (string), e.g. "150.00".
    amount: Optional[str] = None

    # Explicitly controls whether UI should emit amount flyout labels for this tx.updated.
    # - True: payload is expected to have enough data for labels (amount + endpoints via from/to or edges)
    # - False/None: label emission is optional / best-effort (backward compatible)
    amount_flyout: Optional[bool] = None

    ttl_ms: Optional[int] = None
    intensity_key: Optional[str] = None

    edges: Optional[List[SimulatorTxUpdatedEventEdge]] = None
    node_badges: Optional[List[SimulatorTxUpdatedNodeBadge]] = None

    # Optional patches to update the graph without a full snapshot refresh.
    # Declared here so the emitter no longer appends keys the model does not know
    # (011/T1104, `F-011-5`). Item shape stays `Dict[str, Any]` — the same choice
    # `SimulatorClearingDoneEvent` already makes below — because typing the items
    # would make `model_dump` materialize every optional patch key as an explicit
    # null inside each item, which changes the wire that consumers already read.
    node_patch: Optional[List[Dict[str, Any]]] = None
    edge_patch: Optional[List[Dict[str, Any]]] = None

    @model_validator(mode="after")
    def _validate_amount_flyout_contract(self) -> "SimulatorTxUpdatedEvent":
        if self.amount_flyout is True:
            amount = str(self.amount or "").strip()
            if not amount:
                raise ValueError("amount_flyout=true requires non-empty amount")

            has_endpoints = bool(str(self.from_ or "").strip()) and bool(
                str(self.to or "").strip()
            )

            has_edge = False
            if self.edges:
                for e in self.edges:
                    if str(e.from_ or "").strip() and str(e.to or "").strip():
                        has_edge = True
                        break

            if not (has_endpoints or has_edge):
                raise ValueError(
                    "amount_flyout=true requires endpoints via from/to or at least one edge"
                )

        return self

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SimulatorTxFailedError(BaseModel):
    code: str
    message: str
    at: datetime

    model_config = ConfigDict(extra="allow")


class SimulatorTxFailedEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["tx.failed"]
    equivalent: str

    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    error: SimulatorTxFailedError

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SimulatorClearingDoneEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["clearing.done"]
    equivalent: str

    # Identifier for the clearing batch (unique per emission).
    plan_id: str

    # Optional: stats useful for UI/analytics.
    cleared_cycles: Optional[int] = None
    # Total cleared volume in major units (string), e.g. "120.00".
    cleared_amount: Optional[str] = None

    # Authoritative edges that were actually touched by clearing.
    cycle_edges: Optional[List[SimulatorEventEdgeRef]] = None

    # Optional patches to update the graph without a full snapshot refresh.
    # Shape matches NodePatch/EdgePatch used by the Simulator UI.
    node_patch: Optional[List[Dict[str, Any]]] = None
    edge_patch: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra="allow")


class TopologyChangedNodeRef(BaseModel):
    pid: str
    name: Optional[str] = None
    type: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class TopologyChangedEdgeRef(BaseModel):
    from_pid: str
    to_pid: str
    equivalent_code: str
    limit: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class TopologyChangedPayload(BaseModel):
    added_nodes: List[TopologyChangedNodeRef] = Field(default_factory=list)
    # Nodes that were removed from the topology (future use).
    removed_nodes: List[str] = Field(default_factory=list)
    # Nodes that remain but should be considered frozen/suspended.
    frozen_nodes: List[str] = Field(default_factory=list)

    added_edges: List[TopologyChangedEdgeRef] = Field(default_factory=list)
    # Edges that were removed from the topology (future use).
    removed_edges: List[TopologyChangedEdgeRef] = Field(default_factory=list)
    # Edges incident to a suspended participant (028 `F-028-28`: no line status changes; they carry nothing).
    frozen_edges: List[TopologyChangedEdgeRef] = Field(default_factory=list)

    # Optional patches to update the graph without a full snapshot refresh.
    # Shape matches NodePatch/EdgePatch used by the Simulator UI.
    node_patch: Optional[List[Dict[str, Any]]] = None
    edge_patch: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorTopologyChangedEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["topology.changed"]
    equivalent: str

    payload: TopologyChangedPayload

    model_config = ConfigDict(extra="allow")


RunState = Literal["idle", "running", "paused", "stopping", "stopped", "error"]


class SimulatorLastError(BaseModel):
    code: str
    message: str
    at: datetime

    model_config = ConfigDict(extra="forbid")


class SimulatorRunStatusEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["run_status"]

    run_id: str
    scenario_id: str
    state: RunState

    sim_time_ms: Optional[int] = Field(default=None, ge=0)
    intensity_percent: Optional[int] = Field(default=None, ge=0, le=100)
    ops_sec: Optional[float] = Field(default=None, ge=0)
    queue_depth: Optional[int] = Field(default=None, ge=0)

    # Backend-first cumulative stats (authoritative source of truth).
    attempts_total: Optional[int] = Field(default=None, ge=0)
    committed_total: Optional[int] = Field(default=None, ge=0)
    rejected_total: Optional[int] = Field(default=None, ge=0)
    errors_total: Optional[int] = Field(default=None, ge=0)
    timeouts_total: Optional[int] = Field(default=None, ge=0)

    # Diagnostic: consecutive ticks where all planned payments were rejected (capacity stall).
    consec_all_rejected_ticks: Optional[int] = Field(default=None, ge=0)

    last_event_type: Optional[str] = None
    current_phase: Optional[str] = None

    last_error: Optional[SimulatorLastError] = None

    model_config = ConfigDict(extra="allow")


SimulatorEvent = Union[
    SimulatorTxUpdatedEvent,
    SimulatorTxFailedEvent,
    SimulatorClearingDoneEvent,
    SimulatorTopologyChangedEvent,
    SimulatorRunStatusEvent,
]


class ScenarioUploadRequest(BaseModel):
    scenario: Dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class LocalizedText(BaseModel):
    """A text of the scenario in both languages (036): the content lives in the scenario, not in the UI dictionary.

    Both languages are non-empty text; the model is as strict as the upload schema (`localizedText`), so a damaged
    stored scenario cannot be served in a weaker shape than it could have been uploaded in."""

    ru: constr(strict=True, min_length=1)  # type: ignore[valid-type]
    en: constr(strict=True, min_length=1)  # type: ignore[valid-type]

    model_config = ConfigDict(extra="forbid")


class ScenarioSummary(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    scenario_id: str
    name: Optional[str] = None
    # 036: always the pair; a scenario whose description is a plain string serves it as both languages.
    description: Optional[LocalizedText] = None
    created_at: Optional[datetime] = None

    participants_count: int = Field(ge=0)
    trustlines_count: int = Field(ge=0)
    equivalents: List[str]

    clusters_count: Optional[int] = None
    hubs_count: Optional[int] = None
    tags: Optional[List[str]] = None

    model_config = ConfigDict(extra="forbid")


#: A participant id named by a story: non-empty text (the reference to a declared participant is checked on the raw
#: scenario, `app/core/simulator/scenario_story.py`).
ScenarioPid = constr(strict=True, min_length=1)

#: The money grammar of a scripted amount and of an anchor amount (spec 036; the product's money door): plain digits, at
#: most 18 fraction digits, at most 50 digits in all, no sign, exponent or space, nothing after the last digit. The same
#: expression is `amount.pattern` of the scenario schema and of the canon (`(?![\s\S])` is "end of string": `$` would let
#: a terminal newline through).
SCENARIO_AMOUNT_PATTERN = r"(?!(?:\.?[0-9]){51})[0-9]+(?:\.[0-9]{1,18})?"
_SCENARIO_AMOUNT_RE = re.compile(SCENARIO_AMOUNT_PATTERN)


def scenario_amount_is_well_formed(value: Any) -> bool:
    return isinstance(value, str) and _SCENARIO_AMOUNT_RE.fullmatch(value) is not None


class ScenarioFocusEdge(BaseModel):
    """An edge the camera is pointed at. Its own model, not the SSE `SimulatorEventEdgeRef` (a protected wire shape that
    accepts empty ends): both ends are non-empty, and the Python spelling `from_` is NOT accepted as input - the wire and
    the scenario schema have only `from`, and a spelling the reference check does not read must not be one the model takes."""

    from_: ScenarioPid = Field(alias="from")
    to: ScenarioPid

    model_config = ConfigDict(extra="forbid")


class ScenarioEpisodeFocus(BaseModel):
    pids: List[ScenarioPid] = Field(default_factory=list)
    edges: List[ScenarioFocusEdge] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ScenarioEpisodeAnchor(BaseModel):
    """The event whose arrival shows the caption. Per spec 036 a `tx.updated` anchor names `from`, `to`, `amount` and
    `equivalent`; a `tx.failed` anchor names `from`, `to` and `equivalent` and NO amount (the SSE event has none);
    `clearing.done` and `topology.changed` need only the event name."""

    event: Literal["tx.updated", "tx.failed", "clearing.done", "topology.changed"]
    from_: Optional[ScenarioPid] = Field(default=None, alias="from")
    to: Optional[ScenarioPid] = None
    amount: Optional[str] = None
    equivalent: Optional[constr(strict=True, min_length=1)] = None  # type: ignore[valid-type]
    time_ms: Optional[StrictInt] = Field(default=None, ge=0)

    # 'from' (not 'from_') on the wire: the response route dumps by alias; a direct dump uses `by_alias=True`
    # (`serialize_by_alias` of the sibling edge models is a pydantic 2.11 setting and a no-op on the pinned 2.5.3).
    # No `populate_by_name`: `from_` in a source is an unknown key, so the reference check, which reads `from`, sees every
    # participant the model would have taken.
    model_config = ConfigDict(extra="forbid")

    @field_validator("amount")
    @classmethod
    def _amount_is_the_scenario_money_grammar(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not scenario_amount_is_well_formed(value):
            raise ValueError("amount must be a plain decimal string: digits, at most 18 fraction digits, 50 digits in all")
        return value

    @model_validator(mode="after")
    def _the_fields_the_event_is_matched_on(self) -> "ScenarioEpisodeAnchor":
        if self.event == "tx.updated":
            required = {"from": self.from_, "to": self.to, "amount": self.amount, "equivalent": self.equivalent}
        elif self.event == "tx.failed":
            required = {"from": self.from_, "to": self.to, "equivalent": self.equivalent}
            if self.amount is not None:
                raise ValueError("a tx.failed anchor has no amount (the event carries none)")
        else:
            return self
        missing = [name for name, value in required.items() if value is None]
        if missing:
            raise ValueError(f"a {self.event} anchor names {', '.join(required)}; missing: {', '.join(missing)}")
        return self


class ScenarioEpisode(BaseModel):
    """One event of the scenario that carries a caption (036). `index` is its position in the scenario's `events[]`."""

    index: StrictInt = Field(ge=0)
    time_ms: StrictInt = Field(ge=0)
    caption: LocalizedText
    pause_after: StrictBool = False
    kind: Literal["payment", "clearing", "stress", "inject", "note"]
    focus: Optional[ScenarioEpisodeFocus] = None
    anchor: Optional[ScenarioEpisodeAnchor] = None
    expected_cycle: Optional[List[ScenarioPid]] = Field(default=None, min_length=2)

    model_config = ConfigDict(extra="forbid")


class ScenarioPlayback(BaseModel):
    """`settings.playback` of the scenario as written; an absent field is null, no default is invented here.
    The ranges are those of the upload schema."""

    tick_seconds: Optional[float] = Field(default=None, ge=0.25, le=5, strict=True)
    intensity_percent: Optional[StrictInt] = Field(default=None, ge=0, le=100)
    inject_enabled: Optional[StrictBool] = None

    model_config = ConfigDict(extra="forbid")


class ScenarioDetail(ScenarioSummary):
    """`GET /simulator/scenarios/{scenario_id}`: the summary plus the story (036). The list does not carry it."""

    episodes: List[ScenarioEpisode] = Field(default_factory=list)
    playback: Optional[ScenarioPlayback] = None


class ScenariosListResponse(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)
    items: List[ScenarioSummary]

    model_config = ConfigDict(extra="forbid")


RunMode = Literal["fixtures", "real"]


class RunCreateRequest(BaseModel):
    scenario_id: str
    mode: RunMode
    intensity_percent: int = Field(ge=0, le=100)

    model_config = ConfigDict(extra="forbid")


class RunCreateResponse(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)
    run_id: str

    model_config = ConfigDict(extra="forbid")


class ActiveRunResponse(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)
    run_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class RunStatus(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    run_id: str
    scenario_id: str
    mode: RunMode
    state: RunState

    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None

    # Why/where stop was requested (best-effort).
    stop_requested_at: Optional[datetime] = None
    stop_source: Optional[str] = None
    stop_reason: Optional[str] = None
    stop_client: Optional[str] = None

    sim_time_ms: Optional[int] = Field(default=None, ge=0)
    intensity_percent: Optional[int] = Field(default=None, ge=0, le=100)
    ops_sec: Optional[float] = Field(default=None, ge=0)
    queue_depth: Optional[int] = Field(default=None, ge=0)

    errors_total: Optional[int] = Field(default=None, ge=0)
    committed_total: Optional[int] = Field(default=None, ge=0)
    rejected_total: Optional[int] = Field(default=None, ge=0)
    attempts_total: Optional[int] = Field(default=None, ge=0)
    timeouts_total: Optional[int] = Field(default=None, ge=0)
    errors_last_1m: Optional[int] = Field(default=None, ge=0)

    # Diagnostic: consecutive ticks where all planned payments were rejected (capacity stall).
    consec_all_rejected_ticks: Optional[int] = Field(default=None, ge=0)

    last_error: Optional[SimulatorLastError] = None
    last_event_type: Optional[str] = None
    current_phase: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SetIntensityRequest(BaseModel):
    intensity_percent: int = Field(ge=0, le=100)

    model_config = ConfigDict(extra="forbid")


# The wire scale of `MetricPoint.v`. It mirrors `simulator_run_metrics.value`,
# which is `Numeric(20, 8)`: rendering at the store's own scale is what makes
# one single form come out of every producer.
METRIC_VALUE_QUANT = Decimal("0.00000001")


def metric_point_value(value: Optional[Decimal | float | int | str]) -> Optional[str]:
    """Render a measured metric value as the decimal string `MetricPoint.v` carries.

    2026-08-20 / p007_t715. `null` in, `null` out: "not measured" must never
    become a string, and never a zero.

    **One canonical form for every producer.** The value is quantized to the
    eight fractional digits of the `Numeric(20, 8)` column and printed with
    `format(dec, "f")`. Both halves matter:

    * fixed scale - otherwise the DB-backed reader emits `"0.00000000"` while
      the synthetic generator emits `"0.0"` and `"45.3"` for the same field on
      the same endpoint, and string comparison, de-duplication and caching keyed
      on `v` stop being stable across modes;
    * plain notation, never the exponential form - exponential money strings are
      a confirmed defect class in this repository, consumer parsers break on
      them.

    A measured zero is therefore `"0.00000000"`: a value, still distinguishable
    from `null` ("not measured").
    """

    if value is None:
        return None
    dec = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(dec.quantize(METRIC_VALUE_QUANT), "f")


class MetricPoint(BaseModel):
    t_ms: int = Field(ge=0)
    # 2026-08-20 / p007_t715: a decimal string, not a float. Two of the seven
    # series (`total_debt`, `clearing_volume`) are money, and money stays exact
    # (AGENTS.md §8). The field is one type for all seven series on purpose: the
    # column is one column, so a union would only force consumers to branch.
    #
    # The form is fixed: the `Numeric(20, 8)` column's eight fractional digits,
    # plain decimal notation, one shape for the DB-backed and the synthetic
    # producer alike (see `metric_point_value`).
    #
    # `null` means "no measurement at/before this timestamp"; it is intentionally
    # distinguishable from a measured zero (`"0.00000000"`). Producers must not
    # synthesize a zero to keep the field populated.
    v: Optional[str]

    model_config = ConfigDict(extra="forbid")


MetricSeriesKey = Literal[
    "success_rate",
    "avg_route_length",
    "total_debt",
    "clearing_volume",
    "bottlenecks_score",
    "active_participants",
    "active_trustlines",
]


MetricUnit = Optional[Literal["%", "count", "amount"]]


class MetricSeries(BaseModel):
    key: MetricSeriesKey
    unit: MetricUnit = None
    points: List[MetricPoint]

    model_config = ConfigDict(extra="forbid")


class MetricsResponse(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    run_id: str
    equivalent: str
    from_ms: int = Field(ge=0)
    to_ms: int = Field(ge=0)
    step_ms: int = Field(ge=1)

    series: List[MetricSeries]

    model_config = ConfigDict(extra="forbid")


BottleneckReasonCode = Literal[
    "LOW_AVAILABLE",
    "HIGH_USED",
    "FREQUENT_ABORTS",
    "TOO_MANY_TIMEOUTS",
    "ROUTING_TOO_DEEP",
    "CLEARING_PRESSURE",
]


class BottleneckTargetEdge(BaseModel):
    kind: Literal["edge"]
    from_: str = Field(alias="from")
    to: str

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BottleneckTargetNode(BaseModel):
    kind: Literal["node"]
    id: str

    model_config = ConfigDict(extra="forbid")


BottleneckTarget = Union[BottleneckTargetEdge, BottleneckTargetNode]


class BottleneckItem(BaseModel):
    target: BottleneckTarget
    score: float
    reason_code: BottleneckReasonCode

    label: Optional[str] = None
    suggested_action: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class BottlenecksResponse(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    run_id: str
    equivalent: str
    items: List[BottleneckItem]

    model_config = ConfigDict(extra="forbid")


# =====================================
# Interact Mode (Simulator Actions) MVP
# =====================================


class SimulatorActionError(BaseModel):
    """Interact Mode error shape.

    IMPORTANT: this is intentionally NOT wrapped into {"error": ...}.
    """

    code: str
    message: str
    details: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineCreateRequest(BaseModel):
    from_pid: str
    to_pid: str
    equivalent: str
    limit: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineCreateResponse(BaseModel):
    ok: bool = True
    trustline_id: str
    from_pid: str
    to_pid: str
    equivalent: str
    limit: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineUpdateRequest(BaseModel):
    from_pid: str
    to_pid: str
    equivalent: str
    new_limit: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineUpdateResponse(BaseModel):
    ok: bool = True
    trustline_id: str
    old_limit: str
    new_limit: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineCloseRequest(BaseModel):
    from_pid: str
    to_pid: str
    equivalent: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineCloseResponse(BaseModel):
    ok: bool = True
    trustline_id: str
    # 026 `T2603.2`: the line's actual state - `closed`, or still live (`active`) with the request.
    status: str
    close_requested_at: Optional[datetime] = None
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionPaymentRealRequest(BaseModel):
    from_pid: str
    to_pid: str
    equivalent: str
    amount: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionPaymentRealResponse(BaseModel):
    ok: bool = True
    payment_id: str
    from_pid: str
    to_pid: str
    equivalent: str
    amount: str
    status: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionEdgeRef(BaseModel):
    from_: str = Field(alias="from")
    to: str

    # Response payloads must use key `from` (not `from_`) regardless of model_dump(by_alias=...).
    # FastAPI response serialization may or may not request `by_alias=True`, so make this explicit.
    #
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # 011/T1109: the serializer stays exactly as it was - `serialize_by_alias` was tried and
    # emits `from_`, which would change the wire - but the published schema no longer follows
    # its `Dict[str, str]` annotation. Pydantic derived the serialization schema from that
    # annotation and described each edge as an arbitrary string map, losing `from` and `to`:
    # protected wire keys under AGENTS.md section 8. The override below states what this model
    # actually emits, which is the whole point of program 011.
    @model_serializer(mode="plain")
    def _serialize(self) -> Dict[str, str]:
        return {"from": self.from_, "to": self.to}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler) -> Dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["from", "to"],
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
        }


class SimulatorActionClearingCycle(BaseModel):
    cleared_amount: str
    edges: List[SimulatorActionEdgeRef]

    model_config = ConfigDict(extra="forbid")


class SimulatorActionClearingRealRequest(BaseModel):
    # Programme 023 slice (d), decision 8 / R2: no `max_depth` - clearing execution has no depth. With
    # `extra="forbid"` a body still carrying it is a schema error (the action family's flat 400 INVALID_REQUEST).
    equivalent: str
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionClearingRealResponse(BaseModel):
    ok: bool = True
    equivalent: str
    cleared_cycles: int = Field(ge=0)
    total_cleared_amount: str
    cycles: List[SimulatorActionClearingCycle]
    client_action_id: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionParticipantItem(BaseModel):
    pid: str
    name: str
    type: str
    status: str

    model_config = ConfigDict(extra="forbid")


class SimulatorActionParticipantsListResponse(BaseModel):
    items: List[SimulatorActionParticipantItem]

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlineListItem(BaseModel):
    from_pid: str
    from_name: str
    to_pid: str
    to_name: str
    equivalent: str
    limit: str
    used: str
    # Debt in reverse direction (debtor = from_pid, creditor = to_pid). Since 026 `T2603.1` it belongs to the other
    # line and does not hold a close.
    reverse_used: str
    available: str
    status: str
    close_requested_at: Optional[datetime] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorActionTrustlinesListResponse(BaseModel):
    items: List[SimulatorActionTrustlineListItem]

    model_config = ConfigDict(extra="forbid")


# =====================================
# Phase 2.5: backend-first payment targets (reachability)
# =====================================


class SimulatorPaymentTargetsItem(BaseModel):
    # Receiver PID.
    to_pid: str
    # Shortest path hop count (edges) for any route with capacity > 0.
    hops: int = Field(ge=1)
    # Optional heavy field (enabled via include_max_available=true).
    max_available: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SimulatorPaymentTargetsResponse(BaseModel):
    items: List[SimulatorPaymentTargetsItem]

    model_config = ConfigDict(extra="forbid")


class ArtifactItem(BaseModel):
    name: str
    url: str

    content_type: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ArtifactIndex(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    run_id: str
    artifact_path: Optional[str] = None
    items: List[ArtifactItem]
    bundle_url: Optional[str] = None

    model_config = ConfigDict(extra="forbid")
