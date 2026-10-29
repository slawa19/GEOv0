/** OMR-4: actual app/action API/HTTP wiring; only boot reads and the SSE transport are controlled.
 * The flat error envelopes follow the ASGI/PostgreSQL witness registered in BACKLOG OMR-4.
 * No browser, DOM rendering or TCP behavior is asserted here.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios } from '../api/simulatorApi'
import { connectSse, type SseConnectOpts } from '../api/sse'
import { actionErrorText } from '../utils/paymentRefusalText'
import { useSimulatorApp } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (original) => ({
  ...await original<typeof import('../api/simulatorApi')>(),
  listScenarios: vi.fn(), getActiveRun: vi.fn(), getRun: vi.fn(), getSnapshot: vi.fn(), getScenarioPreview: vi.fn(),
  ensureSession: vi.fn(async () => ({ actor_kind: 'anonymous', owner_id: 'owner' })),
}))
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({ connectSse: vi.fn() }))

const RUN = {
  api_version: 'simulator-api/1', run_id: 'R', scenario_id: 'minimal', mode: 'real', state: 'running',
  started_at: '2026-10-29T00:00:00Z', sim_time_ms: 0, intensity_percent: 0,
  ops_sec: 0, queue_depth: 0, last_error: null, last_event_type: null, current_phase: null,
}
const LINE = {
  from_pid: 'A', from_name: 'A', to_pid: 'B', to_name: 'B', equivalent: 'UAH',
  limit: '10.00', used: '0.00', reverse_used: '0.00', available: '10.00', status: 'active', close_requested_at: null,
}
const refusal = (code: string) => ({ code, message: 'Resource not found', details: { equivalent: 'UAH' } })
type Reply = { status: number; body: unknown }
let scope: EffectScope | null = null
let streams: SseConnectOpts[] = []
let calls: Array<{ url: string; method: string }> = []
let actionReply: Reply | (() => Promise<Reply>)
let listReply: Reply
let participantsReply: Reply

async function settle() {
  for (let i = 0; i < 30; i++) { await Promise.resolve(); await nextTick() }
}

beforeEach(() => {
  window.localStorage.clear()
  window.history.replaceState({}, '', '/?mode=real')
  streams = []; calls = []
  actionReply = { status: 404, body: refusal('TRUSTLINE_NOT_FOUND') }
  listReply = { status: 200, body: { items: [LINE] } }
  participantsReply = { status: 200, body: { items: [
    { pid: 'A', name: 'A', type: 'person', status: 'active' }, { pid: 'B', name: 'B', type: 'person', status: 'active' },
  ] } }
  vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [{ scenario_id: 'minimal', label: 'Minimal', equivalents: ['UAH'] }] } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R' } as never)
  vi.mocked(getRun).mockResolvedValue(RUN as never)
  const snapshot = { equivalent: 'UAH', generated_at: RUN.started_at,
    nodes: ['A', 'B'].map(id => ({ id, name: id, type: 'person', status: 'active' })), links: [] }
  vi.mocked(getSnapshot).mockResolvedValue(snapshot as never)
  vi.mocked(getScenarioPreview).mockResolvedValue(snapshot as never)
  vi.mocked(connectSse).mockImplementation((opts) => {
    streams.push(opts)
    opts.onMessage({ id: 'bootstrap', data: JSON.stringify({ ...RUN,
      run_id: decodeURIComponent(opts.url.split('/runs/')[1]!.split('/events')[0]!),
      type: 'run_status', event_id: 'bootstrap', ts: RUN.started_at,
    }) })
    return new Promise<void>((_resolve, reject) => {
      const abort = () => reject(new DOMException('Aborted', 'AbortError'))
      if (opts.signal?.aborted) return abort()
      opts.signal?.addEventListener('abort', abort, { once: true })
    })
  })
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    calls.push({ url, method })
    let reply: Reply
    if (url.endsWith('/pause')) reply = { status: 200, body: { ...RUN, state: 'paused' } }
    else if (url.includes('/actions/participants-list')) reply = participantsReply
    else if (method === 'GET' && url.includes('/actions/trustlines-list')) reply = listReply
    else if (url.includes('/actions/') || url.includes('/payment-targets')) reply = typeof actionReply === 'function' ? await actionReply() : actionReply
    else throw new TypeError(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(reply.body), { status: reply.status,
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'resource-request' } })
  }))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
})

afterEach(() => {
  scope?.stop(); scope = null
  vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.clearAllMocks()
  window.localStorage.clear(); window.history.replaceState({}, '', '/')
})

async function boot() {
  scope = effectScope()
  const app = scope.run(() => useSimulatorApp())!
  await settle()
  expect(app.real.runId).toBe('R')
  expect(app.real.sseState).toBe('open')
  expect(streams.filter(s => !s.signal?.aborted)).toHaveLength(1)
  app.real.lastEventId = 'cursor-before-refusal'
  app.real.runStats.attempts = 7; app.real.runStats.committed = 5
  return app
}

function context(app: Awaited<ReturnType<typeof boot>>) {
  return { runId: app.real.runId, status: app.real.runStatus, cursor: app.real.lastEventId,
    attempts: app.real.runStats.attempts, committed: app.real.runStats.committed,
    sse: app.real.sseState, saved: window.localStorage.getItem('geo.sim.v2.runId'),
    activeStreams: streams.filter(s => !s.signal?.aborted).length }
}

function deferredReply() {
  let resolve!: (reply: Reply) => void
  const promise = new Promise<Reply>(r => { resolve = r })
  return { promise, resolve }
}

describe('OMR-4 resource404 recovery through the real Simulator app', () => {
  it.each([
    ['update', 'TRUSTLINE_NOT_FOUND'], ['close', 'TRUSTLINE_NOT_FOUND'],
    ['update', 'PARTICIPANT_NOT_FOUND'], ['close', 'EQUIVALENT_NOT_FOUND'],
  ] as const)('stale %s / %s keeps run/SSE, refreshes a warm cache and shows the original refusal', async (kind, code) => {
    const app = await boot()
    const mode = app.interact.mode
    mode.selectTrustline('A', 'B')
    await settle()
    expect(mode.findAnsweredTrustline('A', 'B')).toMatchObject(LINE)
    const listCalls = () => calls.filter(c => c.url.includes('/trustlines-list')).length
    const beforeReads = listCalls()
    // Selecting again hits the 15-second cache. Recovery must force a fresh read.
    mode.selectTrustline('A', 'B'); await settle()
    expect(listCalls()).toBe(beforeReads)
    listReply = { status: 200, body: { items: [] } }
    actionReply = { status: 404, body: refusal(code) }
    const before = context(app)
    await (kind === 'update' ? mode.confirmTrustlineUpdate('12.00') : mode.confirmTrustlineClose())
    await settle()
    expect(context(app)).toEqual(before)
    expect(listCalls()).toBe(beforeReads + 1)
    expect(mode.findAnsweredTrustline('A', 'B')).toBeNull()
    expect(mode.trustlines.value).toEqual([])
    expect(mode.state.error).toBe(actionErrorText({ status: 404, ...refusal(code) }).text)
    expect(mode.state.errorRef).toBe('resource-request')
    expect(mode.state.phase).toBe('editing-trustline')
    expect(mode.busy.value).toBe(false)
  })

  it('create resource404 refreshes the missing-line cache and shows the participant refusal', async () => {
    listReply = { status: 200, body: { items: [] } }
    const app = await boot()
    const mode = app.interact.mode
    mode.startTrustlineFlowWithFrom('A'); mode.setTrustlineToPid('B'); await settle()
    expect(mode.state.phase).toBe('confirm-trustline-create')
    const beforeReads = calls.filter(c => c.url.includes('/trustlines-list')).length
    actionReply = { status: 404, body: refusal('PARTICIPANT_NOT_FOUND') }
    const before = context(app)
    await mode.confirmTrustlineCreate('10'); await settle()
    expect(context(app)).toEqual(before)
    expect(calls.filter(c => c.url.includes('/trustlines-list'))).toHaveLength(beforeReads + 1)
    expect(mode.state.errorDetail).toContain('PARTICIPANT_NOT_FOUND')
    expect(mode.state.phase).toBe('confirm-trustline-create')
  })

  it.each(['create', 'payment', 'clearing', 'update', 'close'] as const)('%s preserves run on a structured resource refusal', async (kind) => {
    const app = await boot()
    const a = app.interact.actions
    const before = context(app)
    const code = kind === 'clearing' ? 'EQUIVALENT_NOT_FOUND' : 'PARTICIPANT_NOT_FOUND'
    actionReply = { status: 404, body: refusal(code) }
    const request = kind === 'create' ? a.createTrustline('A', 'B', '10', 'UAH')
      : kind === 'payment' ? a.sendPayment('A', 'B', '1', 'UAH')
        : kind === 'clearing' ? a.runClearing('UAH')
          : kind === 'update' ? a.updateTrustline('A', 'B', '12', 'UAH') : a.closeTrustline('A', 'B', 'UAH')
    await expect(request).rejects.toMatchObject({ status: 404, code })
    await settle()
    expect(context(app)).toEqual(before)
  })

  it.each(['trustlines', 'targets', 'participants'] as const)('%s list resource404 is rejected rather than cached as an empty success', async (kind) => {
    const app = await boot()
    const before = context(app)
    actionReply = listReply = participantsReply = { status: 404, body: refusal('EQUIVALENT_NOT_FOUND') }
    await expect(kind === 'trustlines' ? app.interact.actions.fetchTrustlines('MISSING')
      : kind === 'targets' ? app.interact.actions.fetchPaymentTargets('MISSING', 'A') : app.interact.actions.fetchParticipants())
      .rejects.toMatchObject({ status: 404, code: 'EQUIVALENT_NOT_FOUND' })
    await settle()
    expect(context(app)).toEqual(before)
  })

  it.each(['mutation', 'trustlines', 'targets', 'participants'] as const)('%s RUN_NOT_FOUND still clears run, cursor, stats, persistence and SSE', async (kind) => {
    const app = await boot()
    actionReply = listReply = participantsReply = { status: 404, body: refusal('RUN_NOT_FOUND') }
    if (kind === 'mutation') await expect(app.interact.actions.updateTrustline('A', 'B', '12', 'UAH')).rejects.toMatchObject({ code: 'RUN_NOT_FOUND' })
    else await expect(kind === 'trustlines' ? app.interact.actions.fetchTrustlines('UAH')
      : kind === 'targets' ? app.interact.actions.fetchPaymentTargets('UAH', 'A') : app.interact.actions.fetchParticipants()).resolves.toEqual([])
    await settle()
    expect(context(app)).toEqual({ runId: null, status: null, cursor: null, attempts: 0, committed: 0, sse: 'idle', saved: '', activeStreams: 0 })
    expect(streams.every(s => s.signal?.aborted)).toBe(true)
    // Recovery can attach a later run after genuine disappearance.
    listReply = participantsReply = { status: 200, body: { items: [] } }
    vi.mocked(getRun).mockResolvedValue({ ...RUN, run_id: 'R2' } as never)
    await app.admin.attachRun('R2'); await settle()
    expect(app.real.runId).toBe('R2')
    expect(app.real.sseState).toBe('open')
  })

  it('a refresh failure preserves the original mutation error and an unknown source state', async () => {
    const app = await boot()
    const mode = app.interact.mode
    mode.selectTrustline('A', 'B'); await settle()
    listReply = { status: 503, body: refusal('TEMPORARY_FAILURE') }
    await mode.confirmTrustlineUpdate('12'); await settle()
    expect(mode.state.errorDetail).toContain('TRUSTLINE_NOT_FOUND')
    expect(mode.trustlinesLastError.value).toBeTruthy()
    expect(mode.findAnsweredTrustline('A', 'B')).toBeNull()
    expect(app.real.runId).toBe('R')
    expect(app.real.sseState).toBe('open')
  })

  it('a list read failure remains observable in the actual cache owner and retries on reopening', async () => {
    listReply = { status: 404, body: refusal('EQUIVALENT_NOT_FOUND') }
    const app = await boot()
    const mode = app.interact.mode
    mode.selectTrustline('A', 'B'); await settle()
    expect(mode.trustlinesLastError.value).toBeTruthy()
    expect(mode.trustlinesFetchState.value.kind).toBe('failed')
    expect(app.real.runId).toBe('R')
    expect(app.real.sseState).toBe('open')
    listReply = { status: 200, body: { items: [LINE] } }
    mode.selectTrustline('A', 'B'); await settle()
    expect(mode.findAnsweredTrustline('A', 'B')).toMatchObject(LINE)
    expect(mode.trustlinesLastError.value).toBeNull()
  })

  it.each([
    [404, { message: 'Run not found' }], [404, { code: 'OTHER_NOT_FOUND', message: 'Run not found' }],
    [503, refusal('RUN_NOT_FOUND')],
  ])('status %s and body %j do not establish run disappearance', async (status, body) => {
    const app = await boot()
    const before = context(app)
    actionReply = { status, body }
    await expect(app.interact.actions.closeTrustline('A', 'B', 'UAH')).rejects.toMatchObject({ status })
    await settle()
    expect(context(app)).toEqual(before)
  })

  it('an explicit retry succeeds after the resource refusal without reconnecting the run', async () => {
    const app = await boot()
    const mode = app.interact.mode
    mode.selectTrustline('A', 'B'); await settle()
    const streamCount = streams.length
    await mode.confirmTrustlineUpdate('12'); await settle()
    expect(mode.state.errorDetail).toContain('TRUSTLINE_NOT_FOUND')
    actionReply = { status: 200, body: { ok: true, trustline_id: 'TL', old_limit: '10.00', new_limit: '12.00' } }
    listReply = { status: 200, body: { items: [{ ...LINE, limit: '12.00', available: '12.00' }] } }
    await mode.confirmTrustlineUpdate('12'); await settle()
    expect(mode.state.error).toBeNull()
    expect(mode.state.phase).toBe('idle')
    expect(mode.findAnsweredTrustline('A', 'B')?.limit).toBe('12.00')
    expect(app.real.runId).toBe('R')
    expect(streams).toHaveLength(streamCount)
  })

  it('cancellation and a late resource404 cannot refresh or write an error into a closed flow', async () => {
    const app = await boot()
    const mode = app.interact.mode
    mode.selectTrustline('A', 'B'); await settle()
    const beforeReads = calls.filter(c => c.url.includes('/trustlines-list')).length
    const pending = deferredReply()
    actionReply = () => pending.promise
    const request = mode.confirmTrustlineUpdate('12')
    expect(mode.busy.value).toBe(true)
    // A duplicate confirm while busy must not send a second mutation.
    await mode.confirmTrustlineClose()
    mode.cancel()
    await request
    pending.resolve({ status: 404, body: refusal('TRUSTLINE_NOT_FOUND') })
    await settle()
    expect(calls.filter(c => c.method === 'POST' && c.url.includes('/actions/trustline-'))).toHaveLength(1)
    expect(calls.filter(c => c.url.includes('/trustlines-list'))).toHaveLength(beforeReads)
    expect(mode.state.phase).toBe('idle')
    expect(mode.state.error).toBeNull()
    expect(mode.busy.value).toBe(false)
    expect(app.real.runId).toBe('R')
    expect(app.real.sseState).toBe('open')
  })

  it('a late RUN_NOT_FOUND from the previous run cannot clear a newly attached run', async () => {
    const app = await boot()
    const pending = deferredReply()
    actionReply = () => pending.promise
    const request = app.interact.actions.updateTrustline('A', 'B', '12', 'UAH')
    vi.mocked(getRun).mockResolvedValue({ ...RUN, run_id: 'R2' } as never)
    await app.admin.attachRun('R2'); await settle()
    const before = context(app)
    expect(before.runId).toBe('R2')
    pending.resolve({ status: 404, body: refusal('RUN_NOT_FOUND') })
    await expect(request).rejects.toMatchObject({ code: 'RUN_NOT_FOUND' })
    await settle()
    expect(context(app)).toEqual(before)
  })

  it('a previous run resource404 cannot invalidate or force-read the new run cache', async () => {
    const app = await boot()
    const mode = app.interact.mode
    mode.selectTrustline('A', 'B'); await settle()
    const pending = deferredReply()
    actionReply = () => pending.promise
    const request = mode.confirmTrustlineUpdate('12')
    vi.mocked(getRun).mockResolvedValue({ ...RUN, run_id: 'R2' } as never)
    await app.admin.attachRun('R2'); await settle()
    const before = context(app)
    const beforeReads = calls.filter(c => c.url.includes('/trustlines-list')).length
    pending.resolve({ status: 404, body: refusal('TRUSTLINE_NOT_FOUND') })
    await request; await settle()
    expect(context(app)).toEqual(before)
    expect(calls.filter(c => c.url.includes('/trustlines-list'))).toHaveLength(beforeReads)
    expect(mode.findAnsweredTrustline('A', 'B')).toMatchObject(LINE)
  })
})
