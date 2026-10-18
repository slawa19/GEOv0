import type { Ref } from 'vue'

import type { FxState } from '../../render/fxRenderer'
import { spawnNodeBursts as spawnNodeBurstsDefault, spawnSparks as spawnSparksDefault } from '../../render/fxRenderer'
import type { TxUpdatedEvent } from '../../types'
import { normalizeTxAmountLabelInput } from '../../utils/txAmountLabel'
import { BEAM_FADE_MS, planTxRoute, type TxRoutePlan } from './txRoutePlan'

export type RealTxFxConfig = {
  ratePerSec: number
  burst: number
  maxConcurrentSparks: number
  maxEdgesPerEvent: number
  minGapMs: number
}

/**
 * The longest route whose beam follows its edges. Equal to the cap of payment routing, `ROUTING_MAX_HOPS`
 * (`app/config.py:187`), so every route the backend can execute is shown as it ran. A longer `edges` list is not
 * a payment route the backend produces today; it is drawn as one straight beam sender -> receiver, a recorded
 * degradation (spec 038, slice S1).
 */
export const REAL_TX_MAX_ROUTE_EDGES = 6

export const REAL_TX_FX_DEFAULT: RealTxFxConfig = {
  // Token bucket: how many tx events per second can spawn sparks.
  ratePerSec: 3.0,
  burst: 2.0,
  // Hard caps for perf.
  maxConcurrentSparks: 28,
  // Up to this many edges the beam walks the route edge by edge (one spark alive at a time, see `runRealTxFx`).
  maxEdgesPerEvent: REAL_TX_MAX_ROUTE_EDGES,
  // (039 slice C: `ttlMinMs` / `ttlMaxMs` clamped the wire's `ttl_ms` into the route time, which the beam no longer
  // takes from the wire - see `txRoutePlan.ts`; `activeEdgePadMs` was read by nothing.)
  // Safety: prevent spamming even if tokens allow bursts.
  minGapMs: 120,
}

/** How long the sender's burst lasts, and the bursts of the nodes the beam passes and reaches (039 slice C). */
export const BEAM_SENDER_BURST_MS = 280
export const BEAM_NODE_BURST_MS = 420

export function __pickSparkEdges(
  edges: TxUpdatedEvent['edges'],
  endpoints: { from?: string; to?: string } | undefined,
  cfg: RealTxFxConfig = REAL_TX_FX_DEFAULT,
): Array<{ from: string; to: string }> {
  if (!edges || edges.length === 0) return []
  if (edges.length <= cfg.maxEdgesPerEvent) return edges

  // Degradation for a route longer than the cap: one spark in the sender->receiver direction.
  // It is NOT an edge of the route; the route itself is not shown in this case.
  const src = String(endpoints?.from ?? edges[0]?.from ?? '').trim()
  const dst = String(endpoints?.to ?? edges[edges.length - 1]?.to ?? '').trim()
  if (!src || !dst) return []
  return [{ from: src, to: dst }]
}

type SpawnSparksFn = typeof spawnSparksDefault
type SpawnNodeBurstsFn = typeof spawnNodeBurstsDefault
type FxStateLike = Partial<Pick<FxState, 'sparks' | 'edgePulses' | 'nodeBursts'>>

