/**
 * FX Renderer — Visual Effects Layer for Graph Animations
 * ========================================================
 *
 * This module provides a particle/effect system rendered on a separate canvas
 * layer above the base graph. It supports three types of effects:
 *
 * 1. **Sparks** (`FxSpark`, `spawnSparks`)
 *    - A beam travelling along an edge from source to target: trail + bright packet + head dot ('beam', the only kind
 *      since 039 slice C; the wobbling 'comet' had no spawner left). `moveMs` / `motion` come from the route plan
 *      (`composables/realFx/txRoutePlan.ts`); `ttlMs - moveMs` is the fade after the arrival (last leg only).
 *    - Use for: the legs of a payment, the spark of a clearing going round its cycle.
 *
 * 2. **Edge Pulses** (`FxEdgePulse`, `spawnEdgePulses`)
 *    - The whole edge glows and fades (a clearing's lines, AFTER its spark went round: 039 slice C).
 *
 * 3. **Node Bursts** (`FxNodeBurst`, `spawnNodeBursts`)
 *    - 'tx-impact', the only kind since 039 slice C: the node's contour glows as a beam or a clearing spark leaves,
 *      passes or reaches it. The 'glow' and 'clearing' kinds and the full-screen flash were removed.
 *
 * Colours: cyan (`--ds-fx-tx-spark-*`) for payments, orange (`--ds-fx-clearing-debt`) for clearing; read at spawn.
 */

export { __testing, resetFxRendererCaches } from './fxRenderer/outlineCache'

export type { FxEdgePulse, FxNodeBurst, FxSpark, FxState } from './fxRenderer/state'
export { createFxState, resetFxState } from './fxRenderer/state'

export { spawnEdgePulses, spawnNodeBursts, spawnSparks } from './fxRenderer/spawn'

export { renderFxFrame } from './fxRenderer/renderFrame'
