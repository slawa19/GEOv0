import { computed, effectScope, nextTick, reactive, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSimulatorRealMode, type RealModeState } from './useSimulatorRealMode'
import { ensureSession } from '../api/simulatorApi'
import { fetchEquivalentPrecisions, type EquivalentPrecisionRow } from '../api/equivalentsApi'
import type { HttpConfig } from '../api/http'
import {
  equivalentPrecision,
  resetEquivalentPrecisions,
  SHIPPED_EQUIVALENT_PRECISION,
} from '../config/equivalentPrecision'
import { DEFAULT_MONEY_PRECISION } from '../utils/money'

/**
 * T1211 — the equivalents-precision registry versus a connection context that moves
 * underneath an in-flight catalogue request (012 / `F-012-4`).
 *
 * Two findings, one mechanism:
 *
 *  1. Leaving real mode resets the registry, but a catalogue request that was already in
 *     flight still lands afterwards and repopulates it. Demo fixtures are then read at the
 *     precision of the backend the operator just left.
 *  2. `apiBase` and `accessToken` are editable while real mode stays on, and nothing
 *     re-reads the catalogue when they change — an admin token typed after an anonymous
 *     start never produces a second attempt.
 *
 * Every case below is built so that a wrong implementation cannot satisfy it by accident:
 * the two catalogues involved always declare *different* precisions for the same code, and
 * each case states that premise as an explicit counter-check before asserting on it. A
 * fixture where both sides agree would pass against the unfixed code.
 */

const SESSION_OK = vi.hoisted(() => ({ actor_kind: 'anon', owner_id: 'anon-1' }))

vi.mock('../api/simulatorApi', () => {
  return {
    artifactDownloadUrl: () => 'http://artifact',
    createRun: vi.fn(async () => ({ run_id: 'r1' })),
    ensureSession: vi.fn(async () => SESSION_OK),
    getActiveRun: vi.fn(async () => ({ run_id: null })),
    getRun: vi.fn(async () => null),
    listArtifacts: vi.fn(async () => ({ items: [] })),
    listScenarios: vi.fn(async () => ({ items: [] })),
    pauseRun: vi.fn(async () => undefined),
    resumeRun: vi.fn(async () => undefined),
    setIntensity: vi.fn(async () => undefined),
    stopRun: vi.fn(async () => undefined),
  }
})

vi.mock('../api/sse', () => {
  return { connectSse: vi.fn(async () => undefined) }
})

vi.mock('../api/equivalentsApi', () => {
  return { fetchEquivalentPrecisions: vi.fn(async () => [] as EquivalentPrecisionRow[]) }
})

const fetchMock = vi.mocked(fetchEquivalentPrecisions)
const ensureSessionMock = vi.mocked(ensureSession)

function createRealState(): RealModeState {
  return reactive<RealModeState>({
    apiBase: 'http://backend-a',
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
  })
}

function createHarness() {
  const isRealModeRef = ref(false)
  const real = createRealState()
  const effectiveEq = ref('UAH')
  const state = {
    loading: false,
    error: '',
    sourcePath: '',
    snapshot: null,
    selectedNodeId: null,
    flash: 0,
  }

  const scope = effectScope()
  let h!: ReturnType<typeof useSimulatorRealMode>
  scope.run(() => {
    h = useSimulatorRealMode({
      isRealMode: computed(() => isRealModeRef.value),
      isLocalhost: false,
      effectiveEq: computed(() => effectiveEq.value),
      state,
      real,

      ensureScenarioSelectionValid: () => undefined,
      resetRunStats: () => undefined,
      cleanupRealRunFxAndTimers: () => undefined,

      isUserFacingRunError: () => false,
      inc: () => undefined,

      loadScene: async () => undefined,
      loadRecoveryScene: async () => true,
      realPatchApplier: { applyNodePatches: () => undefined, applyEdgePatches: () => undefined },
      pushTxAmountLabel: () => undefined,
      clampRealTxTtlMs: () => 1200,

      scheduleTimeout: () => undefined,
      runRealTxFx: () => undefined,
      runRealClearingDoneFx: () => undefined,
      wakeUp: () => undefined,
      onAnySseEvent: () => undefined,
    })
  })

  return { h, isRealModeRef, real, scope }
}

/** Drains the microtask queue plus the watcher flushes a boot sequence chains through. */
async function settle(rounds = 25) {
  for (let i = 0; i < rounds; i += 1) {
    await nextTick()
    await Promise.resolve()
  }
}

