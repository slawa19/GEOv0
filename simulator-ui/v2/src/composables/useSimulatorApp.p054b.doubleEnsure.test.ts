/**
 * 054 B-1 (R1 B1a-2 / R2 B1-1): a first visit asks POST /session/ensure ONCE. Before the fix there were two independent
 * cookie bootstraps - the boot watcher's `ensureAnonSessionOnce` (`useSimulatorRealMode.ts`) and an `onMounted` ->
 * `useCookieSessionBootstrap.tryEnsure` (`useSimulatorApp.ts`). The server (`app/api/v1/simulator.py`, `/session/ensure`)
 * makes a NEW session for every request without a cookie, and the browser keeps the last processed Set-Cookie: a late
 * second answer moved the cookie away from the owner of the run the autostart had already created, so that run answered
 * 403 until a reload.
 *
 * Stand: the actual `useSimulatorApp` mounted in a real component (onMounted runs); the API mocked at the module boundary;
 * ensureSession answers are held and released in a chosen order (the boot's first, any second late); each request records
 * the "cookie" (owner) the browser would send at that moment. What this does NOT see: the real HTTP cookie jar and server
 * (`stale-runid-first-load.spec.ts` and the live e2e have a browser, not this order of answers).
 */
import { createApp, defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createRun, ensureSession, getActiveRun, getRun, getScenarioPreview, getSnapshot, listScenarios, pauseRun } from '../api/simulatorApi'
import { useSimulatorApp } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    listScenarios: vi.fn(), getActiveRun: vi.fn(), getRun: vi.fn(), getSnapshot: vi.fn(), getScenarioPreview: vi.fn(),
    createRun: vi.fn(), pauseRun: vi.fn(), resumeRun: vi.fn(), stopRun: vi.fn(), ensureSession: vi.fn(),
  }
})
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({
  connectSse: vi.fn((opts: { signal?: AbortSignal }) => new Promise<void>((_r, reject) => {
    opts.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
  })),
}))

const snap = (id: string) => ({
  equivalent: 'UAH', generated_at: '2026-10-16T00:00:00Z',
  nodes: [{ id, name: id, type: 'person', status: 'active' }, { id: 'B', name: 'B', type: 'person', status: 'active' }],
  links: [{ source: id, target: 'B', trust_limit: '100', used: '0', available: '100', status: 'active' }],
})
const status = (runId: string, state = 'running') => ({
  run_id: runId, scenario_id: 'sc1', state, api_version: 'simulator-api/1', mode: 'real', sim_time_ms: 0,
  intensity_percent: 30, ops_sec: 0, queue_depth: 0, last_event_type: null, current_phase: null, last_error: null,
})

async function settle() {
  for (let r = 0; r < 3; r += 1) {
    for (let i = 0; i < 20; i += 1) { await Promise.resolve(); await nextTick() }
    await vi.advanceTimersByTimeAsync(200)
  }
}

/** Browser cookie jar: '' until a Set-Cookie is processed; the last processed one wins. */
let cookie = ''
const runOwner = new Map<string, string>()
const pending: Array<{ owner: string; release: () => void }> = []

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  cookie = ''; runOwner.clear(); pending.length = 0
  let k = 0
  vi.mocked(ensureSession).mockImplementation((() => {
    // Server side (app/api/v1/simulator.py:150-181): a request without a cookie gets a NEW session (new owner).
    const sentCookie = cookie
    const owner = sentCookie || `anon:${String.fromCharCode(65 + k++)}`
    return new Promise((res) => {
      pending.push({ owner, release: () => { if (!sentCookie) cookie = owner; res({ actor_kind: 'anon', owner_id: owner }) } })
    })
  }) as never)
  vi.mocked(listScenarios).mockResolvedValue({ api_version: 'simulator-api/1', items: [{ scenario_id: 'sc1', label: 'S1' }] } as never)
  vi.mocked(getActiveRun).mockResolvedValue({ run_id: null } as never)
  vi.mocked(getRun).mockImplementation((async (_c: unknown, id: string) => status(id)) as never)
  vi.mocked(getSnapshot).mockImplementation((async (_c: unknown, id: string) => snap(`${id}_N`)) as never)
  vi.mocked(getScenarioPreview).mockImplementation((async () => snap('P')) as never)
  vi.mocked(createRun).mockImplementation((async () => { runOwner.set('R_NEW', cookie); return { run_id: 'R_NEW' } }) as never)
  vi.mocked(pauseRun).mockImplementation((async (_c: unknown, id: string) => {
    if (runOwner.get(id) !== cookie) throw Object.assign(new Error(`HTTP 403 not run owner (cookie ${cookie}, owner ${runOwner.get(id)})`), { status: 403 })
    return status(id, 'paused')
  }) as never)
  window.history.replaceState({}, '', '/')
})
afterEach(() => { vi.useRealTimers(); vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.restoreAllMocks(); window.localStorage.clear() })

describe('054 B-1: one cookie bootstrap on the first visit', () => {
  it('one visit asks /session/ensure once, and the run belongs to the cookie the browser keeps', async () => {
    vi.stubEnv('DEV', false) // a public deployment: no localhost dev-token substitution (useSimulatorApp.ts:711)
    let app!: ReturnType<typeof useSimulatorApp>
    const el = document.createElement('div'); document.body.appendChild(el)
    const vm = createApp(defineComponent({ setup() { app = useSimulatorApp(); return () => h('div') } })).mount(el)
    void vm
    await Promise.resolve(); await nextTick()
    const sentBeforeAnyAnswer = vi.mocked(ensureSession).mock.calls.length

    pending[0]?.release()            // the boot watcher's ensure answers first (owner A)
    await settle()                   // boot + autostart proceed: createRun with cookie A
    const runCreatedAs = runOwner.get('R_NEW')
    pending[1]?.release()            // the onMounted ensure answers late (owner B): Set-Cookie B
    await settle()
    const cookieAfter = cookie

    console.info(JSON.stringify({ sentBeforeAnyAnswer, runCreatedAs, cookieAfter, runId: app.real.runId }))
    expect(createRun, 'stand: autostart did create a run').toHaveBeenCalledTimes(1)
    expect(sentBeforeAnyAnswer, 'POST /session/ensure sent before any answer').toBe(1)
    expect(cookieAfter, 'the cookie the browser keeps is the owner of the run').toBe(runCreatedAs)
    expect(ensureSession, 'one visit, one POST /session/ensure (no late second bootstrap either)').toHaveBeenCalledTimes(1)
  })
})
