/**
 * 034 S5a, review round 1 (adversarial review of 8b5cfc33) - what the first tests let through, and what happens
 * to a timeout and a request id AFTER `http.ts`. Written red-first: every case here was red (or, for the pure
 * mutation guards, goes red under the named mutation) before the matching change.
 *
 * Controlled clocks only (`vi.useFakeTimers`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, DEFAULT_REQUEST_TIMEOUT_MS, httpJson, httpText, isTimeoutError, LONG_REQUEST_TIMEOUT_MS } from './http'
import * as simulatorApi from './simulatorApi'
import { extractErrorMessage } from '../utils/errorMessage'

const CFG = { apiBase: '/api/v1' }
const TIMEOUT_BOUND_MS = 120_000

type Outcome = { settled: false } | { settled: true; value: unknown } | { settled: true; error: unknown }

function track(call: Promise<unknown>): { outcome: () => Outcome } {
  let outcome: Outcome = { settled: false }
  call.then(
    (value) => {
      outcome = { settled: true, value }
    },
    (error: unknown) => {
      outcome = { settled: true, error }
    },
  )
  return { outcome: () => outcome }
}

function errorOf(o: Outcome): unknown {
  return 'error' in o ? o.error : undefined
}

/** A `fetch` that never answers by itself but, like a real one, rejects when its signal is aborted. */
function hangingFetch() {
  return vi.fn((_url: unknown, init?: RequestInit) => {
    return new Promise<Response>((_resolve, reject) => {
      const signal = init?.signal
      if (!signal) return
      const onAbort = () => reject(signal.reason ?? new DOMException('Aborted', 'AbortError'))
      if (signal.aborted) onAbort()
      else signal.addEventListener('abort', onAbort, { once: true })
    })
  })
}

/** A `fetch` that records the signal it was given and neither answers nor reacts to it. */
function signalRecordingFetch() {
  const seen: Array<AbortSignal | null | undefined> = []
  const fn = vi.fn((_url: unknown, init?: RequestInit) => {
    seen.push(init?.signal)
    return new Promise<Response>(() => {})
  })
  return { fn, seen }
}

function responseWithHangingBody(status: number): Response {
  return new Response(new ReadableStream({ start() {} }), { status, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('item 7: the abort really reaches the transport', () => {
  it('the timeout aborts the signal that was handed to fetch', async () => {
    const rec = signalRecordingFetch()
    vi.stubGlobal('fetch', rec.fn)
    const call = track(httpJson(CFG, '/x', { timeoutMs: 500 }))

    await vi.advanceTimersByTimeAsync(500)

    expect(call.outcome().settled).toBe(true)
    expect(rec.seen[0]?.aborted, 'the request was left running after the timeout').toBe(true)
  })

  it('the caller abort aborts the fetch signal with the caller reason', async () => {
    const rec = signalRecordingFetch()
    vi.stubGlobal('fetch', rec.fn)
    const external = new AbortController()
    const call = track(httpJson(CFG, '/x', { signal: external.signal }))
    const reason = new Error('operator closed the panel')

    external.abort(reason)
    await vi.advanceTimersByTimeAsync(0)

    expect(call.outcome()).toEqual({ settled: true, error: reason })
    expect(rec.seen[0]?.aborted, 'the request kept running after the caller gave up').toBe(true)
    expect(rec.seen[0]?.reason).toBe(reason)
  })
})

describe('items 5, 6, 7: sources of the id, the success body, the caller reason', () => {
  it('when both the body and the header carry an id, the body wins', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ error: { code: 'X', message: 'm', request_id: 'req-body' } }), {
          status: 500,
          headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-header' },
        }),
      ),
    )
    const caught = httpJson(CFG, '/x').then(() => null, (e: unknown) => e as ApiError)
    await vi.advanceTimersByTimeAsync(0)
    expect((await caught)?.requestId).toBe('req-body')
  })

  it('httpText: a success body that never ends ends with ApiError by timeout', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => responseWithHangingBody(200)))
    const call = track(httpText(CFG, '/x'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const o = call.outcome()
    expect(o.settled, 'the read of a successful httpText body is not bounded').toBe(true)
    expect(isTimeoutError(errorOf(o))).toBe(true)
  })

  it('a 200 with a body that is not JSON is an ApiError carrying the header id, not a bare SyntaxError', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html>proxy</html>', { status: 200, headers: { 'X-Request-ID': 'req-200' } })),
    )
    const caught = httpJson(CFG, '/x').then(() => null, (e: unknown) => e)
    await vi.advanceTimersByTimeAsync(0)
    const error = await caught

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).requestId).toBe('req-200')
    expect(extractErrorMessage(error)).toContain('(ref: req-200)')
  })

  it('a 200 that breaks the response contract is a SimulatorContractError carrying the header id', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ not: 'a run status' }), { status: 200, headers: { 'X-Request-ID': 'req-contract' } }),
      ),
    )
    const caught = simulatorApi.getRun(CFG, 'run-1').then(() => null, (e: unknown) => e)
    await vi.advanceTimersByTimeAsync(0)
    const error = await caught

    expect((error as Error).name).toBe('SimulatorContractError')
    expect((error as ApiError).requestId).toBe('req-contract')
    expect(extractErrorMessage(error)).toContain('(ref: req-contract)')
  })

  it('counter-check: a valid 200 without any id is unchanged', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: 1 }), { status: 200 })))
    const call = httpJson<{ ok: number }>(CFG, '/x')
    await vi.advanceTimersByTimeAsync(0)
    await expect(call).resolves.toEqual({ ok: 1 })
  })

  it('a caller abort with its own reason while the ERROR body is read is that reason, not ApiError(500)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => responseWithHangingBody(500)))
    const external = new AbortController()
    const call = track(httpJson(CFG, '/x', { signal: external.signal }))
    await vi.advanceTimersByTimeAsync(0)
    const reason = new Error('operator closed the panel')

    external.abort(reason)
    await vi.advanceTimersByTimeAsync(0)

    expect(call.outcome()).toEqual({ settled: true, error: reason })
  })
})