/** A promise the test resolves by hand, so a request can be held mid-flight. */
function deferredRows() {
  let resolve!: (rows: EquivalentPrecisionRow[]) => void
  const promise = new Promise<EquivalentPrecisionRow[]>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

/** Resolves the first time the catalogue loader is entered. */
function firstCallSignal() {
  let seen!: () => void
  const promise = new Promise<void>((r) => {
    seen = r
  })
  return { promise, seen }
}

beforeEach(() => {
  resetEquivalentPrecisions()
  ensureSessionMock.mockReset()
  ensureSessionMock.mockImplementation(async () => SESSION_OK)
  fetchMock.mockReset()
  fetchMock.mockImplementation(async () => [])
})

describe('T1211 — equivalents catalogue must follow the connection context', () => {
  it('discards a catalogue response that lands after real mode was left', async () => {
    // The backend disagrees with the shipped HOUR fixture. If it did not, a stale response
    // would be indistinguishable from a correctly discarded one and this case would pass
    // against the unfixed code.
    const BACKEND_HOUR_PRECISION: number = 4
    expect(
      BACKEND_HOUR_PRECISION === SHIPPED_EQUIVALENT_PRECISION.HOUR,
      'Counter-check premise: the backend catalogue and the shipped HOUR fixture must declare '
        + 'different precision, otherwise "response discarded" and "response applied" look the same.',
    ).toBe(false)

    const inFlight = deferredRows()
    const entered = firstCallSignal()
    fetchMock.mockImplementation(() => {
      entered.seen()
      return inFlight.promise
    })

    const harness = createHarness()
    try {
      harness.isRealModeRef.value = true
      await entered.promise
      await settle()

      // Operator drops back to demo while the catalogue request is still open.
      harness.isRealModeRef.value = false
      await settle()
      expect(
        equivalentPrecision('HOUR'),
        'Leaving real mode must put the shipped fixture precision back in force.',
      ).toBe(SHIPPED_EQUIVALENT_PRECISION.HOUR)

      // The abandoned request now answers.
      inFlight.resolve([{ code: 'HOUR', precision: BACKEND_HOUR_PRECISION }])
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        'Demo mode is active, so HOUR must be read at the precision its shipped fixture was '
          + `generated with (${SHIPPED_EQUIVALENT_PRECISION.HOUR}). Reading `
          + `${BACKEND_HOUR_PRECISION} means the request the operator walked away from `
          + 'repopulated the registry, and every balance stored as atoms is now off by a '
          + 'factor of ten.',
      ).toBe(SHIPPED_EQUIVALENT_PRECISION.HOUR)
    } finally {
      harness.scope.stop()
    }
  })

  it('re-reads the catalogue when an access token is supplied after an anonymous start', async () => {
    // CUSTOM is not a shipped fixture, so the only two outcomes are "the backend answered"
    // and "the default is still in force" — and those two must not coincide.
    const CUSTOM_PRECISION: number = 6
    expect(
      CUSTOM_PRECISION === DEFAULT_MONEY_PRECISION,
      'Counter-check premise: the served precision must differ from the default, otherwise a '
        + 'loader that never ran would look identical to one that succeeded.',
    ).toBe(false)
    expect(
      Object.prototype.hasOwnProperty.call(SHIPPED_EQUIVALENT_PRECISION, 'CUSTOM'),
      'Counter-check premise: CUSTOM must not be a shipped fixture, or layer 2 would answer '
        + 'for it and the catalogue would not be the thing under test.',
    ).toBe(false)

    fetchMock.mockImplementation(async (cfg: HttpConfig) => {
      if (!String(cfg.accessToken ?? '').trim()) throw new Error('HTTP 401 Unauthorized')
      return [{ code: 'CUSTOM', precision: CUSTOM_PRECISION }]
    })

    const harness = createHarness()
    try {
      harness.isRealModeRef.value = true
      await settle()
      expect(
        equivalentPrecision('CUSTOM'),
        'Anonymous start: the catalogue is unreadable, so the default stands.',
      ).toBe(DEFAULT_MONEY_PRECISION)

      // Operator pastes an admin token into the real-mode panel; real mode is never left.
      harness.real.accessToken = 'admin-token'
      await settle()

      expect(
        equivalentPrecision('CUSTOM'),
        'A credential that can read the catalogue is now in place, so CUSTOM must be read at '
          + `${CUSTOM_PRECISION}. Reading ${DEFAULT_MONEY_PRECISION} means the token was never `
          + 'retried and every custom equivalent keeps a precision nobody declared.',
      ).toBe(CUSTOM_PRECISION)
    } finally {
      harness.scope.stop()
    }
  })

  it('re-reads the catalogue when apiBase is repointed at a different backend', async () => {
    const A_HOUR_PRECISION: number = 4
    const B_HOUR_PRECISION: number = 7
    expect(
      A_HOUR_PRECISION === B_HOUR_PRECISION,
      'Counter-check premise: the two backends must declare different precision for HOUR, or '
        + 'keeping the first catalogue would be indistinguishable from re-reading it.',
    ).toBe(false)

    fetchMock.mockImplementation(async (cfg: HttpConfig) => {
      const precision = cfg.apiBase === 'http://backend-b' ? B_HOUR_PRECISION : A_HOUR_PRECISION
      return [{ code: 'HOUR', precision }]
    })

    const harness = createHarness()
    harness.real.accessToken = 'admin-token'
    try {
      harness.isRealModeRef.value = true
      await settle()
      expect(equivalentPrecision('HOUR')).toBe(A_HOUR_PRECISION)

      harness.real.apiBase = 'http://backend-b'
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        `apiBase now points at backend-b, which declares HOUR at ${B_HOUR_PRECISION}. Reading `
          + `${A_HOUR_PRECISION} means the catalogue of the previous backend is still being `
          + 'used to format amounts fetched from the new one.',
      ).toBe(B_HOUR_PRECISION)
    } finally {
      harness.scope.stop()
    }
  })

  it('lets the newest connection context win when two catalogue loads overlap', async () => {
    // The two findings interact: re-reading on a context change opens a second request
    // while the first is still in flight. If the slow first answer is allowed to land last,
    // the fix for finding 2 reintroduces finding 1 in a new disguise.
    const STALE_HOUR_PRECISION: number = 4
    const FRESH_HOUR_PRECISION: number = 7
    expect(
      STALE_HOUR_PRECISION === FRESH_HOUR_PRECISION,
      'Counter-check premise: the overlapping responses must disagree, or "last write wins" '
        + 'and "newest context wins" produce the same registry.',
    ).toBe(false)

    const slow = deferredRows()
    const firstEntered = firstCallSignal()
    fetchMock.mockImplementation((cfg: HttpConfig) => {
      if (!String(cfg.accessToken ?? '').trim()) {
        firstEntered.seen()
        return slow.promise
      }
      return Promise.resolve([{ code: 'HOUR', precision: FRESH_HOUR_PRECISION }])
    })

    const harness = createHarness()
    try {
      harness.isRealModeRef.value = true
      await firstEntered.promise
      await settle()

      harness.real.accessToken = 'admin-token'
      await settle()
      expect(equivalentPrecision('HOUR')).toBe(FRESH_HOUR_PRECISION)

      // The anonymous request finally answers, long after its context was replaced.
      slow.resolve([{ code: 'HOUR', precision: STALE_HOUR_PRECISION }])
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        'The anonymous request belongs to a connection context that no longer exists; its '
          + `answer (${STALE_HOUR_PRECISION}) must not overwrite the one read with the current `
          + `credential (${FRESH_HOUR_PRECISION}).`,
      ).toBe(FRESH_HOUR_PRECISION)
    } finally {
      harness.scope.stop()
    }
  })
  it('does not let a pre-toggle load land on a freshly re-entered real mode', async () => {
    // Narrower than the case above: here the *re-entry* has not issued its own load yet, so
    // nothing newer exists to out-rank the abandoned one. Its context — real mode, same
    // apiBase, same (empty) credential — is byte-for-byte the current one again, which is
    // exactly why the context comparison alone cannot reject it.
    const STALE_HOUR_PRECISION: number = 4
    expect(
      STALE_HOUR_PRECISION === SHIPPED_EQUIVALENT_PRECISION.HOUR,
      'Counter-check premise: the abandoned catalogue must disagree with the shipped HOUR '
        + 'fixture, or applying it and dropping it would leave the same registry.',
    ).toBe(false)

    const abandoned = deferredRows()
    const firstEntered = firstCallSignal()
    fetchMock.mockImplementation(() => {
      firstEntered.seen()
      return abandoned.promise
    })

    // First bootstrap fails, so the anonymous session is retried on re-entry — and that
    // retry is held open, which keeps the re-entry from issuing its own catalogue load.
    const heldSession = deferredRows()
    let sessionAttempts = 0
    ensureSessionMock.mockImplementation(async () => {
      sessionAttempts += 1
      if (sessionAttempts === 1) throw new Error('session bootstrap failed')
      await heldSession.promise
      return SESSION_OK
    })

    const harness = createHarness()
    try {
      harness.isRealModeRef.value = true
      await firstEntered.promise
      await settle()

      harness.isRealModeRef.value = false
      await settle()

      harness.isRealModeRef.value = true
      await settle()
      expect(
        fetchMock,
        'Precondition: the re-entry must still be waiting on its session, so no second '
          + 'catalogue load exists yet.',
      ).toHaveBeenCalledTimes(1)

      abandoned.resolve([{ code: 'HOUR', precision: STALE_HOUR_PRECISION }])
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        `The load was issued before the operator toggled out of real mode; reading `
          + `${STALE_HOUR_PRECISION} means a catalogue nobody is waiting for any more was `
          + 'installed just because the connection context happened to look the same again.',
      ).toBe(SHIPPED_EQUIVALENT_PRECISION.HOUR)
    } finally {
      heldSession.resolve([])
      harness.scope.stop()
    }
  })
})

