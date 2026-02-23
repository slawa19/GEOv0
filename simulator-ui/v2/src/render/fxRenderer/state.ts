export type FxSpark = {
  key: string
  source: string
  target: string
  startedAtMs: number
  ttlMs: number
  colorCore: string
  colorTrail: string
  thickness: number
  seed: number
  kind: 'comet' | 'beam'
}

export type FxEdgePulse = {
  key: string
  from: string
  to: string
  startedAtMs: number
  durationMs: number
  color: string
  thickness: number
  seed: number
}

export type FxNodeBurst = {
  key: string
  nodeId: string
  startedAtMs: number
  durationMs: number
  color: string
  seed: number
  kind: 'clearing' | 'tx-impact' | 'glow'
}

export type FxState = {
  sparks: FxSpark[]
  edgePulses: FxEdgePulse[]
  nodeBursts: FxNodeBurst[]

  // Optional runtime-only cap to keep FX bounded (set by render loop).
  __maxParticles?: number

  // Optional runtime-only telemetry / knobs (set by render loop).
  __fxBudgetScale?: number
  __lastFps?: number

  // Optional runtime-only render knobs (set by render loop).
  __renderQuality?: 'low' | 'med' | 'high'
  __dprClamp?: number
}

export function createFxState(): FxState {
  return { sparks: [], edgePulses: [], nodeBursts: [] }
}

export function resetFxState(fxState: FxState): void {
  fxState.sparks.length = 0
  fxState.edgePulses.length = 0
  fxState.nodeBursts.length = 0
}

export function getFxParticleBudget(fxState: FxState): number {
  const max =
    typeof fxState.__maxParticles === 'number' && Number.isFinite(fxState.__maxParticles)
      ? Math.max(0, Math.floor(fxState.__maxParticles))
      : null

  return max === null
    ? Number.POSITIVE_INFINITY
    : Math.max(0, max - (fxState.sparks.length + fxState.edgePulses.length + fxState.nodeBursts.length))
}
