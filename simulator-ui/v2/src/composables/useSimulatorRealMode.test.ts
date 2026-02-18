import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'
import { ApiError } from '../api/http'
import { connectSse } from '../api/sse'
import { createRun, getActiveRun } from '../api/simulatorApi'

vi.mock('../api/simulatorApi', () => {
  return {
    artifactDownloadUrl: () => 'http://artifact',
    createRun: vi.fn(async () => ({ run_id: 'r1' })),
    getActiveRun: vi.fn(async () => ({ run_id: 'r_active' })),
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
      runRealClearingDoneFx: () => undefined,
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
      runRealClearingDoneFx: () => undefined,
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

describe('useSimulatorRealMode - startRun conflict attach', () => {
  it('attaches to active run when createRun returns HTTP 409', async () => {
    const createRunMock = vi.mocked(createRun)
    const getActiveRunMock = vi.mocked(getActiveRun)

    createRunMock.mockImplementationOnce(async () => {
      throw new ApiError('HTTP 409 Conflict for /simulator/runs', { status: 409 })
    })
    // useSimulatorRealMode may call getActiveRun during boot discovery and again
    // when handling the 409 attach-to-active flow. Keep it stable for this test.
    getActiveRunMock.mockResolvedValue({ run_id: 'r_attached' } as any)

    const real = createRealState()
    real.apiBase = 'http://x'
    real.accessToken = 't'
    real.selectedScenarioId = 'sc1'

    const loadScene = vi.fn(async () => undefined)

    const h = useSimulatorRealMode({
      isRealMode: computed(() => true),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null, flash: 0 },
      real,

      ensureScenarioSelectionValid: () => undefined,
      resetRunStats: () => undefined,
      cleanupRealRunFxAndTimers: () => undefined,

      isUserFacingRunError: () => false,
      inc: () => undefined,

      loadScene,
      realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
      pushTxAmountLabel: () => undefined,
      clampRealTxTtlMs: () => 1200,
      scheduleTimeout: () => undefined,
      runRealTxFx: () => undefined,
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    expect(real.runId).toBe('r_attached')
    expect(loadScene).toHaveBeenCalled()

    h.stopSse()
  })
})

describe('useSimulatorRealMode - receiver label guards', () => {
  it('receiver label not emitted when amount is empty', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: any) => {
      const payload = {
        event_id: 'evt_no_amount',
        ts: '2026-01-01T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        from: 'A',
        to: 'B',
        amount: '', // empty amount
        ttl_ms: 1200,
        edges: [{ from: 'A', to: 'B' }],
      }
      opts.onMessage({ id: payload.event_id, data: JSON.stringify(payload) })
      await new Promise<void>((resolve) => {
        if (opts?.signal?.aborted) return resolve()
        opts?.signal?.addEventListener?.('abort', () => resolve(), { once: true })
      })
    })

    const real = createRealState()
    real.apiBase = 'http://x'
    real.accessToken = 't'
    real.selectedScenarioId = 'sc1'

    const pushTxAmountLabel = vi.fn()
    const scheduleTimeout = vi.fn()

    const h = useSimulatorRealMode({
      isRealMode: computed(() => true),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null, flash: 0 },
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
      runRealTxFx: vi.fn(),
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    // No amount → no sender label, no receiver label scheduled.
    expect(pushTxAmountLabel).toHaveBeenCalledTimes(0)
    expect(scheduleTimeout).toHaveBeenCalledTimes(0)

    h.stopSse()
    connectSseMock.mockImplementation(prevImpl as any)
  })

  it('receiver label not emitted for self-payment (from === to)', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: any) => {
      const payload = {
        event_id: 'evt_self_pay',
        ts: '2026-01-01T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        from: 'A',
        to: 'A', // self-payment
        amount: '5.00',
        ttl_ms: 1200,
        edges: [{ from: 'A', to: 'A' }],
      }
      opts.onMessage({ id: payload.event_id, data: JSON.stringify(payload) })
      await new Promise<void>((resolve) => {
        if (opts?.signal?.aborted) return resolve()
        opts?.signal?.addEventListener?.('abort', () => resolve(), { once: true })
      })
    })

    const real = createRealState()
    real.apiBase = 'http://x'
    real.accessToken = 't'
    real.selectedScenarioId = 'sc1'

    const pushTxAmountLabel = vi.fn()
    const scheduleTimeout = vi.fn()

    const h = useSimulatorRealMode({
      isRealMode: computed(() => true),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null, flash: 0 },
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
      runRealTxFx: vi.fn(),
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    // Self-payment: sender label pushed but NO receiver label scheduled.
    expect(pushTxAmountLabel).toHaveBeenCalledTimes(1)
    expect(pushTxAmountLabel.mock.calls[0]?.[0]).toBe('A')
    expect(pushTxAmountLabel.mock.calls[0]?.[1]).toBe('-5.00')
    expect(scheduleTimeout).toHaveBeenCalledTimes(0)

    h.stopSse()
    connectSseMock.mockImplementation(prevImpl as any)
  })

  it('receiver label uses resolveTxDirection when from/to missing but edges present', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: any) => {
      const payload = {
        event_id: 'evt_edges_only',
        ts: '2026-01-01T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        // from/to MISSING — must infer from edges
        amount: '3.00',
        ttl_ms: 1200,
        edges: [
          { from: 'X', to: 'Y' },
          { from: 'Y', to: 'Z' },
        ],
      }
      opts.onMessage({ id: payload.event_id, data: JSON.stringify(payload) })
      await new Promise<void>((resolve) => {
        if (opts?.signal?.aborted) return resolve()
        opts?.signal?.addEventListener?.('abort', () => resolve(), { once: true })
      })
    })

    const real = createRealState()
    real.apiBase = 'http://x'
    real.accessToken = 't'
    real.selectedScenarioId = 'sc1'

    const pushTxAmountLabel = vi.fn()
    const scheduleTimeout = vi.fn()

    const h = useSimulatorRealMode({
      isRealMode: computed(() => true),
      isLocalhost: false,
      effectiveEq: computed(() => 'EUR'),
      state: { loading: false, error: '', sourcePath: '', snapshot: null, selectedNodeId: null, flash: 0 },
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
      runRealTxFx: vi.fn(),
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    // Sender label: inferred from edges[0].from = 'X'.
    expect(pushTxAmountLabel).toHaveBeenCalledTimes(1)
    expect(pushTxAmountLabel.mock.calls[0]?.[0]).toBe('X')
    expect(pushTxAmountLabel.mock.calls[0]?.[1]).toBe('-3.00')

    // Receiver label: inferred from edges[-1].to = 'Z', scheduled via timeout.
    expect(scheduleTimeout).toHaveBeenCalledTimes(1)

    h.stopSse()
    connectSseMock.mockImplementation(prevImpl as any)
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
      runRealClearingDoneFx: () => undefined,
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

  it('amount_flyout=false suppresses amount labels but keeps tx FX', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: any) => {
      const payload = {
        event_id: 'evt_tx_2',
        ts: '2026-01-01T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        from: 'A',
        to: 'B',
        amount: '1.00',
        amount_flyout: false,
        ttl_ms: 1200,
        edges: [{ from: 'A', to: 'B' }],
      }

      opts.onMessage({ id: 'evt_tx_2', data: JSON.stringify(payload) })

      await new Promise<void>((resolve) => {
        if (opts?.signal?.aborted) return resolve()
        opts?.signal?.addEventListener?.('abort', () => resolve(), { once: true })
      })
    })

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
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    expect(runRealTxFx).toHaveBeenCalledTimes(1)
    expect(pushTxAmountLabel).toHaveBeenCalledTimes(0)
    expect(scheduleTimeout).toHaveBeenCalledTimes(0)

    h.stopSse()

    // Restore default mock for other tests.
    connectSseMock.mockImplementation(prevImpl as any)
  })
})

