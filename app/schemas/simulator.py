from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field
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

    ttl_ms: Optional[int] = None
    intensity_key: Optional[str] = None

    edges: Optional[List[SimulatorTxUpdatedEventEdge]] = None
    node_badges: Optional[List[SimulatorTxUpdatedNodeBadge]] = None

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


class SimulatorClearingPlanStep(BaseModel):
    at_ms: int

    intensity_key: Optional[str] = None
    highlight_edges: Optional[List[SimulatorEventEdgeRef]] = None
    particles_edges: Optional[List[SimulatorEventEdgeRef]] = None
    flash: Optional[Dict[str, Any]] = None

    # Ensure nested SimulatorEventEdgeRef objects serialize with alias 'from'
    model_config = ConfigDict(extra="allow", serialize_by_alias=True)


class SimulatorClearingPlanEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["clearing.plan"]
    equivalent: str

    plan_id: str
    steps: List[SimulatorClearingPlanStep]

    # Ensure nested steps serialize with alias 'from' for edge refs
    model_config = ConfigDict(extra="allow", serialize_by_alias=True)


class SimulatorClearingDoneEvent(BaseModel):
    event_id: str
    ts: datetime
    type: Literal["clearing.done"]
    equivalent: str

    # Links clearing.done back to the previously emitted clearing.plan.
    # UI uses this to drive the clearing animation lifecycle.
    plan_id: str

    # Optional: stats useful for UI/analytics.
    cleared_cycles: Optional[int] = None
    # Total cleared volume in major units (string), e.g. "120.00".
    cleared_amount: Optional[str] = None

    # Optional patches to update the graph without a full snapshot refresh.
    # Shape matches NodePatch/EdgePatch used by the Simulator UI.
    node_patch: Optional[List[Dict[str, Any]]] = None
    edge_patch: Optional[List[Dict[str, Any]]] = None

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

    last_event_type: Optional[str] = None
    current_phase: Optional[str] = None

    last_error: Optional[SimulatorLastError] = None

    model_config = ConfigDict(extra="allow")


SimulatorEvent = Union[
    SimulatorTxUpdatedEvent,
    SimulatorTxFailedEvent,
    SimulatorClearingPlanEvent,
    SimulatorClearingDoneEvent,
    SimulatorRunStatusEvent,
]


class ScenarioUploadRequest(BaseModel):
    scenario: Dict[str, Any]

    model_config = ConfigDict(extra="forbid")


class ScenarioSummary(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    scenario_id: str
    name: Optional[str] = None
    created_at: Optional[datetime] = None

    participants_count: int = Field(ge=0)
    trustlines_count: int = Field(ge=0)
    equivalents: List[str]

    clusters_count: Optional[int] = None
    hubs_count: Optional[int] = None
    tags: Optional[List[str]] = None

    model_config = ConfigDict(extra="forbid")


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


class RunStatus(BaseModel):
    api_version: str = Field(default=SIMULATOR_API_VERSION)

    run_id: str
    scenario_id: str
    mode: RunMode
    state: RunState

    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None

    sim_time_ms: Optional[int] = Field(default=None, ge=0)
    intensity_percent: Optional[int] = Field(default=None, ge=0, le=100)
    ops_sec: Optional[float] = Field(default=None, ge=0)
    queue_depth: Optional[int] = Field(default=None, ge=0)

    errors_total: Optional[int] = Field(default=None, ge=0)
    errors_last_1m: Optional[int] = Field(default=None, ge=0)

    last_error: Optional[SimulatorLastError] = None
    last_event_type: Optional[str] = None
    current_phase: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SetIntensityRequest(BaseModel):
    intensity_percent: int = Field(ge=0, le=100)

    model_config = ConfigDict(extra="forbid")


class MetricPoint(BaseModel):
    t_ms: int = Field(ge=0)
    v: float

    model_config = ConfigDict(extra="forbid")


MetricSeriesKey = Literal[
    "success_rate",
    "avg_route_length",
    "total_debt",
    "clearing_volume",
    "bottlenecks_score",
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
