import { httpJson, httpUrl, type HttpConfig } from './http'
import {
  decodeBottlenecksResponse,
  decodeClearingOnceResponse,
  decodeGraphSnapshotResponse,
  decodeMetricsResponse,
  decodeRunStatusResponse,
  decodeScenariosListResponse,
  decodeScenarioSummaryResponse,
  decodeSimulatorActionClearingRealResponse,
  decodeSimulatorActionParticipantsListResponse,
  decodeSimulatorActionPaymentRealResponse,
  decodeSimulatorActionTrustlineCloseResponse,
  decodeSimulatorActionTrustlineCreateResponse,
  decodeSimulatorActionTrustlineUpdateResponse,
  decodeSimulatorActionTrustlinesListResponse,
  decodeSimulatorPaymentTargetsResponse,
  decodeTxOnceResponse,
} from './simulatorContracts'
import type {
  ActiveRunResponse,
  ArtifactIndexResponse,
  BottlenecksResponse,
  ClearingOnceRequest,
  ClearingOnceResponse,
  MetricsResponse,
  SimulatorActionClearingRealRequest,
  SimulatorActionClearingRealResponse,
  SimulatorActionParticipantsListResponse,
  SimulatorActionPaymentRealRequest,
  SimulatorActionPaymentRealResponse,
  SimulatorActionTrustlineCloseRequest,
  SimulatorActionTrustlineCloseResponse,
  SimulatorActionTrustlineCreateRequest,
  SimulatorActionTrustlineCreateResponse,
  SimulatorActionTrustlineUpdateRequest,
  SimulatorActionTrustlineUpdateResponse,
  SimulatorActionTrustlinesListResponse,
  SimulatorPaymentTargetsResponse,
  RunCreateRequest,
  RunCreateResponse,
  RunStatus,
  ScenarioSummary,
  ScenariosListResponse,
  SimulatorGraphSnapshot,
  SimulatorMode,
  TxOnceRequest,
  TxOnceResponse,
} from './simulatorTypes'

async function simulatorContractJson<T>(
  cfg: HttpConfig,
  path: string,
  decoder: (value: unknown) => T,
  init?: RequestInit,
): Promise<T> {
  const value = await httpJson<unknown>(cfg, path, init)
  return decoder(value)
}

export function listScenarios(cfg: HttpConfig): Promise<ScenariosListResponse> {
  return simulatorContractJson(cfg, '/simulator/scenarios', decodeScenariosListResponse)
}

export function getScenario(cfg: HttpConfig, scenarioId: string): Promise<ScenarioSummary> {
  return simulatorContractJson(
    cfg,
    `/simulator/scenarios/${encodeURIComponent(scenarioId)}`,
    decodeScenarioSummaryResponse,
  )
}

export function getScenarioPreview(
  cfg: HttpConfig,
  scenarioId: string,
  equivalent: string,
  opts?: { mode?: SimulatorMode },
): Promise<SimulatorGraphSnapshot> {
  const q = new URLSearchParams({ equivalent, ...(opts?.mode ? { mode: opts.mode } : {}) }).toString()
  return simulatorContractJson(
    cfg,
    `/simulator/scenarios/${encodeURIComponent(scenarioId)}/graph/preview?${q}`,
    decodeGraphSnapshotResponse,
  )
}

