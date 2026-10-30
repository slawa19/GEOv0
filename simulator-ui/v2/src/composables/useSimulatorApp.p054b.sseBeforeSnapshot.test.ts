/**
 * 054-B-2 (P2, AGENTS.md §8 Simulator SSE), end to end on the discover path (no stored run id, the backend finds an active
 * run). Before 054-B-2 the run-context watcher opened the event stream BEFORE the run's snapshot was loaded: a payment
 * delivered in that window was applied to the old scene object and lost when the snapshot (built before the payment)
 * replaced it, and the reconnect after the snapshot carried the advanced cursor, so it was not replayed (`used 40.00`).
 * Intended: the snapshot first, then the stream - the stream is opened only after the snapshot answered, and what it
 * delivers lands on the run's scene (`used 50.00`).
 * Actual useSimulatorApp (real patch applier, real scene state), REAL SSE transport (api/sse.ts) over a stubbed fetch, API
 * functions mocked at the module boundary, the snapshot answer held.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios } from '../api/simulatorApi'
import { useSimulatorApp } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    listScenarios: vi.fn(), getActiveRun: vi.fn(), getRun: vi.fn(), getSnapshot: vi.fn(), getScenarioPreview: vi.fn(),
    createRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(),
    ensureSession: vi.fn(async () => ({ actor_kind: 'anon', owner_id: 'o1' })),
  }
})
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))

const snap = (used: string) => ({
  equivalent: 'UAH', generated_at: '2026-10-16T00:00:00Z',
  nodes: [{ id: 'A', name: 'A', type: 'person', status: 'active' }, { id: 'B', name: 'B', type: 'person', status: 'active' }],
  links: [{ source: 'A', target: 'B', trust_limit: '100.00', used, available: (100 - Number(used)).toFixed(2), status: 'active' }],
})
const status = (runId: string) => ({
  run_id: runId, scenario_id: 'sc1', state: 'running', api_version: 'simulator-api/1', mode: 'real', sim_time_ms: 0,
  intensity_percent: 30, ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null,
})

type Conn = { lastEventId: string | null; url: string; push: (t: string) => void }
function stubSseFetch() {
  const conns: Conn[] = []
  const enc = new TextEncoder()
  vi.stubGlobal('fetch', vi.fn(async (url: unknown, init?: RequestInit) => {
    if (!String(url).includes('/events')) return Promise.reject(new TypeError('Failed to fetch'))
    let ctl!: ReadableStreamDefaultController<Uint8Array>
    const stream = new ReadableStream<Uint8Array>({ start(c) { ctl = c } })
    conns.push({ url: String(url), lastEventId: new Headers(init?.headers).get('Last-Event-ID'), push: (t) => ctl.enqueue(enc.encode(t)) })
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
  }))
  return conns
}
const frame = (id: string, payload: Record<string, unknown>) =>
  `id: ${id}\nevent: simulator.event\ndata: ${JSON.stringify({ event_id: id, ...payload })}\n\n`

async function settle() {
  for (let r = 0; r < 3; r += 1) {
    for (let i = 0; i < 20; i += 1) { await Promise.resolve(); await nextTick() }
    await vi.advanceTimersByTimeAsync(200)
  }
}

let scope: EffectScope | null = null
beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0.5)
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  window.localStorage.clear()
  window.history.replaceState({}, '', '/')
})
afterEach(() => { scope?.stop(); scope = null; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.localStorage.clear() })

describe('054-B-2: the stream opens after the snapshot on the discover path', () => {
  it('no stream before the snapshot answered; the payment the stream delivers is on screen', async () => {
    const conns = stubSseFetch()
    vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [{ scenario_id: 'sc1', label: 'S1' }] } as never)
    vi.mocked(getActiveRun).mockResolvedValue({ run_id: 'R1' } as never) // no stored id: the backend finds the run
    vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => status(id)) as never)
    vi.mocked(getScenarioPreview).mockImplementation((async () => snap('0.00')) as never)
    const held: Array<() => void> = []
    // The server built this snapshot BEFORE the payment below (used 40.00); its answer is still in flight.
    vi.mocked(getSnapshot).mockImplementation((() => new Promise((res) => held.push(() => res(snap('40.00') as never)))) as never)

    scope = effectScope()
    const app = scope.run(() => useSimulatorApp())!
    await settle()

    expect(app.real.runId, 'stand: the run was discovered').toBe('R1')
    expect(held.length, 'stand: the snapshot request is in flight').toBeGreaterThan(0)
    const connsBeforeSnapshot = conns.length
    expect(connsBeforeSnapshot, 'no event stream is opened before the snapshot of the run answered').toBe(0)

    for (const r of held.splice(0)) r() // the snapshot (used 40.00) arrives
    await settle()
    expect(conns.length, 'the stream opens after the snapshot').toBe(1)

    // Stream: the bootstrap status, then a payment the snapshot does not contain (used 40 -> 50).
    conns[0]!.push(frame('evt_R1_000010', { ts: '2026-10-16T00:00:01Z', type: 'run_status', run_id: 'R1', scenario_id: 'sc1', state: 'running', last_error: null }))
    conns[0]!.push(frame('evt_R1_000011', {
      ts: '2026-10-16T00:00:02Z', type: 'tx.updated', equivalent: 'UAH', from: 'A', to: 'B', amount: '10.00', edges: [{ from: 'A', to: 'B' }],
      edge_patch: [{ source: 'A', target: 'B', used: '50.00', available: '50.00' }],
    }))
    await settle()

    const link = app.state.snapshot?.links.find((l) => l.source === 'A' && l.target === 'B')
    expect(app.real.lastEventId, 'stand: the payment was accepted by the pipeline').toBe('evt_R1_000011')
    expect(conns.length, 'one stream: nothing reconnected over the applied payment').toBe(1)
    expect(link?.used, 'the payment delivered by the stream is on screen').toBe('50.00')
  })
})
