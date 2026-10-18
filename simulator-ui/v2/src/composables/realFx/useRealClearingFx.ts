import type { Ref } from 'vue'

import type { ClearingDoneEvent } from '../../types'
import { canonicalEquivalent } from '../../config/equivalents'
import { CLEARING_LABEL_COLOR } from '../../config/fxConfig'
import type { FxState } from '../../render/fxRenderer'
import {
  spawnEdgePulses as spawnEdgePulsesDefault,
  spawnNodeBursts as spawnNodeBurstsDefault,
  spawnSparks as spawnSparksDefault,
} from '../../render/fxRenderer'
import { EDGE_PULSE_AFTERGLOW_K } from '../../render/fxConfig'
import type { LayoutNode } from '../../types/layout'
import { computeClearingAmountAnchorFromEdgeMidpoints } from '../../utils/clearingAmountAnchor'
import { isZeroDecimalString } from '../../utils/isZeroDecimalString'
import { __retryUntilTruthyOrDeadline } from '../../utils/retryUntilTruthy'
import type { ClearingAmountOverlay } from '../useOverlayState'
import { BEAM_NODE_BURST_MS } from './useRealTxFx'
import { BEAM_FADE_MS } from './txRoutePlan'

export type ClearingFxParams = {
  edges: Array<{ from: string; to: string }>
  totalAmount: string // e.g. "10.00"
  equivalent: string // e.g. "UAH"
  planId?: string // optional; used for throttle key
}

type ClearingFxOptions = { animate?: boolean }

/**
 * 039 slice C - the clearing effect, in this order (spec 039, slice C: "one spark goes round the cycle in 1.2 s, then
 * the labels"):
 *  1. `CLEARING_LOOP_MS`: one spark per cycle goes round it once, along the cycle's lines (creditor -> debtor, the
 *     direction the snapshot draws them), at one speed; every node of the cycle lights up as the spark leaves it;
 *  2. then the lines of the cycles pulse for `CLEARING_PULSE_MS` (and fade over the renderer's afterglow), the cycle is
 *     highlighted on the base canvas for as long, and the amount label comes - it lives as long as its CSS fade.
 * What is gone with this (it was there until 039 slice C): the full-screen flash, the bloom-and-shockwave bursts of
 * every node with `screen` composite at the first instant, and the 4.2 s pulse of all lines at once - the cycle was
 * lit before anything had moved round it.
 */
export const CLEARING_LOOP_MS = 1200
export const CLEARING_PULSE_MS = 1600
/** The whole effect: the loop, then the pulse with its afterglow. The offline workbench spaces its events by it. */
export const CLEARING_EFFECT_MS = CLEARING_LOOP_MS + CLEARING_PULSE_MS * (1 + EDGE_PULSE_AFTERGLOW_K)

type SpawnEdgePulsesFn = typeof spawnEdgePulsesDefault
type SpawnNodeBurstsFn = typeof spawnNodeBurstsDefault
type SpawnSparksFn = typeof spawnSparksDefault
type FxStateLike = Partial<Pick<FxState, 'sparks' | 'edgePulses' | 'nodeBursts'>>
type LayoutNodePosition = Pick<LayoutNode, '__x' | '__y'>
type Edge = { from: string; to: string }

function nodesFromEdges(edges: Edge[]): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const e of edges) {
    if (!seen.has(e.from)) {
      seen.add(e.from)
      out.push(e.from)
    }
    if (!seen.has(e.to)) {
      seen.add(e.to)
      out.push(e.to)
    }
  }
  return out
}

/**
 * The cycles of a set of lines, each in walking order (`clearing.done.cycle_edges` is the SET of lines a pass touched,
 * sorted, of possibly several cycles - `app/core/simulator/tick.py`, `_done_cycle_edges`). Each line is used once:
 * from the first unused line, the shortest way back to its start over unused lines closes a cycle. A line that closes
 * no cycle (the set was cut at the backend's limit, a line flipped to the topology) is returned in `rest`: it pulses
 * with the others, no spark goes over it.
 */
