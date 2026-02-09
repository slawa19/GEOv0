import { httpJson, httpUrl, type HttpConfig } from './http'
import type {
  ActiveRunResponse,
  ArtifactIndexResponse,
  BottlenecksResponse,
  ClearingOnceRequest,
  ClearingOnceResponse,
  MetricsResponse,
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

export function listScenarios(cfg: HttpConfig): Promise<ScenariosListResponse> {
  return httpJson(cfg, '/simulator/scenarios')
}

export function getScenario(cfg: HttpConfig, scenarioId: string): Promise<ScenarioSummary> {
  return httpJson(cfg, `/simulator/scenarios/${encodeURIComponent(scenarioId)}`)
}

export function getScenarioPreview(
  cfg: HttpConfig,
  scenarioId: string,
  equivalent: string,
  opts?: { mode?: SimulatorMode },
): Promise<SimulatorGraphSnapshot> {
  const q = new URLSearchParams({ equivalent, ...(opts?.mode ? { mode: opts.mode } : {}) }).toString()
  return httpJson(cfg, `/simulator/scenarios/${encodeURIComponent(scenarioId)}/graph/preview?${q}`)
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
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}`)
}

export function pauseRun(cfg: HttpConfig, runId: string): Promise<RunStatus> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/pause`, { method: 'POST' })
}

export function resumeRun(cfg: HttpConfig, runId: string): Promise<RunStatus> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/resume`, { method: 'POST' })
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
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/stop${suffix}`, { method: 'POST' })
}

export function restartRun(cfg: HttpConfig, runId: string): Promise<RunStatus> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/restart`, { method: 'POST' })
}

export function setIntensity(cfg: HttpConfig, runId: string, intensity_percent: number): Promise<RunStatus> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/intensity`, {
    method: 'POST',
    body: JSON.stringify({ intensity_percent }),
  })
}

export function getSnapshot(cfg: HttpConfig, runId: string, equivalent: string): Promise<SimulatorGraphSnapshot> {
  const q = new URLSearchParams({ equivalent }).toString()
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/graph/snapshot?${q}`)
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
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/metrics?${q}`)
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
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/bottlenecks?${q}`)
}

export function listArtifacts(cfg: HttpConfig, runId: string): Promise<ArtifactIndexResponse> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/artifacts`)
}

export function artifactDownloadUrl(cfg: HttpConfig, runId: string, name: string): string {
  // Note: browser downloads will include Authorization only if same-origin + cookies; we use proxy + same-origin.
  return httpUrl(cfg, `/simulator/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`)
}

export function actionTxOnce(cfg: HttpConfig, runId: string, req: TxOnceRequest): Promise<TxOnceResponse> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/actions/tx-once`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export function actionClearingOnce(
  cfg: HttpConfig,
  runId: string,
  req: ClearingOnceRequest,
): Promise<ClearingOnceResponse> {
  return httpJson(cfg, `/simulator/runs/${encodeURIComponent(runId)}/actions/clearing-once`, {
    method: 'POST',
    body: JSON.stringify(req),
  })
}
