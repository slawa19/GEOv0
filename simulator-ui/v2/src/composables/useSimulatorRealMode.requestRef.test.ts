/**
 * 034 S5a, review round 1, item 2: the message of a failed run-lifecycle call carries the server's request id.
 * `useSimulatorRealMode` had its own `getErrorMessage` that never looked at `ApiError.requestId`.
 */
import { computed, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { getRun, listScenarios } from '../api/simulatorApi'
import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

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
    desiredMode: 'real',
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
    state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null, flash: 0 },
    real,
    ensureScenarioSelectionValid: () => undefined,
    resetRunStats: () => undefined,
    cleanupRealRunFxAndTimers: () => undefined,
    isUserFacingRunError: () => false,
    inc: () => undefined,
    loadScene: async () => undefined,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
    pushTxAmountLabel: () => undefined,
    clampRealTxTtlMs: () => 0,
    scheduleTimeout: () => undefined,
    runRealTxFx: () => undefined,
    runRealClearingDoneFx: () => undefined,
    wakeUp: () => undefined,
  })
}

describe('item 2: real.lastError carries the request id of a failed lifecycle call', () => {
  it('a failed run status read: lastError ends with (ref: <id>)', async () => {
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500  for /simulator/runs/r1', { status: 500, requestId: 'req-run' }))
    const real = realState()

    await lifecycle(real).refreshRunStatus()

    expect(real.lastError).toContain('HTTP 500')
    expect(real.lastError).toContain('(ref: req-run)')
  })

  it('a failed scenario list: lastError ends with (ref: <id>)', async () => {
    vi.mocked(listScenarios).mockRejectedValueOnce(new ApiError('HTTP 502  for /simulator/scenarios', { status: 502, requestId: 'req-list' }))
    const real = realState()

    await lifecycle(real).refreshScenarios()

    expect(real.lastError).toContain('(ref: req-list)')
  })

  it('counter-check: an error without an id is shown as before', async () => {
    vi.mocked(getRun).mockRejectedValueOnce(new ApiError('HTTP 500  for /simulator/runs/r1', { status: 500 }))
    const real = realState()

    await lifecycle(real).refreshRunStatus()

    expect(real.lastError).toBe('HTTP 500  for /simulator/runs/r1')
  })
})
