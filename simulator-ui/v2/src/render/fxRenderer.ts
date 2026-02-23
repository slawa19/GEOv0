/**
 * FX Renderer — Visual Effects Layer for Graph Animations
 * ========================================================
 *
 * This module provides a particle/effect system rendered on a separate canvas
 * layer above the base graph. It supports three types of effects:
 *
 * 1. **Sparks** (`FxSpark`, `spawnSparks`)
 *    - Moving particles that travel along edges from source to target
 *    - Two styles: 'comet' (wobbly trail) and 'beam' (straight line + glowing dot head)
 *    - Use for: transaction animations, clearing micro-transactions
 *    - NOTE: 'beam' style renders trail from source to head position
 *
 * 2. **Edge Pulses** (`FxEdgePulse`, `spawnEdgePulses`)
 *    - Soft glow traveling along an edge with fading trail
 *    - Use for: highlighting cycle paths, showing routes (without spark head)
 *    - NOTE: Do NOT combine with 'beam' sparks on same edge — causes double animation
 *
 * 3. **Node Bursts** (`FxNodeBurst`, `spawnNodeBursts`)
 *    - Expanding/fading effects centered on nodes
 *    - Styles: 'glow' (soft circle), 'tx-impact' (rim + shockwave), 'clearing' (bloom + ring)
 *    - Use for: impact bursts when spark arrives, highlighting nodes
 *    - NOTE: Global (screen-space) flash overlay is handled in App.vue, not here.
 *
 * Animation Pattern for Edge Transactions:
 * ----------------------------------------
 * For a single edge animation (tx or clearing micro-tx):
 *   1. spawnNodeBursts(source, 'glow') — optional source highlight
 *   2. spawnSparks(edge, 'beam') — spark flies, beam renders edge glow
 *   3. After ttlMs: spawnNodeBursts(target, 'glow'/'tx-impact') — impact flash
 *
 * DO NOT add spawnEdgePulses when using 'beam' sparks — the beam already
 * renders the edge glow internally. EdgePulses are for standalone edge
 * highlighting (e.g., showing a cycle path before animation starts).
 *
 * Color Conventions:
 * ------------------
 * - Cyan (#22d3ee): Single transactions (tx.updated)
 * - Gold (#fbbf24): Clearing operations
 * - Use colorCore for spark head, colorTrail for beam/trail
 */

export { __testing, resetFxRendererCaches } from './fxRenderer/outlineCache'

export type { FxEdgePulse, FxNodeBurst, FxSpark, FxState } from './fxRenderer/state'
export { createFxState, resetFxState } from './fxRenderer/state'

export { spawnEdgePulses, spawnNodeBursts, spawnSparks } from './fxRenderer/spawn'

export { renderFxFrame } from './fxRenderer/renderFrame'
