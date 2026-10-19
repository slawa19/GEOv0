/**
 * 034 S5a, review round 1, item 2: the message of a failed run-lifecycle call carries the server's request id.
 * `useSimulatorRealMode` had its own `getErrorMessage` that never looked at `ApiError.requestId`.
 *
 * 048 S3 (decision 3), rewritten - not removed: the id is no longer glued into the line as «(ref: …)»; it is carried apart
 * by the line's value (`runErrorLineRef`, shown as the line's own second line and `data-request-id`), and the line is a
 * sentence of the dictionary with no «HTTP»; the transport's text is the detail (`runErrorLineLog`).
 */
import { computed, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { getRun, listScenarios } from '../api/simulatorApi'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'
import { t } from '../i18n/t'
import { runErrorLineRef, runErrorLineLog } from '../utils/runErrorClassification'

vi.mock('../api/simulatorApi', () => ({
  artifactDownloadUrl: () => 'http://artifact',
  createRun: vi.fn(),
  getActiveRun: vi.fn(async () => ({ run_id: null })),
  getRun: vi.fn(),
  listArtifacts: vi.fn(async () => ({ items: [] })),
  listScenarios: vi.fn(),
  pauseRun: vi.fn(),
  resumeRun: vi.fn(),
  setIntensity: vi.fn(),
  stopRun: vi.fn(),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({ connectSse: vi.fn(async () => undefined) }))

function realState(): RealModeState {
  return reactive<RealModeState>({
    apiBase: 'http://x',
    accessToken: '',
    loadingScenarios: false,
    scenarios: [],
    selectedScenarioId: '',
    intensityPercent: 0,
    runId: 'r1',
    runStatus: null,
    sseState: 'idle',
    lastEventId: null,
    lastError: '',
    artifacts: [],
    artifactsLoading: false,
    runStats: {
      startedAtMs: 0,
      attempts: 0,
      committed: 0,
      rejected: 0,
      errors: 0,
      timeouts: 0,
      rejectedByCode: {},
      errorsByCode: {},
    },
  })
}

function lifecycle(real: RealModeState) {
  return useSimulatorRealMode({
    isRealMode: computed(() => false),
    isLocalhost: false,
    effectiveEq: computed(() => 'EUR'),
    state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null },
    real,
    ensureScenarioSelectionValid: () => undefined,
    resetRunStats: () => undefined,
    cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false,
    inc: () => undefined,
    loadScene: async () => undefined,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined, applyNodeStatus: () => undefined, applyEdgeStatus: () => undefined },
    pushTxAmountLabel: () => undefined,
    routeArrivalMs: () => 0,
    scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined,
    runRealClearingDoneFx: () => undefined,
    wakeUp: () => undefined,
  })
}

describe('item 2: the error line carries the request id of a failed lifecycle call - apart from the sentence (048 S3)', () => {
  it('a failed run status read: the sentence, the id apart, the status in the detail', async () => {
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500  for /simulator/runs/r1', { status: 500, requestId: 'req-run' }))
    const real = realState()

    await lifecycle(real).refreshRunStatus()

    expect(real.lastError).toBe(t('failure.serverFailed'))
    expect(real.lastError).not.toMatch(/HTTP|req-run|ref:/)
    expect(runErrorLineRef(real.lastErrorLine)).toBe('req-run')
    expect(runErrorLineLog(real.lastErrorLine)).toContain('HTTP 500')
  })

  it('a failed scenario list: the id apart', async () => {
    vi.mocked(listScenarios).mockRejectedValueOnce(new ApiError('HTTP 502  for /simulator/scenarios', { status: 502, requestId: 'req-list' }))
    const real = realState()

    await lifecycle(real).refreshScenarios()

    expect(real.lastError).not.toContain('req-list')
    expect(runErrorLineRef(real.lastErrorLine)).toBe('req-list')
  })

  it('counter-check: an error without an id - the same sentence, no id', async () => {
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500  for /simulator/runs/r1', { status: 500 }))
    const real = realState()

    await lifecycle(real).refreshRunStatus()

    expect(real.lastError).toBe(t('failure.serverFailed'))
    expect(runErrorLineRef(real.lastErrorLine)).toBeNull()
    expect(runErrorLineLog(real.lastErrorLine)).toBe('HTTP 500  for /simulator/runs/r1')
  })
})

/**
 * 048 S3, §15 Codex P2 (orchestrator 2026-10-19): a 403 clears the saved run ONLY during the page opening. Mid-session (a
 * status read after the stream ended, an Attach) the backend's 403 has one code for three causes (E006: not the run's
 * owner, an inactive participant, a wrong admin token - `app/utils/exceptions.py:64-66`), so it is not proof that the run
 * is gone: the run is kept and the failure is shown on the run's line. Red before (839eac9b cleared on any 403).
 */
describe('048 S3: a 403 on a status read mid-session keeps the run', () => {
  it('mid-session 403: the run id and its state stay, the failure is on the run line with its request id', async () => {
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 403 Forbidden for /simulator/runs/r1', { status: 403, requestId: 'req-403-mid' }))
    const real = realState()
    real.runStatus = { run_id: 'r1', scenario_id: 'sc', state: 'running' } as RealModeState['runStatus']

    await lifecycle(real).refreshRunStatus()

    expect(real.runId, 'not cleared').toBe('r1')
    expect(real.runStatus?.state, 'its state stays').toBe('running')
    expect(real.lastError).toBe(t('failure.forbidden'))
    expect(runErrorLineRef(real.lastErrorLine)).toBe('req-403-mid')
  })

  it('counter-check: a 404 mid-session still clears the run (unchanged)', async () => {
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 404 Not Found for /simulator/runs/r1', { status: 404 }))
    const real = realState()

    await lifecycle(real).refreshRunStatus()

    expect(real.runId).toBeNull()
  })
})
