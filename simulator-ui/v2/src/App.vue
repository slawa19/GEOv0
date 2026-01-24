<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import type { DemoEvent, EdgePatch, GraphLink, GraphNode, GraphSnapshot, NodePatch } from './types'
import { loadEvents, loadSnapshot } from './fixtures'
import { VIZ_MAPPING } from './vizMapping'
import { drawBaseGraph, type LayoutLink as RenderLayoutLink } from './render/baseGraph'
import { fillForNode, sizeForNode, type LayoutNode as RenderLayoutNode } from './render/nodePainter'
import { createFxState, renderFxFrame, resetFxState, spawnEdgePulses, spawnNodeBursts, spawnSparks } from './render/fxRenderer'

type SceneId = 'A' | 'B' | 'C' | 'D' | 'E'
type LayoutMode = 'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'

const eq = ref('UAH')
const scene = ref<SceneId>('A')

const layoutMode = ref<LayoutMode>('admin-force')

const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

// Playwright sets navigator.webdriver=true. Use it to keep screenshot tests stable even if
// someone runs the dev server with VITE_TEST_MODE=1.
const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true

const effectiveEq = computed(() => {
  // Demo (fixtures) mode: only UAH. Other equivalents are reserved for future real-mode.
  if (isDemoFixtures.value) return 'UAH'
  // Scene E must use canonical clearing cycles (UAH) per spec.
  if (scene.value === 'E') return 'UAH'
  return eq.value
})

const state = reactive({
  loading: true,
  error: '' as string,
  sourcePath: '' as string,
  eventsPath: '' as string,
  snapshot: null as GraphSnapshot | null,
  selectedNodeId: null as string | null,
  activeEdges: new Set<string>(),
  flash: 0 as number,
  floatingLabels: [] as Array<{ id: number; x: number; y: number; text: string; color: string; expiresAtMs: number }>,
})

const canvasEl = ref<HTMLCanvasElement | null>(null)
const fxCanvasEl = ref<HTMLCanvasElement | null>(null)
const hostEl = ref<HTMLDivElement | null>(null)

let rafId: number | null = null

function keyEdge(a: string, b: string) {
  return `${a}→${b}`
}

function getNodeById(id: string | null): GraphNode | null {
  if (!id || !state.snapshot) return null
  return state.snapshot.nodes.find((n) => n.id === id) ?? null
}

function fxColorForNode(nodeId: string, fallback: string): string {
  const n = getNodeById(nodeId)
  if (!n) return fallback
  return fillForNode(n, VIZ_MAPPING)
}

const selectedNode = computed(() => getNodeById(state.selectedNodeId))

type LayoutNode = GraphNode & { __x: number; __y: number }
type LayoutLink = GraphLink & { __key: string }

const layout = reactive({
  nodes: [] as LayoutNode[],
  links: [] as LayoutLink[],
  w: 0,
  h: 0,
})

const fxState = createFxState()

let txRunSeq = 0
let clearingRunSeq = 0

const activeTimeouts: number[] = []

function scheduleTimeout(fn: () => void, delayMs: number) {
  const id = window.setTimeout(fn, delayMs)
  activeTimeouts.push(id)
  return id
}

function clearScheduledTimeouts() {
  if (activeTimeouts.length === 0) return
  for (const id of activeTimeouts) window.clearTimeout(id)
  activeTimeouts.length = 0
}

function assertPlaylistEdgesExistInSnapshot(opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) {
  const { snapshot, events, eventsPath } = opts
  const ok = new Set(snapshot.links.map((l) => keyEdge(l.source, l.target)))

  const assertEdge = (from: string, to: string, ctx: string) => {
    const k = keyEdge(from, to)
    if (!ok.has(k)) {
      throw new Error(`Unknown edge '${k}' referenced by ${ctx} (${eventsPath})`)
    }
  }

  for (let ei = 0; ei < events.length; ei++) {
    const evt = events[ei]!
    const baseCtx = `event[${ei}] ${evt.type} ${'event_id' in evt ? String((evt as any).event_id ?? '') : ''}`.trim()

    if (evt.type === 'tx.updated') {
      for (let i = 0; i < evt.edges.length; i++) {
        const e = evt.edges[i]!
        assertEdge(e.from, e.to, `${baseCtx} edges[${i}]`)
      }
      continue
    }

    if (evt.type === 'clearing.plan') {
      for (let si = 0; si < evt.steps.length; si++) {
        const step = evt.steps[si]!
        const he = step.highlight_edges ?? []
        const pe = step.particles_edges ?? []
        for (let i = 0; i < he.length; i++) {
          const e = he[i]!
          assertEdge(e.from, e.to, `${baseCtx} steps[${si}].highlight_edges[${i}]`)
        }
        for (let i = 0; i < pe.length; i++) {
          const e = pe[i]!
          assertEdge(e.from, e.to, `${baseCtx} steps[${si}].particles_edges[${i}]`)
        }
      }
    }
  }
}

