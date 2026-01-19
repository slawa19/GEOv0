<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type Simulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { ShieldCheck, Users } from 'lucide-vue-next'

import { useGeoSimulatorStore } from '../../stores/simulator/geoSimulator'
import GeoNodeCard from './GeoNodeCard.vue'
import SimulatorControls from './SimulatorControls.vue'

type NodeType = 'business' | 'person'

type GeoNode = SimulationNodeDatum & {
  id: string
  type: NodeType
  name: string
  trustLimit: number
  balance: number
  trustScore: number
  baseSize: number
  currentSize: number
  renderX: number
  renderY: number
}

type GeoLink = SimulationLinkDatum<GeoNode> & {
  id: string
  source: string | GeoNode
  target: string | GeoNode
  trust: number
}

type ParticleEndpoint = { kind: 'node'; id: string } | { kind: 'point'; x: number; y: number }

type TxParticle = {
  type: 'tx' | 'clearing'
  from: ParticleEndpoint
  to: ParticleEndpoint
  progress: number // 0..1
  speedPx: number
  color: string
  labelText?: string
  labelRgb?: string
  labelLife?: number // seconds
}

type DustParticle =
  | {
      type: 'dust'
      x: number
      y: number
      vx: number
      vy: number
      life: number
      decay: number
      size: number
      colorRgb: string
    }
  | {
      type: 'shockwave'
      x: number
      y: number
      life: number
      size: number
      colorRgb: string
    }

const RENDER = {
  dpr: {
    defaultClamp: 1.5,
    min: 1,
  },
  fps: {
    lowThreshold: 45,
    highThreshold: 57,
    lowSustainSeconds: 2.0,
    highSustainSeconds: 5.0,
  },
  bg: {
    base: '#020408',
    starCellPx: 5200, // smaller => denser
    starLayers: 3 as const,
    bokehSpots: 6,
  },
  colors: {
    nodeBusiness: '#10fb81', // More saturated green
    nodePerson: '#00d2ff', // More saturated cyan/blue
    nodeDebtorLow: '#fbbf24', // Light yellow for low debt
    nodeDebtorHigh: '#f43f5e', // Pinkish/red for high debt
    nodeDebtor: '#f97316', // Fallback/base debtor orange
    gold: '#fbbf24',
    cyan: '#22d3ee',
    danger: '#ef4444',
    violet: '#a78bfa',
    slateLineRgb: '148, 163, 184',
    nebula1: 'rgba(34, 211, 238, 0.05)',
    nebula2: 'rgba(167, 139, 250, 0.04)',
  },
  links: {
    baseAlpha: 0.2,
    dimAlpha: 0.05,
    activeAlpha: 0.9,
    width: 0.6,
    activeWidth: 1.5,
    glowBlur: 6,
  },
  nodes: {
    jitterPx: 0, // Disabled jitter for stability
    selectedScale: 1.6,
    hoverScale: 1.25,
    bloomIntensity: 0.3, // Reduced bloom significantly
    minSize: 6,
    maxSize: 28,
  },
  vfx: {
    maxParticles: 48,
    maxDust: 180,
    cometTailPx: 80, // Longer tail
    txSpeed: 3.5, // Slower sparks
    clearingSpeed: 5.0,
  },
} as const

const PHYSICS = {
  drag: 0.95,
} as const

const wrapperRef = ref<HTMLElement | null>(null)
const canvasRef = ref<HTMLCanvasElement | null>(null)
const store = useGeoSimulatorStore()

const nodes = shallowRef<GeoNode[]>([])
const links = shallowRef<GeoLink[]>([])
const nodeById = shallowRef<Map<string, GeoNode>>(new Map())

const selectedNode = computed<GeoNode | null>(() => {
  const id = store.selectedNodeId
  if (!id) return null
  return nodeById.value.get(id) ?? null
})

const cardRef = ref<InstanceType<typeof GeoNodeCard> | null>(null)
const connectedNodes = shallowRef<Set<string>>(new Set())

watch(() => store.selectedNodeId, (id) => {
  const connected = new Set<string>()
  if (id) {
    connected.add(id)
    const currentLinks = links.value
    for (const l of currentLinks) {
      const s = typeof l.source === 'string' ? l.source : l.source.id
      const d = typeof l.target === 'string' ? l.target : l.target.id
      if (s === id) connected.add(d)
      else if (d === id) connected.add(s)
    }
  }
  connectedNodes.value = connected
}, { immediate: true })

let sim: Simulation<GeoNode, GeoLink> | null = null
let rafId: number | null = null
let resizeObserver: ResizeObserver | null = null
let clearingIntervalId: number | null = null

// Performance: VFX state is mutable arrays (not reactive)
const particles: TxParticle[] = []
const dust: DustParticle[] = []

// Performance: cached, pre-rendered assets
let renderCtx: CanvasRenderingContext2D | null = null
let bgBaseCanvas: HTMLCanvasElement | null = null
let bgStarLayers: HTMLCanvasElement[] = []
const glowSpriteCache = new Map<string, HTMLCanvasElement>()
const nodeSpriteCache = new Map<string, HTMLCanvasElement>()

let t = 0
let dpr = 1
let canvasW = 0
let canvasH = 0

let qualityIndex: 0 | 1 | 2 = 1
let dprClamp: number = RENDER.dpr.defaultClamp

const fps = {
  lastTs: 0,
  ema: 60,
  lowSeconds: 0,
  highSeconds: 0,
}

type DragState = {
  dragging: boolean
  node: GeoNode | null
  pointerId: number | null
  startX: number
  startY: number
  moved: boolean
}

const drag = ref<DragState>({
  dragging: false,
  node: null,
  pointerId: null,
  startX: 0,
  startY: 0,
  moved: false,
})

function clamp(v: number, min: number, max: number) {
  return Math.max(min, Math.min(max, v))
}

function quantize(v: number, step: number, min: number, max: number) {
  return clamp(Math.round(v / step) * step, min, max)
}

function hexToRgb(hex: string): string {
  const c = hex.replace('#', '')
  const r = Number.parseInt(c.slice(0, 2), 16)
  const g = Number.parseInt(c.slice(2, 4), 16)
  const b = Number.parseInt(c.slice(4, 6), 16)
  return `${r}, ${g}, ${b}`
}

