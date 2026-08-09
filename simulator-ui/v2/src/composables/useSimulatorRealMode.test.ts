import { computed, effectScope, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'
import { ApiError } from '../api/http'
import { connectSse, type SseConnectOpts } from '../api/sse'
import { createRun, getActiveRun, getRun, stopRun } from '../api/simulatorApi'
import type { ActiveRunResponse } from '../api/simulatorTypes'

function waitForAbort(signal: AbortSignal | undefined): Promise<void> {
  return new Promise<void>((resolve) => {
    if (signal?.aborted) return resolve()
    signal?.addEventListener('abort', () => resolve(), { once: true })
  })
}

function emitSsePayload(opts: SseConnectOpts, payload: { event_id: string } & Record<string, unknown>) {
  opts.onMessage({ id: payload.event_id, data: JSON.stringify(payload) })
}

function restoreConnectSseImplementation(prevImpl: typeof connectSse | undefined) {
  if (!prevImpl) throw new Error('expected connectSse mock implementation')
  vi.mocked(connectSse).mockImplementation(prevImpl)
}

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
    connectSse: vi.fn(async (opts: SseConnectOpts) => {
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

      emitSsePayload(opts, payload)
      emitSsePayload(opts, payload)

      // Keep the connection open until aborted.
      await waitForAbort(opts.signal)
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

function deferred() {
  let resolve!: () => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function createSseCharacterizationHarness(opts?: {
  lastEventId?: string | null
  loadScene?: () => Promise<void>
}) {
  const isRealModeRef = ref(false)
  const real = createRealState()
  real.apiBase = 'http://x'
  real.accessToken = 't'
  real.selectedScenarioId = 'sc1'
  real.runId = 'r1'
  real.lastEventId = opts?.lastEventId ?? null

  const resetRunStats = vi.fn(() => {
    real.runStats.attempts = 0
    real.runStats.committed = 0
    real.runStats.rejected = 0
    real.runStats.errors = 0
    real.runStats.timeouts = 0
    real.runStats.rejectedByCode = {}
    real.runStats.errorsByCode = {}
  })
  const cleanupRealRunFxAndTimers = vi.fn<() => void>(() => undefined)
  const loadScene = vi.fn(opts?.loadScene ?? (async () => undefined))
  const pushTxAmountLabel = vi.fn(() => undefined)
  const scheduleTimeout = vi.fn((_fn: () => void) => undefined)
  const runRealTxFx = vi.fn(() => undefined)
  const onAnySseEvent = vi.fn(() => undefined)

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
    resetRunStats,
    cleanupRealRunFxAndTimers,

    isUserFacingRunError: () => false,
    inc: () => undefined,

    loadScene,
    realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
    pushTxAmountLabel,
    clampRealTxTtlMs: () => 1200,

    scheduleTimeout,
    runRealTxFx,
    runRealClearingDoneFx: () => undefined,
    wakeUp: () => undefined,
    onAnySseEvent,
  })

  return {
    h,
    isRealModeRef,
    real,
    resetRunStats,
    cleanupRealRunFxAndTimers,
    loadScene,
    pushTxAmountLabel,
    scheduleTimeout,
    runRealTxFx,
    onAnySseEvent,
  }
}

describe('useSimulatorRealMode - refreshSnapshot debounce regression', () => {
  it('stopSse cancels pending refreshSnapshot debounce so timer cannot trigger a second loadScene()', async () => {
    vi.useFakeTimers()

    const isRealModeRef = ref(true)
    // §10: Pre-set runId so the immediate watcher does not call getActiveRun and trigger an
    // extra loadScene() before the test body runs. Anonymous visitors use cookie-auth, so
    // getActiveRun is now called even without accessToken — the test must account for this.
    const real = { ...createRealState(), runId: 'r1' as string | null, accessToken: 't' }

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

    // Wait for the immediate watcher (isRealMode) to complete its async boot sequence.
    // It calls refreshRunStatus() + refreshSnapshot() because real.runId is already set.
    // We need to drain those calls before the test body runs.
    await vi.runAllTimersAsync()
    loadScene.mockClear()

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

describe('useSimulatorRealMode - admin helpers', () => {
  it('attachToRun trims runId and no-ops on blank', async () => {
    const isRealModeRef = ref(false)
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

    vi.mocked(getRun).mockClear()
    await h.attachToRun('   ')
    expect(real.runId).toBeNull()
    expect(getRun).toHaveBeenCalledTimes(0)
    expect(loadScene).toHaveBeenCalledTimes(0)

    await h.attachToRun('  r1  ')
    expect(real.runId).toBe('r1')
    expect(getRun).toHaveBeenCalledTimes(1)
    expect(loadScene).toHaveBeenCalledTimes(0) // isRealMode=false -> refreshSnapshot no-op
  })

  it('stopRunById trims runId and no-ops on blank', async () => {
    const isRealModeRef = ref(false)
    const real = createRealState()

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

      loadScene: async () => undefined,
      realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
      pushTxAmountLabel: () => undefined,
      clampRealTxTtlMs: () => 0,

      scheduleTimeout: () => undefined,
      runRealTxFx: () => undefined,
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
    })

    vi.mocked(stopRun).mockClear()

    await h.stopRunById('   ')
    expect(stopRun).toHaveBeenCalledTimes(0)

    await h.stopRunById('  r2  ')
    expect(stopRun).toHaveBeenCalledTimes(1)

    // Verify the trimmed id is used.
    const args = vi.mocked(stopRun).mock.calls[0]
    expect(args?.[1]).toBe('r2')
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
    const activeRun: ActiveRunResponse = { run_id: 'r_attached' }
    getActiveRunMock.mockResolvedValue(activeRun)

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
    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
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
      emitSsePayload(opts, payload)
      await waitForAbort(opts.signal)
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
    restoreConnectSseImplementation(prevImpl)
  })

  it('receiver label not emitted for self-payment (from === to)', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
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
      emitSsePayload(opts, payload)
      await waitForAbort(opts.signal)
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
    restoreConnectSseImplementation(prevImpl)
  })

  it('receiver label uses resolveTxDirection when from/to missing but edges present', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
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
      emitSsePayload(opts, payload)
      await waitForAbort(opts.signal)
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
    restoreConnectSseImplementation(prevImpl)
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
    expect(real.lastEventId).toBe('evt_tx_1')
    expect(real.runStats).toMatchObject({ attempts: 1, committed: 1 })

    // Cleanup: abort SSE connection.
    h.stopSse()
  })

  it('does not mutate cursor, dedup, state or effects for rejected SSE input', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      emitSsePayload(opts, {
        event_id: 'evt_unknown',
        ts: '2026-01-01T00:00:00Z',
        type: 'future.event',
      })
      emitSsePayload(opts, {
        event_id: 'evt_malformed',
        ts: '2026-01-01T00:00:01Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        edges: [{ from: 'A' }],
      })
      opts.onMessage({
        id: 'frame_id_does_not_match',
        data: JSON.stringify({
          event_id: 'evt_valid_payload',
          ts: '2026-01-01T00:00:02Z',
          type: 'tx.updated',
          equivalent: 'EUR',
          from: 'A',
          to: 'B',
          amount: '1.00',
          ttl_ms: 1200,
          edges: [{ from: 'A', to: 'B' }],
        }),
      })
      emitSsePayload(opts, {
        event_id: 'evt_wrong_run',
        ts: '2026-01-01T00:00:03Z',
        type: 'run_status',
        run_id: 'r_other',
        scenario_id: 'sc1',
        state: 'running',
        attempts_total: 99,
        committed_total: 99,
      })
      emitSsePayload(opts, {
        event_id: 'evt_wrong_equivalent',
        ts: '2026-01-01T00:00:04Z',
        type: 'tx.updated',
        equivalent: 'UAH',
        from: 'A',
        to: 'B',
        amount: '5.00',
        edges: [{ from: 'A', to: 'B' }],
        node_patch: [{ id: 'A', net_balance: '5.00' }],
      })
      for (let index = 0; index < 64; index += 1) {
        emitSsePayload(opts, {
          event_id: `evt_hostile_${index}`,
          ts: '2026-01-01T00:00:05Z',
          type: `hostile.${index}.${'x'.repeat(index)}`,
        })
      }
      await waitForAbort(opts.signal)
    })

    const real = createRealState()
    real.apiBase = 'http://x'
    real.accessToken = 't'
    real.selectedScenarioId = 'sc1'

    const applyNodePatches = vi.fn(() => undefined)
    const applyEdgePatches = vi.fn(() => undefined)
    const pushTxAmountLabel = vi.fn(() => undefined)
    const scheduleTimeout = vi.fn(() => undefined)
    const runRealTxFx = vi.fn(() => undefined)
    const runRealClearingDoneFx = vi.fn(() => undefined)
    const onAnySseEvent = vi.fn(() => undefined)

    const h = useSimulatorRealMode({
      isRealMode: computed(() => true),
      isLocalhost: true,
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

      realPatchApplier: { applyNodePatches, applyEdgePatches },
      pushTxAmountLabel,
      clampRealTxTtlMs: () => 1200,

      scheduleTimeout,
      runRealTxFx,
      runRealClearingDoneFx,
      wakeUp: () => undefined,
      onAnySseEvent,
    })

    await h.startRun({ mode: 'real', intensityPercent: 0 })

    expect(real.lastEventId).toBeNull()
    expect(real.runStatus?.run_id).not.toBe('r_other')
    expect(real.runStats).toMatchObject({ attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0 })
    expect(applyNodePatches).not.toHaveBeenCalled()
    expect(applyEdgePatches).not.toHaveBeenCalled()
    expect(pushTxAmountLabel).not.toHaveBeenCalled()
    expect(scheduleTimeout).not.toHaveBeenCalled()
    expect(runRealTxFx).not.toHaveBeenCalled()
    expect(runRealClearingDoneFx).not.toHaveBeenCalled()
    expect(onAnySseEvent).not.toHaveBeenCalled()

    const diag = (
      globalThis as typeof globalThis & {
        __geo_real_mode_diag?: {
          normalize_dropped: number
          frame_id_mismatches: number
          rejected_by_reason: Record<string, number>
        }
      }
    ).__geo_real_mode_diag
    expect(diag?.normalize_dropped).toBeGreaterThanOrEqual(69)
    expect(diag?.frame_id_mismatches).toBeGreaterThanOrEqual(1)
    const rejectedByReason = diag?.rejected_by_reason ?? {}
    expect(Object.keys(rejectedByReason)).toEqual(
      expect.arrayContaining([
        'unknown:other:unknown_event_type',
        'malformed:tx.updated:invalid_tx_updated_collection',
        'malformed:tx.updated:frame_id_mismatch',
        'context:run_status:run_id_mismatch',
        'context:tx.updated:equivalent_mismatch',
      ]),
    )
    expect(rejectedByReason['unknown:other:unknown_event_type']).toBeGreaterThanOrEqual(65)
    expect(Object.keys(rejectedByReason)).toHaveLength(5)
    expect(Object.keys(rejectedByReason).some((key) => key.includes('hostile.'))).toBe(false)

    h.stopSse()
    restoreConnectSseImplementation(prevImpl)
  })

  it('amount_flyout=false suppresses amount labels but keeps tx FX', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
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

      emitSsePayload(opts, payload)
      await waitForAbort(opts.signal)
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
    restoreConnectSseImplementation(prevImpl)
  })
})