describe('fix-delta item 2: a timeout while the body is read keeps the id of the headers already received', () => {
  function hangingBodyWithHeader(status: number, id: string | null): Response {
    return new Response(new ReadableStream({ start() {} }), {
      status,
      headers: { 'Content-Type': 'application/json', ...(id ? { 'X-Request-ID': id } : {}) },
    })
  }

  it.each([200, 500])('a %s answer whose body never ends: the timeout ApiError carries the header id', async (status) => {
    vi.stubGlobal('fetch', vi.fn(async () => hangingBodyWithHeader(status, 'req-late')))
    const call = track(httpJson(CFG, '/x'))

    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)

    const error = errorOf(call.outcome())
    expect(isTimeoutError(error)).toBe(true)
    expect((error as ApiError).requestId).toBe('req-late')
    expect(extractErrorMessage(error)).toContain('(ref: req-late)')
  })

  it('anti-vacuum: no header, or a timeout BEFORE any header arrived, invents no id', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => hangingBodyWithHeader(200, null)))
    const noHeader = track(httpJson(CFG, '/x'))
    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)
    expect((errorOf(noHeader.outcome()) as ApiError).requestId).toBeNull()

    vi.stubGlobal('fetch', hangingFetch())
    const noHeaders = track(httpJson(CFG, '/x'))
    await vi.advanceTimersByTimeAsync(TIMEOUT_BOUND_MS)
    expect(isTimeoutError(errorOf(noHeaders.outcome()))).toBe(true)
    expect((errorOf(noHeaders.outcome()) as ApiError).requestId).toBeNull()
  })
})

describe('item 3 (transport half): an unsafe-method timeout says the outcome is unknown', () => {
  it('a POST timeout carries outcomeUnknown and says so; a GET timeout does not', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const post = track(httpJson(CFG, '/x', { method: 'POST', body: '{}', timeoutMs: 100 }))
    const get = track(httpJson(CFG, '/x', { timeoutMs: 100 }))

    await vi.advanceTimersByTimeAsync(100)

    type Flagged = ApiError & { outcomeUnknown?: boolean }
    const postError = errorOf(post.outcome()) as Flagged
    const getError = errorOf(get.outcome()) as Flagged
    expect(isTimeoutError(postError)).toBe(true)
    expect(postError.outcomeUnknown).toBe(true)
    expect(postError.message).toMatch(/unknown/i)
    expect(isTimeoutError(getError)).toBe(true)
    expect(getError.outcomeUnknown ?? false).toBe(false)
    expect(getError.message).not.toMatch(/unknown/i)
  })

  it('the demo actions tx-once and clearing-once (not Interact) carry it too', async () => {
    vi.stubGlobal('fetch', hangingFetch())
    const tx = track(simulatorApi.actionTxOnce(CFG, 'run-1', {} as never))
    const clearing = track(simulatorApi.actionClearingOnce(CFG, 'run-1', {} as never))

    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS)

    for (const call of [tx, clearing]) {
      const error = errorOf(call.outcome()) as ApiError & { outcomeUnknown?: boolean }
      expect(error.outcomeUnknown).toBe(true)
      expect(extractErrorMessage(error)).toMatch(/unknown/i)
    }
  })
})