const fnv1a = (s: string) => {
  let h = 2166136261
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

type ForceGroupAnchors = Map<string, { x: number; y: number }>

function computeOrganicGroupAnchors(opts: {
  keys: string[]
  w: number
  h: number
  seedPrefix: string
}): ForceGroupAnchors {
  const { keys, w, h, seedPrefix } = opts
  const margin = 22
  const availW = Math.max(1, w - margin * 2)
  const availH = Math.max(1, h - margin * 2)
  const cx = w / 2
  const cy = h / 2
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

  const ordered = [...keys].sort((a, b) => a.localeCompare(b))
  const m = Math.max(1, ordered.length)
  const ax = new Float64Array(m)
  const ay = new Float64Array(m)
  const vx = new Float64Array(m)
  const vy = new Float64Array(m)

  // Deterministic initial placement: bias along the longer axis so groups use free space.
  const majorIsX = availW >= availH
  for (let i = 0; i < m; i++) {
    const key = ordered[i]!
    const seed = fnv1a(`${seedPrefix}:${key}`)
    const jx = (((seed % 1000) / 1000) - 0.5) * Math.min(availW, availH) * 0.08
    const jy = ((((Math.floor(seed / 1000) % 1000) / 1000) - 0.5) * Math.min(availW, availH) * 0.08)

    if (m <= 3) {
      const t = m === 1 ? 0.5 : m === 2 ? (i === 0 ? 0.33 : 0.67) : 0.25 + (i * 0.5)
      const x = majorIsX ? margin + availW * t : cx + jx
      const y = majorIsX ? cy + jy : margin + availH * t
      ax[i] = clamp(x, margin, w - margin)
      ay[i] = clamp(y, margin, h - margin)
      continue
    }

    // More groups: spread through area with deterministic pseudo-random.
    const rx = (seed % 4096) / 4096
    const ry = ((Math.floor(seed / 4096) % 4096) / 4096)
    ax[i] = margin + rx * availW + jx
    ay[i] = margin + ry * availH + jy
  }

  const area = availW * availH
  const k = Math.sqrt(area / Math.max(1, m))
  const iters = isTestMode.value ? 60 : 90
  const damping = 0.82
  const centerStrength = 0.08
  const repulseStrength = Math.max(0.001, k * k) * 0.12
  const softening2 = 64
  const minDist = Math.max(140, k * 1.1)

  for (let it = 0; it < iters; it++) {
    const alpha = 1 - it / iters
    for (let i = 0; i < m; i++) {
      vx[i] += (cx - ax[i]) * centerStrength * alpha
      vy[i] += (cy - ay[i]) * centerStrength * alpha
    }

    for (let i = 0; i < m; i++) {
      for (let j = i + 1; j < m; j++) {
        const dx = ax[i] - ax[j]
        const dy = ay[i] - ay[j]
        const dist2 = dx * dx + dy * dy + softening2
        const dist = Math.sqrt(dist2)
        const ux = dx / dist
        const uy = dy / dist

        const fRep = (repulseStrength / dist2) * 0.9
        vx[i] += ux * fRep
        vy[i] += uy * fRep
        vx[j] -= ux * fRep
        vy[j] -= uy * fRep

        if (dist < minDist) {
          const push = ((minDist - dist) / minDist) * 0.7
          vx[i] += ux * push
          vy[i] += uy * push
          vx[j] -= ux * push
          vy[j] -= uy * push
        }
      }
    }

    for (let i = 0; i < m; i++) {
      vx[i] *= damping
      vy[i] *= damping
      ax[i] += vx[i]
      ay[i] += vy[i]
      ax[i] = clamp(ax[i], margin, w - margin)
      ay[i] = clamp(ay[i], margin, h - margin)
    }
  }

  const out: ForceGroupAnchors = new Map()
  for (let i = 0; i < m; i++) out.set(ordered[i]!, { x: ax[i]!, y: ay[i]! })
  return out
}

function applyForceLayout(opts: {
  snapshot: GraphSnapshot
  w: number
  h: number
  seedKey: string
  groupKeyByNodeId?: Map<string, string>
  groupAnchors?: ForceGroupAnchors
  groupStrength?: number
  centerStrength?: number
  linkDistanceScaleSameGroup?: number
  linkDistanceScaleCrossGroup?: number
}) {
  const {
    snapshot,
    w,
    h,
    seedKey,
    groupKeyByNodeId,
    groupAnchors,
    groupStrength,
    centerStrength,
    linkDistanceScaleSameGroup,
    linkDistanceScaleCrossGroup,
  } = opts
  // Deterministic Force-Directed Layout:
  // - Gravity (center force)
  // - Optional group attraction (anchor force)
  // - Repulsion (many-body / charge)
  // - Link distance (springs)
  // - Collision
  const margin = 22
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

  const cx = w / 2
  const cy = h / 2

  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const n = Math.max(1, nodesSorted.length)

  const availW = Math.max(1, w - margin * 2)
  const availH = Math.max(1, h - margin * 2)
  const area = availW * availH
  const k = Math.sqrt(area / n)

  const idxById = new Map<string, number>()
  for (let i = 0; i < nodesSorted.length; i++) idxById.set(nodesSorted[i]!.id, i)

  const r = nodesSorted.map((node) => {
    const s = sizeForNode(node)
    return Math.max(8, Math.max(s.w, s.h) * 0.56)
  })
  const collidePad = 3

  // Initial positions: around group anchors (if provided), else centered disc.
  const x = new Float64Array(n)
  const y = new Float64Array(n)
  const vx = new Float64Array(n)
  const vy = new Float64Array(n)

  const baseRad = Math.max(24, Math.min(Math.min(availW, availH) * 0.22, k * 1.25))
  for (let i = 0; i < n; i++) {
    const node = nodesSorted[i]!
    const seed = fnv1a(`${snapshot.equivalent}:${seedKey}:${node.id}`)
    const a = ((seed % 4096) / 4096) * Math.PI * 2
    const rr = Math.sqrt(((Math.floor(seed / 4096) % 4096) / 4096))
    const rad = baseRad * rr

    const gKey = groupKeyByNodeId?.get(node.id)
    const anchor = (gKey && groupAnchors?.get(gKey)) ? groupAnchors!.get(gKey)! : { x: cx, y: cy }

    x[i] = anchor.x + Math.cos(a) * rad
    y[i] = anchor.y + Math.sin(a) * rad
    vx[i] = 0
    vy[i] = 0
  }

  const linksSorted = [...snapshot.links].sort((a, b) =>
    keyEdge(a.source, a.target).localeCompare(keyEdge(b.source, b.target)),
  )

  const iterations = isTestMode.value ? 140 : 180
  const damping = 0.88
  const maxSpeed = Math.max(0.8, k * 0.35)

  const cStrength = centerStrength ?? 0.060
  const gStrength = groupStrength ?? 0
  const chargeStrength = Math.max(0.001, k * k) * 0.020
  const springStrength = 0.022
  const linkDistanceBase = Math.max(16, k * 0.70)
  const collisionStrength = 0.70
  const softening2 = 36

  const sameK = linkDistanceScaleSameGroup ?? 0.90
  const crossK = linkDistanceScaleCrossGroup ?? 1.12

  for (let it = 0; it < iterations; it++) {
    const alpha = 1 - it / iterations

    // Center force.
    for (let i = 0; i < n; i++) {
      vx[i] += (cx - x[i]) * cStrength * alpha
      vy[i] += (cy - y[i]) * cStrength * alpha
    }

    // Group anchors: pull nodes toward their group's anchor (organic grouping, no rigid boxes).
    if (gStrength > 0 && groupKeyByNodeId && groupAnchors) {
      for (let i = 0; i < n; i++) {
        const gKey = groupKeyByNodeId.get(nodesSorted[i]!.id)
        if (!gKey) continue
        const a = groupAnchors.get(gKey)
        if (!a) continue
        vx[i] += (a.x - x[i]) * gStrength * alpha
        vy[i] += (a.y - y[i]) * gStrength * alpha
      }
    }

    // Many-body repulsion + collision.
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = x[i] - x[j]
        const dy = y[i] - y[j]
        const dist2 = dx * dx + dy * dy + softening2
        const dist = Math.sqrt(dist2)

        const far = dist / (k * 2.2)
        const falloff = 1 / (1 + far * far)
        const fRep = (chargeStrength / dist2) * falloff
        const ux = dx / dist
        const uy = dy / dist
        vx[i] += ux * fRep
        vy[i] += uy * fRep
        vx[j] -= ux * fRep
        vy[j] -= uy * fRep

        const minDist = r[i] + r[j] + collidePad
        if (dist < minDist) {
          const push = ((minDist - dist) / minDist) * collisionStrength
          vx[i] += ux * push
          vy[i] += uy * push
          vx[j] -= ux * push
          vy[j] -= uy * push
        }
      }
    }

    // Link springs.
    for (const l of linksSorted) {
      const si = idxById.get(l.source)
      const ti = idxById.get(l.target)
      if (si == null || ti == null) continue

      let desired = linkDistanceBase
      if (groupKeyByNodeId) {
        const gs = groupKeyByNodeId.get(l.source)
        const gt = groupKeyByNodeId.get(l.target)
        if (gs && gt && gs === gt) desired *= sameK
        else if (gs && gt && gs !== gt) desired *= crossK
      }

      const dx = x[si] - x[ti]
      const dy = y[si] - y[ti]
      const dist = Math.sqrt(dx * dx + dy * dy + softening2)
      const ux = dx / dist
      const uy = dy / dist
      const delta = dist - desired
      const f = delta * springStrength
      vx[si] -= ux * f
      vy[si] -= uy * f
      vx[ti] += ux * f
      vy[ti] += uy * f
    }

    // Seeded micro-jitter (very small, early only) to avoid symmetric traps.
    const jitter = alpha * k * 0.002
    if (jitter > 0) {
      for (let i = 0; i < n; i++) {
        const s = fnv1a(`${snapshot.equivalent}:${seedKey}:${nodesSorted[i]!.id}:${it}`)
        vx[i] += (((s % 2048) / 1024 - 1) * jitter)
        vy[i] += ((((Math.floor(s / 2048) % 2048) / 1024 - 1) * jitter))
      }
    }

    // Integrate.
    for (let i = 0; i < n; i++) {
      vx[i] *= damping
      vy[i] *= damping
      const sp = Math.sqrt(vx[i] * vx[i] + vy[i] * vy[i])
      if (sp > maxSpeed) {
        const s = maxSpeed / sp
        vx[i] *= s
        vy[i] *= s
      }
      x[i] += vx[i]
      y[i] += vy[i]
    }
  }

  // Recentering (translation only).
  let mx = 0
  let my = 0
  for (let i = 0; i < n; i++) {
    mx += x[i]
    my += y[i]
  }
  mx /= n
  my /= n
  const sx = cx - mx
  const sy = cy - my
  for (let i = 0; i < n; i++) {
    x[i] += sx
    y[i] += sy
  }

  // Fit to available viewport size (no wall/box forces; just a final affine transform).
  let maxNodeR = 0
  for (let i = 0; i < n; i++) maxNodeR = Math.max(maxNodeR, r[i] ?? 0)

  let halfW = 0
  let halfH = 0
  for (let i = 0; i < n; i++) {
    halfW = Math.max(halfW, Math.abs(x[i] - cx))
    halfH = Math.max(halfH, Math.abs(y[i] - cy))
  }

  const safeHalfW = Math.max(40, availW / 2 - maxNodeR - 12)
  const safeHalfH = Math.max(40, availH / 2 - maxNodeR - 12)
  const targetHalfW = safeHalfW * 0.92
  const targetHalfH = safeHalfH * 0.92

  if (halfW > 1 && halfH > 1) {
    const scaleX = Math.max(0.70, Math.min(2.20, targetHalfW / halfW))
    const scaleY = Math.max(0.70, Math.min(2.20, targetHalfH / halfH))
    for (let i = 0; i < n; i++) {
      x[i] = cx + (x[i] - cx) * scaleX
      y[i] = cy + (y[i] - cy) * scaleY
    }
  }

  layout.nodes = nodesSorted.map((base) => {
    const i = idxById.get(base.id) ?? 0
    return {
      ...base,
      __x: clamp(x[i] ?? w / 2, margin, w - margin),
      __y: clamp(y[i] ?? h / 2, margin, h - margin),
    }
  })

  const posCheck = new Map(layout.nodes.map((nn) => [nn.id, nn]))
  layout.links = snapshot.links.map((l) => ({
    ...l,
    __key: keyEdge(l.source, l.target),
  }))

  for (const l of layout.links) {
    if (!posCheck.has(l.source) || !posCheck.has(l.target)) {
      throw new Error(`Dangling link in layout: ${l.source}→${l.target}`)
    }
  }
}