describe('useSimulatorRealMode - SSE reconnect characterization', () => {
  it('advances the cursor for an accepted event and passes it to the reconnect', async () => {
    vi.useFakeTimers()
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    const secondConnection = deferred()
    const seenCursors: Array<string | null | undefined> = []

    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      seenCursors.push(opts.lastEventId)
      if (seenCursors.length === 1) {
        emitSsePayload(opts, {
          event_id: 'evt_cursor_1',
          ts: '2026-01-01T00:00:00Z',
          type: 'tx.updated',
          equivalent: 'EUR',
          from: 'A',
          to: 'B',
          amount: '1.00',
          amount_flyout: false,
          ttl_ms: 1200,
          edges: [{ from: 'A', to: 'B' }],
        })
        return
      }
      secondConnection.resolve()
      await waitForAbort(opts.signal)
    })

    const harness = createSseCharacterizationHarness()
    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await vi.advanceTimersByTimeAsync(1000)
      await secondConnection.promise

      expect(seenCursors).toEqual([null, 'evt_cursor_1'])
      expect(harness.real.lastEventId).toBe('evt_cursor_1')
      expect(harness.onAnySseEvent).toHaveBeenCalledTimes(1)
      expect(harness.runRealTxFx).toHaveBeenCalledTimes(1)
      expect(harness.real.runStats).toMatchObject({ attempts: 1, committed: 1 })
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevImpl)
      randomSpy.mockRestore()
      vi.useRealTimers()
    }
  })

  it('SSE 404 clears stale run context and guards already-scheduled receiver work', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevImpl = connectSseMock.getMockImplementation()
    const staleReset = deferred()

    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      emitSsePayload(opts, {
        event_id: 'evt_before_404',
        ts: '2026-01-01T00:00:00Z',
        type: 'tx.updated',
        equivalent: 'EUR',
        from: 'A',
        to: 'B',
        amount: '2.00',
        ttl_ms: 1200,
        edges: [{ from: 'A', to: 'B' }],
      })
      throw new Error('SSE HTTP 404 Not Found')
    })

    const harness = createSseCharacterizationHarness({ lastEventId: 'evt_previous' })
    harness.real.artifacts = [{ name: 'stale.json', url: '/stale.json' }]
    harness.real.runStats.attempts = 9
    harness.real.runStats.committed = 8
    harness.real.runStats.rejected = 1
    harness.cleanupRealRunFxAndTimers.mockImplementation(() => staleReset.resolve())

    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await staleReset.promise

      expect(harness.real.runId).toBeNull()
      expect(harness.real.runStatus).toBeNull()
      expect(harness.real.lastEventId).toBeNull()
      expect(harness.real.artifacts).toEqual([])
      expect(harness.real.sseState).toBe('idle')
      expect(harness.real.lastError).toBe('')
      expect(harness.resetRunStats).toHaveBeenCalledTimes(1)
      expect(harness.cleanupRealRunFxAndTimers).toHaveBeenCalledTimes(1)
      expect(harness.real.runStats).toMatchObject({ attempts: 0, committed: 0, rejected: 0 })
      expect(harness.scheduleTimeout).toHaveBeenCalledTimes(1)

      const delayedReceiver = harness.scheduleTimeout.mock.calls[0]?.[0]
      expect(delayedReceiver).toBeTypeOf('function')
      expect(harness.pushTxAmountLabel).toHaveBeenCalledTimes(1)
      delayedReceiver?.()
      expect(harness.pushTxAmountLabel).toHaveBeenCalledTimes(1)
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevImpl)
    }
  })

  it('SSE EOF followed by a status 404 preserves the stale reset idle state', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const getRunMock = vi.mocked(getRun)
    const prevConnectImpl = connectSseMock.getMockImplementation()
    const prevGetRunImpl = getRunMock.getMockImplementation()
    if (!prevGetRunImpl) throw new Error('expected getRun mock implementation')
    const staleReset = deferred()
    let statusCalls = 0

    connectSseMock.mockClear()
    getRunMock.mockClear()
    connectSseMock.mockResolvedValue(undefined)
    getRunMock.mockImplementation(async (...args) => {
      statusCalls += 1
      if (statusCalls === 1) return await prevGetRunImpl(...args)
      throw new ApiError('HTTP 404 Not Found for /simulator/runs/r1', { status: 404 })
    })

    const harness = createSseCharacterizationHarness()
    harness.cleanupRealRunFxAndTimers.mockImplementation(() => staleReset.resolve())
    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await staleReset.promise
      await nextTick()

      expect(connectSseMock).toHaveBeenCalledTimes(1)
      expect(statusCalls).toBe(2)
      expect(harness.real.runId).toBeNull()
      expect(harness.real.runStatus).toBeNull()
      expect(harness.real.sseState).toBe('idle')
      expect(harness.real.lastError).toBe('')
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevConnectImpl)
      getRunMock.mockImplementation(prevGetRunImpl)
    }
  })

  it('reverse-resolved status success from an old run cannot overwrite the active run', async () => {
    const getRunMock = vi.mocked(getRun)
    const prevGetRunImpl = getRunMock.getMockImplementation()
    if (!prevGetRunImpl) throw new Error('expected getRun mock implementation')
    type Status = Awaited<ReturnType<typeof getRun>>
    let resolveOld!: (value: Status) => void
    const oldStatus = new Promise<Status>((resolve) => {
      resolveOld = resolve
    })
    const statusFor = (runId: string): Status => ({
      run_id: runId,
      scenario_id: 'sc1',
      state: 'running',
      sim_time_ms: 0,
      intensity_percent: 0,
      ops_sec: 0,
      queue_depth: 0,
      last_event_type: null,
      current_phase: null,
      last_error: null,
    })
    getRunMock.mockImplementation(async (_client, runId) =>
      runId === 'r1' ? await oldStatus : statusFor(runId),
    )
    const harness = createSseCharacterizationHarness()

    try {
      const oldRefresh = harness.h.refreshRunStatus()
      harness.real.runId = 'r2'
      await harness.h.refreshRunStatus()
      resolveOld(statusFor('r1'))
      await oldRefresh

      expect(harness.real.runId).toBe('r2')
      expect(harness.real.runStatus?.run_id).toBe('r2')
    } finally {
      harness.h.stopSse()
      getRunMock.mockImplementation(prevGetRunImpl)
    }
  })

  it('reverse-resolved status 404 from an old run cannot reset the active run', async () => {
    const getRunMock = vi.mocked(getRun)
    const prevGetRunImpl = getRunMock.getMockImplementation()
    if (!prevGetRunImpl) throw new Error('expected getRun mock implementation')
    let rejectOld!: (reason: unknown) => void
    const oldStatus = new Promise<never>((_resolve, reject) => {
      rejectOld = reject
    })
    getRunMock.mockImplementation(async (_client, runId) => {
      if (runId === 'r1') return await oldStatus
      return await prevGetRunImpl(_client, runId)
    })
    const harness = createSseCharacterizationHarness()

    try {
      const oldRefresh = harness.h.refreshRunStatus()
      harness.real.runId = 'r2'
      await harness.h.refreshRunStatus()
      rejectOld(new ApiError('HTTP 404 old run', { status: 404 }))
      await oldRefresh

      expect(harness.real.runId).toBe('r2')
      expect(harness.real.runStatus).not.toBeNull()
      expect(harness.resetRunStats).not.toHaveBeenCalled()
    } finally {
      harness.h.stopSse()
      getRunMock.mockImplementation(prevGetRunImpl)
    }
  })

  it('SSE 410 followed by status 404 resets to idle and does not continue reconnecting', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const getRunMock = vi.mocked(getRun)
    const prevConnectImpl = connectSseMock.getMockImplementation()
    const prevGetRunImpl = getRunMock.getMockImplementation()
    if (!prevGetRunImpl) throw new Error('expected getRun mock implementation')
    const staleReset = deferred()
    let statusCalls = 0

    connectSseMock.mockResolvedValue(undefined)
    connectSseMock.mockRejectedValueOnce(new Error('SSE HTTP 410 Gone'))
    getRunMock.mockImplementation(async (...args) => {
      statusCalls += 1
      if (statusCalls === 1) return await prevGetRunImpl(...args)
      throw new ApiError('HTTP 404 Not Found for /simulator/runs/r1', { status: 404 })
    })
    const harness = createSseCharacterizationHarness({ lastEventId: 'evt_expired' })
    harness.cleanupRealRunFxAndTimers.mockImplementation(() => staleReset.resolve())

    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await staleReset.promise
      await nextTick()

      expect(harness.real.runId).toBeNull()
      expect(harness.real.sseState).toBe('idle')
      expect(harness.real.lastEventId).toBeNull()
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevConnectImpl)
      getRunMock.mockImplementation(prevGetRunImpl)
    }
  })

  it('SSE 410 clears the cursor, refreshes status and snapshot, then reconnects without a cursor', async () => {
    vi.useFakeTimers()
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const connectSseMock = vi.mocked(connectSse)
    const getRunMock = vi.mocked(getRun)
    const prevConnectImpl = connectSseMock.getMockImplementation()
    const prevGetRunImpl = getRunMock.getMockImplementation()
    if (!prevGetRunImpl) throw new Error('expected getRun mock implementation')

    const firstConnection = deferred()
    const rejectFirstConnection = deferred()
    const secondConnection = deferred()
    const recoverySnapshot = deferred()
    const order: string[] = []
    let connectionCount = 0
    let recoveryPhase = false

    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      connectionCount += 1
      order.push(`connect:${opts.lastEventId ?? 'null'}`)
      if (connectionCount === 1) {
        firstConnection.resolve()
        await rejectFirstConnection.promise
        throw new Error('SSE HTTP 410 Gone')
      }
      secondConnection.resolve()
      await waitForAbort(opts.signal)
    })
    getRunMock.mockImplementation(async (...args) => {
      order.push('status')
      return prevGetRunImpl(...args)
    })

    const harness = createSseCharacterizationHarness({
      lastEventId: 'evt_expired',
      loadScene: async () => {
        order.push('snapshot')
        if (recoveryPhase) recoverySnapshot.resolve()
      },
    })

    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await firstConnection.promise
      await vi.advanceTimersByTimeAsync(80)

      order.length = 0
      recoveryPhase = true
      rejectFirstConnection.resolve()
      await recoverySnapshot.promise

      expect(harness.real.lastEventId).toBeNull()
      expect(order).toEqual(['status', 'snapshot'])

      await vi.advanceTimersByTimeAsync(1000)
      await secondConnection.promise
      expect(order).toEqual(['status', 'snapshot', 'connect:null'])
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevConnectImpl)
      getRunMock.mockImplementation(prevGetRunImpl)
      randomSpy.mockRestore()
      vi.useRealTimers()
    }
  })

  it('SSE 410 plus transient status failure stays reconnecting and retries without the stale cursor', async () => {
    vi.useFakeTimers()
    const randomSpy = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const connectSseMock = vi.mocked(connectSse)
    const getRunMock = vi.mocked(getRun)
    const prevConnectImpl = connectSseMock.getMockImplementation()
    const prevGetRunImpl = getRunMock.getMockImplementation()
    if (!prevGetRunImpl) throw new Error('expected getRun mock implementation')
    const secondConnection = deferred()
    const recoveryAttempted = deferred()
    const seenCursors: Array<string | null | undefined> = []
    let statusCalls = 0

    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      seenCursors.push(opts.lastEventId)
      if (seenCursors.length === 1) throw new Error('SSE HTTP 410 Gone')
      secondConnection.resolve()
      await waitForAbort(opts.signal)
    })
    getRunMock.mockImplementation(async (...args) => {
      statusCalls += 1
      if (statusCalls === 2) {
        recoveryAttempted.resolve()
        throw new ApiError('HTTP 503 status unavailable', { status: 503 })
      }
      return prevGetRunImpl(...args)
    })

    const harness = createSseCharacterizationHarness({ lastEventId: 'evt_expired' })
    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await recoveryAttempted.promise
      await Promise.resolve()

      expect(harness.real.runId).toBe('r1')
      expect(harness.real.lastEventId).toBeNull()
      expect(harness.real.sseState).toBe('reconnecting')
      expect(harness.real.lastError).toContain('503')

      await vi.advanceTimersByTimeAsync(1000)
      await secondConnection.promise
      expect(seenCursors).toEqual(['evt_expired', null])
      expect(harness.real.sseState).toBe('open')
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevConnectImpl)
      getRunMock.mockImplementation(prevGetRunImpl)
      randomSpy.mockRestore()
      vi.useRealTimers()
    }
  })

  it('restores SSE observation when stop fails without a terminal 404', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const stopRunMock = vi.mocked(stopRun)
    const prevConnectImpl = connectSseMock.getMockImplementation()
    const prevStopImpl = stopRunMock.getMockImplementation()
    if (!prevStopImpl) throw new Error('expected stopRun mock implementation')
    const firstConnection = deferred()
    const secondConnection = deferred()
    const signals: AbortSignal[] = []

    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      if (opts.signal) signals.push(opts.signal)
      if (signals.length === 1) firstConnection.resolve()
      if (signals.length === 2) secondConnection.resolve()
      await waitForAbort(opts.signal)
    })
    stopRunMock.mockRejectedValueOnce(new Error('HTTP 500 stop failed'))

    const harness = createSseCharacterizationHarness()
    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await firstConnection.promise

      await harness.h.stop()
      await secondConnection.promise

      expect(signals).toHaveLength(2)
      expect(signals[0]?.aborted).toBe(true)
      expect(signals[1]?.aborted).toBe(false)
      expect(harness.real.sseState).toBe('open')
    } finally {
      harness.h.stopSse()
      restoreConnectSseImplementation(prevConnectImpl)
      stopRunMock.mockImplementation(prevStopImpl)
    }
  })

  it('scope disposal aborts the stream and cancels pending snapshot work', async () => {
    const connectSseMock = vi.mocked(connectSse)
    const prevConnectImpl = connectSseMock.getMockImplementation()
    const connected = deferred()
    let signal: AbortSignal | undefined
    connectSseMock.mockImplementation(async (opts: SseConnectOpts) => {
      signal = opts.signal
      connected.resolve()
      await waitForAbort(opts.signal)
    })

    const scope = effectScope()
    let harness!: ReturnType<typeof createSseCharacterizationHarness>
    scope.run(() => {
      harness = createSseCharacterizationHarness()
    })
    try {
      harness.isRealModeRef.value = true
      await nextTick()
      await connected.promise
      expect(signal?.aborted).toBe(false)

      scope.stop()

      expect(signal?.aborted).toBe(true)
      expect(harness.real.sseState).toBe('idle')
    } finally {
      scope.stop()
      restoreConnectSseImplementation(prevConnectImpl)
    }
  })
})