export function useRealTxFx(deps: {
  fxState: FxStateLike
  isTestMode: Readonly<Ref<boolean>>
  isWebDriver: boolean

  intensityScale: (intensityKey?: string) => number

  resolveTxDirection: (input: {
    from?: string
    to?: string
    edges: Array<{ from: string; to: string }>
  }) => { from: string; to: string; edges: Array<{ from: string; to: string }> }

  keyEdge: (from: string, to: string) => string
  seedFn: (s: string) => number

  /**
   * The FX colours, read at the moment an effect is spawned (039 slice C, BACKLOG 039-5 (zh)): the mapping's `fx`
   * object is replaced on mount and on a theme change (`vizMapping.ts`, `resolveVizColors`), so a string captured
   * when this composable was created would never follow it.
   */
  fxColors: () => { txSparkCore: string; txSparkTrail: string; clearingDebt: string }

  /** Colour of a node's burst: the node's role colour (`fillForNode`), read when the burst is spawned. */
  fxColorForNode: (nodeId: string, fallback: string) => string

  /**
   * Length in SCREEN px of the visible segment of a route edge (between the link terminations, at the current zoom),
   * or null when a node is not laid out. The beam keeps one speed along the route by it (`txRoutePlan.ts`). Absent:
   * every leg counts as equal.
   */
  edgeLengthPx?: (from: string, to: string) => number | null

  /** How long an amount label lives: the duration of its CSS fade (R2 of spec 039; `vizMapping.ts` `motion`). */
  txLabelLifeMs: () => number

  /**
   * Managed timer (`demo/timerRegistry.ts`). The legs of a beam and the receiver's burst are scheduled `critical`,
   * the class of the receiver's amount label they accompany: a snapshot REFRESH of the scene on screen keeps them
   * (`useSceneState.ts` clears only non-critical timers when a load starts), a change of scene and an unmount clear
   * them (`clearScene`, `timers.clearAll`). The cleanup of a run (`cleanupRealRunFxAndTimers`) does not clear
   * critical timers, so they guard themselves: see `fxGeneration` for which ways of ending a run that covers.
   */
  scheduleTimeout: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => void
  pushFloatingLabelWhenReady: (opts: {
    nodeId: string
    text: string
    color: string
    ttlMs: number
    offsetYPx?: number
    throttleKey?: string
    throttleMs?: number
  }) => void

  nowMs?: () => number
  config?: Partial<RealTxFxConfig>

  spawnSparks?: SpawnSparksFn
  spawnNodeBursts?: SpawnNodeBurstsFn
}): {
  /** When the beam of this payment reaches the receiver, ms after it starts: the ONE route time (`planOf`). */
  routeArrivalMs: (evt: TxUpdatedEvent) => number
  /** When the whole effect of this payment is over (the beam faded out, the receiver's burst gone). */
  routeEffectMs: (evt: TxUpdatedEvent) => number
  runRealTxFx: (evt: TxUpdatedEvent) => void
  pushTxAmountLabel: (nodeId: string, signedAmount: string, unit: string, opts?: { throttleMs?: number }) => void
  resetRateLimiter: () => void
} {
  const cfg: RealTxFxConfig = { ...REAL_TX_FX_DEFAULT, ...(deps.config ?? {}) }

  const nowMs =
    deps.nowMs ??
    (() => {
      return typeof performance !== 'undefined' ? performance.now() : Date.now()
    })

  const spawnSparks = deps.spawnSparks ?? spawnSparksDefault
  const spawnNodeBursts = deps.spawnNodeBursts ?? spawnNodeBurstsDefault

  let txFxTokens = cfg.burst
  let txFxLastRefillAtMs = nowMs()
  let txFxLastSpawnAtMs = 0
  // Bumped by `resetRateLimiter`, which `cleanupRealRunFxAndTimers` calls: on the user's own Stop, on start/restart,
  // on attach and on a stale-run reset. A pending leg or burst of an earlier generation does nothing.
  // NOT bumped by an administrative Stop / Stop-all or by a run that ends on the server: those paths do not run that
  // cleanup, and the effect of the last payment lives on, on the same scene, until the next start/attach
  // (BACKLOG 038-1 (c)).
  let fxGeneration = 0

  function refillTxFxTokens(tNowMs: number) {
    const dt = Math.max(0, tNowMs - txFxLastRefillAtMs)
    txFxLastRefillAtMs = tNowMs
    txFxTokens = Math.min(cfg.burst, txFxTokens + (dt * cfg.ratePerSec) / 1000)
  }

  /**
   * THE plan of a payment's beam, computed once per event object and shared by every consumer of its time (the legs
   * here, the receiver's label in `realEventPipeline.ts`, the offline workbench's pause): P-3 of spec 039. Computed
   * when it is first asked for - by the beam or by the label, whichever comes first, in the same synchronous pass of
   * `executeRealEventIntents` - so both see the same layout and zoom. A plan belongs to its generation (`fxGeneration`):
   * the offline workbench delivers the SAME event objects again in a fresh playback, and their plan is made anew
   * there, on the layout and zoom of that moment.
   */
  const plans = new WeakMap<TxUpdatedEvent, { gen: number; plan: TxRoutePlan; resolved: { from: string; to: string }; edges: Array<{ from: string; to: string }> }>()
  function planOf(evt: TxUpdatedEvent) {
    const hit = plans.get(evt)
    if (hit && hit.gen === fxGeneration) return hit
    const resolved = deps.resolveTxDirection({ from: evt.from, to: evt.to, edges: evt.edges })
    const edges = __pickSparkEdges(resolved.edges, { from: resolved.from, to: resolved.to }, cfg)
    const lengthOf = deps.edgeLengthPx
    const plan = planTxRoute(edges, (e) => (lengthOf ? lengthOf(e.from, e.to) : null))
    const out = { gen: fxGeneration, plan, resolved: { from: resolved.from, to: resolved.to }, edges }
    plans.set(evt, out)
    return out
  }

  function routeArrivalMs(evt: TxUpdatedEvent): number {
    return planOf(evt).plan.arrivalMs
  }

  function routeEffectMs(evt: TxUpdatedEvent): number {
    const { plan } = planOf(evt)
    return Math.max(plan.endMs, plan.arrivalMs + BEAM_NODE_BURST_MS)
  }

  function resetRateLimiter(): void {
    txFxTokens = cfg.burst
    txFxLastRefillAtMs = nowMs()
    txFxLastSpawnAtMs = 0
    fxGeneration += 1
  }

  function getFxState(): FxState {
    const fxState = deps.fxState as FxState
    if (!Array.isArray(fxState.sparks)) fxState.sparks = []
    if (!Array.isArray(fxState.edgePulses)) fxState.edgePulses = []
    if (!Array.isArray(fxState.nodeBursts)) fxState.nodeBursts = []
    return fxState
  }

  function runRealTxFx(evt: TxUpdatedEvent) {
    const tNowMs = nowMs()
    if (tNowMs - txFxLastSpawnAtMs < cfg.minGapMs) return

    refillTxFxTokens(tNowMs)
    if (txFxTokens < 1) return
    const fxState = getFxState()
    if ((fxState.sparks?.length ?? 0) >= cfg.maxConcurrentSparks) return

    const { plan, resolved, edges: sparkEdges } = planOf(evt)
    if (sparkEdges.length === 0) return

    txFxTokens -= 1
    txFxLastSpawnAtMs = tNowMs

    const k = deps.intensityScale(evt.intensity_key ?? undefined)

    // The beam walks the route one edge after another, from the sender to the receiver (spec 034, target 3;
    // F-038-3), on the plan (`txRoutePlan.ts`): one speed, a floor per leg, eased only into the receiver, then a
    // fade. One spark is alive at a time, so a long route costs no more particles than a short one. A leg that finds
    // the particle budget spent (`getFxParticleBudget`) is skipped: degradation, not a failure.
    // Every node the beam LEAVES lights up when it does (039 slice C: the intermediaries are seen): the sender at
    // once, every intermediate node at the start of its leg; the receiver when the beam arrives.
    const fxOff = deps.isTestMode.value && deps.isWebDriver
    const gen = fxGeneration
    plan.legs.forEach((leg, i) => {
      // The planned start, not the moment the timer fired: a late timer shortens its own leg and the total holds.
      const startAtMs = tNowMs + leg.startMs
      const spawnLeg = () => {
        if (gen !== fxGeneration) return
        // Read when the leg is spawned: a theme change in the middle of a flight reaches the next leg (039-5 (zh)).
        const colors = deps.fxColors()
        spawnSparks(fxState, {
          edges: [{ from: leg.from, to: leg.to }],
          nowMs: startAtMs,
          ttlMs: leg.moveMs + (leg.arrive ? BEAM_FADE_MS : 0),
          moveMs: leg.moveMs,
          motion: leg.arrive ? 'arrive' : 'linear',
          colorCore: colors.txSparkCore,
          colorTrail: colors.txSparkTrail,
          thickness: 0.95 * k,
          kind: 'beam',
          seedPrefix: `real-tx:${evt.equivalent}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: fxOff,
        })
        if (i > 0 && !fxOff) {
          spawnNodeBursts(fxState, {
            nodeIds: [leg.from],
            nowMs: startAtMs,
            durationMs: BEAM_NODE_BURST_MS,
            color: deps.fxColorForNode(leg.from, colors.txSparkTrail),
            kind: 'tx-impact',
            seedPrefix: 'real-tx-via',
            seedFn: deps.seedFn,
            isTestMode: false,
          })
        }
      }
      if (i === 0) spawnLeg()
      else if (!fxOff) deps.scheduleTimeout(spawnLeg, leg.startMs, { critical: true })
    })

    if (!fxOff) {
      const src = resolved.from || sparkEdges[0]!.from
      const dst = resolved.to || sparkEdges[sparkEdges.length - 1]!.to
      spawnNodeBursts(fxState, {
        nodeIds: [src],
        nowMs: tNowMs,
        durationMs: BEAM_SENDER_BURST_MS,
        color: deps.fxColorForNode(src, deps.fxColors().txSparkTrail),
        kind: 'tx-impact',
        seedPrefix: 'real-tx-src',
        seedFn: deps.seedFn,
        isTestMode: false,
      })
      deps.scheduleTimeout(() => {
        if (gen !== fxGeneration) return
        spawnNodeBursts(fxState, {
          nodeIds: [dst],
          nowMs: nowMs(),
          durationMs: BEAM_NODE_BURST_MS,
          color: deps.fxColorForNode(dst, deps.fxColors().txSparkTrail),
          kind: 'tx-impact',
          seedPrefix: 'real-tx-dst',
          seedFn: deps.seedFn,
          isTestMode: false,
        })
      }, plan.arrivalMs, { critical: true })
    }
  }

  function pushTxAmountLabel(nodeId: string, signedAmount: string, unit: string, opts?: { throttleMs?: number }) {
    const input = normalizeTxAmountLabelInput(nodeId, signedAmount)
    if (!input) return

    const id = input.nodeId
    const sign = input.sign
    const amountText = input.amountText
    // The colour says what the number is - money arriving or leaving - not who the node is (039 slice C, BACKLOG
    // 039-5 (b)): until then it was the node's role colour, so a "+10.00" arriving at a net debtor was painted the
    // debtor's red, and a later patch of the node did not repaint a label already on screen.
    const colors = deps.fxColors()
    const color = sign === '+' ? colors.txSparkTrail : colors.clearingDebt

    deps.pushFloatingLabelWhenReady({
      nodeId: id,
      text: `${sign}${amountText.replace(/^-/, '')} ${unit}`,
      color,
      // R2 of spec 039: the label lives as long as its CSS fade, so it has faded to nothing when it is removed.
      ttlMs: deps.txLabelLifeMs(),
      offsetYPx: sign === '+' ? -18 : -6,
      throttleKey: `amt:${sign}:${id}`,
      throttleMs: opts?.throttleMs ?? 240,
    })
  }

  return { routeArrivalMs, routeEffectMs, runRealTxFx, pushTxAmountLabel, resetRateLimiter }
}