function computeLayoutCommunityClusters(snapshot: GraphSnapshot, w: number, h: number) {
  // Community-style layout (deterministic): hubs + local clusters.
  // Implemented via the same force engine (organic + uses full viewport) with group anchors.
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const n = Math.max(1, nodesSorted.length)

  const degree = new Map<string, number>()
  for (const node of nodesSorted) degree.set(node.id, 0)
  for (const l of snapshot.links) {
    degree.set(l.source, (degree.get(l.source) ?? 0) + 1)
    degree.set(l.target, (degree.get(l.target) ?? 0) + 1)
  }

  const hubsCount = clamp(Math.round(Math.sqrt(n) / 2), 3, 8)
  const hubs = [...nodesSorted]
    .sort((a, b) => {
      const da = degree.get(a.id) ?? 0
      const db = degree.get(b.id) ?? 0
      if (db !== da) return db - da
      return a.id.localeCompare(b.id)
    })
    .slice(0, Math.min(hubsCount, nodesSorted.length))

  const hubIdx = new Map<string, number>()
  for (let i = 0; i < hubs.length; i++) hubIdx.set(hubs[i]!.id, i)

  const linksByNode = new Map<string, Set<string>>()
  for (const node of nodesSorted) linksByNode.set(node.id, new Set())
  for (const l of snapshot.links) {
    linksByNode.get(l.source)?.add(l.target)
    linksByNode.get(l.target)?.add(l.source)
  }

  // Assign each node to the hub it connects to the most (fallback: hash).
  const clusterKeyByNode = new Map<string, string>()
  for (const node of nodesSorted) {
    if (hubIdx.has(node.id)) {
      clusterKeyByNode.set(node.id, node.id)
      continue
    }
    const neigh = linksByNode.get(node.id) ?? new Set()
    let bestHub = hubs[0]?.id ?? node.id
    let bestScore = -1
    for (const hub of hubs) {
      const score = neigh.has(hub.id) ? 1 : 0
      if (score > bestScore) {
        bestScore = score
        bestHub = hub.id
      }
    }
    if (bestScore <= 0) {
      const seed = fnv1a(`${snapshot.equivalent}:assign:${node.id}`)
      bestHub = hubs.length ? hubs[seed % hubs.length]!.id : node.id
    }
    clusterKeyByNode.set(node.id, bestHub)
  }

  const groupKeys = hubs.map((h) => h.id)
  const anchors = computeOrganicGroupAnchors({
    keys: groupKeys,
    w,
    h,
    seedPrefix: `${snapshot.equivalent}:community:hubs`,
  })

  // Stronger grouping + shorter within-cluster links.
  applyForceLayout({
    snapshot,
    w,
    h,
    seedKey: 'community-clusters',
    groupKeyByNodeId: clusterKeyByNode,
    groupAnchors: anchors,
    groupStrength: 0.12,
    centerStrength: 0.045,
    linkDistanceScaleSameGroup: 0.82,
    linkDistanceScaleCrossGroup: 1.18,
  })
}

