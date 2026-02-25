import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'

import type { LayoutLink, LayoutNode } from './forceLayout'
import { sizeForNode } from '../render/nodePainter'
import { clamp, safeClampToViewport } from '../utils/math'

export type PhysicsQuality = 'low' | 'med' | 'high'

const DEFAULT_SUBSTEPS_BY_QUALITY: Record<PhysicsQuality, number> = {
  low: 1,
  med: 1,
  high: 2,
}

// Values based on v1 (GeoSimulatorMesh.vue), adjusted for baseline-started v2.
// Tuned for faster stabilization (~1.5s instead of ~3.5s) while keeping organic feel.
const PHYSICS_DEFAULTS = {
  CHARGE_STRENGTH: -150,

  MIN_LINK_DISTANCE_PX: 80,
  MAX_LINK_DISTANCE_PX: 150,
  LINK_DISTANCE_K_MULTIPLIER: 1.1,

  LINK_STRENGTH: 0.3,
  CENTER_STRENGTH: 0.03,
  COLLISION_PADDING_PX: 8,

  // Faster stabilization: ~65 iterations instead of ~150
  // At 24ms tick interval = ~1.5s instead of ~3.6s
  ALPHA_START: 0.6,        // Was 0.8: lower start energy
  ALPHA_MIN: 0.012,        // Was 0.006: stop earlier
  ALPHA_DECAY: 0.055,      // Was 0.032: faster decay (0.055 → ~65 iterations)
  VELOCITY_DECAY: 0.78,    // Was 0.82: more friction = faster settling

  VIEWPORT_MARGIN_PX: 30,
} as const

const SIGNIFICANT_VIEWPORT_AREA_RATIO_DEFAULT = 3

function readSignificantViewportAreaRatio(): number {
  const raw = Number(import.meta.env.VITE_SIM_PHYSICS_VIEWPORT_RETUNE_AREA_RATIO ?? SIGNIFICANT_VIEWPORT_AREA_RATIO_DEFAULT)
  if (!Number.isFinite(raw)) return SIGNIFICANT_VIEWPORT_AREA_RATIO_DEFAULT
  return Math.max(1.5, Math.min(8, raw))
}

const SIGNIFICANT_VIEWPORT_AREA_RATIO = readSignificantViewportAreaRatio()

function viewportArea(w: number, h: number): number {
  return Math.max(1, w) * Math.max(1, h)
}

function isSignificantViewportResize(oldW: number, oldH: number, newW: number, newH: number): boolean {
  const oldArea = viewportArea(oldW, oldH)
  const newArea = viewportArea(newW, newH)
  const ratio = newArea / oldArea
  return ratio >= SIGNIFICANT_VIEWPORT_AREA_RATIO || ratio <= 1 / SIGNIFICANT_VIEWPORT_AREA_RATIO
}

export function __testOnly_computeLinkDistancePx(opts: {
  width: number
  height: number
  nodeCount: number
}): number {
  const { width, height, nodeCount } = opts
  const area = viewportArea(width, height)
  const k = Math.sqrt(area / Math.max(1, nodeCount))
  return Math.max(
    PHYSICS_DEFAULTS.MIN_LINK_DISTANCE_PX,
    Math.min(PHYSICS_DEFAULTS.MAX_LINK_DISTANCE_PX, k * PHYSICS_DEFAULTS.LINK_DISTANCE_K_MULTIPLIER),
  )
}

export type PhysicsConfig = {
  width: number
  height: number
  quality: PhysicsQuality

  chargeStrength: number
  linkDistance: number
  linkStrength: number
  centerStrength: number
  collisionPadding: number

  alphaStart: number
  alphaMin: number
  alphaDecay: number
  velocityDecay: number

  margin: number
  substeps: number
}

export type D3Node = LayoutNode &
  SimulationNodeDatum & {
    fx?: number | null
    fy?: number | null
  }

export type D3Link = SimulationLinkDatum<D3Node> & {
  source: string | D3Node
  target: string | D3Node
}

function centerWithMicroJitter1px(center: number): number {
  // Jitter range must be exactly ±1px.
  return center + (Math.random() * 2 - 1)
}

export function __testOnly_nanFallbackCoord(value: unknown, center: number): number {
  // Preserve behavior for valid numbers; only apply jitter for NaN/invalid/unset.
  return typeof value === 'number' && Number.isFinite(value) ? value : centerWithMicroJitter1px(center)
}