export function cyclesOf(edges: Edge[]): { cycles: Edge[][]; rest: Edge[] } {
  const unused = edges.map((e, i) => ({ ...e, i }))
  const used = new Set<number>()
  const cycles: Edge[][] = []
  const rest: Edge[] = []
  for (const first of unused) {
    if (used.has(first.i)) continue
    // Breadth-first from first.to back to first.from over unused lines other than `first`.
    const prev = new Map<string, { node: string; edge: number } | null>([[first.to, null]])
    const queue = [first.to]
    let found = first.to === first.from
    while (queue.length > 0 && !found) {
      const at = queue.shift()!
      for (const e of unused) {
        if (e.i === first.i || used.has(e.i) || e.from !== at || prev.has(e.to)) continue
        prev.set(e.to, { node: at, edge: e.i })
        if (e.to === first.from) { found = true; break }
        queue.push(e.to)
      }
    }
    if (!found) {
      used.add(first.i)
      rest.push({ from: first.from, to: first.to })
      continue
    }
    const back: Edge[] = []
    for (let node = first.from; node !== first.to;) {
      const p = prev.get(node)!
      const e = unused[p.edge]!
      back.push({ from: e.from, to: e.to })
      used.add(e.i)
      node = p.node
    }
    used.add(first.i)
    cycles.push([{ from: first.from, to: first.to }, ...back.reverse()])
  }
  return { cycles, rest }
}