function computeLayoutAdminForce(snapshot: GraphSnapshot, w: number, h: number) {
  applyForceLayout({ snapshot, w, h, seedKey: 'admin-force', centerStrength: 0.070 })
}

function computeLayoutConstellations(
  snapshot: GraphSnapshot,
  w: number,
  h: number,
  groups: Array<{ key: string; label: string; nodes: GraphNode[] }>,
  flavorKey: string,
) {
  // Organic grouped layout: same force engine + group anchors.
  const activeGroups = groups.filter((g) => g.nodes.length > 0)

  const groupKeyByNode = new Map<string, string>()
  for (const g of activeGroups) {
    for (const n of g.nodes) groupKeyByNode.set(n.id, g.key)
  }

  const anchors = computeOrganicGroupAnchors({
    keys: activeGroups.map((g) => g.key),
    w,
    h,
    seedPrefix: `${snapshot.equivalent}:constellations:${flavorKey}`,
  })

  applyForceLayout({
    snapshot,
    w,
    h,
    seedKey: `constellations:${flavorKey}`,
    groupKeyByNodeId: groupKeyByNode,
    groupAnchors: anchors,
    groupStrength: 0.10,
    centerStrength: 0.040,
    linkDistanceScaleSameGroup: 0.90,
    linkDistanceScaleCrossGroup: 1.20,
  })
}

function computeLayoutTypeSplit(snapshot: GraphSnapshot, w: number, h: number) {
  // “Constellations by type”: institutions vs people.
  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const business = nodesSorted.filter((n) => String(n.type ?? '') === 'business')
  const other = nodesSorted.filter((n) => String(n.type ?? '') !== 'business')
  computeLayoutConstellations(
    snapshot,
    w,
    h,
    [
      { key: 'business', label: 'business', nodes: business },
      { key: 'other', label: 'other', nodes: other },
    ],
    'type',
  )
}

function computeLayoutStatusSplit(snapshot: GraphSnapshot, w: number, h: number) {
  // “Constellations by status”: active vs suspended vs left/deleted.
  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const statusKey = (n: GraphNode) => {
    const s = String(n.status ?? '')
    if (s === 'suspended') return 'suspended'
    if (s === 'left' || s === 'deleted') return 'inactive'
    return 'active'
  }
  const active = nodesSorted.filter((n) => statusKey(n) === 'active')
  const suspended = nodesSorted.filter((n) => statusKey(n) === 'suspended')
  const inactive = nodesSorted.filter((n) => statusKey(n) === 'inactive')

  computeLayoutConstellations(
    snapshot,
    w,
    h,
    [
      { key: 'active', label: 'active', nodes: active },
      { key: 'suspended', label: 'suspended', nodes: suspended },
      { key: 'inactive', label: 'inactive', nodes: inactive },
    ],
    'status',
  )
}

function computeLayoutBalanceSplit(snapshot: GraphSnapshot, w: number, h: number) {
  // “Constellations by balance”: debtors / neutral / creditors.
  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const debtors = nodesSorted.filter((n) => Number(n.net_sign ?? 0) < 0)
  const neutral = nodesSorted.filter((n) => Number(n.net_sign ?? 0) === 0)
  const creditors = nodesSorted.filter((n) => Number(n.net_sign ?? 0) > 0)

  computeLayoutConstellations(
    snapshot,
    w,
    h,
    [
      { key: 'debtors', label: 'debtors', nodes: debtors },
      { key: 'neutral', label: 'neutral', nodes: neutral },
      { key: 'creditors', label: 'creditors', nodes: creditors },
    ],
    'balance',
  )
}

function computeLayout(snapshot: GraphSnapshot, w: number, h: number, mode: LayoutMode) {
  if (mode === 'admin-force') return computeLayoutAdminForce(snapshot, w, h)
  if (mode === 'balance-split') return computeLayoutBalanceSplit(snapshot, w, h)
  if (mode === 'type-split') return computeLayoutTypeSplit(snapshot, w, h)
  if (mode === 'status-split') return computeLayoutStatusSplit(snapshot, w, h)
  return computeLayoutCommunityClusters(snapshot, w, h)
}

function resetOverlays() {
  state.activeEdges = new Set()
  state.flash = 0

  resetFxState(fxState)
}

function applyNodePatches(patches: NodePatch[] | undefined) {
  if (!patches?.length || !state.snapshot) return

  const snapIdx = new Map(state.snapshot.nodes.map((n, i) => [n.id, i]))
  const layoutIdx = new Map(layout.nodes.map((n, i) => [n.id, i]))

  for (const p of patches) {
    const si = snapIdx.get(p.id)
    if (si !== undefined) {
      const cur = state.snapshot.nodes[si]!
      state.snapshot.nodes[si] = {
        ...cur,
        net_balance_atoms: p.net_balance_atoms !== undefined ? p.net_balance_atoms : cur.net_balance_atoms,
        net_sign: p.net_sign !== undefined ? p.net_sign : cur.net_sign,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_size: p.viz_size !== undefined ? p.viz_size : cur.viz_size,
      }
    }

    const li = layoutIdx.get(p.id)
    if (li !== undefined) {
      const cur = layout.nodes[li]!
      layout.nodes[li] = {
        ...cur,
        net_balance_atoms: p.net_balance_atoms !== undefined ? p.net_balance_atoms : cur.net_balance_atoms,
        net_sign: p.net_sign !== undefined ? p.net_sign : cur.net_sign,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_size: p.viz_size !== undefined ? p.viz_size : cur.viz_size,
      }
    }
  }
}

