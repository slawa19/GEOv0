import type { Ref } from 'vue'

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

export const REAL_TX_FX_DEFAULT: RealTxFxConfig = {
  // Token bucket: how many tx events per second can spawn sparks.
  ratePerSec: 3.0,
  burst: 2.0,
  // Hard caps for perf.
  maxConcurrentSparks: 28,
  // Avoid animating long routes as a wall of particles.
  maxEdgesPerEvent: 2,
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

  // UX: for long multi-hop routes, a couple of disjoint edge sparks can look like
  // the amount labels belong to "different" nodes. Prefer a single clear spark
  // in the sender->receiver direction.
  const src = String(endpoints?.from ?? edges[0]?.from ?? '').trim()
  const dst = String(endpoints?.to ?? edges[edges.length - 1]?.to ?? '').trim()
  if (!src || !dst) return []
  return [{ from: src, to: dst }]
}

type SpawnSparksFn = typeof spawnSparksDefault
type SpawnNodeBurstsFn = typeof spawnNodeBurstsDefault

export function useRealTxFx(deps: {
  fxState: any
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

  scheduleTimeout: (fn: () => void, delayMs: number) => void
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
  }

  function runRealTxFx(evt: TxUpdatedEvent) {
    const tNowMs = nowMs()
    if (tNowMs - txFxLastSpawnAtMs < cfg.minGapMs) return

    refillTxFxTokens(tNowMs)
    if (txFxTokens < 1) return
    if ((deps.fxState?.sparks?.length ?? 0) >= cfg.maxConcurrentSparks) return

    const resolved = deps.resolveTxDirection({ from: evt.from, to: evt.to, edges: evt.edges })
    const sparkEdges = __pickSparkEdges(resolved.edges, { from: resolved.from, to: resolved.to }, cfg)
    if (sparkEdges.length === 0) return

    txFxTokens -= 1
    txFxLastSpawnAtMs = tNowMs

    const ttlMs = clampRealTxTtlMs(evt.ttl_ms)
    const k = deps.intensityScale(evt.intensity_key ?? undefined)

    spawnSparks(deps.fxState, {
      edges: sparkEdges,
      nowMs: tNowMs,
      ttlMs,
      colorCore: deps.txSparkColorCore,
      colorTrail: deps.txSparkColorTrail,
      thickness: 0.95 * k,
      kind: 'beam',
      seedPrefix: `real-tx:${evt.equivalent}`,
      countPerEdge: 1,
      keyEdge: deps.keyEdge,
      seedFn: deps.seedFn,
      isTestMode: deps.isTestMode.value && deps.isWebDriver,
    })

    if (!(deps.isTestMode.value && deps.isWebDriver)) {
      const src = resolved.from || sparkEdges[0]!.from
      const dst = resolved.to || sparkEdges[sparkEdges.length - 1]!.to
      spawnNodeBursts(deps.fxState, {
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
        spawnNodeBursts(deps.fxState, {
          nodeIds: [dst],
          nowMs: nowMs(),
          durationMs: 420,
          color: deps.fxColorForNode(dst, deps.txSparkColorTrail),
          kind: 'tx-impact',
          seedPrefix: 'real-tx-dst',
          seedFn: deps.seedFn,
          isTestMode: false,
        })
      }, ttlMs)
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
