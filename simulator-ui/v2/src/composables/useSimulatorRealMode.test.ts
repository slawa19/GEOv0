import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

vi.mock('../api/simulatorApi', () => {
  return {
    artifactDownloadUrl: () => 'http://artifact',
    createRun: vi.fn(async () => ({ run_id: 'r1' })),
    getRun: vi.fn(async () => ({
      run_id: 'r1',
      scenario_id: 'sc1',
      state: 'running',
      sim_time_ms: 0,
      intensity_percent: 0,
      ops_sec: 0,
      queue_depth: 0,
      last_event_type: null,
      current_phase: null,
      last_error: null,
    })),
    listArtifacts: vi.fn(async () => ({ items: [] })),
    listScenarios: vi.fn(async () => ({ items: [] })),
    pauseRun: vi.fn(async () => undefined),
    resumeRun: vi.fn(async () => undefined),
    setIntensity: vi.fn(async () => undefined),
    stopRun: vi.fn(async () => undefined),
  }
})

vi.mock('../api/sse', () => {
  return {
    connectSse: vi.fn(async (opts: any) => {
      // Simulate SSE replay: same event twice.
      const payload = {
        event_id: 'evt_tx_1',
        ts: '2026-01-01T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        from: 'A',
        to: 'B',
        amount: '1.00',
        ttl_ms: 1200,
        edges: [{ from: 'A', to: 'B' }],
      }

      opts.onMessage({ id: 'evt_tx_1', data: JSON.stringify(payload) })
      opts.onMessage({ id: 'evt_tx_1', data: JSON.stringify(payload) })

      // Keep the connection open until aborted.
      await new Promise<void>((resolve) => {
        if (opts?.signal?.aborted) return resolve()
        opts?.signal?.addEventListener?.('abort', () => resolve(), { once: true })
      })
    }),
  }
})

function createRealState(): RealModeState {
  return {
    apiBase: 'http://x',
    accessToken: '',
    loadingScenarios: false,
    scenarios: [],
    selectedScenarioId: '',
    desiredMode: 'real',
    intensityPercent: 0,
    runId: null,
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
  }
}

describe('useSimulatorRealMode - refreshSnapshot debounce regression', () => {
  it('stopSse cancels pending refreshSnapshot debounce so timer cannot trigger a second loadScene()', async () => {
    vi.useFakeTimers()

    const isRealModeRef = ref(true)
    const real = createRealState()

    const loadScene = vi.fn(async () => undefined)

    const h = useSimulatorRealMode({
      isRealMode: computed(() => isRealModeRef.value),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: {
        loading: false,
        error: '',
        sourcePath: '',
        snapshot: null,
        selectedNodeId: null,
        flash: 0,
      },
      real,

      ensureScenarioSelectionValid: () => undefined,
      resetRunStats: () => undefined,
      cleanupRealRunFxAndTimers: () => undefined,

      isUserFacingRunError: () => false,
      inc: () => undefined,

      loadScene,

      realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
      pushTxAmountLabel: () => undefined,
      clampRealTxTtlMs: () => 0,

      scheduleTimeout: () => undefined,
      runRealTxFx: () => undefined,
      runRealClearingPlanFx: () => undefined,
      runRealClearingDoneFx: () => undefined,

      clearingPlansById: new Map(),
      wakeUp: () => undefined,
    })

    // Enable refreshSnapshot guard conditions but avoid watcher-triggered refreshSnapshot.
    real.accessToken = 't'
    real.runId = 'r1'

    // 1st call performs loadScene immediately; 2nd call marks pending while debounce timer is active.
    const p1 = h.refreshSnapshot()
    const p2 = h.refreshSnapshot()
    await p1
    await p2

    expect(loadScene).toHaveBeenCalledTimes(1)

    // Regression: debounce timer must not be able to trigger a new refresh after SSE stop/teardown.
    h.stopSse()

    await vi.advanceTimersByTimeAsync(100)
    expect(loadScene).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })

  it('stale run context (runId changed) prevents pending debounce timer from triggering loadScene()', async () => {
    vi.useFakeTimers()

    const isRealModeRef = ref(true)
    const real = createRealState()

    const loadScene = vi.fn(async () => undefined)

    const h = useSimulatorRealMode({
      isRealMode: computed(() => isRealModeRef.value),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: {
        loading: false,
        error: '',
        sourcePath: '',
        snapshot: null,
        selectedNodeId: null,
        flash: 0,
      },
      real,

      ensureScenarioSelectionValid: () => undefined,
      resetRunStats: () => undefined,
      cleanupRealRunFxAndTimers: () => undefined,

      isUserFacingRunError: () => false,
      inc: () => undefined,

      loadScene,

      realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
      pushTxAmountLabel: () => undefined,
      clampRealTxTtlMs: () => 0,

      scheduleTimeout: () => undefined,
      runRealTxFx: () => undefined,
      runRealClearingPlanFx: () => undefined,
      runRealClearingDoneFx: () => undefined,

      clearingPlansById: new Map(),
      wakeUp: () => undefined,
    })

    real.accessToken = 't'
    real.runId = 'r1'

    const p1 = h.refreshSnapshot()
    h.refreshSnapshot() // mark pending while debounce timer is active
    await p1

    expect(loadScene).toHaveBeenCalledTimes(1)

    // Simulate restart / context switch before debounce callback fires.
    real.runId = 'r2'

    await vi.advanceTimersByTimeAsync(100)
    expect(loadScene).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })
})

describe('useSimulatorRealMode - SSE replay dedup', () => {
  it('drops duplicate events by event_id (prevents duplicate labels/FX)', async () => {
    const isRealModeRef = ref(true)
    const real = createRealState()

    real.apiBase = 'http://x'
    real.accessToken = 't'
    real.selectedScenarioId = 'sc1'

    const pushTxAmountLabel = vi.fn(() => undefined)
    const scheduleTimeout = vi.fn(() => undefined)
    const runRealTxFx = vi.fn(() => undefined)

    const h = useSimulatorRealMode({
      isRealMode: computed(() => isRealModeRef.value),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: {
        loading: false,
        error: '',
        sourcePath: '',
        snapshot: null,
        selectedNodeId: null,
        flash: 0,
      },
      real,

      ensureScenarioSelectionValid: () => undefined,
      resetRunStats: () => undefined,
      cleanupRealRunFxAndTimers: () => undefined,

      isUserFacingRunError: () => false,
      inc: () => undefined,

      loadScene: vi.fn(async () => undefined),

      realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
      pushTxAmountLabel,
      clampRealTxTtlMs: () => 1200,

      scheduleTimeout,
      runRealTxFx,
      runRealClearingPlanFx: () => undefined,
      runRealClearingDoneFx: () => undefined,

      clearingPlansById: new Map(),
      wakeUp: () => undefined,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    // connectSse mock will emit the same tx.updated twice.
    // Only the first should produce UI effects.
    expect(pushTxAmountLabel).toHaveBeenCalledTimes(1)
    expect(runRealTxFx).toHaveBeenCalledTimes(1)
    expect(scheduleTimeout).toHaveBeenCalledTimes(1)

    // Cleanup: abort SSE connection.
    h.stopSse()
  })
})