export function uploadScenario(cfg: HttpConfig, req: { scenario: unknown; scenario_id?: string }): Promise<ScenarioSummary> {
  return httpJson(cfg, '/simulator/scenarios', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export function createRun(cfg: HttpConfig, req: RunCreateRequest): Promise<RunCreateResponse> {
  return httpJson(cfg, '/simulator/runs', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export function getActiveRun(cfg: HttpConfig): Promise<ActiveRunResponse> {
  return httpJson(cfg, '/simulator/runs/active')
}

export function getRun(cfg: HttpConfig, runId: string): Promise<RunStatus> {
  return simulatorContractJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}`, decodeRunStatusResponse)
}

export function pauseRun(cfg: HttpConfig, runId: string): Promise<RunStatus> {
  return simulatorContractJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/pause`, decodeRunStatusResponse, {
    method: 'POST',
  })
}

export function resumeRun(cfg: HttpConfig, runId: string): Promise<RunStatus> {
  return simulatorContractJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/resume`, decodeRunStatusResponse, {
    method: 'POST',
  })
}

export function stopRun(
  cfg: HttpConfig,
  runId: string,
  opts?: { source?: string; reason?: string },
): Promise<RunStatus> {
  const q = new URLSearchParams({
    ...(opts?.source ? { source: opts.source } : {}),
    ...(opts?.reason ? { reason: opts.reason } : {}),
  }).toString()
  const suffix = q ? `?${q}` : ''
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/stop${suffix}`,
    decodeRunStatusResponse,
    { method: 'POST' },
  )
}

export function setIntensity(cfg: HttpConfig, runId: string, intensity_percent: number): Promise<RunStatus> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/intensity`,
    decodeRunStatusResponse,
    {
      method: 'POST',
      body: JSON.stringify({ intensity_percent }),
    },
  )
}

export function getSnapshot(cfg: HttpConfig, runId: string, equivalent: string): Promise<SimulatorGraphSnapshot> {
  const q = new URLSearchParams({ equivalent }).toString()
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/graph/snapshot?${q}`,
    decodeGraphSnapshotResponse,
  )
}

export function getMetrics(
  cfg: HttpConfig,
  runId: string,
  equivalent: string,
  params: { from_ms: number; to_ms: number; step_ms: number },
): Promise<MetricsResponse> {
  const q = new URLSearchParams({
    equivalent,
    from_ms: String(params.from_ms),
    to_ms: String(params.to_ms),
    step_ms: String(params.step_ms),
  }).toString()
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/metrics?${q}`,
    decodeMetricsResponse,
  )
}

export function getBottlenecks(
  cfg: HttpConfig,
  runId: string,
  equivalent: string,
  params: { limit?: number; min_score?: number },
): Promise<BottlenecksResponse> {
  const q = new URLSearchParams({
    equivalent,
    ...(params.limit != null ? { limit: String(params.limit) } : {}),
    ...(params.min_score != null ? { min_score: String(params.min_score) } : {}),
  }).toString()
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/bottlenecks?${q}`,
    decodeBottlenecksResponse,
  )
}