describe('item 7: the long bound is a table over every simulatorApi function', () => {
  const R = 'run-1'
  const X = {} as never
  /** Calls that run work INSIDE the request (Interact/demo actions and the two reads that seed the scenario). */
  const LONG: Record<string, () => Promise<unknown>> = {
    actionTxOnce: () => simulatorApi.actionTxOnce(CFG, R, X),
    actionClearingOnce: () => simulatorApi.actionClearingOnce(CFG, R, X),
    actionTrustlineCreate: () => simulatorApi.actionTrustlineCreate(CFG, R, X),
    actionTrustlineUpdate: () => simulatorApi.actionTrustlineUpdate(CFG, R, X),
    actionTrustlineClose: () => simulatorApi.actionTrustlineClose(CFG, R, X),
    actionPaymentReal: () => simulatorApi.actionPaymentReal(CFG, R, X),
    actionClearingReal: () => simulatorApi.actionClearingReal(CFG, R, X),
    getTrustlinesList: () => simulatorApi.getTrustlinesList(CFG, R, 'UAH'),
    getPaymentTargets: () => simulatorApi.getPaymentTargets(CFG, R, 'UAH', 'a'),
  }
  const ORDINARY: Record<string, () => Promise<unknown>> = {
    listScenarios: () => simulatorApi.listScenarios(CFG),
    getScenario: () => simulatorApi.getScenario(CFG, 's1'),
    getScenarioPreview: () => simulatorApi.getScenarioPreview(CFG, 's1', 'UAH'),
    uploadScenario: () => simulatorApi.uploadScenario(CFG, { scenario: {} }),
    createRun: () => simulatorApi.createRun(CFG, X),
    getActiveRun: () => simulatorApi.getActiveRun(CFG),
    getRun: () => simulatorApi.getRun(CFG, R),
    pauseRun: () => simulatorApi.pauseRun(CFG, R),
    resumeRun: () => simulatorApi.resumeRun(CFG, R),
    stopRun: () => simulatorApi.stopRun(CFG, R),
    setIntensity: () => simulatorApi.setIntensity(CFG, R, 50),
    getSnapshot: () => simulatorApi.getSnapshot(CFG, R, 'UAH'),
    getMetrics: () => simulatorApi.getMetrics(CFG, R, 'UAH', { from_ms: 0, to_ms: 1, step_ms: 1 }),
    getBottlenecks: () => simulatorApi.getBottlenecks(CFG, R, 'UAH', {}),
    listArtifacts: () => simulatorApi.listArtifacts(CFG, R),
    getParticipantsList: () => simulatorApi.getParticipantsList(CFG, R),
    ensureSession: () => simulatorApi.ensureSession(CFG),
    adminGetAllRuns: () => simulatorApi.adminGetAllRuns(CFG),
    adminStopAllRuns: () => simulatorApi.adminStopAllRuns(CFG),
  }

  it('anti-vacuum: every exported function of simulatorApi is classified in exactly one table', () => {
    const exported = Object.entries(simulatorApi)
      .filter(([, v]) => typeof v === 'function')
      .map(([k]) => k)
      .filter((k) => k !== 'artifactDownloadUrl')
    const classified = [...Object.keys(LONG), ...Object.keys(ORDINARY)]
    expect([...exported].sort()).toEqual([...classified].sort())
  })

  it.each(Object.keys(LONG))('%s: pending at the default bound, TIMEOUT at the long one', async (name) => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(LONG[name]!())

    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS)
    expect(call.outcome().settled, `${name} was cut at the default bound`).toBe(false)
    await vi.advanceTimersByTimeAsync(LONG_REQUEST_TIMEOUT_MS - DEFAULT_REQUEST_TIMEOUT_MS)

    const o = call.outcome()
    expect(o.settled).toBe(true)
    expect(isTimeoutError(errorOf(o))).toBe(true)
  })

  it.each(Object.keys(ORDINARY))('%s: TIMEOUT at the default bound', async (name) => {
    vi.stubGlobal('fetch', hangingFetch())
    const call = track(ORDINARY[name]!())

    await vi.advanceTimersByTimeAsync(DEFAULT_REQUEST_TIMEOUT_MS)

    const o = call.outcome()
    expect(o.settled, `${name} has no default bound`).toBe(true)
    expect(isTimeoutError(errorOf(o))).toBe(true)
  })
})
