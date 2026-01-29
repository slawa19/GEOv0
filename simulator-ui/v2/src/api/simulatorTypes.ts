import type { GraphSnapshot, TxUpdatedEvent, ClearingPlanEvent, ClearingDoneEvent } from '../types'

export type SimulatorMode = 'fixtures' | 'real'

export type ScenarioSummary = {
  scenario_id: string
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

export type RunState = 'created' | 'running' | 'paused' | 'stopped' | 'error'

export type RunError = {
  code: string
  message: string
  at?: string
}

export type RunStatus = {
  run_id: string
  scenario_id: string
  state: RunState | string
  sim_time_ms: number
  intensity_percent: number
  ops_sec: number
  queue_depth: number
  last_event_type?: string | null
  current_phase?: string | null
  last_error?: RunError | null
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

export type SimulatorEvent =
  | RunStatusEvent
  | (TxUpdatedEvent & { type: 'tx.updated' })
  | (ClearingPlanEvent & { type: 'clearing.plan' })
  | (ClearingDoneEvent & { type: 'clearing.done' })
  | TxFailedEvent
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