export function listArtifacts(cfg: HttpConfig, runId: string): Promise<ArtifactIndexResponse> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/artifacts`)
}

export function artifactDownloadUrl(cfg: HttpConfig, runId: string, name: string): string {
  // Note: browser downloads will include Authorization only if same-origin + cookies; we use proxy + same-origin.
  return httpUrl(cfg, `/simulator/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`)
}

export function actionTxOnce(cfg: HttpConfig, runId: string, req: TxOnceRequest): Promise<TxOnceResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/tx-once`,
    decodeTxOnceResponse,
    {
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

export function actionClearingOnce(
  cfg: HttpConfig,
  runId: string,
  req: ClearingOnceRequest,
): Promise<ClearingOnceResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/clearing-once`,
    decodeClearingOnceResponse,
    {
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

// ============================
// Interact Mode (Actions API)
// ============================

export function actionTrustlineCreate(
  cfg: HttpConfig,
  runId: string,
  req: SimulatorActionTrustlineCreateRequest,
  init?: RequestInit,
): Promise<SimulatorActionTrustlineCreateResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/trustline-create`,
    decodeSimulatorActionTrustlineCreateResponse,
    {
      ...(init ?? {}),
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

export function actionTrustlineUpdate(
  cfg: HttpConfig,
  runId: string,
  req: SimulatorActionTrustlineUpdateRequest,
  init?: RequestInit,
): Promise<SimulatorActionTrustlineUpdateResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/trustline-update`,
    decodeSimulatorActionTrustlineUpdateResponse,
    {
      ...(init ?? {}),
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

export function actionTrustlineClose(
  cfg: HttpConfig,
  runId: string,
  req: SimulatorActionTrustlineCloseRequest,
  init?: RequestInit,
): Promise<SimulatorActionTrustlineCloseResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/trustline-close`,
    decodeSimulatorActionTrustlineCloseResponse,
    {
      ...(init ?? {}),
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

export function actionPaymentReal(
  cfg: HttpConfig,
  runId: string,
  req: SimulatorActionPaymentRealRequest,
  init?: RequestInit,
): Promise<SimulatorActionPaymentRealResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/payment-real`,
    decodeSimulatorActionPaymentRealResponse,
    {
      ...(init ?? {}),
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

export function actionClearingReal(
  cfg: HttpConfig,
  runId: string,
  req: SimulatorActionClearingRealRequest,
  init?: RequestInit,
): Promise<SimulatorActionClearingRealResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/clearing-real`,
    decodeSimulatorActionClearingRealResponse,
    {
      ...(init ?? {}),
      method: 'POST',
      body: JSON.stringify(req),
    },
  )
}

export function getParticipantsList(cfg: HttpConfig, runId: string): Promise<SimulatorActionParticipantsListResponse> {
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/participants-list`,
    decodeSimulatorActionParticipantsListResponse,
  )
}

export function getTrustlinesList(
  cfg: HttpConfig,
  runId: string,
  equivalent: string,
  participantPid?: string,
): Promise<SimulatorActionTrustlinesListResponse> {
  const q = new URLSearchParams({
    equivalent,
    ...(participantPid ? { participant_pid: participantPid } : {}),
  }).toString()
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/actions/trustlines-list?${q}`,
    decodeSimulatorActionTrustlinesListResponse,
  )
}

// ============================
// Phase 2.5: backend-first payment targets (reachability)
// ============================

export function getPaymentTargets(
  cfg: HttpConfig,
  runId: string,
  equivalent: string,
  fromPid: string,
  opts?: { maxHops?: number },
): Promise<SimulatorPaymentTargetsResponse> {
  const q = new URLSearchParams({
    equivalent,
    from_pid: fromPid,
    ...(opts?.maxHops != null ? { max_hops: String(opts.maxHops) } : {}),
  }).toString()
  return simulatorContractJson(
    cfg,
    `/simulator/runs/${encodeURIComponent(runId)}/payment-targets?${q}`,
    decodeSimulatorPaymentTargetsResponse,
  )
}

// ============================
// Session bootstrap (§10 — anonymous/cookie auth)
// ============================

export type SessionEnsureResponse = {
  actor_kind: string
  owner_id: string
}

/**
 * Ensures a cookie session exists (creates or validates `geo_sim_sid` cookie).
 * Call on startup when no accessToken is present (anonymous visitors workflow).
 * Uses credentials: 'include' via the underlying httpJson implementation.
 */
export function ensureSession(cfg: HttpConfig): Promise<SessionEnsureResponse> {
  return httpJson(cfg, '/simulator/session/ensure', { method: 'POST' })
}

// ============================
// Admin endpoints (require admin token)
// ============================

export type AdminRunSummary = {
  run_id: string
  owner_id: string
  owner_kind: string
  state: string
  scenario_id?: string | null
  created_at?: string | null
}

export type AdminRunsListResponse = {
  items: AdminRunSummary[]
}

/**
 * Returns all active/recent runs across all owners (admin only).
 * Requires X-Admin-Token or equivalent admin auth header.
 */
export function adminGetAllRuns(cfg: HttpConfig): Promise<AdminRunsListResponse> {
  return httpJson(cfg, '/simulator/admin/runs')
}

/**
 * Stops all currently running simulator runs (admin only).
 * Returns count of stopped runs.
 */
export function adminStopAllRuns(cfg: HttpConfig): Promise<{ stopped: number }> {
  return httpJson(cfg, '/simulator/admin/runs/stop-all', { method: 'POST' })
}