function applyEdgePatches(patches: EdgePatch[] | undefined) {
  if (!patches?.length || !state.snapshot) return

  const snapIdx = new Map(state.snapshot.links.map((l, i) => [keyEdge(l.source, l.target), i]))
  const layoutIdx = new Map(layout.links.map((l, i) => [keyEdge(l.source, l.target), i]))

  for (const p of patches) {
    const k = keyEdge(p.source, p.target)

    const si = snapIdx.get(k)
    if (si !== undefined) {
      const cur = state.snapshot.links[si]!
      state.snapshot.links[si] = {
        ...cur,
        used: p.used !== undefined ? p.used : cur.used,
        available: p.available !== undefined ? p.available : cur.available,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_width_key: p.viz_width_key !== undefined ? p.viz_width_key : cur.viz_width_key,
        viz_alpha_key: p.viz_alpha_key !== undefined ? p.viz_alpha_key : cur.viz_alpha_key,
      }
    }

    const li = layoutIdx.get(k)
    if (li !== undefined) {
      const cur = layout.links[li]!
      layout.links[li] = {
        ...cur,
        used: p.used !== undefined ? p.used : cur.used,
        available: p.available !== undefined ? p.available : cur.available,
        viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
        viz_width_key: p.viz_width_key !== undefined ? p.viz_width_key : cur.viz_width_key,
        viz_alpha_key: p.viz_alpha_key !== undefined ? p.viz_alpha_key : cur.viz_alpha_key,
      }
    }
  }
}

function applyPatchesFromEvent(evt: DemoEvent) {
  if (evt.type !== 'tx.updated' && evt.type !== 'clearing.done') return
  applyNodePatches(evt.node_patch)
  applyEdgePatches(evt.edge_patch)
}

async function loadScene() {
  clearScheduledTimeouts()
  state.loading = true
  state.error = ''
  state.sourcePath = ''
  state.eventsPath = ''
  state.snapshot = null
  state.selectedNodeId = null
  resetOverlays()

  try {
    const { snapshot, sourcePath } = await loadSnapshot(effectiveEq.value)
    state.snapshot = snapshot
    state.sourcePath = sourcePath

    if (scene.value === 'D') {
      const r = await loadEvents(effectiveEq.value, 'demo-tx')
      state.eventsPath = r.sourcePath
    }
    if (scene.value === 'E') {
      const r = await loadEvents(effectiveEq.value, 'demo-clearing')
      state.eventsPath = r.sourcePath
    }

    resizeAndLayout()
    ensureRenderLoop()
  } catch (e: any) {
    state.error = String(e?.message ?? e)
  } finally {
    state.loading = false
  }
}

function resizeAndLayout() {
  const canvas = canvasEl.value
  const fxCanvas = fxCanvasEl.value
  const host = hostEl.value
  if (!canvas || !fxCanvas || !host || !state.snapshot) return

  const rect = host.getBoundingClientRect()
  const dpr = isTestMode.value ? 1 : Math.min(2, window.devicePixelRatio || 1)

  canvas.width = Math.max(1, Math.floor(rect.width * dpr))
  canvas.height = Math.max(1, Math.floor(rect.height * dpr))
  canvas.style.width = `${Math.floor(rect.width)}px`
  canvas.style.height = `${Math.floor(rect.height)}px`

  fxCanvas.width = canvas.width
  fxCanvas.height = canvas.height
  fxCanvas.style.width = canvas.style.width
  fxCanvas.style.height = canvas.style.height

  layout.w = rect.width
  layout.h = rect.height

  computeLayout(state.snapshot, rect.width, rect.height, layoutMode.value)
}

watch(layoutMode, () => {
  // Re-layout deterministically when user switches layout mode.
  resizeAndLayout()
})

onMounted(() => {
  try {
    const v = String(localStorage.getItem('geo.sim.layoutMode') ?? '')
    if (v === 'admin-force' || v === 'community-clusters' || v === 'balance-split') {
      layoutMode.value = v
    }
  } catch {
    // ignore
  }
})

onMounted(() => {
  // Dev-only hook for quick runtime sanity checks (does not affect rendering).
  if (!import.meta.env.DEV) return
  ;(window as any).__geoSim = {
    get isTestMode() {
      return isTestMode.value
    },
    get isWebDriver() {
      return isWebDriver
    },
    get loading() {
      return state.loading
    },
    get error() {
      return state.error
    },
    get hasSnapshot() {
      return !!state.snapshot
    },
    fxState,
    runTxOnce,
    runClearingOnce,
  }
})

watch(layoutMode, () => {
  try {
    localStorage.setItem('geo.sim.layoutMode', layoutMode.value)
  } catch {
    // ignore
  }
})

function renderFrame(nowMs: number) {
  const canvas = canvasEl.value
  const fxCanvas = fxCanvasEl.value
  if (!canvas || !fxCanvas || !state.snapshot) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const fx = fxCanvas.getContext('2d')
  if (!fx) return

  const dpr = canvas.width / Math.max(1, layout.w)
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

  fx.setTransform(dpr, 0, 0, dpr, 0, 0)

  ctx.clearRect(0, 0, layout.w, layout.h)
  fx.clearRect(0, 0, layout.w, layout.h)

  // Prevent unbounded DOM growth (labels are transient).
  if (state.floatingLabels.length > 0) {
    let write = 0
    for (let read = 0; read < state.floatingLabels.length; read++) {
      const fl = state.floatingLabels[read]!
      if (nowMs >= fl.expiresAtMs) continue
      state.floatingLabels[write++] = fl
    }
    state.floatingLabels.length = write
  }

  const pos = drawBaseGraph(ctx, {
    w: layout.w,
    h: layout.h,
    nodes: layout.nodes as unknown as RenderLayoutNode[],
    links: layout.links as unknown as RenderLayoutLink[],
    mapping: VIZ_MAPPING,
    selectedNodeId: state.selectedNodeId,
    activeEdges: state.activeEdges,
  })

  const r = renderFxFrame({
    nowMs,
    ctx: fx,
    pos,
    w: layout.w,
    h: layout.h,
    mapping: VIZ_MAPPING,
    fxState,
    flash: state.flash,
    isTestMode: isTestMode.value,
  })
  state.flash = r.flash
}

function ensureRenderLoop() {
  if (rafId !== null) return
  const loop = (t: number) => {
    renderFrame(t)
    rafId = window.requestAnimationFrame(loop)
  }
  rafId = window.requestAnimationFrame(loop)
}

function stopRenderLoop() {
  if (rafId !== null) {
    window.cancelAnimationFrame(rafId)
    rafId = null
  }
}