function interpolateRgb(rgb1: string, rgb2: string, factor: number): string {
  const [r1, g1, b1] = rgb1.split(',').map(Number)
  const [r2, g2, b2] = rgb2.split(',').map(Number)
  const r = Math.round(r1 + (r2 - r1) * factor)
  const g = Math.round(g1 + (g2 - g1) * factor)
  const b = Math.round(b1 + (b2 - b1) * factor)
  return `${r}, ${g}, ${b}`
}

function resolveNode(refLike: string | GeoNode): GeoNode | null {
  if (typeof refLike === 'string') return nodeById.value.get(refLike) ?? null
  return refLike
}

function resolveEndpoint(ep: ParticleEndpoint): { x: number; y: number } | null {
  if (ep.kind === 'point') return { x: ep.x, y: ep.y }
  const n = nodeById.value.get(ep.id)
  if (!n) return null
  return { x: n.renderX, y: n.renderY }
}

function createOffscreenCanvas(w: number, h: number) {
  const c = document.createElement('canvas')
  c.width = Math.max(1, Math.floor(w))
  c.height = Math.max(1, Math.floor(h))
  return c
}

function getGlowSprite(colorRgb: string, radius: number): HTMLCanvasElement {
  const r = quantize(radius, 2, 6, 64)
  const key = `glow:${colorRgb}:${r}`
  const cached = glowSpriteCache.get(key)
  if (cached) return cached

  const size = r * 6
  const c = createOffscreenCanvas(size, size)
  const ctx = c.getContext('2d')
  if (!ctx) return c

  const cx = size / 2
  const cy = size / 2
  const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r * 2.8)
  g.addColorStop(0, `rgba(${colorRgb}, 0.70)`)
  g.addColorStop(0.45, `rgba(${colorRgb}, 0.22)`)
  g.addColorStop(1, 'transparent')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, size, size)

  glowSpriteCache.set(key, c)
  return c
}

function getNodeSprite(type: NodeType, radius: number, intensity: number, balance: number): HTMLCanvasElement {
  const r = quantize(radius, 2, 6, 28)
  const inten = quantize(intensity, 0.1, 0.4, 2.5)
  // Quantize balance for caching: 0, -10, -50, -100, -500, etc. (logarithmic-ish)
  const balKey = balance >= 0 ? 0 : -Math.round(Math.log10(Math.abs(balance) + 1) * 10)
  const key = `node:${type}:${r}:${inten}:${balKey}`
  const cached = nodeSpriteCache.get(key)
  if (cached) return cached

  const startCache = performance.now()
  const baseColor = type === 'business' ? RENDER.colors.nodeBusiness : RENDER.colors.nodePerson
  let colorRgb = hexToRgb(baseColor)

  if (balance < 0) {
    const lowDebtRgb = hexToRgb(RENDER.colors.nodeDebtorLow)
    const highDebtRgb = hexToRgb(RENDER.colors.nodeDebtorHigh)
    // Interpolate: small debt => yellow, large debt => pinkish/red
    // Assume 2000 is a "large" debt for full color transition
    const factor = clamp(Math.abs(balance) / 2000, 0, 1)
    colorRgb = interpolateRgb(lowDebtRgb, highDebtRgb, factor)
  }
  
  const color = `rgb(${colorRgb})`

  const size = r * 12
  const c = createOffscreenCanvas(size, size)
  const ctx = c.getContext('2d')
  if (!ctx) return c

  const cx = size / 2
  const cy = size / 2

  // Multi-layer Bloom - reduced for cleaner look
  const isHighIntensity = inten > 1.2
  const bloomBase = isHighIntensity ? 0.15 : 0.05
  const bloomLayers = isHighIntensity ? 2 : 1
  for (let i = 0; i < bloomLayers; i++) {
    const layerR = r * (2.0 + i * 1.5)
    const layerAlpha = (bloomBase / (i + 1)) * inten
    const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, layerR)
    glow.addColorStop(0, `rgba(${colorRgb}, ${layerAlpha})`)
    glow.addColorStop(1, 'transparent')
    ctx.fillStyle = glow
    ctx.fillRect(0, 0, size, size)
  }

  // Core
  ctx.save()
  ctx.translate(cx, cy)
  ctx.shadowColor = `rgba(${colorRgb}, 0.8)`
  ctx.shadowBlur = r * (isHighIntensity ? 1.5 : 0.5)
  ctx.fillStyle = color
  
  const coreSize = r * 1.25
  ctx.beginPath()
  if (type === 'business') {
    const s = coreSize * 1.25
    const corner = s * 0.15
    ctx.moveTo(-s / 2 + corner, -s / 2)
    ctx.lineTo(s / 2 - corner, -s / 2)
    ctx.quadraticCurveTo(s / 2, -s / 2, s / 2, -s / 2 + corner)
    ctx.lineTo(s / 2, s / 2 - corner)
    ctx.quadraticCurveTo(s / 2, s / 2, s / 2 - corner, s / 2)
    ctx.lineTo(-s / 2 + corner, s / 2)
    ctx.quadraticCurveTo(-s / 2, s / 2, -s / 2, s / 2 - corner)
    ctx.lineTo(-s / 2, -s / 2 + corner)
    ctx.quadraticCurveTo(-s / 2, -s / 2, -s / 2 + corner, -s / 2)
  } else {
    ctx.arc(0, 0, coreSize, 0, Math.PI * 2)
  }
  ctx.fill()
  ctx.restore()

  // High-intensity rim (Sharp Border)
  ctx.save()
  ctx.translate(cx, cy)
  // White/light rim for clarity
  ctx.strokeStyle = `rgba(255, 255, 255, ${isHighIntensity ? 1.0 : 0.4})`
  ctx.lineWidth = isHighIntensity ? 1.8 : 1.0
  ctx.shadowColor = '#ffffff'
  ctx.shadowBlur = isHighIntensity ? 3 : 0
  
  ctx.beginPath()
  if (type === 'business') {
    const s = coreSize * 1.25
    const corner = s * 0.15
    ctx.moveTo(-s / 2 + corner, -s / 2)
    ctx.lineTo(s / 2 - corner, -s / 2)
    ctx.quadraticCurveTo(s / 2, -s / 2, s / 2, -s / 2 + corner)
    ctx.lineTo(s / 2, s / 2 - corner)
    ctx.quadraticCurveTo(s / 2, s / 2, s / 2 - corner, s / 2)
    ctx.lineTo(-s / 2 + corner, s / 2)
    ctx.quadraticCurveTo(-s / 2, s / 2, -s / 2, s / 2 - corner)
    ctx.lineTo(-s / 2, -s / 2 + corner)
    ctx.quadraticCurveTo(-s / 2, -s / 2, -s / 2 + corner, -s / 2)
  } else {
    ctx.arc(0, 0, coreSize, 0, Math.PI * 2)
  }
  ctx.stroke()
  ctx.restore()

  nodeSpriteCache.set(key, c)
  const endCache = performance.now()
  if (endCache - startCache > 2) {
    console.debug(`[PERF] Node sprite generated: ${key} in ${Math.round(endCache - startCache)}ms. Cache size: ${nodeSpriteCache.size}`)
  }
  return c
}

