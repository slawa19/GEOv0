import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { createFxState } from '../../render/fxRenderer'
import { BEAM_FADE_MS } from './txRoutePlan'
import { CLEARING_EFFECT_MS, CLEARING_LOOP_MS, CLEARING_PULSE_MS, cyclesOf, useRealClearingFx } from './useRealClearingFx'
import type { ClearingAmountOverlay } from '../useOverlayState'

type OverlayCall = {
  overlay: ClearingAmountOverlay
  opts: { color: string; throttleMs?: number }
}

/** The composable over the real spawn functions on a hand-driven clock (`advance` fires due timers in order). */
function harness(o: { lengths?: Record<string, number>; layout?: Record<string, { __x: number; __y: number }> } = {}) {
  const fxState = createFxState()
  let now = 1000
  let seq = 0
  let timers: Array<{ at: number; seq: number; fn: () => void; critical: boolean }> = []
  const overlays: OverlayCall[] = []
  const active = { nodes: [] as Array<[string, number]>, edges: [] as Array<[string, number]> }
  let color = '#f97316'
  const fx = useRealClearingFx({
    fxState,
    isTestMode: ref(false),
    isWebDriver: false,
    keyEdge: (a, b) => `${a}>${b}`,
    seedFn: () => 0,
    clearingColor: () => color,
    sparkCoreColor: () => '#fff',
    addActiveNode: (id, ttl) => { active.nodes.push([id, ttl]) },
    addActiveEdge: (k, ttl) => { active.edges.push([k, ttl]) },
    pushClearingAmountOverlay: (overlay, opts) => { overlays.push({ overlay, opts }) },
    clearingLabelLifeMs: () => 4200,
    scheduleTimeout: (fn, ms, opt) => { timers.push({ at: now + ms, seq: seq += 1, fn, critical: !!opt?.critical }) },
    getLayoutNodeById: (id) => (o.layout ?? { A: { __x: 0, __y: 0 }, B: { __x: 10, __y: 0 }, C: { __x: 5, __y: 9 } })[id] ?? null,
    edgeLengthPx: (a, b) => o.lengths?.[`${a}>${b}`] ?? 100,
    nowMs: () => now,
    nowEpochMs: () => now,
  })
  const advance = (ms: number) => {
    const until = now + ms
    for (;;) {
      const due = timers.filter((t) => t.at <= until).sort((a, b) => a.at - b.at || a.seq - b.seq)[0]
      if (!due) break
      timers = timers.filter((t) => t !== due)
      now = due.at
      due.fn()
    }
    now = until
  }
  return {
    fx, fxState, advance, overlays, active, setColor: (c: string) => { color = c },
    sparks: () => fxState.sparks.map((s) => `${s.source}>${s.target}@${Math.round(s.startedAtMs - 1000)}+${Math.round(s.moveMs!)}`),
    bursts: () => fxState.nodeBursts.map((b) => `${b.nodeId}@${Math.round(b.startedAtMs - 1000)}`),
    clearTimers: () => { timers = [] },
  }
}

const TRIANGLE = [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }]

describe('cyclesOf - the cycles of the set of lines a clearing touched', () => {
  it('walks each cycle in order, whatever order the lines come in (the backend sends them sorted, not walked)', () => {
    const sorted = [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }].sort((x, y) => (x.to < y.to ? -1 : 1))
    const { cycles, rest } = cyclesOf(sorted)
    expect(rest).toEqual([])
    expect(cycles).toHaveLength(1)
    const c = cycles[0]!
    for (let i = 0; i < c.length; i += 1) expect(c[i]!.to, 'each line starts where the one before it ended').toBe(c[(i + 1) % c.length]!.from)
  })

  it('several cycles sharing a node are each walked once, each line used once; a line that closes no cycle is left over', () => {
    // The committed demo clearing (UAH): 5 cycles of 3 over 15 lines, one node in three of them.
    const lines = [
      ['X', 'P'], ['Q', 'X'], ['P', 'Q'],
      ['X', 'R'], ['S', 'X'], ['R', 'S'],
      ['X', 'T'], ['U', 'X'], ['T', 'U'],
      ['V', 'W'],
    ].map(([from, to]) => ({ from: from!, to: to! }))
    const { cycles, rest } = cyclesOf(lines)
    expect(cycles.map((c) => c.length)).toEqual([3, 3, 3])
    expect(rest).toEqual([{ from: 'V', to: 'W' }])
    const used = cycles.flat().map((e) => `${e.from}>${e.to}`).sort()
    expect(new Set(used).size, 'no line twice').toBe(9)
  })
})

