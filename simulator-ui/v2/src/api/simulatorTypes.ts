import type { EdgePatch, GraphSnapshot, NodePatch, TxUpdatedEvent, ClearingDoneEvent } from '../types'

export type SimulatorMode = 'fixtures' | 'real'

export type ScenarioSummary = {
  api_version?: string
  scenario_id: string
  name?: string | null
  /** UI-only compatibility field; REST decoder accepts only canonical backend fields. */
  label?: string
  mode?: string
  created_at?: string | null
  updated_at?: string
  participants_count?: number
  trustlines_count?: number
  equivalents?: string[]
  clusters_count?: number | null
  hubs_count?: number | null
  tags?: string[] | null
}

export type ScenariosListResponse = {
  api_version?: string
  items: ScenarioSummary[]
}

export type RunCreateRequest = {
  scenario_id: string
  mode: SimulatorMode
  intensity_percent: number
}

export type RunCreateResponse = {
  run_id: string
}

export type ActiveRunResponse = {
  run_id: string | null
}

export type TxOnceRequest = {
  equivalent: string
  from?: string
  to?: string
  amount?: string
  ttl_ms?: number
  intensity_key?: string
  seed?: unknown
  client_action_id?: string
}

export type TxOnceResponse = {
  ok: true
  emitted_event_id: string
  client_action_id?: string | null
}

export type ClearingOnceRequest = {
  equivalent: string
  cycle_edges?: Array<{ from: string; to: string }>
  cleared_amount?: string
  seed?: unknown
  client_action_id?: string
}

export type ClearingOnceResponse = {
  ok: true
  plan_id: string
  done_event_id: string
  client_action_id?: string | null
}

// ============================
// Interact Mode (Actions API)
// ============================

/**
 * Backend error shape for all `/simulator/runs/{run_id}/actions/*` endpoints.
 * Note: returned as a top-level JSON object (not wrapped).
 */
export type SimulatorActionError = {
  code: string
  message: string
  details?: Record<string, unknown> | null
}

export type SimulatorActionTrustlineCreateRequest = {
  from_pid: string
  to_pid: string
  equivalent: string
  limit: string
  client_action_id?: string | null
}

export type SimulatorActionTrustlineCreateResponse = {
  ok: true
  trustline_id: string
  from_pid: string
  to_pid: string
  equivalent: string
  limit: string
  client_action_id?: string | null
}

export type SimulatorActionTrustlineUpdateRequest = {
  from_pid: string
  to_pid: string
  equivalent: string
  new_limit: string
  client_action_id?: string | null
}

export type SimulatorActionTrustlineUpdateResponse = {
  ok: true
  trustline_id: string
  old_limit: string
  new_limit: string
  client_action_id?: string | null
}

export type SimulatorActionTrustlineCloseRequest = {
  from_pid: string
  to_pid: string
  equivalent: string
  client_action_id?: string | null
}

export type SimulatorActionTrustlineCloseResponse = {
  ok: true
  trustline_id: string
  client_action_id?: string | null
}

export type SimulatorActionPaymentRealRequest = {
  from_pid: string
  to_pid: string
  equivalent: string
  amount: string
  client_action_id?: string | null
}

export type SimulatorActionPaymentRealResponse = {
  ok: true
  payment_id: string
  from_pid: string
  to_pid: string
  equivalent: string
  amount: string
  status: string
  client_action_id?: string | null
}

export type SimulatorActionEdgeRef = {
  from: string
  to: string
}

export type SimulatorActionClearingCycle = {
  cleared_amount: string
  edges: SimulatorActionEdgeRef[]
}

export type SimulatorActionClearingRealRequest = {
  equivalent: string
  max_depth?: number
  client_action_id?: string | null
}

export type SimulatorActionClearingRealResponse = {
  ok: true
  equivalent: string
  cleared_cycles: number
  total_cleared_amount: string
  cycles: SimulatorActionClearingCycle[]
  client_action_id?: string | null
}

export type ParticipantInfo = {
  pid: string
  name: string
  type: string
  status: string
}

export type SimulatorActionParticipantsListResponse = {
  items: ParticipantInfo[]
}

export type TrustlineInfo = {
  from_pid: string
  from_name: string
  to_pid: string
  to_name: string
  equivalent: string
  limit: string
  used: string
  /** Debt in reverse direction (debtor=from_pid, creditor=to_pid). */
  reverse_used?: string
  available: string
  status: string
}

export type SimulatorActionTrustlinesListResponse = {
  items: TrustlineInfo[]
}

// ============================
// Phase 2.5: backend-first payment targets (reachability)
// ============================

export type SimulatorPaymentTargetsItem = {
  /** Receiver PID. */
  to_pid: string
  /** Shortest path hop count (edges) for any route with capacity > 0. */
  hops: number
  /** Optional heavy field (enabled via include_max_available=true). */
  max_available?: string | null
}

export type SimulatorPaymentTargetsResponse = {
  items: SimulatorPaymentTargetsItem[]
}

export type RunState = 'created' | 'idle' | 'running' | 'paused' | 'stopping' | 'stopped' | 'error'

export type RunError = {
  code: string
  message: string
  at: string
}