function buildBackground() {
  // Hi-res offscreen canvases (we draw them scaled-down in user-space, the main dpr transform scales back up).
  const w = canvasW * dpr
  const h = canvasH * dpr
  if (w <= 0 || h <= 0) return

  const base = createOffscreenCanvas(w, h)
  const bctx = base.getContext('2d')
  if (!bctx) return
  bctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  // Base fill (deep space)
  bctx.fillStyle = RENDER.bg.base
  bctx.fillRect(0, 0, canvasW, canvasH)

  // Starfield Noise / Deep Dust
  for (let i = 0; i < 400; i++) {
    const x = Math.random() * canvasW
    const y = Math.random() * canvasH
    const a = Math.random() * 0.04
    bctx.fillStyle = `rgba(255, 255, 255, ${a})`
    bctx.fillRect(x, y, 1, 1)
  }

  // Soft nebula/bokeh spots (few, but large)
  const rnd = (min: number, max: number) => min + Math.random() * (max - min)
  for (let i = 0; i < RENDER.bg.bokehSpots; i++) {
    const x = rnd(0.05, 0.95) * canvasW
    const y = rnd(0.05, 0.95) * canvasH
    const r = rnd(0.35, 0.75) * Math.min(canvasW, canvasH)
    const color = i % 2 === 0 ? RENDER.colors.nebula1 : RENDER.colors.nebula2
    
    const g = bctx.createRadialGradient(x, y, 0, x, y, r)
    g.addColorStop(0, color)
    g.addColorStop(1, 'transparent')
    bctx.fillStyle = g
    bctx.fillRect(0, 0, canvasW, canvasH)
  }

  // Deep Space Vignette
  {
    const vg = bctx.createRadialGradient(
      canvasW * 0.5, 
      canvasH * 0.5, 
      Math.min(canvasW, canvasH) * 0.1, 
      canvasW * 0.5, 
      canvasH * 0.5, 
      Math.max(canvasW, canvasH) * 0.8
    )
    vg.addColorStop(0, 'rgba(0, 0, 0, 0)')
    vg.addColorStop(0.7, 'rgba(2, 4, 8, 0.3)')
    vg.addColorStop(1, 'rgba(0, 0, 0, 0.85)')
    bctx.fillStyle = vg
    bctx.fillRect(0, 0, canvasW, canvasH)
  }

  bgBaseCanvas = base

  // Star layers
  bgStarLayers = []
  const total = Math.floor((canvasW * canvasH) / RENDER.bg.starCellPx)
  for (let layer = 1; layer <= RENDER.bg.starLayers; layer++) {
    const c = createOffscreenCanvas(w, h)
    const sctx = c.getContext('2d')
    if (!sctx) continue
    sctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    // distribution: many tiny, few bigger
    const layerMul = layer === 1 ? 1.5 : layer === 2 ? 0.9 : 0.5
    const count = Math.floor(total * layerMul)
    for (let i = 0; i < count; i++) {
      const x = Math.random() * canvasW
      const y = Math.random() * canvasH
      const p = Math.random()
      
      // Variable star sizes and brightness
      const r = p < 0.92 ? 0.8 : p < 0.98 ? 1.2 : 2.0
      const alpha = rnd(0.1, 0.4) / layer

      sctx.fillStyle = `rgba(255, 255, 255, ${alpha})`
      if (r < 1.0) {
        sctx.fillRect(x, y, 1, 1)
      } else {
        sctx.beginPath()
        sctx.arc(x, y, r, 0, Math.PI * 2)
        sctx.fill()
        
        // Star bloom for larger stars
        if (r > 1.5) {
          const sg = sctx.createRadialGradient(x, y, 0, x, y, r * 4)
          sg.addColorStop(0, `rgba(255, 255, 255, ${alpha * 0.5})`)
          sg.addColorStop(1, 'transparent')
          sctx.fillStyle = sg
          sctx.arc(x, y, r * 4, 0, Math.PI * 2)
          sctx.fill()
        }
      }
    }

    bgStarLayers.push(c)
  }
}

function applyQuality(next: 0 | 1 | 2) {
  qualityIndex = next
  dprClamp = next === 2 ? 1.75 : next === 1 ? RENDER.dpr.defaultClamp : 1.0
  // Rebuild cached assets to match the new DPI.
  resizeCanvas()
  buildBackground()
}

function getApiBaseUrl(): string {
  const raw = String(import.meta.env.VITE_API_BASE_URL || '').trim()
  return raw.replace(/\/$/, '')
}

