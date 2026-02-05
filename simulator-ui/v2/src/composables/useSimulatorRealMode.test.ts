import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'

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
        eventsPath: '',
        snapshot: null,
        demoTxEvents: [],
        demoClearingPlan: null,
        demoClearingDone: null,
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
        eventsPath: '',
        snapshot: null,
        demoTxEvents: [],
        demoClearingPlan: null,
        demoClearingDone: null,
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