/**
 * The invariant `refreshEquivalentPrecisions` rests on, made executable.
 *
 * That loader guards on the generation ALONE (`useSimulatorRealMode.ts:1049,1055`), unlike
 * the file's three other stale-guards, which each pair a generation with an explicit
 * re-check of the context they were issued under (`isSceneContextCurrent`, :330-353;
 * `real.runId === runId`, :403; `real.runId === runIdAtStart`, :442). Those three need the
 * re-check because their context can move without touching their generation — `resetStaleRun`
 * clears `real.runId` and bumps nothing. Here it cannot, and the equivalent re-check was
 * therefore unkillable by any test and removed rather than shipped unverified.
 *
 * That removal is only sound while this holds: EVERY way the connection context can change
 * bumps the generation. Stated in a comment, such an invariant survives the edit that
 * falsifies it; stated here, that edit fails this case instead.
 *
 * COMPLETENESS IS NOT PROVEN BY THIS TEST, AND CANNOT BE. The list below is complete AS OF
 * WRITING against the three values that constitute the context — `isRealMode`,
 * `real.apiBase`, `real.accessToken` — which are exactly the watch sources of the
 * connection-context watcher (`useSimulatorRealMode.ts:1389`) and exactly what the loader
 * pins into its request (`useSimulatorRealMode.ts:1052`). If a fourth input ever joins that
 * context, this list must grow by hand; nothing here will notice on its own.
 */

