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
import { clamp } from '../utils/math'

export type PhysicsQuality = 'low' | 'med' | 'high'

const DEFAULT_SUBSTEPS_BY_QUALITY: Record<PhysicsQuality, number> = {
  low: 1,
  med: 1,
  high: 2,
}

// Values based on v1 (GeoSimulatorMesh.vue), adjusted for baseline-started v2.
// Increased charge and alpha for stronger repulsion and longer "life".
const PHYSICS_DEFAULTS = {
  CHARGE_STRENGTH: -150,

  MIN_LINK_DISTANCE_PX: 80,
  MAX_LINK_DISTANCE_PX: 150,
  LINK_DISTANCE_K_MULTIPLIER: 1.1,

  LINK_STRENGTH: 0.3,
  CENTER_STRENGTH: 0.03,
  COLLISION_PADDING_PX: 8,

  ALPHA_START: 0.8,
  ALPHA_MIN: 0.006,
  ALPHA_DECAY: 0.032,
  VELOCITY_DECAY: 0.82,

  VIEWPORT_MARGIN_PX: 30,
} as const

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

export function createDefaultConfig(opts: {
  width: number
  height: number
  nodeCount: number
  quality: PhysicsQuality
}): PhysicsConfig {
  const { width, height, nodeCount, quality } = opts

  const area = Math.max(1, width) * Math.max(1, height)
  const k = Math.sqrt(area / Math.max(1, nodeCount))

  const substeps = DEFAULT_SUBSTEPS_BY_QUALITY[quality]
  const margin = PHYSICS_DEFAULTS.VIEWPORT_MARGIN_PX

  return {
    width,
    height,
    quality,

    chargeStrength: PHYSICS_DEFAULTS.CHARGE_STRENGTH, // Stronger repulsion (was -80)
    linkDistance: Math.max(
      PHYSICS_DEFAULTS.MIN_LINK_DISTANCE_PX,
      Math.min(PHYSICS_DEFAULTS.MAX_LINK_DISTANCE_PX, k * PHYSICS_DEFAULTS.LINK_DISTANCE_K_MULTIPLIER),
    ), // Longer links (was 60-120)
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
  const { nodes, links, config } = opts

  // IMPORTANT:
  // - We reuse the same node objects (layout.nodes) so App.vue can mutate __x/__y during drag.
  // - We must NOT reuse layout.links, because d3-force mutates link.source/target into node objects.
  const d3Nodes = nodes as unknown as D3Node[]
  for (const n of d3Nodes) {
    n.x = n.__x
    n.y = n.__y
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
      const x = typeof n.x === 'number' ? n.x : w / 2
      const y = typeof n.y === 'number' ? n.y : h / 2

      const cx = clamp(x, margin, w - margin)
      const cy = clamp(y, margin, h - margin)
      n.x = cx
      n.y = cy

      if (n.fx != null) n.fx = clamp(n.fx, margin, w - margin)
      if (n.fy != null) n.fy = clamp(n.fy, margin, h - margin)
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
        n.x = n.__x
        n.y = n.__y
        if (n.fx != null) n.fx = n.__x
        if (n.fy != null) n.fy = n.__y
      }
    },

    syncToLayout: () => {
      for (const n of d3Nodes) {
        if (typeof n.x === 'number') n.__x = n.x
        if (typeof n.y === 'number') n.__y = n.y
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
      config.width = w
      config.height = h

      const cx = simulation.force('cx') as ReturnType<typeof forceX>
      const cy = simulation.force('cy') as ReturnType<typeof forceY>
      cx?.x(w / 2)
      cy?.y(h / 2)

      applyViewportClamp()
    },
  }
}