describe('two cycles sharing a line: the SSE set and the Interact flattening are drawn the same (Codex §15, 2026-10-18)', () => {
  // Cycles A>B>C>A (5) and A>B>D>A (7) share A>B. SSE sends the set (tick.py); Interact flattens res.cycles (A>B twice).
  const SSE = [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'B', to: 'D' }, { from: 'C', to: 'A' }, { from: 'D', to: 'A' }]
  const HTTP = [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }, { from: 'A', to: 'B' }, { from: 'B', to: 'D' }, { from: 'D', to: 'A' }]
  const layout = { A: { __x: 0, __y: 0 }, B: { __x: 10, __y: 0 }, C: { __x: 5, __y: 9 }, D: { __x: 5, __y: -9 } }
  const draw = (edges: typeof SSE, how: 'sse' | 'http') => {
    const h = harness({ layout })
    if (how === 'sse') h.fx.runRealClearingDoneFx({ event_id: 'e', ts: 't', type: 'clearing.done', equivalent: 'UAH', plan_id: 'p', cleared_amount: '12.00', cycle_edges: edges } as never)
    else h.fx.runClearingFx({ edges, totalAmount: '12.00', equivalent: 'UAH' })
    h.advance(5000)
    return { sparks: h.fxState.sparks.map((s) => `${s.source}>${s.target}`).sort(), pulses: h.fxState.edgePulses.map((p) => `${p.from}>${p.to}`).sort() }
  }
  it('the same sparks and the same pulses whichever path the clearing came by', () => {
    expect(draw(HTTP, 'http')).toEqual(draw(SSE, 'sse'))
  })
  it('the same also when Interact lists the cycles in the order unfavourable to the reconstruction (fix-delta review, 2026-10-18)', () => {
    // Execution order A>B>D>A first: in first-seen order the union rebuilds ABD; the server's sorted set rebuilds ABC.
    const HTTP_REORDERED = [{ from: 'A', to: 'B' }, { from: 'B', to: 'D' }, { from: 'D', to: 'A' }, { from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'A' }]
    expect(draw(HTTP_REORDERED, 'http')).toEqual(draw(SSE, 'sse'))
  })
  it('what is drawn: one spark round the one cycle the union rebuilds; every line of both cycles pulses, the shared one once', () => {
    const d = draw(SSE, 'sse')
    expect(d.sparks).toHaveLength(3)
    expect(d.pulses).toEqual(['A>B', 'B>C', 'B>D', 'C>A', 'D>A'])
  })
})