export type RunStatus = {
  api_version?: string
  run_id: string
  scenario_id: string
  mode?: SimulatorMode
  state: RunState | string
  started_at?: string | null
  stopped_at?: string | null

  // Why/where stop was requested (best-effort).
  stop_requested_at?: string | null
  stop_source?: string | null
  stop_reason?: string | null
  stop_client?: string | null
  sim_time_ms?: number | null
  intensity_percent?: number | null
  ops_sec?: number | null
  queue_depth?: number | null
  last_event_type?: string | null
  current_phase?: string | null
  last_error?: RunError | null

  // Backend-first cumulative stats (authoritative; sent in every run_status event).
  attempts_total?: number | null
  committed_total?: number | null
  rejected_total?: number | null
  errors_total?: number | null
  timeouts_total?: number | null
  errors_last_1m?: number | null

  // Diagnostic: consecutive ticks where all planned payments were rejected (capacity stall).
  // Only present in SSE run_status events when > 0.
  consec_all_rejected_ticks?: number | null
}

export type RunStatusEvent = Omit<RunStatus, 'api_version' | 'mode' | 'started_at' | 'stopped_at'> & {
  event_id: string
  ts: string
  type: 'run_status'
}

export type TxFailedEvent = {
  event_id: string
  ts: string
  type: 'tx.failed'
  equivalent: string
  from: string
  to: string
  error: RunError
}

export type TopologyChangedNodeRef = {
  pid: string
  name?: string | null
  type?: string | null
}

export type TopologyChangedEdgeRef = {
  from_pid: string
  to_pid: string
  equivalent_code: string
  limit?: string | null
}

export type TopologyChangedPayload = {
  added_nodes: TopologyChangedNodeRef[]
  removed_nodes: string[]
  frozen_nodes?: string[]
  added_edges: TopologyChangedEdgeRef[]
  removed_edges: TopologyChangedEdgeRef[]
  frozen_edges?: TopologyChangedEdgeRef[]

  // Optional patches to update the graph without full snapshot refresh.
  node_patch?: NodePatch[]
  edge_patch?: EdgePatch[]
}

export type TopologyChangedEvent = {
  event_id: string
  ts: string
  type: 'topology.changed'
  equivalent: string
  payload: TopologyChangedPayload
  reason?: string
}

export type SimulatorEvent =
  | RunStatusEvent
  | (TxUpdatedEvent & { type: 'tx.updated' })
  | (ClearingDoneEvent & { type: 'clearing.done' })
  | TxFailedEvent
  | TopologyChangedEvent
  | ({ event_id: string; ts: string; type: string; [k: string]: unknown } & { equivalent?: string })

export type SimulatorGraphSnapshot = GraphSnapshot

export type MetricSeriesKey =
  | 'success_rate'
  | 'avg_route_length'
  | 'total_debt'
  | 'clearing_volume'
  | 'bottlenecks_score'
  | 'active_participants'
  | 'active_trustlines'

/**
 * The value when the backend declares a unit for the series. The key itself is OPTIONAL:
 * the canon lists `MetricSeries.required: [key, points]` and pydantic gives it a default
 * (`unit: MetricUnit = None`), so a response without the key is contract-valid.
 */
export type MetricUnit = '%' | 'count' | 'amount' | null

/**
 * 2026-08-20 / T715: `v` is a decimal string and nullable.
 *
 * `null` means "no measurement at/before this timestamp"; a string means there was one, so a
 * measured zero arrives as `"0.00000000"` and stays distinguishable from `null`. Two of the seven
 * series (`total_debt`, `clearing_volume`) are money, so the value must never be parsed into a JS
 * number — that is the step where exactness is lost (AGENTS.md §8).
 */
export type MetricPoint = { t_ms: number; v: string | null }

// `unit?`, not `unit`: an absent key and an explicit `null` mean the same thing here — no unit
// declared — which is exactly the opposite of `MetricPoint.v`, where the two are different
// statements. The asymmetry is deliberate; see the decoder for both halves.
export type MetricSeries = { key: MetricSeriesKey; unit?: MetricUnit; points: MetricPoint[] }

export type MetricsResponse = {
  api_version: string
  run_id: string
  equivalent: string
  from_ms: number
  to_ms: number
  step_ms: number
  /** Seven keys are declared, but a run may legitimately carry fewer series: never index blindly. */
  series: MetricSeries[]
}

export type BottleneckReasonCode =
  | 'LOW_AVAILABLE'
  | 'HIGH_USED'
  | 'FREQUENT_ABORTS'
  | 'TOO_MANY_TIMEOUTS'
  | 'ROUTING_TOO_DEEP'
  | 'CLEARING_PRESSURE'

export type BottleneckTargetEdge = { kind: 'edge'; from: string; to: string }
export type BottleneckTargetNode = { kind: 'node'; id: string }
export type BottleneckTarget = BottleneckTargetEdge | BottleneckTargetNode

export type BottleneckItem = {
  target: BottleneckTarget
  score: number
  reason_code: BottleneckReasonCode
  label?: string | null
  suggested_action?: string | null
}

export type BottlenecksResponse = {
  api_version: string
  run_id: string
  equivalent: string
  items: BottleneckItem[]
}

export type ArtifactIndexItem = {
  name: string
  url: string
  content_type?: string
  size_bytes?: number
  updated_at?: string
}

export type ArtifactIndexResponse = {
  items: ArtifactIndexItem[]
}