export function createDefaultConfig(opts: {
  width: number
  height: number
  nodeCount: number
  quality: PhysicsQuality
}): PhysicsConfig {
  const { width, height, nodeCount, quality } = opts

  const linkDistance = __testOnly_computeLinkDistancePx({ width, height, nodeCount })

  const substeps = DEFAULT_SUBSTEPS_BY_QUALITY[quality]
  const margin = PHYSICS_DEFAULTS.VIEWPORT_MARGIN_PX

  return {
    width,
    height,
    quality,

    chargeStrength: PHYSICS_DEFAULTS.CHARGE_STRENGTH, // Stronger repulsion (was -80)
    linkDistance, // Longer links (was 60-120)
    linkStrength: PHYSICS_DEFAULTS.LINK_STRENGTH, // Weaker springs for more organic feel (was 0.4)
    centerStrength: PHYSICS_DEFAULTS.CENTER_STRENGTH, // Weaker centering (was 0.05)
    collisionPadding: PHYSICS_DEFAULTS.COLLISION_PADDING_PX, // Larger padding (was 3)

    alphaStart: PHYSICS_DEFAULTS.ALPHA_START, // "Hot" start (was 0.3)
    alphaMin: PHYSICS_DEFAULTS.ALPHA_MIN, // Stop earlier to avoid long drifting
    alphaDecay: PHYSICS_DEFAULTS.ALPHA_DECAY, // Faster decay = less "floating"
    velocityDecay: PHYSICS_DEFAULTS.VELOCITY_DECAY, // More friction = heavier feel

    margin,
    substeps,
  }
}

export type PhysicsEngine = {
  isRunning: () => boolean
  start: () => void
  stop: () => void
  reheat: (alpha?: number) => void
  tick: (substeps?: number) => void

  syncFromLayout: () => void
  syncToLayout: () => void

  pin: (nodeId: string, x: number, y: number) => void
  unpin: (nodeId: string) => void

  updateViewport: (w: number, h: number) => void
}