describe('useRealClearingFx (039 slice C: one spark round the cycle in 1.2 s, then the labels)', () => {
  it('the loop: one spark goes round the cycle at one speed in 1200 ms, each node lights up as the spark leaves it', () => {
    const h = harness({ lengths: { 'A>B': 100, 'B>C': 200, 'C>A': 300 } })
    h.fx.runClearingFx({ edges: TRIANGLE, totalAmount: '7.00', equivalent: 'UAH', planId: 'p1' })
    h.advance(CLEARING_LOOP_MS)
    expect(h.sparks()).toEqual(['A>B@0+200', 'B>C@200+400', 'C>A@600+600'])
    expect(h.bursts()).toEqual(['A@0', 'B@200', 'C@600'])
    expect(h.fxState.sparks.map((s) => s.ttlMs - s.moveMs!), 'the last leg fades out, the others hand over').toEqual([0, 0, BEAM_FADE_MS])
    expect(h.fxState.sparks.every((s) => s.motion === 'linear')).toBe(true)
  })

  it('then the labels: nothing pulses and no amount is shown while the spark goes round; at 1200 ms the lines pulse, the cycle is highlighted and the amount comes', () => {
    const h = harness()
    h.fx.runClearingFx({ edges: TRIANGLE, totalAmount: '7.00', equivalent: 'UAH', planId: 'p1' })
    h.advance(CLEARING_LOOP_MS - 1)
    expect({ pulses: h.fxState.edgePulses.length, overlays: h.overlays.length, active: h.active.edges.length }).toEqual({ pulses: 0, overlays: 0, active: 0 })
    h.advance(1)
    expect(h.fxState.edgePulses.map((p) => `${p.from}>${p.to}+${p.durationMs}`)).toEqual(TRIANGLE.map((e) => `${e.from}>${e.to}+${CLEARING_PULSE_MS}`))
    expect(h.active.nodes.map(([id]) => id)).toEqual(['A', 'B', 'C'])
    expect(h.overlays).toHaveLength(1)
    expect(h.overlays[0]!.overlay).toMatchObject({ text: '−7.00 UAH', ttlMs: 4200, styleKey: 'clearing-premium' })
    expect(CLEARING_EFFECT_MS).toBe(CLEARING_LOOP_MS + CLEARING_PULSE_MS * 1.3)
  })

  it('the clearing colour is read when the effect is spawned (a theme change reaches the next clearing)', () => {
    const h = harness()
    h.setColor('#123456')
    h.fx.runClearingFx({ edges: TRIANGLE, totalAmount: '7.00', equivalent: 'UAH', planId: 'p1' })
    expect(h.fxState.sparks[0]!.colorTrail).toBe('#123456')
  })

  it('a reset (new run, new playback) cancels the pending loop and its tail: no later leg, no pulse, no label', () => {
    const h = harness()
    h.fx.runClearingFx({ edges: TRIANGLE, totalAmount: '7.00', equivalent: 'UAH', planId: 'p1' })
    h.fx.resetDedup()
    h.advance(5000)
    expect(h.sparks()).toEqual(['A>B@0+400'])
    expect({ pulses: h.fxState.edgePulses.length, overlays: h.overlays.length }).toEqual({ pulses: 0, overlays: 0 })
  })

  it('lines that close no cycle get no spark: they pulse and the amount comes at once', () => {
    const h = harness()
    h.fx.runClearingFx({ edges: [{ from: 'A', to: 'B' }], totalAmount: '7.00', equivalent: 'UAH', planId: 'p1' })
    expect(h.fxState.sparks).toHaveLength(0)
    expect(h.fxState.edgePulses).toHaveLength(1)
    expect(h.overlays).toHaveLength(1)
  })

  it('deferred clearing amount overlay: pushed only after layout coords become available', () => {
    const overlays: OverlayCall[] = []
    let ready = false
    vi.useFakeTimers()
    vi.setSystemTime(0)
    try {
      const fx = useRealClearingFx({
        fxState: createFxState(), isTestMode: ref(false), isWebDriver: false, keyEdge: (a, b) => `${a}>${b}`, seedFn: () => 0,
        clearingColor: () => '#000', sparkCoreColor: () => '#fff', addActiveNode: () => undefined, addActiveEdge: () => undefined,
        pushClearingAmountOverlay: (overlay, opts) => { overlays.push({ overlay, opts }) }, clearingLabelLifeMs: () => 4200,
        scheduleTimeout: (fn, ms) => { setTimeout(fn, ms) },
        getLayoutNodeById: (id) => (!ready ? null : id === 'A' ? { __x: 0, __y: 0 } : id === 'B' ? { __x: 10, __y: 0 } : null),
        nowMs: () => Date.now(), nowEpochMs: () => Date.now(),
      })
      setTimeout(() => { ready = true }, 120)
      fx.runClearingFx({ edges: [{ from: 'A', to: 'B' }], totalAmount: '10.00', equivalent: 'UAH', planId: 'p1' })
      vi.advanceTimersByTime(100)
      expect(overlays, 'not before the coords exist').toHaveLength(0)
      vi.advanceTimersByTime(100)
      expect(overlays).toHaveLength(1)
      expect(overlays[0]!.overlay).toMatchObject({ text: '−10.00 UAH', worldX: 5, worldY: 0 })
    } finally {
      vi.useRealTimers()
    }
  })

  it('deferred clearing amount overlay: times out and does not push when coords never appear', () => {
    const overlays: OverlayCall[] = []
    vi.useFakeTimers()
    vi.setSystemTime(0)
    try {
      const fx = useRealClearingFx({
        fxState: createFxState(), isTestMode: ref(false), isWebDriver: false, keyEdge: (a, b) => `${a}>${b}`, seedFn: () => 0,
        clearingColor: () => '#000', sparkCoreColor: () => '#fff', addActiveNode: () => undefined, addActiveEdge: () => undefined,
        pushClearingAmountOverlay: (overlay, opts) => { overlays.push({ overlay, opts }) }, clearingLabelLifeMs: () => 4200,
        scheduleTimeout: (fn, ms) => { setTimeout(fn, ms) }, getLayoutNodeById: () => null,
        nowMs: () => Date.now(), nowEpochMs: () => Date.now(),
      })
      fx.runClearingFx({ edges: [{ from: 'A', to: 'B' }], totalAmount: '10.00', equivalent: 'UAH', planId: 'p1' })
      vi.advanceTimersByTime(3000)
      expect(overlays.length).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })

  it('animation off (reduced motion): the amount label at once, nothing moves or lights up', () => {
    const pushClearingAmountOverlay = vi.fn()
    const spawnEdgePulses = vi.fn()
    const spawnNodeBursts = vi.fn()
    const spawnSparks = vi.fn()
    const addActiveNode = vi.fn()
    const addActiveEdge = vi.fn()
    const fx = useRealClearingFx({
      fxState: {}, isTestMode: ref(false), isWebDriver: false, keyEdge: (from, to) => `${from}>${to}`, seedFn: () => 0,
      clearingColor: () => '#000', sparkCoreColor: () => '#fff', addActiveNode, addActiveEdge, pushClearingAmountOverlay,
      clearingLabelLifeMs: () => 4200, scheduleTimeout: (fn) => fn(),
      getLayoutNodeById: (id) => (id === 'A' ? { __x: 0, __y: 0 } : { __x: 10, __y: 0 }),
      spawnEdgePulses, spawnNodeBursts, spawnSparks,
    })
    fx.runClearingFx({ edges: TRIANGLE, totalAmount: '10.00', equivalent: 'UAH', planId: 'p-reduced' }, { animate: false })
    expect(pushClearingAmountOverlay).toHaveBeenCalledOnce()
    expect(pushClearingAmountOverlay.mock.calls[0]?.[0]).toMatchObject({ text: '−10.00 UAH' })
    for (const f of [spawnEdgePulses, spawnNodeBursts, spawnSparks, addActiveNode, addActiveEdge]) expect(f).not.toHaveBeenCalled()
  })

  it('dedup reset: HTTP path (no planId) is suppressed until resetDedup()', () => {
    const spawnSparks = vi.fn()
    const fx = useRealClearingFx({
      fxState: {}, isTestMode: ref(false), isWebDriver: false, keyEdge: (from, to) => `${from}>${to}`, seedFn: () => 0,
      clearingColor: () => '#000', sparkCoreColor: () => '#fff', addActiveNode: () => undefined, addActiveEdge: () => undefined,
      clearingLabelLifeMs: () => 4200, scheduleTimeout: () => undefined, getLayoutNodeById: () => null,
      nowMs: () => 0, nowEpochMs: () => 0, spawnSparks, spawnEdgePulses: vi.fn(), spawnNodeBursts: vi.fn(),
    })
    fx.runClearingFx({ edges: TRIANGLE, totalAmount: '0', equivalent: 'UAH' })
    fx.runClearingFx({ edges: TRIANGLE, totalAmount: '0', equivalent: 'UAH' })
    expect(spawnSparks).toHaveBeenCalledTimes(1)

    fx.resetDedup()
    fx.runClearingFx({ edges: TRIANGLE, totalAmount: '0', equivalent: 'UAH' })
    expect(spawnSparks).toHaveBeenCalledTimes(2)
  })
})