/** A code no shipped fixture declares, so only the catalogue can give it a precision. */
const PINNED_CODE = 'CTX'
const STALE_PRECISION: number = 5

/** States what makes each assertion below capable of failing. */
function expectPinnedCodeCanOnlyComeFromTheAbandonedLoad() {
  expect(
    STALE_PRECISION === DEFAULT_MONEY_PRECISION,
    'Counter-check premise: the abandoned catalogue must declare a precision the default does '
      + 'not, otherwise "generation bumped, response dropped" and "response applied" read the same.',
  ).toBe(false)
  expect(
    Object.prototype.hasOwnProperty.call(SHIPPED_EQUIVALENT_PRECISION, PINNED_CODE),
    'Counter-check premise: the pinned code must not be a shipped fixture, or layer 2 would '
      + 'answer for it and the catalogue would not be what the assertion observes.',
  ).toBe(false)
}

type ContextChange = {
  name: string
  /** Runs before real mode is entered, so that `apply` is a genuine transition. */
  arrange?: (harness: ReturnType<typeof createHarness>) => void
  apply: (harness: ReturnType<typeof createHarness>) => void | Promise<void>
}

const CONTEXT_CHANGES: ReadonlyArray<ContextChange> = [
  {
    name: 'apiBase is repointed at another backend',
    apply: (h) => {
      h.real.apiBase = 'http://backend-b'
    },
  },
  {
    name: 'a credential is supplied to an anonymous session',
    apply: (h) => {
      h.real.accessToken = 'admin-token'
    },
  },
  {
    name: 'a credential is cleared back to anonymous',
    arrange: (h) => {
      h.real.accessToken = 'admin-token'
    },
    apply: (h) => {
      h.real.accessToken = ''
    },
  },
  {
    name: 'real mode is left',
    apply: (h) => {
      h.isRealModeRef.value = false
    },
  },
  {
    name: 'real mode is left and re-entered',
    // The first session bootstrap fails, so the anonymous session is retried on re-entry --
    // and that retry never resolves. The re-entry therefore issues no catalogue load of its
    // own, which would otherwise bump the generation and mask the bump under test. What is
    // left is the bump on LEAVING real mode, and nothing else can drop the abandoned load.
    arrange: () => {
      let attempts = 0
      ensureSessionMock.mockImplementation(async () => {
        attempts += 1
        if (attempts === 1) throw new Error('session bootstrap failed')
        await new Promise<void>(() => undefined)
        return SESSION_OK
      })
    },
    apply: async (h) => {
      h.isRealModeRef.value = false
      await settle()
      h.isRealModeRef.value = true
    },
  },
]