function pickNodeAt(clientX: number, clientY: number): LayoutNode | null {
  const host = hostEl.value
  const canvas = canvasEl.value
  if (!host || !canvas) return null

  const rect = host.getBoundingClientRect()
  const x = clientX - rect.left
  const y = clientY - rect.top

  // Simple hit test (no pan/zoom yet).
  for (const n of layout.nodes) {
    const { w, h } = sizeForNode(n)
    const r = Math.max(w, h) * 0.8
    const dx = x - n.__x
    const dy = y - n.__y
    if (dx * dx + dy * dy <= r * r) return n
  }
  return null
}

function onCanvasClick(ev: MouseEvent) {
  const hit = pickNodeAt(ev.clientX, ev.clientY)
  if (!hit) {
    state.selectedNodeId = null
    return
  }
  state.selectedNodeId = hit.id
}

function nodeCardStyle() {
  const host = hostEl.value
  if (!host || !selectedNode.value) return { display: 'none' }
  const node = layout.nodes.find((n) => n.id === selectedNode.value?.id)
  if (!node) return { display: 'none' }

  const rect = host.getBoundingClientRect()
  const x = Math.min(rect.width - 280, Math.max(12, node.__x + 16))
  const y = Math.min(rect.height - 160, Math.max(12, node.__y - 60))
  return { left: `${x}px`, top: `${y}px` }
}

async function runTxOnce() {
  if (!state.snapshot) return

  state.error = ''

  if (import.meta.env.DEV) {
    ;(window as any).__geoSimTxCalls = ((window as any).__geoSimTxCalls ?? 0) + 1
  }

  try {
    ensureRenderLoop()

    clearScheduledTimeouts()
    resetOverlays()

    const runId = ++txRunSeq

    const { events, sourcePath: eventsPath } = await loadEvents(effectiveEq.value, 'demo-tx')
    assertPlaylistEdgesExistInSnapshot({ snapshot: state.snapshot, events, eventsPath })
    const evt = events.find((e) => e.type === 'tx.updated')
    if (!evt || evt.type !== 'tx.updated') return

    for (const e of evt.edges) {
      state.activeEdges.add(keyEdge(e.from, e.to))
    }

    const ttl = Math.max(250, evt.ttl_ms || 1200)

    const fxTestMode = isTestMode.value && isWebDriver

    spawnSparks(fxState, {
      edges: evt.edges,
      nowMs: performance.now(),
      ttlMs: ttl,
      colorCore: VIZ_MAPPING.fx.tx_spark.core,
      colorTrail: VIZ_MAPPING.fx.tx_spark.trail,
      thickness: 1.0,
      kind: 'beam',
      seedPrefix: `tx:${effectiveEq.value}`,
      countPerEdge: 1,
      keyEdge,
      seedFn: fnv1a,
      isTestMode: fxTestMode,
    })

    // Flash at source
    if (!fxTestMode && evt.edges.length > 0) {
      const sourceId = evt.edges[0]!.from
      spawnNodeBursts(fxState, {
        nodeIds: [sourceId],
        nowMs: performance.now(),
        durationMs: 360,
        color: fxColorForNode(sourceId, VIZ_MAPPING.fx.tx_spark.trail),
        kind: 'tx-impact',
        seedPrefix: 'burst-src',
        seedFn: fnv1a,
        isTestMode: fxTestMode,
      })
    }

    if (import.meta.env.DEV) {
      ;(window as any).__geoSimLastTxSpawn = {
        t: Date.now(),
        sparks: fxState.sparks.length,
        edges: evt.edges.length,
        fxTestMode,
      }
    }

    // Flash and Label at destination
    if (!fxTestMode && evt.edges.length > 0) {
      const lastEdge = evt.edges[evt.edges.length - 1]!
      const targetId = lastEdge.to

      scheduleTimeout(() => {
        if (runId !== txRunSeq) return
        // 1. Flash Burst on target
        spawnNodeBursts(fxState, {
          nodeIds: [targetId],
          nowMs: performance.now(),
          durationMs: 520,
          color: fxColorForNode(targetId, VIZ_MAPPING.fx.tx_spark.trail),
          kind: 'tx-impact',
          seedPrefix: 'burst',
          seedFn: fnv1a,
          isTestMode: fxTestMode,
        })

        // 2. Floating Label
        const ln = layout.nodes.find((n) => n.id === targetId)
        if (ln) {
          state.floatingLabels.push({
            id: Math.floor(performance.now()),
            x: ln.__x,
            y: ln.__y - 20,
            text: '+125 GC',
            color: fxColorForNode(targetId, '#22d3ee'),
            // Keep in DOM slightly longer than CSS animation (prevents growth without visual change).
            expiresAtMs: performance.now() + 2200,
          })
        }
      }, ttl)
    }

    if (fxTestMode) return

    // Reset after all effects complete:
    // - spark flight takes `ttl` ms
    // - target burst starts at ttl and lasts 520ms
    // - add small buffer (50ms)
    const burstDurationMs = 520
    const cleanupDelayMs = ttl + burstDurationMs + 50

    scheduleTimeout(() => {
      if (runId !== txRunSeq) return
      applyPatchesFromEvent(evt)
      resetOverlays()
    }, cleanupDelayMs)
  } catch (e: any) {
    const msg = String(e?.message ?? e)
    state.error = msg
    if (import.meta.env.DEV) {
      ;(window as any).__geoSimLastTxError = msg
    }
    // eslint-disable-next-line no-console
    console.error(e)
  }
}

