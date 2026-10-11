import type { Ref } from 'vue'

import type { FxState } from '../../render/fxRenderer'
import { spawnNodeBursts as spawnNodeBurstsDefault, spawnSparks as spawnSparksDefault } from '../../render/fxRenderer'
import type { TxUpdatedEvent } from '../../types'
import { normalizeTxAmountLabelInput } from '../../utils/txAmountLabel'

export type RealTxFxConfig = {
  ratePerSec: number
  burst: number
  maxConcurrentSparks: number
  maxEdgesPerEvent: number
  ttlMinMs: number
  ttlMaxMs: number
  activeEdgePadMs: number
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
  // Clamp TTL to keep the scene from accumulating particles.
  ttlMinMs: 340,
  ttlMaxMs: 1200,
  // Extra highlight time to make motion readable.
  activeEdgePadMs: 260,
  // Safety: prevent spamming even if tokens allow bursts.
  minGapMs: 120,
}

export function __clampRealTxTtlMs(
  ttlRaw: unknown,
  cfg: RealTxFxConfig = REAL_TX_FX_DEFAULT,
  fallbackMs: number = cfg.ttlMaxMs,
): number {
  const ttlN = Number(ttlRaw ?? fallbackMs)
  const ttl = Number.isFinite(ttlN) ? ttlN : fallbackMs
  return Math.max(cfg.ttlMinMs, Math.min(cfg.ttlMaxMs, ttl))
}

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

  txSparkColorCore: string
  txSparkColorTrail: string
  clearingDebtColor: string

  fxColorForNode: (nodeId: string, fallback: string) => string

  /**
   * Managed timer (`demo/timerRegistry.ts`). The legs of a beam and the receiver's burst are scheduled `critical`,
   * the class of the receiver's amount label they accompany: a snapshot REFRESH of the scene on screen keeps them
   * (`useSceneState.ts` clears only non-critical timers when a load starts), a change of scene and an unmount clear
   * them (`clearScene`, `timers.clearAll`). A stopped, restarted or stale run does not clear critical timers
   * (`cleanupRealRunFxAndTimers`), so they guard themselves: see `fxGeneration`.
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
  clampRealTxTtlMs: (ttlRaw: unknown, fallbackMs?: number) => number
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
  // Bumped by `resetRateLimiter`, which the app calls when a run is stopped, restarted or found stale
  // (`cleanupRealRunFxAndTimers`). A pending leg or burst of an earlier generation does nothing.
  let fxGeneration = 0

  function refillTxFxTokens(tNowMs: number) {
    const dt = Math.max(0, tNowMs - txFxLastRefillAtMs)
    txFxLastRefillAtMs = tNowMs
    txFxTokens = Math.min(cfg.burst, txFxTokens + (dt * cfg.ratePerSec) / 1000)
  }

  function clampRealTxTtlMs(ttlRaw: unknown, fallbackMs = cfg.ttlMaxMs): number {
    return __clampRealTxTtlMs(ttlRaw, cfg, fallbackMs)
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

    const resolved = deps.resolveTxDirection({ from: evt.from, to: evt.to, edges: evt.edges })
    const sparkEdges = __pickSparkEdges(resolved.edges, { from: resolved.from, to: resolved.to }, cfg)
    if (sparkEdges.length === 0) return

    txFxTokens -= 1
    txFxLastSpawnAtMs = tNowMs

    const ttlMs = clampRealTxTtlMs(evt.ttl_ms)
    const k = deps.intensityScale(evt.intensity_key ?? undefined)

    // The beam walks the route one edge after another, from the sender to the receiver (spec 034, target 3;
    // F-038-3). The whole route takes `ttlMs` whatever its length: the receiver's burst below and the receiver's
    // amount label (`realEventPipeline.ts`, `tx-label-delayed`) are timed on the same total, so each edge gets an
    // equal share of it. One spark is alive at a time, so a long route costs no more particles than a short one.
    // A leg that finds the particle budget spent (`getFxParticleBudget`) is skipped: degradation, not a failure.
    const fxOff = deps.isTestMode.value && deps.isWebDriver
    const gen = fxGeneration
    const legMs = ttlMs / sparkEdges.length
    sparkEdges.forEach((edge, i) => {
      // The planned start, not the moment the timer fired: a late timer shortens its own leg and the total holds.
      const startAtMs = tNowMs + i * legMs
      const spawnLeg = () => {
        if (gen !== fxGeneration) return
        spawnSparks(fxState, {
          edges: [edge],
          nowMs: startAtMs,
          ttlMs: legMs,
          colorCore: deps.txSparkColorCore,
          colorTrail: deps.txSparkColorTrail,
          thickness: 0.95 * k,
          kind: 'beam',
          seedPrefix: `real-tx:${evt.equivalent}`,
          countPerEdge: 1,
          keyEdge: deps.keyEdge,
          seedFn: deps.seedFn,
          isTestMode: fxOff,
        })
      }
      if (i === 0) spawnLeg()
      else if (!fxOff) deps.scheduleTimeout(spawnLeg, i * legMs, { critical: true })
    })

    if (!fxOff) {
      const src = resolved.from || sparkEdges[0]!.from
      const dst = resolved.to || sparkEdges[sparkEdges.length - 1]!.to
      spawnNodeBursts(fxState, {
        nodeIds: [src],
        nowMs: tNowMs,
        durationMs: 280,
        color: deps.fxColorForNode(src, deps.txSparkColorTrail),
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
          durationMs: 420,
          color: deps.fxColorForNode(dst, deps.txSparkColorTrail),
          kind: 'tx-impact',
          seedPrefix: 'real-tx-dst',
          seedFn: deps.seedFn,
          isTestMode: false,
        })
      }, ttlMs, { critical: true })
    }
  }

  function pushTxAmountLabel(nodeId: string, signedAmount: string, unit: string, opts?: { throttleMs?: number }) {
    const input = normalizeTxAmountLabelInput(nodeId, signedAmount)
    if (!input) return

    const id = input.nodeId
    const sign = input.sign
    const amountText = input.amountText
    const color = sign === '+' ? deps.txSparkColorTrail : deps.clearingDebtColor

    deps.pushFloatingLabelWhenReady({
      nodeId: id,
      text: `${sign}${amountText.replace(/^-/, '')} ${unit}`,
      color: deps.fxColorForNode(id, color),
      ttlMs: 1900,
      offsetYPx: sign === '+' ? -18 : -6,
      throttleKey: `amt:${sign}:${id}`,
      throttleMs: opts?.throttleMs ?? 240,
    })
  }

  return { clampRealTxTtlMs, runRealTxFx, pushTxAmountLabel, resetRateLimiter }
}