function generateGraph(count: number, width: number, height: number): { nodes: GeoNode[]; links: GeoLink[] } {
  const ns: GeoNode[] = []
  const ls: GeoLink[] = []

  const cx = width / 2
  const cy = height / 2

  for (let i = 0; i < count; i++) {
    const angle = i * 0.5
    const dist = 50 + i * 7
    const jitter = 40
    const x = cx + Math.cos(angle) * dist + (Math.random() - 0.5) * jitter
    const y = cy + Math.sin(angle) * dist * 0.8 + (Math.random() - 0.5) * jitter

    const isBusiness = i % 4 === 0
    const baseSize = isBusiness ? 10 : 5

    ns.push({
      id: `n-${i}`,
      x: clamp(x, 50, width - 50),
      y: clamp(y, 50, height - 50),
      type: isBusiness ? 'business' : 'person',
      baseSize,
      currentSize: baseSize,
      name: isBusiness ? `BizNode ${i}` : `User ${i + 100}`,
      trustLimit: Math.floor(Math.random() * 5000) + 1000,
      balance: Math.floor(Math.random() * 2000) - 1000,
      trustScore: 80 + Math.floor(Math.random() * 20),
      renderX: clamp(x, 50, width - 50),
      renderY: clamp(y, 50, height - 50),
    })
  }

  for (const node of ns) {
    const neighbors = ns
      .filter((n) => n.id !== node.id)
      .map((n) => ({ id: n.id, dist: Math.hypot((n.x ?? 0) - (node.x ?? 0), (n.y ?? 0) - (node.y ?? 0)) }))
      .sort((a, b) => a.dist - b.dist)
      .slice(0, 3)

    for (const neighbor of neighbors) {
      const exists = ls.some((l) => {
        const s = typeof l.source === 'string' ? l.source : l.source.id
        const d = typeof l.target === 'string' ? l.target : l.target.id
        return (s === node.id && d === neighbor.id) || (s === neighbor.id && d === node.id)
      })
      if (exists) continue

      ls.push({
        id: `l-${node.id}-${neighbor.id}`,
        source: node.id,
        target: neighbor.id,
        trust: Math.floor(Math.random() * 1000),
      })
    }
  }

  return { nodes: ns, links: ls }
}