describe('T1211 — every connection-context change bumps the catalogue generation', () => {
  for (const change of CONTEXT_CHANGES) {
    it(`drops a load issued before the change when ${change.name}`, async () => {
      expectPinnedCodeCanOnlyComeFromTheAbandonedLoad()

      const inFlight = deferredRows()
      const entered = firstCallSignal()
      let issued = 0
      fetchMock.mockImplementation(() => {
        issued += 1
        if (issued === 1) {
          entered.seen()
          return inFlight.promise
        }
        // Whatever load the change itself starts answers with an empty catalogue, so the
        // only way PINNED_CODE can read as STALE_PRECISION is the abandoned load landing.
        return Promise.resolve([])
      })

      const harness = createHarness()
      change.arrange?.(harness)
      try {
        harness.isRealModeRef.value = true
        await entered.promise
        await settle()

        await change.apply(harness)
        await settle()

        // The load issued under the previous context finally answers.
        inFlight.resolve([{ code: PINNED_CODE, precision: STALE_PRECISION }])
        await settle()

        expect(
          equivalentPrecision(PINNED_CODE),
          `The catalogue load was issued before ${change.name}, so its generation is stale and `
            + `its answer must be dropped. Reading ${STALE_PRECISION} instead of the default `
            + `${DEFAULT_MONEY_PRECISION} means this path changes the connection context without `
            + 'bumping the generation — and the loader has no second guard to catch that.',
        ).toBe(DEFAULT_MONEY_PRECISION)
      } finally {
        harness.scope.stop()
      }
    })
  }
})

/**
 * T1211 fix-round — the FAILURE path of the catalogue load.
 *
 * The cases above all walk a load that SUCCEEDS. A load can also fail, and the loader's
 * `catch` used to keep "whatever precisions are already in force"
 * (`useSimulatorRealMode.ts:1048`). Since the loader only ever runs at boot or after the
 * connection context changed, "already in force" on a failure is never a still-valid
 * catalogue for the current backend — it is the PREVIOUS backend's. That is not a missing
 * value, it is a wrong one, and `NodeCardOverlay.vue:108` converts `net_balance_atoms`
 * through it, so the error lands as a power of ten on a money figure.
 *
 * What must be in force after a failure is the same state an anonymous visitor is already
 * in — no catalogue, so the shipped fixture precision answers (`equivalentPrecision`
 * layer 2). That state is documented and deliberate; the previous backend's numbers are
 * neither.
 */
