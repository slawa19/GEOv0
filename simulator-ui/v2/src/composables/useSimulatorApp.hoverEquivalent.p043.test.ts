/**
 * 043 S1, external review finding 1 (class 1): a trust line's tooltip must never print the figures of one equivalent
 * under the name of another.
 *
 * The hover copies a line's figures when the pointer MOVES (`useEdgeHover.ts`). A person who keeps the pointer still
 * and replaces the scene from the keyboard (another equivalent, another scenario) kept the old figures on screen;
 * since 043 S1 the tooltip printed them with a unit, and the unit followed the live scene - UAH figures labelled EUR.
 *
 * Observed through the REAL composable (`useSimulatorApp`, transport mocked at the module boundary as in
 * `useSimulatorApp.runMode.p042.test.ts`): a hover held over a scene replacement is gone after it, and a hover
 * carries the equivalent of the scene its figures were read from.
 */
import { effectScope, nextTick, type EffectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getActiveRun, getScenarioPreview, listScenarios } from '../api/simulatorApi'
import type { SimulatorGraphSnapshot } from '../api/simulatorTypes'
import { useSimulatorApp } from './useSimulatorApp'

vi.mock('../api/simulatorApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/simulatorApi')>()
  return {
    ...actual,
    listScenarios: vi.fn(),
    getActiveRun: vi.fn(),
    getRun: vi.fn(async () => { throw new Error('no run in this test') }),
    getSnapshot: vi.fn(async () => { throw new Error('no run snapshot in this test') }),
    getScenarioPreview: vi.fn(),
    ensureSession: vi.fn(async () => ({ actor_kind: 'anonymous', owner_id: 'o1' })),
  }
})
vi.mock('../api/equivalentsApi', () => ({ fetchEquivalentPrecisions: vi.fn(async () => []) }))
vi.mock('../api/sse', () => ({
  connectSse: vi.fn((opts: { signal?: AbortSignal }) => new Promise<void>((_r, reject) => {
    const abort = () => reject(new DOMException('aborted', 'AbortError'))
    if (opts.signal?.aborted) return abort()
    opts.signal?.addEventListener('abort', abort, { once: true })
  })),
}))

/** The same two participants in every scene; only the equivalent (and the scenario prefix) differ. */
function preview(scenarioId: string, equivalent: string): SimulatorGraphSnapshot {
  return {
    equivalent,
    generated_at: '2026-10-16T00:00:00Z',
    nodes: [{ id: 'm', name: 'M', type: 'person', status: 'active' }, { id: 'o', name: 'O', type: 'person', status: 'active' }],
    links: [{ source: 'm', target: 'o', trust_limit: equivalent === 'UAH' ? '500.00' : '7.00', used: '120.00', available: '380.00', status: 'active' }],
    equivalents: ['UAH', 'EUR'],
    scenario: scenarioId,
  } as unknown as SimulatorGraphSnapshot
}

async function settle(): Promise<void> {
  for (let i = 0; i < 20; i += 1) {
    await Promise.resolve()
    await nextTick()
  }
}

let scope: EffectScope | null = null

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))))
  vi.spyOn(console, 'warn').mockImplementation(() => undefined)
  window.history.replaceState({}, '', '/?mode=real')
  window.localStorage.setItem('geo.sim.v2.selectedScenarioId', 'sc1')
  vi.mocked(listScenarios).mockResolvedValue({
    api_version: 'simulator-api/1',
    items: [{ scenario_id: 'sc1', label: 'One', equivalents: ['UAH', 'EUR'] }, { scenario_id: 'sc2', label: 'Two', equivalents: ['UAH', 'EUR'] }],
  } as never)
  vi.mocked(getActiveRun).mockResolvedValue({} as never)
  vi.mocked(getScenarioPreview).mockImplementation((async (_cfg: unknown, scenarioId: string, equivalent: string) => preview(scenarioId, equivalent)) as never)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  scope?.stop()
  scope = null
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
  vi.clearAllMocks()
})

function boot() {
  scope = effectScope()
  return scope.run(() => useSimulatorApp())!
}

/** What the hover leaves behind when the pointer stops over the line (the fields `useEdgeHover` writes). */
function holdHover(app: ReturnType<typeof boot>, equivalent: string) {
  const h = app.hoveredEdge as unknown as Record<string, unknown>
  Object.assign(h, { key: 'm→o', fromId: 'm', toId: 'o', amountText: `from: 120.00 / to: 500.00 ${equivalent}`, screenX: 10, screenY: 10, trustLimit: '500.00', used: '120.00', available: '380.00', edgeStatus: 'active', equivalent })
}

describe('043 S1 review finding 1: a held tooltip does not outlive its scene', () => {
  it('another equivalent replaces the scene: the held tooltip is gone', async () => {
    const app = boot()
    await settle()
    await vi.waitFor(() => expect(app.state.snapshot?.equivalent).toBe('UAH'))
    holdHover(app, 'UAH')
    expect(app.hoveredEdge.key, 'counter-check: the hover is held').toBe('m→o')

    app.eq.value = 'EUR'
    await vi.waitFor(() => expect(app.state.snapshot?.equivalent, 'the scene was replaced by the EUR one').toBe('EUR'))
    expect(app.hoveredEdge.key, 'the UAH figures of the old scene are not on screen under the EUR scene').toBeNull()
  })

  it('another scenario replaces the scene: the held tooltip is gone', async () => {
    const app = boot()
    await settle()
    await vi.waitFor(() => expect(app.state.snapshot).not.toBeNull())
    holdHover(app, 'UAH')

    app.realActions.setSelectedScenarioId('sc2')
    await vi.waitFor(() => expect(vi.mocked(getScenarioPreview).mock.calls.at(-1)?.[1]).toBe('sc2'))
    await settle()
    expect(app.hoveredEdge.key).toBeNull()
  })

  it('clearing the hover drops its equivalent with its figures', async () => {
    const app = boot()
    await settle()
    holdHover(app, 'UAH')
    app.clearHoveredEdge()
    const h = app.hoveredEdge as unknown as Record<string, unknown>
    expect([h.key, h.trustLimit ?? null, h.equivalent ?? null]).toEqual([null, null, null])
  })
})