async function loadGraph(): Promise<{ nodes: GeoNode[]; links: GeoLink[] }> {
  const base = getApiBaseUrl()
  const url = `${base}/graph/snapshot`
  try {
    const res = await fetch(url, { method: 'GET' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const json = (await res.json()) as unknown
    if (!json || typeof json !== 'object') throw new Error('Invalid JSON')
    const data = json as { nodes?: unknown[]; links?: unknown[] }
    if (!Array.isArray(data.nodes) || !Array.isArray(data.links)) throw new Error('Invalid snapshot shape')

    const ns: GeoNode[] = data.nodes
      .filter((n): n is Record<string, unknown> => !!n && typeof n === 'object')
      .map((n, idx) => {
        const id = String(n.id ?? `api-n-${idx}`)
        const type = (n.type === 'business' || n.type === 'person' ? n.type : 'person') as NodeType
        const name = String(n.name ?? id)
        const trustLimit = Number(n.trustLimit ?? 1000)
        const balance = Number(n.balance ?? 0)

        const coords = (n.coordinates && typeof n.coordinates === 'object' ? (n.coordinates as Record<string, unknown>) : null) as
          | { x?: unknown; y?: unknown }
          | null

        const nx = coords?.x
        const ny = coords?.y
        const xRaw = typeof nx === 'number' ? nx : 0.5
        const yRaw = typeof ny === 'number' ? ny : 0.5

        const x = clamp(xRaw, 0, 1) * canvasW
        const y = clamp(yRaw, 0, 1) * canvasH

        const baseSize = type === 'business' ? 10 : 5
        return {
          id,
          type,
          name,
          trustLimit,
          balance,
          trustScore: 80,
          baseSize,
          currentSize: baseSize,
          x: clamp(x, 50, canvasW - 50),
          y: clamp(y, 50, canvasH - 50),
          renderX: clamp(x, 50, canvasW - 50),
          renderY: clamp(y, 50, canvasH - 50),
        }
      })

    const ls: GeoLink[] = data.links
      .filter((l): l is Record<string, unknown> => !!l && typeof l === 'object')
      .map((l, idx) => ({
        id: String(l.id ?? `api-l-${idx}`),
        source: String(l.source ?? ''),
        target: String(l.target ?? ''),
        trust: Number(l.trust ?? 0),
      }))
      .filter((l) => !!l.source && !!l.target)

    if (ns.length >= 2 && ls.length >= 1) return { nodes: ns, links: ls }
    throw new Error('Empty snapshot')
  } catch {
    return generateGraph(40, canvasW, canvasH)
  }
}

function resizeCanvas() {
  const wrapper = wrapperRef.value
  const canvas = canvasRef.value
  if (!wrapper || !canvas) return

  const rect = wrapper.getBoundingClientRect()
  canvasW = Math.max(320, Math.floor(rect.width))
  canvasH = Math.max(240, Math.floor(rect.height))
  dpr = Math.min(dprClamp, window.devicePixelRatio || 1)
  dpr = clamp(dpr, RENDER.dpr.min, dprClamp)

  canvas.width = Math.floor(canvasW * dpr)
  canvas.height = Math.floor(canvasH * dpr)
  canvas.style.width = `${canvasW}px`
  canvas.style.height = `${canvasH}px`

  renderCtx = canvas.getContext('2d', { alpha: false })
  if (renderCtx) renderCtx.setTransform(dpr, 0, 0, dpr, 0, 0)

  buildBackground()

  if (sim) {
    const center = sim.force('center') as ReturnType<typeof forceCenter> | undefined
    center?.x(canvasW / 2)
    center?.y(canvasH / 2)
    sim.alpha(0.8).restart()
  }
}

function spawnExplosion(x: number, y: number, colorHex: string, isBig: boolean) {
  const colorRgb = hexToRgb(colorHex)
  // Shockwave effect
  if (dust.length < RENDER.vfx.maxDust) {
    dust.push({ 
      type: 'shockwave', 
      x, 
      y, 
      life: 1.0, 
      size: isBig ? 10 : 5, 
      colorRgb 
    })
  }
}

function pickRelevantLink(): GeoLink | null {
  const ls = links.value
  if (ls.length === 0) return null
  const activeId = store.selectedNodeId
  if (!activeId) return ls[Math.floor(Math.random() * ls.length)] ?? null
  const filtered = ls.filter((l) => {
    const s = typeof l.source === 'string' ? l.source : l.source.id
    const d = typeof l.target === 'string' ? l.target : l.target.id
    return s === activeId || d === activeId
  })
  if (filtered.length === 0) return null
  return filtered[Math.floor(Math.random() * filtered.length)] ?? null
}

function triggerTransaction() {
  if (store.isClearing) return
  const link = pickRelevantLink()
  if (!link) return
  const src = resolveNode(link.source)
  const dst = resolveNode(link.target)
  if (!src || !dst) return

  const activeId = store.selectedNodeId
  const isOutgoing = activeId ? src.id === activeId : true
  const start = isOutgoing ? src : dst
  const end = isOutgoing ? dst : src

  if (particles.length >= RENDER.vfx.maxParticles) particles.shift()
  const amount = 150
  particles.push({
    type: 'tx',
    from: { kind: 'node', id: start.id },
    to: { kind: 'node', id: end.id },
    progress: 0,
    speedPx: 8,
    color: RENDER.colors.cyan,
    labelText: isOutgoing ? `+${amount}` : `-${amount}`,
    labelRgb: isOutgoing ? hexToRgb(RENDER.colors.cyan) : hexToRgb(RENDER.colors.danger),
    labelLife: 0.9,
  })
}

function findClearingGroup(): GeoNode[] {
  const allNodes = nodes.value
  const allLinks = links.value
  if (allNodes.length < 3) return []

  // Начинаем от выбранного узла или случайного
  const startNode = selectedNode.value || allNodes[Math.floor(Math.random() * allNodes.length)]
  if (!startNode) return []

  const group: GeoNode[] = [startNode]
  const visited = new Set<string>([startNode.id])

  const getNeighbors = (nodeId: string) => {
    return allLinks
      .filter((l) => {
        const s = typeof l.source === 'string' ? l.source : l.source.id
        const d = typeof l.target === 'string' ? l.target : l.target.id
        return s === nodeId || d === nodeId
      })
      .map((l) => {
        const s = typeof l.source === 'string' ? l.source : l.source.id
        const d = typeof l.target === 'string' ? l.target : l.target.id
        return s === nodeId ? d : s
      })
  }

  let currentId = startNode.id
  // Ищем цепочку из 3-4 узлов
  const targetSize = Math.floor(Math.random() * 2) + 3 // 3 или 4
  for (let i = 0; i < targetSize - 1; i++) {
    const neighbors = getNeighbors(currentId).filter((id) => !visited.has(id))
    if (neighbors.length === 0) break

    const nextId = neighbors[Math.floor(Math.random() * neighbors.length)]
    const nextNode = nodeById.value.get(nextId)
    if (nextNode) {
      group.push(nextNode)
      visited.add(nextId)
      currentId = nextId
    } else {
      break
    }
  }

  return group
}

function triggerClearing() {
  if (store.isClearing) return
  const group = findClearingGroup()
  if (group.length < 2) return

  store.setClearing(true)

  // Вычисляем визуальный центр группы для эффекта столкновения
  const centerX = group.reduce((sum, n) => sum + n.renderX, 0) / group.length
  const centerY = group.reduce((sum, n) => sum + n.renderY, 0) / group.length
  const clearingSum = Math.floor(Math.random() * 800 + 200)

  // Запуск взаимных искр между узлами группы
  for (let i = 0; i < group.length; i++) {
    const a = group[i]
    const nextIdx = (i + 1) % group.length
    const b = group[nextIdx]

    // Если это конец цепочки и она не замкнута в цикле в графе, 
    // можем либо пропустить, либо форсировать визуальный цикл.
    // Для эффекта клиринга лучше визуально замыкать или делать "все ко всем" к центру.
    
    // Искры к следующему узлу
    if (particles.length >= RENDER.vfx.maxParticles) particles.shift()
    particles.push({
      type: 'clearing',
      from: { kind: 'node', id: a.id },
      to: { kind: 'node', id: b.id },
      progress: 0,
      speedPx: RENDER.vfx.clearingSpeed,
      color: RENDER.colors.cyan,
    })

    // Встречные искры другого цвета
    if (particles.length >= RENDER.vfx.maxParticles) particles.shift()
    particles.push({
      type: 'clearing',
      from: { kind: 'node', id: b.id },
      to: { kind: 'node', id: a.id },
      progress: 0,
      speedPx: RENDER.vfx.clearingSpeed,
      color: RENDER.colors.nodeDebtor,
    })

    // Искры к центру для усиления эффекта столкновения
    if (particles.length >= RENDER.vfx.maxParticles) particles.shift()
    particles.push({
      type: 'clearing',
      from: { kind: 'node', id: a.id },
      to: { kind: 'point', x: centerX, y: centerY },
      progress: 0,
      speedPx: RENDER.vfx.clearingSpeed * 0.8,
      color: i % 2 === 0 ? RENDER.colors.cyan : RENDER.colors.nodeDebtor,
    })
  }

  // Завершение анимации через 1.5 секунды
  window.setTimeout(() => {
    // Эффект взрыва в центре
    spawnExplosion(centerX, centerY, RENDER.colors.gold, true)
    
    // Вылет итоговой суммы
    particles.push({
      type: 'tx',
      from: { kind: 'point', x: centerX, y: centerY },
      to: { kind: 'point', x: centerX, y: centerY - 40 },
      progress: 1.0,
      speedPx: 0,
      color: RENDER.colors.gold,
      labelText: `CLEARED: ${clearingSum}`,
      labelRgb: hexToRgb(RENDER.colors.gold),
      labelLife: 2.0,
    })

    // Визуальное обновление балансов
    group.forEach((n) => {
      // Имитируем уменьшение долга/баланса после клиринга
      n.balance = Math.trunc(n.balance * 0.6)
      spawnExplosion(n.renderX, n.renderY, RENDER.colors.cyan, false)
    })

    store.setClearing(false)
  }, 1500)
}

function initSimulation() {
  sim?.stop()
  const ns = nodes.value
  const ls = links.value
  if (ns.length === 0) return

  sim = forceSimulation<GeoNode>(ns)
    .alpha(1)
    .alphaMin(0.02) // lower min alpha
    .alphaDecay(0.04) // slower decay for smoother settling
    .force('charge', forceManyBody().strength(-70))
    .force('center', forceCenter(canvasW / 2, canvasH / 2))
    .force(
      'link',
      forceLink<GeoNode, GeoLink>(ls)
        .id((d: GeoNode) => d.id)
        .distance(100) // slightly more space
        .strength(0.65),
    )
    .force('collide', forceCollide<GeoNode>().radius((d: GeoNode) => d.baseSize * 3).strength(0.8))

  // We drive simulation manually (tick in RAF) to keep a single render loop.
  sim.stop()
}

function getPointerPos(e: PointerEvent): { x: number; y: number } {
  const canvas = canvasRef.value
  if (!canvas) return { x: 0, y: 0 }
  const rect = canvas.getBoundingClientRect()
  return { x: e.clientX - rect.left, y: e.clientY - rect.top }
}

function hitTestNode(x: number, y: number): GeoNode | null {
  let best: { node: GeoNode; dist: number } | null = null
  for (const n of nodes.value) {
    const dx = n.renderX - x
    const dy = n.renderY - y
    const d = Math.hypot(dx, dy)
    const r = n.baseSize * 3
    if (d > r) continue
    if (!best || d < best.dist) best = { node: n, dist: d }
  }
  return best ? best.node : null
}

function onPointerDown(e: PointerEvent) {
  if (e.button !== 0) return
  const canvas = canvasRef.value
  if (!canvas) return

  const { x, y } = getPointerPos(e)
  const node = hitTestNode(x, y)
  
  if (!node) {
    store.selectNode(null)
    return
  }

  e.preventDefault()
  canvas.setPointerCapture(e.pointerId)
  
  // Immediately select node to provide instant visual feedback (highlight)
  store.selectNode(node.id)

  drag.value = { dragging: true, node, pointerId: e.pointerId, startX: x, startY: y, moved: false }
  node.fx = x
  node.fy = y
  sim?.alphaTarget(0.32)
}

function onPointerMove(e: PointerEvent) {
  if (drag.value.dragging) {
    if (drag.value.pointerId !== e.pointerId) return
    const node = drag.value.node
    if (!node) return

    const { x, y } = getPointerPos(e)
    const dist = Math.hypot(x - drag.value.startX, y - drag.value.startY)
    const moved = dist > 3
    if (moved && !drag.value.moved) {
      drag.value.moved = true
    }
    node.fx = x
    node.fy = y
  }
}

function endDrag() {
  const node = drag.value.node
  if (node) {
    node.fx = null
    node.fy = null
  }
  drag.value = { dragging: false, node: null, pointerId: null, startX: 0, startY: 0, moved: false }
  sim?.alphaTarget(0)
}

function onPointerUp(e: PointerEvent) {
  const canvas = canvasRef.value
  if (canvas && drag.value.pointerId === e.pointerId) {
    try {
      canvas.releasePointerCapture(e.pointerId)
    } catch {
      // ignore
    }
  }
  endDrag()
}

function onClick(e: MouseEvent) {
  // If we moved significantly, it's a drag, not a click
  if (drag.value.moved) return
  
  const canvas = canvasRef.value
  if (!canvas) return

  const rect = canvas.getBoundingClientRect()
  const x = e.clientX - rect.left
  const y = e.clientY - rect.top
  const node = hitTestNode(x, y)
  
  // Selection is already handled in onPointerDown for nodes.
  // We only need to handle clicking on empty space here if needed,
  // but onPointerDown already handles store.selectNode(null) when hitTestNode fails.
  if (!node) {
    store.selectNode(null)
  }
}

function renderFrame() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = renderCtx
  if (!ctx) return

  const now = performance.now()
  if (fps.lastTs > 0) {
    const dt = Math.max(0.001, (now - fps.lastTs) / 1000)
    const inst = 1 / dt
    fps.ema += (inst - fps.ema) * 0.08
    if (fps.ema < RENDER.fps.lowThreshold) {
      fps.lowSeconds += dt
      fps.highSeconds = Math.max(0, fps.highSeconds - dt)
    } else if (fps.ema > RENDER.fps.highThreshold) {
      fps.highSeconds += dt
      fps.lowSeconds = Math.max(0, fps.lowSeconds - dt)
    } else {
      fps.lowSeconds = Math.max(0, fps.lowSeconds - dt)
      fps.highSeconds = Math.max(0, fps.highSeconds - dt)
    }

    if (fps.lowSeconds >= RENDER.fps.lowSustainSeconds && qualityIndex > 0) {
      fps.lowSeconds = 0
      applyQuality((qualityIndex - 1) as 0 | 1 | 2)
    }
    if (fps.highSeconds >= RENDER.fps.highSustainSeconds && qualityIndex < 2) {
      fps.highSeconds = 0
      applyQuality((qualityIndex + 1) as 0 | 1 | 2)
    }
  }
  fps.lastTs = now

  // physics step
  const s = sim
  if (s) {
    // Adaptive simulation: tick aggressively while "hot", then coast.
    const hot = drag.value.dragging || s.alpha() > 0.12
    const warm = s.alpha() > 0.045
    const ticks = hot ? 2 : warm ? 1 : (t % 3 < 0.01 ? 1 : 0)
    
    for (let i = 0; i < ticks; i++) s.tick()
  }

  t += 0.008

  // 1) Background (STATIC)
  ctx.globalCompositeOperation = 'source-over'
  if (bgBaseCanvas) ctx.drawImage(bgBaseCanvas, 0, 0, canvasW, canvasH)
  // Dynamic star layers removed to avoid "twitching" and maintain absolute stability
  /*
  if (bgStarLayers.length) {
    for (let i = 0; i < bgStarLayers.length; i++) {
      ctx.drawImage(bgStarLayers[i], 0, 0, canvasW, canvasH)
    }
  }
  */

  const sn = selectedNode.value
  const activeId = store.selectedNodeId

  // 2) Derived coords + size
  for (const n of nodes.value) {
    const isSelected = sn?.id === n.id
    const isActive = !!activeId && n.id === activeId
    
    // Nonlinear scaling based on balance
    // Sizes of nodes with positive and negative balance should be the same for equal absolute values
    const absBalance = Math.abs(n.balance)
    const balanceSizeAdd = Math.sqrt(absBalance) * 0.4
    
    const baseWithBalance = clamp(n.baseSize + balanceSizeAdd, RENDER.nodes.minSize, RENDER.nodes.maxSize)
    
    const targetSize = isSelected
      ? baseWithBalance * RENDER.nodes.selectedScale
      : isActive
        ? baseWithBalance * RENDER.nodes.hoverScale
        : baseWithBalance
    n.currentSize += (targetSize - n.currentSize) * 0.11

    const x = n.x ?? 0
    const y = n.y ?? 0
    const jitter = RENDER.nodes.jitterPx
    n.renderX = clamp(x + Math.sin(t * 1.5 + n.id.length) * jitter, 24, canvasW - 24)
    n.renderY = clamp(y + Math.cos(t * 1.5 + n.id.length) * jitter, 24, canvasH - 24)
  }

  // 3) Links
  // Base links (batch)
  ctx.save()
  ctx.globalCompositeOperation = 'source-over'
  ctx.beginPath()
  for (const l of links.value) {
    const a = resolveNode(l.source)
    const b = resolveNode(l.target)
    if (!a || !b) continue
    ctx.moveTo(a.renderX, a.renderY)
    ctx.lineTo(b.renderX, b.renderY)
  }
  ctx.lineWidth = RENDER.links.width
  ctx.strokeStyle = `rgba(${RENDER.colors.slateLineRgb}, ${activeId ? RENDER.links.dimAlpha : RENDER.links.baseAlpha})`
  ctx.stroke()
  ctx.restore()

  // Active links (glow + flicker)
  if (activeId) {
    ctx.save()
    ctx.globalCompositeOperation = 'lighter'
    ctx.lineWidth = RENDER.links.activeWidth
    ctx.shadowColor = RENDER.colors.cyan
    ctx.shadowBlur = RENDER.links.glowBlur + Math.sin(t * 8) * 4
    const flicker = 0.5 + Math.random() * 0.3
    ctx.strokeStyle = `rgba(${hexToRgb(RENDER.colors.cyan)}, ${RENDER.links.activeAlpha * flicker})`
    for (const l of links.value) {
      const a = resolveNode(l.source)
      const b = resolveNode(l.target)
      if (!a || !b) continue
      const isConnected = a.id === activeId || b.id === activeId
      if (!isConnected) continue
      ctx.beginPath()
      ctx.moveTo(a.renderX, a.renderY)
      ctx.lineTo(b.renderX, b.renderY)
      ctx.stroke()
    }
    ctx.restore()
  }

  // 4) Nodes
  ctx.save()
  const currentSnId = sn?.id
  const currentConnectedNodes = connectedNodes.value

  for (const n of nodes.value) {
    const isSelected = currentSnId === n.id
    const isActiveNode = activeId && (n.id === activeId || currentConnectedNodes.has(n.id))
    
    // Define a "resting" intensity when nothing is selected
    const baseIntensity = 0.85
    const intensity = activeId 
      ? (isSelected ? 1.5 : (isActiveNode ? 1.1 : 0.35))
      : baseIntensity
    
    const r = quantize(n.currentSize, 2, 6, 28)
    const inten = quantize(intensity, 0.1, 0.4, 2.5)
    // Quantize balance for caching: 0, -10, -50, -100, -500, etc. (logarithmic-ish)
    const balKey = n.balance >= 0 ? 0 : -Math.round(Math.log10(Math.abs(n.balance) + 1) * 10)
    const key = `node:${n.type}:${r}:${inten}:${balKey}`
    
    let sprite = nodeSpriteCache.get(key)
    if (!sprite) {
        sprite = getNodeSprite(n.type, n.currentSize, intensity, n.balance)
    }

    const dw = sprite.width / dpr
    const dh = sprite.height / dpr
    
    const isGhosted = activeId && !isActiveNode
    ctx.globalAlpha = isGhosted ? 0.3 : 1.0
    ctx.globalCompositeOperation = isGhosted ? 'source-over' : 'lighter'
    ctx.drawImage(sprite, n.renderX - dw / 2, n.renderY - dh / 2, dw, dh)
  }
  ctx.restore()

  // 5) Particles
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  ctx.font = '600 12px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'

  for (let i = particles.length - 1; i >= 0; i--) {
    const p = particles[i]
    if (!p) continue
    const a = resolveEndpoint(p.from)
    const b = resolveEndpoint(p.to)
    if (!a || !b) {
      particles.splice(i, 1)
      continue
    }

    const dx = b.x - a.x
    const dy = b.y - a.y
    const dist = Math.max(1, Math.hypot(dx, dy))
    
    // Slow down spark movement
    const speed = p.type === 'clearing' ? RENDER.vfx.clearingSpeed : RENDER.vfx.txSpeed
    p.progress += speed / dist
  if (p.progress >= 1) {
    // If it's a tx, we keep it for a bit longer to show the "flying out" label if it hasn't finished
    if (p.type === 'tx' && (p.labelLife ?? 0) > 0) {
      // Just keep progress at 1 and don't splice yet
    } else {
      particles.splice(i, 1)
      const explosionColor = p.type === 'clearing' ? RENDER.colors.cyan : p.color
      spawnExplosion(b.x, b.y, explosionColor, p.type === 'clearing')
      
      // Floating text for clearing collision
      if (p.type === 'clearing') {
        particles.push({
          type: 'tx', // reused tx type for label
          from: { kind: 'point', x: b.x, y: b.y },
          to: { kind: 'point', x: b.x, y: b.y - 10 },
          progress: 1.0,
          speedPx: 0,
          color: RENDER.colors.gold,
          labelText: `-${Math.floor(Math.random() * 50 + 10)}`,
          labelRgb: hexToRgb(RENDER.colors.gold),
          labelLife: 0.8
        })
      }
      continue
    }
  }

    const px = a.x + dx * Math.min(1, p.progress)
    const py = a.y + dy * Math.min(1, p.progress)
    const nx = -dy / dist
    const ny = dx / dist

    // Dynamic Spark + Long Tail (Schleif)
    if (p.progress < 1.0) {
      const tailLen = Math.min(RENDER.vfx.cometTailPx, dist * 0.45)
      const segments = 8
      const colorRgb = hexToRgb(p.color)

      for (let s = 0; s < segments; s++) {
        const s0 = s / segments
        const s1 = (s + 1) / segments
        const alpha = (1 - s0) * 0.8
        const width = (1 - s0) * (p.type === 'clearing' ? 3.5 : 4.0)
        
        const x0 = px - (dx / dist) * tailLen * s0
        const y0 = py - (dy / dist) * tailLen * s0
        const x1 = px - (dx / dist) * tailLen * s1
        const y1 = py - (dy / dist) * tailLen * s1

        ctx.strokeStyle = `rgba(${colorRgb}, ${alpha})`
        ctx.lineWidth = width
        ctx.beginPath()
        ctx.moveTo(x0, y0)
        ctx.lineTo(x1, y1)
        ctx.stroke()
      }

      // Spark Head Bloom
      const headGlow = getGlowSprite(colorRgb, 8)
      const hgw = headGlow.width / dpr
      const hgh = headGlow.height / dpr
      ctx.drawImage(headGlow, px - hgw / 2, py - hgh / 2, hgw, hgh)

      // Intense Core
      ctx.fillStyle = '#ffffff'
      ctx.beginPath()
      ctx.arc(px, py, 1.8, 0, Math.PI * 2)
      ctx.fill()
    }

    // label (tx only) - appears only at destination
    if (p.type === 'tx' && p.labelText && p.labelRgb && p.progress >= 1 && (p.labelLife ?? 0) > 0) {
      const life = (p.labelLife ?? 0) - 0.016
      p.labelLife = life
      const alpha = clamp(life / 0.9, 0, 1)
      
      // Fly up from node B
      const totalLife = 0.9
      const flyProgress = 1 - (life / totalLife)
      const lx = b.x
      const ly = b.y - 15 - flyProgress * 50

      ctx.save()
      ctx.fillStyle = `rgba(${p.labelRgb}, ${alpha})`
      ctx.font = '600 15px ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial'
      ctx.fillText(p.labelText + ' GC', lx, ly)
      ctx.restore()
    }
  }
  ctx.restore()

  // 6) Dust
  ctx.save()
  ctx.globalCompositeOperation = 'lighter'
  for (let i = dust.length - 1; i >= 0; i--) {
    const p = dust[i]
    if (!p) continue
    if (p.type === 'shockwave') {
      p.life -= 0.045
      p.size += 1.8
      if (p.life <= 0) {
        dust.splice(i, 1)
        continue
      }
      
      const alpha = p.life * 0.6
      ctx.save()
      ctx.beginPath()
      ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2)
      
      // Compact pulse ring
      ctx.strokeStyle = `rgba(${p.colorRgb}, ${alpha})`
      ctx.lineWidth = 1.2
      ctx.stroke()
      
      // Second slightly larger and fainter ring
      if (p.life > 0.4) {
        ctx.beginPath()
        ctx.arc(p.x, p.y, p.size * 1.25, 0, Math.PI * 2)
        ctx.strokeStyle = `rgba(${p.colorRgb}, ${alpha * 0.35})`
        ctx.lineWidth = 0.8
        ctx.stroke()
      }
      ctx.restore()
      continue
    }

    p.x += p.vx
    p.y += p.vy
    p.vx *= PHYSICS.drag
    p.vy *= PHYSICS.drag
    p.life -= p.decay
    if (p.life <= 0) {
      dust.splice(i, 1)
      continue
    }

    const alpha = p.life
    const size = p.size * alpha
    const sprite = getGlowSprite(p.colorRgb, size * 2.2)
    const dw = sprite.width / dpr
    const dh = sprite.height / dpr
    ctx.globalAlpha = alpha
    ctx.drawImage(sprite, p.x - dw / 2, p.y - dh / 2, dw, dh)
  }
  ctx.restore()

  // 7) Line to card
  ctx.globalCompositeOperation = 'source-over'
  if (sn) {
    const cx = clamp(sn.renderX - 110, 10, canvasW - 230)
    const cy = clamp(sn.renderY - 180, 10, canvasH - 220)
    
    ctx.beginPath()
    ctx.moveTo(sn.renderX, sn.renderY - (sn.currentSize + 5))
    ctx.lineTo(sn.renderX, cy + 100)
    let color = sn.type === 'business' ? RENDER.colors.nodeBusiness : RENDER.colors.nodePerson
    if (sn.balance < 0) color = RENDER.colors.nodeDebtor
    ctx.strokeStyle = `rgba(${hexToRgb(color)}, 0.25)`
    ctx.lineWidth = 1.0
    ctx.stroke()
  }

  // DUPLICATE REMOVED BY DIAGNOSIS
  /*
  if (sn) {
    cardX.value = clamp(sn.renderX - 120, 12, canvasW - 252)
    cardY.value = clamp(sn.renderY - 260, 12, canvasH - 320)
  }
  */

  rafId = window.requestAnimationFrame(renderFrame)
}