export function createPhysicsEngine(opts: {
  nodes: LayoutNode[]
  links: LayoutLink[]
  config: PhysicsConfig
}): PhysicsEngine {
  const { nodes, links } = opts
  // Keep config immutable from the caller's perspective.
  // The engine may still adjust its internal viewport width/height.
  const config: PhysicsConfig = { ...opts.config }

  // IMPORTANT:
  // - We reuse the same node objects (layout.nodes) so App.vue can mutate __x/__y during drag.
  // - We must NOT reuse layout.links, because d3-force mutates link.source/target into node objects.
  const d3Nodes = nodes as unknown as D3Node[]
  for (const n of d3Nodes) {
    // Defensive: never let NaN propagate into the simulation.
    const cx = opts.config.width / 2
    const cy = opts.config.height / 2
    n.x = __testOnly_nanFallbackCoord(n.__x, cx)
    n.y = __testOnly_nanFallbackCoord(n.__y, cy)
  }

  const d3Links: D3Link[] = links.map((l) => ({ source: l.source, target: l.target }))

  const degreeById = new Map<string, number>()
  for (const l of d3Links) {
    const s = String(l.source)
    const t = String(l.target)
    degreeById.set(s, (degreeById.get(s) ?? 0) + 1)
    degreeById.set(t, (degreeById.get(t) ?? 0) + 1)
  }

  const nodeById = new Map<string, D3Node>()
  for (const n of d3Nodes) nodeById.set(n.id, n)

  const ISOLATE_CENTER_STRENGTH_MIN = 0.12
  const ISOLATE_CHARGE_FACTOR = 0.22
  const COLLIDE_STRENGTH = 0.7
  const NODE_RADIUS_MIN_PX = 8
  const NODE_SIZE_TO_RADIUS_MULTIPLIER = 0.56

  const isolateCenterStrength = Math.max(ISOLATE_CENTER_STRENGTH_MIN, config.centerStrength)

  const simulation: Simulation<D3Node, D3Link> = forceSimulation(d3Nodes)
    .alpha(config.alphaStart)
    .alphaMin(config.alphaMin)
    .alphaDecay(config.alphaDecay)
    .velocityDecay(config.velocityDecay)
    .force(
      'charge',
      forceManyBody<D3Node>().strength((n) => {
        const deg = degreeById.get(n.id) ?? 0
        if (deg === 0) return config.chargeStrength * ISOLATE_CHARGE_FACTOR
        return config.chargeStrength
      }),
    )
    // forceCenter has no strength(); use forceX/forceY for controllable strength.
    .force(
      'cx',
      forceX<D3Node>(config.width / 2).strength((n) => {
        const deg = degreeById.get(n.id) ?? 0
        return deg === 0 ? isolateCenterStrength : config.centerStrength
      }),
    )
    .force(
      'cy',
      forceY<D3Node>(config.height / 2).strength((n) => {
        const deg = degreeById.get(n.id) ?? 0
        return deg === 0 ? isolateCenterStrength : config.centerStrength
      }),
    )
    .force(
      'link',
      forceLink<D3Node, D3Link>(d3Links)
        .id((d) => d.id)
        .distance(config.linkDistance)
        .strength(config.linkStrength),
    )
    .force(
      'collide',
      forceCollide<D3Node>()
        .radius((n) => {
          const s = sizeForNode(n)
          const r = Math.max(NODE_RADIUS_MIN_PX, Math.max(s.w, s.h) * NODE_SIZE_TO_RADIUS_MULTIPLIER)
          return r + config.collisionPadding
        })
        .strength(COLLIDE_STRENGTH),
    )
    .stop()

  function applyViewportClamp() {
    const margin = config.margin
    const w = config.width
    const h = config.height

    for (const n of d3Nodes) {
      // `typeof n.x === 'number'` includes NaN; treat non-finite values as unset.
      const x = __testOnly_nanFallbackCoord(n.x, w / 2)
      const y = __testOnly_nanFallbackCoord(n.y, h / 2)

      const cx = safeClampToViewport(x, margin, w)
      const cy = safeClampToViewport(y, margin, h)
      n.x = cx
      n.y = cy

      // If a pinned coordinate is invalid, unpin rather than locking the node in NaN.
      if (n.fx != null) n.fx = Number.isFinite(n.fx) ? safeClampToViewport(n.fx, margin, w) : null
      if (n.fy != null) n.fy = Number.isFinite(n.fy) ? safeClampToViewport(n.fy, margin, h) : null
    }
  }

  return {
    isRunning: () => simulation.alpha() >= config.alphaMin,

    start: () => {
      simulation.alpha(config.alphaStart)
    },

    stop: () => {
      simulation.stop()
    },

    reheat: (alpha = config.alphaStart) => {
      simulation.alpha(alpha)
    },

    tick: (substeps = config.substeps) => {
      if (simulation.alpha() < config.alphaMin) return
      for (let i = 0; i < Math.max(1, substeps); i++) simulation.tick()
      applyViewportClamp()
    },

    syncFromLayout: () => {
      for (const n of d3Nodes) {
        const x = __testOnly_nanFallbackCoord(n.__x, config.width / 2)
        const y = __testOnly_nanFallbackCoord(n.__y, config.height / 2)
        n.x = x
        n.y = y
        if (n.fx != null) n.fx = x
        if (n.fy != null) n.fy = y
      }
    },

    syncToLayout: () => {
      for (const n of d3Nodes) {
        if (typeof n.x === 'number' && Number.isFinite(n.x)) n.__x = n.x
        if (typeof n.y === 'number' && Number.isFinite(n.y)) n.__y = n.y
      }
    },

    pin: (nodeId: string, x: number, y: number) => {
      const n = nodeById.get(nodeId)
      if (!n) return
      n.fx = x
      n.fy = y
      n.x = x
      n.y = y
    },

    unpin: (nodeId: string) => {
      const n = nodeById.get(nodeId)
      if (!n) return
      n.fx = null
      n.fy = null
    },

    updateViewport: (w: number, h: number) => {
      const prevW = config.width
      const prevH = config.height

      config.width = w
      config.height = h

      const cx = simulation.force('cx') as ReturnType<typeof forceX>
      const cy = simulation.force('cy') as ReturnType<typeof forceY>
      cx?.x(w / 2)
      cy?.y(h / 2)

      // ITEM-16 (MINOR): retune forces that depend on viewport size after significant resize.
      // Criterion: area changed by >= SIGNIFICANT_VIEWPORT_AREA_RATIO
      // (or <= 1 / SIGNIFICANT_VIEWPORT_AREA_RATIO).
      if (isSignificantViewportResize(prevW, prevH, w, h)) {
        const nextLinkDistance = __testOnly_computeLinkDistancePx({ width: w, height: h, nodeCount: d3Nodes.length })
        if (nextLinkDistance !== config.linkDistance) {
          config.linkDistance = nextLinkDistance
          const linkF = simulation.force('link') as ReturnType<typeof forceLink>
          linkF?.distance(config.linkDistance)
        }
      }

      applyViewportClamp()
    },
  }
}
