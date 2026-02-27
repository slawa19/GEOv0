import type { EdgePatch, GraphSnapshot, NodePatch, TxUpdatedEvent, ClearingDoneEvent } from '../types'

export type SimulatorMode = 'fixtures' | 'real'

export type ScenarioSummary = {
  scenario_id: string
  /** Human-friendly name (preferred over legacy `label`). */
  name?: string
  label?: string
  mode?: string
  created_at?: string
  updated_at?: string
}

export type ScenariosListResponse = {
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

export type RunState = 'created' | 'running' | 'paused' | 'stopped' | 'error'

export type RunError = {
  code: string
  message: string
  at?: string
}

export type RunStatus = {
  run_id: string
  scenario_id: string
  mode?: SimulatorMode
  state: RunState | string
  started_at?: string
  stopped_at?: string

  // Why/where stop was requested (best-effort).
  stop_requested_at?: string
  stop_source?: string | null
  stop_reason?: string | null
  stop_client?: string | null
  sim_time_ms: number
  intensity_percent: number
  ops_sec: number
  queue_depth: number
  last_event_type?: string | null
  current_phase?: string | null
  last_error?: RunError | null

  // Backend-first cumulative stats (authoritative; sent in every run_status event).
  attempts_total?: number
  committed_total?: number
  rejected_total?: number
  errors_total?: number
  timeouts_total?: number

  // Diagnostic: consecutive ticks where all planned payments were rejected (capacity stall).
  // Only present in SSE run_status events when > 0.
  consec_all_rejected_ticks?: number
}

export type RunStatusEvent = RunStatus & {
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

export type MetricsResponse = {
  api_version?: string
  equivalent: string
  points: Array<{ t_ms: number } & Record<string, number | null>>
}

export type BottleneckItem = {
  kind: string
  score: number
  from?: string
  to?: string
  details?: Record<string, unknown>
}

export type BottlenecksResponse = {
  api_version?: string
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