describe('T1211 fix-round — a failed catalogue load must not leave the old one in force', () => {
  it('drops the previous backend catalogue when the new backend fails to answer', async () => {
    const A_HOUR_PRECISION: number = 4
    expect(
      A_HOUR_PRECISION === SHIPPED_EQUIVALENT_PRECISION.HOUR,
      'Counter-check premise: backend A must disagree with the shipped HOUR fixture, or '
        + '"dropped" and "kept" would read the same.',
    ).toBe(false)

    fetchMock.mockImplementation(async (cfg: HttpConfig) => {
      if (cfg.apiBase === 'http://backend-b') throw new Error('HTTP 500 Internal Server Error')
      return [{ code: 'HOUR', precision: A_HOUR_PRECISION }]
    })

    const harness = createHarness()
    harness.real.accessToken = 'admin-token'
    try {
      harness.isRealModeRef.value = true
      await settle()
      expect(
        equivalentPrecision('HOUR'),
        'Precondition: backend A answered, so its precision is in force.',
      ).toBe(A_HOUR_PRECISION)

      harness.real.apiBase = 'http://backend-b'
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        `The catalogue of backend B could not be read, so nothing is known about B's `
          + `precisions and the shipped fixture value (${SHIPPED_EQUIVALENT_PRECISION.HOUR}) `
          + `must answer. Reading ${A_HOUR_PRECISION} means amounts fetched from B are scaled `
          + "by backend A's precision — a power-of-ten error on a money figure.",
      ).toBe(SHIPPED_EQUIVALENT_PRECISION.HOUR)
    } finally {
      harness.scope.stop()
    }
  })

  it('adopts the new catalogue when a failed load is followed by a successful one', async () => {
    const A_HOUR_PRECISION: number = 4
    const C_HOUR_PRECISION: number = 7
    expect(
      A_HOUR_PRECISION === C_HOUR_PRECISION,
      'Counter-check premise: the two readable backends must disagree, or recovery would be '
        + 'indistinguishable from never having dropped A.',
    ).toBe(false)

    fetchMock.mockImplementation(async (cfg: HttpConfig) => {
      if (cfg.apiBase === 'http://backend-b') throw new Error('HTTP 500 Internal Server Error')
      if (cfg.apiBase === 'http://backend-c') return [{ code: 'HOUR', precision: C_HOUR_PRECISION }]
      return [{ code: 'HOUR', precision: A_HOUR_PRECISION }]
    })

    const harness = createHarness()
    harness.real.accessToken = 'admin-token'
    try {
      harness.isRealModeRef.value = true
      await settle()

      harness.real.apiBase = 'http://backend-b'
      await settle()
      expect(equivalentPrecision('HOUR')).toBe(SHIPPED_EQUIVALENT_PRECISION.HOUR)

      harness.real.apiBase = 'http://backend-c'
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        'A failure must leave the registry able to accept the next catalogue; reading anything '
          + `other than ${C_HOUR_PRECISION} means the failure left the loader wedged.`,
      ).toBe(C_HOUR_PRECISION)
    } finally {
      harness.scope.stop()
    }
  })

  it('falls back to the shipped precision when the load fails with no catalogue ever held', async () => {
    fetchMock.mockImplementation(async () => {
      throw new Error('HTTP 401 Unauthorized')
    })

    const harness = createHarness()
    try {
      harness.isRealModeRef.value = true
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        'A failure on the very first load has no previous catalogue to drop; the shipped '
          + 'fixture precision must simply stay in force.',
      ).toBe(SHIPPED_EQUIVALENT_PRECISION.HOUR)
      expect(
        equivalentPrecision('CTX'),
        'And a code no fixture ships still resolves to the default.',
      ).toBe(DEFAULT_MONEY_PRECISION)
    } finally {
      harness.scope.stop()
    }
  })

  it('does not let a stale failure wipe the catalogue a newer load already installed', async () => {
    // The mirror of the stale-success case: if the `catch` clears unconditionally, a slow
    // rejection from an abandoned context destroys a good catalogue that landed after it.
    const FRESH_HOUR_PRECISION: number = 7
    expect(
      FRESH_HOUR_PRECISION === SHIPPED_EQUIVALENT_PRECISION.HOUR,
      'Counter-check premise: the fresh catalogue must differ from the shipped fixture, or '
        + '"survived" and "wiped" would read the same.',
    ).toBe(false)

    const slowFailure = { reject: (_e: Error) => undefined as void }
    const firstEntered = firstCallSignal()
    fetchMock.mockImplementation((cfg: HttpConfig) => {
      if (!String(cfg.accessToken ?? '').trim()) {
        firstEntered.seen()
        return new Promise<EquivalentPrecisionRow[]>((_resolve, reject) => {
          slowFailure.reject = reject
        })
      }
      return Promise.resolve([{ code: 'HOUR', precision: FRESH_HOUR_PRECISION }])
    })

    const harness = createHarness()
    try {
      harness.isRealModeRef.value = true
      await firstEntered.promise
      await settle()

      harness.real.accessToken = 'admin-token'
      await settle()
      expect(equivalentPrecision('HOUR')).toBe(FRESH_HOUR_PRECISION)

      // The abandoned anonymous request finally rejects.
      slowFailure.reject(new Error('HTTP 401 Unauthorized'))
      await settle()

      expect(
        equivalentPrecision('HOUR'),
        `The rejection belongs to a connection context that no longer exists. Reading `
          + `${SHIPPED_EQUIVALENT_PRECISION.HOUR} instead of ${FRESH_HOUR_PRECISION} means a `
          + 'stale failure was allowed to destroy a catalogue read under the current credential.',
      ).toBe(FRESH_HOUR_PRECISION)
    } finally {
      harness.scope.stop()
    }
  })
})
