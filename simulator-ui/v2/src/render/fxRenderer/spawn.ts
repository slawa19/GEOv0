import type { FxNodeBurst, FxSpark, FxState } from './state'
import { getFxParticleBudget } from './state'

export function spawnEdgePulses(
  fxState: FxState,
  opts: {
    edges: Array<{ from: string; to: string }>
    nowMs: number
    durationMs: number
    color: string
    thickness: number
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
): void {
  if (opts.isTestMode) return

  let budget = getFxParticleBudget(fxState)
  if (budget <= 0) return

  const { edges, nowMs, durationMs, color, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  for (const e of edges) {
    const k = keyEdge(e.from, e.to)
    for (let i = 0; i < Math.max(1, countPerEdge); i++) {
      if (budget-- <= 0) return
      const seed = seedFn(`${seedPrefix}:${k}:${i}`)
      fxState.edgePulses.push({
        key: `pulse:${k}#${i}#${nowMs.toFixed(0)}`,
        from: e.from,
        to: e.to,
        startedAtMs: nowMs,
        durationMs,
        color,
        thickness,
        seed,
      })
    }
  }
}

export function spawnNodeBursts(
  fxState: FxState,
  opts: {
    nodeIds: string[]
    nowMs: number
    durationMs: number
    color: string
    kind?: FxNodeBurst['kind']
    seedPrefix: string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
): void {
  if (opts.isTestMode) return

  let budget = getFxParticleBudget(fxState)
  if (budget <= 0) return

  const { nodeIds, nowMs, durationMs, color, seedPrefix, seedFn } = opts
  const kind = opts.kind ?? 'clearing'
  for (const id of nodeIds) {
    if (budget-- <= 0) return
    const seed = seedFn(`${seedPrefix}:${id}`)
    fxState.nodeBursts.push({
      key: `burst:${id}#${nowMs.toFixed(0)}`,
      nodeId: id,
      startedAtMs: nowMs,
      durationMs,
      color,
      seed,
      kind,
    })
  }
}

export function spawnSparks(
  fxState: FxState,
  opts: {
    edges: Array<{ from: string; to: string }>
    nowMs: number
    ttlMs: number
    colorCore: string
    colorTrail: string
    thickness: number
    kind?: FxSpark['kind']
    seedPrefix: string
    countPerEdge: number
    keyEdge: (a: string, b: string) => string
    seedFn: (s: string) => number
    isTestMode: boolean
  },
): void {
  if (opts.isTestMode) return

  let budget = getFxParticleBudget(fxState)
  if (budget <= 0) return

  const { edges, nowMs, ttlMs, colorCore, colorTrail, thickness, seedPrefix, countPerEdge, keyEdge, seedFn } = opts
  const kind = opts.kind ?? 'comet'
  for (const e of edges) {
    const k = keyEdge(e.from, e.to)
    for (let i = 0; i < Math.max(1, countPerEdge); i++) {
      if (budget-- <= 0) return
      const seed = seedFn(`${seedPrefix}:${k}:${i}`)
      fxState.sparks.push({
        key: `${k}#${i}#${nowMs.toFixed(0)}`,
        source: e.from,
        target: e.to,
        startedAtMs: nowMs,
        ttlMs,
        colorCore,
        colorTrail,
        thickness,
        seed,
        kind,
      })
    }
  }
}