export function useRealClearingFx(deps: {
  fxState: FxStateLike
  isTestMode: Readonly<Ref<boolean>>
  isWebDriver: boolean

  keyEdge: (from: string, to: string) => string
  seedFn: (s: string) => number

  /** The clearing colour, read when the effect is spawned (the mapping's `fx` is replaced on a theme change). */
  clearingColor: () => string
  /** The beam's core colour (the head of the clearing spark). */
  sparkCoreColor: () => string

  addActiveNode: (nodeId: string, ttlMs: number) => void
  addActiveEdge: (edgeKey: string, ttlMs: number) => void

  pushClearingAmountOverlay?: (
    overlay: ClearingAmountOverlay,
    opts: {
      color: string
      throttleMs?: number
    },
  ) => void
  /** How long the amount label lives: the duration of its CSS animation (Р2; `vizMapping.ts` `motion`). */
  clearingLabelLifeMs: () => number

  /**
   * Managed timer. The steps of the loop and the step after it are scheduled `critical` (a refresh of the scene on
   * screen does not cut them, a change of scene does) and guard themselves by a generation `resetDedup` bumps, as the
   * beam of a payment does (`useRealTxFx.ts`).
   */
  scheduleTimeout: (fn: () => void, ms: number, opts?: { critical?: boolean }) => void
  getLayoutNodeById: (nodeId: string) => LayoutNodePosition | null
  /** Screen px of a line's visible segment (as for the beam); absent or null: the lines of a cycle count as equal. */
  edgeLengthPx?: (from: string, to: string) => number | null

  nowMs?: () => number
  nowEpochMs?: () => number

  spawnEdgePulses?: SpawnEdgePulsesFn
  spawnNodeBursts?: SpawnNodeBurstsFn
  spawnSparks?: SpawnSparksFn
}): {
  runClearingFx: (params: ClearingFxParams, opts?: ClearingFxOptions) => void
  runRealClearingDoneFx: (done: ClearingDoneEvent, opts?: ClearingFxOptions) => void
  resetDedup: () => void
} {
  const nowMs =
    deps.nowMs ??
    (() => {
      return typeof performance !== 'undefined' ? performance.now() : Date.now()
    })

  const nowEpochMs = deps.nowEpochMs ?? (() => Date.now())

  const spawnEdgePulses = deps.spawnEdgePulses ?? spawnEdgePulsesDefault
  const spawnNodeBursts = deps.spawnNodeBursts ?? spawnNodeBurstsDefault
  const spawnSparks = deps.spawnSparks ?? spawnSparksDefault

  let generation = 0

  /** Edge-signature dedup: prevents double FX spawn when both HTTP and SSE paths fire. */
  // IMPORTANT: dedup policy must NOT suppress distinct clearing.done events that happen to
  // share the same edge set (common in small graphs / debug "clearing-once" loops).
  //
  // - HTTP path (Interact clearing-real) has no plan_id -> dedup by edge signature
  // - SSE path (clearing.done) has plan_id -> dedup by plan_id, but also suppress if
  //   a matching edge signature was just processed by the HTTP path (double-fire)
  const _clearingFxDedupByEdgeSig = new Map<string, number>() // edgeSig → timestamp (HTTP)
  const _clearingFxDedupByPlanId = new Map<string, number>() // planId → timestamp (SSE)
  const CLEARING_FX_DEDUP_WINDOW_MS = 3000

  function gcClearingFxDedup(map: Map<string, number>, now: number) {
    if (map.size <= 50) return
    for (const [k, t] of map) {
      if (now - t > CLEARING_FX_DEDUP_WINDOW_MS * 2) map.delete(k)
    }
  }

  function pushClearingLabelDeferred(
    opts: { edges: Edge[]; text: string; planId?: string },
    startedAtMs = nowMs(),
    retryDelayMs = 80,
  ) {
    const getNodeWorldPos = (id: string) => {
      const ln = deps.getLayoutNodeById(id)
      if (!ln || typeof ln.__x !== 'number' || typeof ln.__y !== 'number') return null
      return { x: ln.__x, y: ln.__y }
    }

    const maxWaitMs = 800
    const overlayId = `clearing-total:${opts.planId ?? Math.round(startedAtMs)}`

    __retryUntilTruthyOrDeadline({
      startedAtMs,
      maxWaitMs,
      retryDelayMs,
      nowMs,
      scheduleTimeout: deps.scheduleTimeout,
      get: () => computeClearingAmountAnchorFromEdgeMidpoints(opts.edges, getNodeWorldPos),
      onSuccess: (anchor) => {
        const overlay = {
          id: overlayId,
          text: opts.text,
          worldX: anchor.x,
          worldY: anchor.y,
          // Р2 of spec 039: the label lives as long as its CSS animation (it ends at opacity 0) - until 039 slice C a
          // literal 3800 against 4200 of the CSS, the label disappeared at opacity 0.05.
          ttlMs: deps.clearingLabelLifeMs(),
          styleKey: 'clearing-premium',
          planId: opts.planId,
        } satisfies ClearingAmountOverlay

        if (deps.pushClearingAmountOverlay) {
          deps.pushClearingAmountOverlay(overlay, { color: CLEARING_LABEL_COLOR, throttleMs: 500 })
        }
      },
      onTimeout: () => undefined,
    })
  }

  function getFxState(): FxState {
    const fxState = deps.fxState as FxState
    if (!Array.isArray(fxState.sparks)) fxState.sparks = []
    if (!Array.isArray(fxState.edgePulses)) fxState.edgePulses = []
    if (!Array.isArray(fxState.nodeBursts)) fxState.nodeBursts = []
    return fxState
  }

  /** One spark round one cycle in `CLEARING_LOOP_MS`, at one speed; each node lights up as the spark leaves it. */
  function loopSpark(fxState: FxState, cycle: Edge[], tNowMs: number, seedKey: string, gen: number, fxOff: boolean) {
    const lengths = cycle.map((e) => deps.edgeLengthPx?.(e.from, e.to) ?? null)
    const known = lengths.every((l) => typeof l === 'number' && Number.isFinite(l) && l > 0)
    const len = known ? (lengths as number[]) : cycle.map(() => 1)
    const total = len.reduce((s, l) => s + l, 0)
    let at = 0
    cycle.forEach((e, i) => {
      const moveMs = (CLEARING_LOOP_MS * len[i]!) / total
      const startMs = at
      at += moveMs
      const last = i === cycle.length - 1
      const spawn = () => {
        if (gen !== generation) return
        spawnSparks(fxState, {
          edges: [e],
          nowMs: tNowMs + startMs,
          ttlMs: moveMs + (last ? BEAM_FADE_MS : 0),
          moveMs,
          motion: 'linear',
          colorCore: deps.sparkCoreColor(),
          colorTrail: deps.clearingColor(),
          thickness: 1.1,
          kind: 'beam',
          seedPrefix: `clearing-spark:${seedKey}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: fxOff,
        })
        spawnNodeBursts(fxState, {
          nodeIds: [e.from],
          nowMs: tNowMs + startMs,
          durationMs: BEAM_NODE_BURST_MS,
          color: deps.clearingColor(),
          kind: 'tx-impact',
          seedPrefix: `clearing-node:${seedKey}`,
          seedFn: deps.seedFn,
          isTestMode: fxOff,
        })
      }
      if (i === 0) spawn()
      else deps.scheduleTimeout(spawn, startMs, { critical: true })
    })
  }

  function runClearingFx(params: ClearingFxParams, opts: ClearingFxOptions = {}) {
    const { edges: edgesAll, totalAmount, equivalent, planId } = params

    // 028 F-028-47 (B5): the same edges cleared in another equivalent are another clearing.
    const edgeSig = `${canonicalEquivalent(equivalent)}#${edgesAll.map((e) => `${e.from}>${e.to}`).sort().join('|')}`
    const now = nowEpochMs()

    if (planId) {
      const prevPlan = _clearingFxDedupByPlanId.get(planId)
      if (prevPlan !== undefined && now - prevPlan < CLEARING_FX_DEDUP_WINDOW_MS) return

      const prevHttp = _clearingFxDedupByEdgeSig.get(edgeSig)
      if (prevHttp !== undefined && now - prevHttp < CLEARING_FX_DEDUP_WINDOW_MS) return

      _clearingFxDedupByPlanId.set(planId, now)
      gcClearingFxDedup(_clearingFxDedupByPlanId, now)
    } else {
      const prev = _clearingFxDedupByEdgeSig.get(edgeSig)
      if (prev !== undefined && now - prev < CLEARING_FX_DEDUP_WINDOW_MS) return
      _clearingFxDedupByEdgeSig.set(edgeSig, now)
      gcClearingFxDedup(_clearingFxDedupByEdgeSig, now)
    }

    const clearedAmount = totalAmount.trim()
    const label = () => {
      if (edgesAll.length > 0 && clearedAmount && !isZeroDecimalString(clearedAmount)) {
        pushClearingLabelDeferred({
          edges: edgesAll,
          text: `−${clearedAmount.replace(/^-/, '')} ${equivalent}`,
          planId,
        })
      }
    }

    // Reduced motion / animations off: the amount label at once, nothing moves or lights up.
    if (opts.animate === false) {
      label()
      return
    }

    const tNowMs = nowMs()
    const fxState = getFxState()
    const fxOff = deps.isTestMode.value && deps.isWebDriver
    const gen = generation
    const seedKey = planId ?? tNowMs.toFixed(0)
    const edgesFx = edgesAll.length > 30 ? edgesAll.slice(0, 30) : edgesAll

    // 1. The loop.
    const found = cyclesOf(edgesFx)
    found.cycles.forEach((cycle, k) => loopSpark(fxState, cycle, tNowMs, `${seedKey}:${k}`, gen, fxOff))

    // 2. After it: the lines pulse, the cycle is highlighted, the amount comes.
    const afterLoop = () => {
      if (gen !== generation) return
      const t = nowMs()
      if (edgesFx.length > 0) {
        spawnEdgePulses(fxState, {
          edges: edgesFx,
          nowMs: t,
          durationMs: CLEARING_PULSE_MS,
          color: deps.clearingColor(),
          thickness: 3.2,
          seedPrefix: `clearing:${seedKey}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: fxOff,
        })
      }
      const highlightMs = CLEARING_PULSE_MS * (1 + EDGE_PULSE_AFTERGLOW_K)
      for (const id of nodesFromEdges(edgesAll)) deps.addActiveNode(id, highlightMs)
      for (const e of edgesAll) deps.addActiveEdge(deps.keyEdge(e.from, e.to), highlightMs)
      label()
    }
    if (found.cycles.length === 0) afterLoop()
    else deps.scheduleTimeout(afterLoop, CLEARING_LOOP_MS, { critical: true })
  }

  function runRealClearingDoneFx(done: ClearingDoneEvent, opts: ClearingFxOptions = {}) {
    const edges: Edge[] = []
    if (Array.isArray(done.cycle_edges) && done.cycle_edges.length > 0) {
      for (const e of done.cycle_edges) edges.push({ from: e.from, to: e.to })
    }
    if (edges.length === 0) return

    runClearingFx(
      {
        edges,
        totalAmount: String(done.cleared_amount ?? '0'),
        equivalent: String(done.equivalent ?? ''),
        planId: String(done.plan_id ?? '') || undefined,
      },
      opts,
    )
  }

  function resetDedup(): void {
    _clearingFxDedupByEdgeSig.clear()
    _clearingFxDedupByPlanId.clear()
    // A pending step of a loop of the run (or playback) that is reset does nothing.
    generation += 1
  }

  return { runClearingFx, runRealClearingDoneFx, resetDedup }
}