async function runClearingOnce() {
  if (!state.snapshot) return

  state.error = ''

  try {

  clearScheduledTimeouts()
  resetOverlays()

  const runId = ++clearingRunSeq

  const { events, sourcePath: eventsPath } = await loadEvents(effectiveEq.value, 'demo-clearing')
  assertPlaylistEdgesExistInSnapshot({ snapshot: state.snapshot, events, eventsPath })
  const plan = events.find((e) => e.type === 'clearing.plan')
  const done = events.find((e) => e.type === 'clearing.done')
  if (!plan || plan.type !== 'clearing.plan') return

  if (isTestMode.value) {
    // In test-mode, apply the first highlight step deterministically.
    const step0 = plan.steps[0]
    for (const e of step0?.highlight_edges ?? []) state.activeEdges.add(keyEdge(e.from, e.to))
    return
  }

  const t0 = performance.now()

  // Demo clearing animation (hardcoded look until real-mode):
  // - Same spark flight as Single Tx (beam)
  // - Golden color
  // - Only node flashes (NO full-screen flash)
  // - Floating amounts per closed edge
  // - Edge glow via FX layer (not activeEdges which is cyan)
  const clearingGold = '#fbbf24'
  // Rhythm tuning: slightly faster cadence, slightly longer flight
  // so the cycle reads as an exchange, but uses the same beam shape.
  const microTtlMs = 780 // flight duration (ms)
  const microGapMs = 110 // delay between micro-txs (ms)
  const labelLifeMs = 2200

  const formatDemoDebtAmount = (from: string, to: string, atMs: number) => {
    const h = fnv1a(`clearing:amt:${effectiveEq.value}:${atMs}:${keyEdge(from, to)}`)
    // 10..990, step 5 (stable + readable)
    const amt = 10 + (h % 197) * 5
    return `${amt} GC`
  }

  // Helper: animate single edge (beam spark + node glows)
  // NOTE: beam spark already renders the full edge glow, no need for separate edgePulse
  const animateEdge = (
    e: { from: string; to: string },
    delayMs: number,
    stepAtMs: number,
    idx: number,
  ) => {
    // 1. Start: source glow + spawn beam spark (beam renders edge glow internally)
    scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return

      spawnNodeBursts(fxState, {
        nodeIds: [e.from],
        nowMs: performance.now(),
        durationMs: 360,
        color: fxColorForNode(e.from, clearingGold),
        kind: 'tx-impact',
        seedPrefix: `clearing:fromGlow:${effectiveEq.value}:${stepAtMs}:${idx}`,
        seedFn: fnv1a,
        isTestMode: false,
      })

      spawnSparks(fxState, {
        edges: [e],
        nowMs: performance.now(),
        ttlMs: microTtlMs,
        colorCore: '#ffffff',
        colorTrail: clearingGold,
        thickness: 1.1,
        kind: 'beam',
        seedPrefix: `clearing:micro:${effectiveEq.value}:${stepAtMs}:${idx}`,
        countPerEdge: 1,
        keyEdge,
        seedFn: fnv1a,
        isTestMode: isTestMode.value,
      })
    }, delayMs)

    // 2. Spark arrives → target glow + label
    scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return

      spawnNodeBursts(fxState, {
        nodeIds: [e.to],
        nowMs: performance.now(),
        durationMs: 520,
        color: fxColorForNode(e.to, clearingGold),
        kind: 'tx-impact',
        seedPrefix: `clearing:impact:${effectiveEq.value}:${stepAtMs}:${idx}`,
        seedFn: fnv1a,
        isTestMode: false,
      })

      const ln = layout.nodes.find((n) => n.id === e.to)
      if (ln) {
        state.floatingLabels.push({
          id: Math.floor(performance.now()) + fnv1a(`lbl:${stepAtMs}:${idx}:${keyEdge(e.from, e.to)}`),
          x: ln.__x,
          y: ln.__y - 20,
          text: formatDemoDebtAmount(e.from, e.to, stepAtMs),
          color: fxColorForNode(e.to, clearingGold),
          expiresAtMs: performance.now() + labelLifeMs,
        })
      }
    }, delayMs + microTtlMs)
  }

  for (const step of plan.steps) {
    scheduleTimeout(() => {
      if (runId !== clearingRunSeq) return

      if (step.highlight_edges && step.highlight_edges.length > 0) {
        spawnEdgePulses(fxState, {
          edges: step.highlight_edges,
          nowMs: performance.now(),
          durationMs: 650,
          color: clearingGold,
          thickness: 1.0,
          seedPrefix: `clearing:highlight:${effectiveEq.value}:${step.at_ms}`,
          countPerEdge: 1,
          keyEdge,
          seedFn: fnv1a,
          isTestMode: false,
        })
      }

      // Particles step: sequential micro-transactions along the cycle
      if (step.particles_edges) {
        const edges = step.particles_edges
        for (let i = 0; i < edges.length; i++) {
          animateEdge(edges[i]!, i * microGapMs, step.at_ms, i)
        }
      }
    }, Math.max(0, step.at_ms))
  }

  // IMPORTANT: the clearing demo schedules extra timers (micro txs).
  // If we reset overlays too early, sparks get wiped mid-flight.
  // Timeline for each edge in particles_edges:
  // - spark starts at: step.at_ms + idx * microGapMs
  // - spark arrives at: step.at_ms + idx * microGapMs + microTtlMs
  // - target burst starts at arrival and lasts 520ms
  // So the last effect ends at: step.at_ms + (count-1)*microGapMs + microTtlMs + 520ms
  const targetBurstDurationMs = 520
  let doneAt = 0
  for (const s of plan.steps) {
    const base = Math.max(0, s.at_ms)
    const count = s.particles_edges?.length ?? 0
    // Time when last spark arrives + burst duration
    const lastBurstEndsAt = count > 0
      ? (count - 1) * microGapMs + microTtlMs + targetBurstDurationMs
      : 0
    // highlight_edges pulse lasts 650ms
    doneAt = Math.max(doneAt, base + 650, base + lastBurstEndsAt)
  }
  // Add small buffer
  const cleanupDelayMs = doneAt + 50

  scheduleTimeout(() => {
    if (runId !== clearingRunSeq) return
    if (done && done.type === 'clearing.done') applyPatchesFromEvent(done)
    resetOverlays()
  }, cleanupDelayMs)
  } catch (e: any) {
    const msg = String(e?.message ?? e)
    state.error = msg
    if (import.meta.env.DEV) {
      ;(window as any).__geoSimLastClearingError = msg
    }
    // eslint-disable-next-line no-console
    console.error(e)
  }
}

watch([eq, scene], () => {
  loadScene()
})

onMounted(() => {
  // Allow deterministic deep-links for e2e/visual tests.
  try {
    const params = new URLSearchParams(window.location.search)

    const s = String(params.get('scene') ?? '')
    if (s === 'A' || s === 'B' || s === 'C' || s === 'D' || s === 'E') {
      scene.value = s
    }

    const lm = String(params.get('layout') ?? '')
    if (lm === 'admin-force' || lm === 'community-clusters' || lm === 'balance-split' || lm === 'type-split' || lm === 'status-split') {
      layoutMode.value = lm
    }
  } catch {
    // ignore
  }

  loadScene()
  window.addEventListener('resize', resizeAndLayout)
})

onUnmounted(() => {
  window.removeEventListener('resize', resizeAndLayout)
  clearScheduledTimeouts()
  stopRenderLoop()
})
</script>