// Global debug flag to toggle logs from console
(window as any).SIM_DEBUG = false;

const NodeTypeIcon = computed(() => (selectedNode.value?.type === 'business' ? ShieldCheck : Users))

function closeCard() {
  store.selectNode(null)
}

onMounted(async () => {
  // initialize quality early (may change later via FPS adaptation)
  applyQuality(1)
  resizeCanvas()
  resizeObserver = new ResizeObserver(() => resizeCanvas())
  if (wrapperRef.value) resizeObserver.observe(wrapperRef.value)

  const data = await loadGraph()
  nodes.value = data.nodes
  links.value = data.links
  nodeById.value = new Map(data.nodes.map((n) => [n.id, n]))

  initSimulation()
  rafId = window.requestAnimationFrame(renderFrame)
})

onBeforeUnmount(() => {
  if (rafId !== null) window.cancelAnimationFrame(rafId)
  rafId = null

  if (clearingIntervalId !== null) window.clearInterval(clearingIntervalId)
  clearingIntervalId = null

  resizeObserver?.disconnect()
  resizeObserver = null

  sim?.stop()
  sim = null
})
</script>

<template>
  <div
    ref="wrapperRef"
    class="simRoot"
  >
    <canvas
      ref="canvasRef"
      class="simCanvas"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @click="onClick"
    />

    <GeoNodeCard
      v-if="selectedNode"
      ref="cardRef"
      :node="selectedNode"
      @close="closeCard"
    />

    <SimulatorControls
      :disabled-clearing="store.isClearing"
      @single-tx="triggerTransaction"
      @clearing="triggerClearing"
    />
  </div>
</template>

<style scoped>
.simRoot {
  position: relative;
  width: 100%;
  height: 100%;
  background: #020408;
  overflow: hidden;
  user-select: none;
}

.simCanvas {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
  cursor: pointer;
}
</style>