<template>
  <div
    ref="hostEl"
    class="root"
    :data-ready="!state.loading && !state.error && state.snapshot ? '1' : '0'"
    :data-scene="scene"
    :data-layout="layoutMode"
  >
    <canvas ref="canvasEl" class="canvas" @click="onCanvasClick" />
    <canvas ref="fxCanvasEl" class="canvas canvas-fx" />

    <!-- Minimal top HUD (controls + small status) -->
    <div class="hud-top">
      <div class="hud-row">
        <div class="pill">
          <span class="label">EQ</span>
          <template v-if="isDemoFixtures">
            <span class="value">UAH</span>
          </template>
          <template v-else>
            <select v-model="eq" class="select" aria-label="Equivalent">
              <option value="UAH">UAH</option>
              <option value="HOUR">HOUR</option>
              <option value="EUR">EUR</option>
            </select>
          </template>
        </div>

        <div class="field">
          <span class="label">Layout</span>
          <select v-model="layoutMode" class="select" aria-label="Layout">
            <option value="admin-force">Organic cloud (links)</option>
            <option value="community-clusters">Community clusters</option>
            <option value="balance-split">Constellations: balance</option>
            <option value="type-split">Constellations: type</option>
            <option value="status-split">Constellations: status</option>
          </select>
        </div>

        <div class="pill">
          <span class="label">Scene</span>
          <select v-model="scene" class="select" aria-label="Scene">
            <option value="A">A — Overview</option>
            <option value="B">B — Focus</option>
            <option value="C">C — Statuses</option>
            <option value="D">D — Tx burst</option>
            <option value="E">E — Clearing</option>
          </select>
        </div>

        <div v-if="state.snapshot" class="pill subtle" aria-label="Stats">
          <span class="mono">Nodes {{ state.snapshot.nodes.length }} | Links {{ state.snapshot.links.length }}</span>
        </div>
      </div>
    </div>

    <!-- Node card -->
    <div v-if="selectedNode" class="node-card" :style="nodeCardStyle()">
      <div class="node-title">{{ selectedNode.name ?? selectedNode.id }}</div>
      <div class="node-meta">
        <div><span class="k">Type</span> <span class="v">{{ selectedNode.type ?? '—' }}</span></div>
        <div><span class="k">Status</span> <span class="v">{{ selectedNode.status ?? '—' }}</span></div>
        <div><span class="k">Net</span> <span class="v mono">{{ selectedNode.net_balance_atoms ?? '—' }}</span></div>
      </div>
    </div>

    <!-- Bottom HUD (as per prototypes: minimal buttons) -->
    <div class="hud-bottom">
      <button class="btn" type="button" @click="runTxOnce">Single Tx</button>
      <button class="btn" type="button" @click="runClearingOnce">Run Clearing</button>
    </div>

    <!-- Loading / error overlay (fail-fast, but non-intrusive) -->
    <div v-if="state.loading" class="overlay">Loading fixtures…</div>
    <div v-else-if="state.error" class="overlay overlay-error">
      <div class="overlay-title">Fixtures error</div>
      <div class="overlay-text mono">{{ state.error }}</div>
    </div>

    <!-- Floating Labels -->
    <div
      v-for="fl in state.floatingLabels"
      :key="fl.id"
      class="floating-label"
      :style="{ left: fl.x + 'px', top: fl.y + 'px', color: fl.color }"
    >
      {{ fl.text }}
    </div>
  </div>
</template>

<style scoped>
.root {
  position: relative;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  background: #020617;
  color: rgba(226, 232, 240, 0.9);
  font-family:
    ui-sans-serif,
    system-ui,
    -apple-system,
    Segoe UI,
    Roboto,
    Ubuntu,
    Cantarell,
    Noto Sans,
    Arial,
    "Apple Color Emoji",
    "Segoe UI Emoji";
}

.canvas {
  position: absolute;
  inset: 0;
}

.canvas-fx {
  pointer-events: none;
}

.hud-top {
  position: absolute;
  top: 12px;
  left: 12px;
  right: 12px;
  pointer-events: none;
}

.hud-row {
  display: flex;
  gap: 10px;
  align-items: center;
  pointer-events: auto;
}

.pill {
  display: inline-flex;
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
}

.pill.subtle {
  opacity: 0.85;
}

.label {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.65);
}

.select {
  background: transparent;
  color: rgba(226, 232, 240, 0.95);
  border: none;
  outline: none;
  font-size: 12px;
}

.value {
  color: rgba(226, 232, 240, 0.95);
  font-size: 12px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New";
}

.node-card {
  position: absolute;
  width: 260px;
  z-index: 10;
  padding: 12px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.78);
  border: 1px solid rgba(148, 163, 184, 0.18);
  backdrop-filter: blur(12px);
  box-shadow: 0 18px 60px rgba(0, 0, 0, 0.35);
}

.node-title {
  font-size: 13px;
  font-weight: 650;
  margin-bottom: 8px;
}

.node-meta {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.78);
  display: grid;
  gap: 4px;
}

.k {
  color: rgba(226, 232, 240, 0.55);
  margin-right: 6px;
}

.hud-bottom {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  display: flex;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 16px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
}

.btn {
  appearance: none;
  border: 1px solid rgba(148, 163, 184, 0.22);
  background: rgba(2, 6, 23, 0.15);
  color: rgba(226, 232, 240, 0.9);
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 12px;
  cursor: pointer;
}

.btn:hover {
  border-color: rgba(34, 211, 238, 0.45);
}

.overlay {
  position: absolute;
  inset: 12px;
  display: flex;
  align-items: flex-start;
  justify-content: flex-start;
  pointer-events: none;
  z-index: 20;
}

.overlay-title {
  font-size: 12px;
  font-weight: 650;
  margin-bottom: 6px;
}

.overlay-text {
  font-size: 12px;
  color: rgba(226, 232, 240, 0.78);
  max-width: 820px;
  white-space: pre-wrap;
}

.overlay,
.overlay-error {
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
}

.overlay-error {
  border-color: rgba(248, 113, 113, 0.35);
}

.floating-label {
  position: absolute;
  pointer-events: none;
  font-size: 15px;
  font-weight: 700;
  text-shadow: 0 0 10px currentColor; /* Glow */
  animation: floatUpFade 1.8s ease-out forwards;
  z-index: 50;
  transform: translate(-50%, -50%); /* Centered on spawn point */
}

@keyframes floatUpFade {
  0% {
    opacity: 0;
    transform: translate(-50%, -40%) scale(0.8);
  }
  15% {
    opacity: 1;
    transform: translate(-50%, -50%) scale(1.1);
  }
  100% {
    opacity: 0;
    transform: translate(-50%, -120%) scale(1.0);
  }
}
</style>
