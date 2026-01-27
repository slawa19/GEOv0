import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { sizeForNode } from '../render/nodePainter'
import { fnv1a } from '../utils/hash'
import { clamp } from '../utils/math'
import { keyEdge } from '../utils/edgeKey'

export type LayoutMode = 'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'

export type { LayoutLink, LayoutNode } from '../types/layout'

type ForceGroupAnchors = Map<string, { x: number; y: number }>

export type ForceLayoutOptions = {
  snapshot: GraphSnapshot
  w: number
  h: number
  seedKey: string
  isTestMode: boolean

  groupKeyByNodeId?: Map<string, string>
  groupAnchors?: ForceGroupAnchors
  groupStrength?: number
  centerStrength?: number
  linkDistanceScaleSameGroup?: number
  linkDistanceScaleCrossGroup?: number
}

function computeOrganicGroupAnchors(opts: {
  keys: string[]
  w: number
  h: number
  seedPrefix: string
  isTestMode: boolean
}): ForceGroupAnchors {
  const { keys, w, h, seedPrefix, isTestMode } = opts
  const margin = 22
  const availW = Math.max(1, w - margin * 2)
  const availH = Math.max(1, h - margin * 2)
  const cx = w / 2
  const cy = h / 2

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
  const iters = isTestMode ? 60 : 90
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

export function applyForceLayout(opts: ForceLayoutOptions): { nodes: LayoutNode[]; links: LayoutLink[] } {
  const {
    snapshot,
    w,
    h,
    seedKey,
    isTestMode,
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
    const anchor = gKey && groupAnchors?.get(gKey) ? groupAnchors.get(gKey)! : { x: cx, y: cy }

    x[i] = anchor.x + Math.cos(a) * rad
    y[i] = anchor.y + Math.sin(a) * rad
    vx[i] = 0
    vy[i] = 0
  }

  const linksSorted = [...snapshot.links].sort((a, b) =>
    keyEdge(a.source, a.target).localeCompare(keyEdge(b.source, b.target)),
  )

  const iterations = isTestMode ? 140 : 180
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

  const nodes: LayoutNode[] = nodesSorted.map((base) => {
    const i = idxById.get(base.id) ?? 0
    return {
      ...base,
      __x: clamp(x[i] ?? w / 2, margin, w - margin),
      __y: clamp(y[i] ?? h / 2, margin, h - margin),
    }
  })

  const posCheck = new Map(nodes.map((nn) => [nn.id, nn]))
  const links: LayoutLink[] = snapshot.links.map((l) => ({
    ...l,
    __key: keyEdge(l.source, l.target),
  }))

  for (const l of links) {
    if (!posCheck.has(l.source) || !posCheck.has(l.target)) {
      throw new Error(`Dangling link in layout: ${l.source}→${l.target}`)
    }
  }

  return { nodes, links }
}

function computeLayoutCommunityClusters(snapshot: GraphSnapshot, w: number, h: number, isTestMode: boolean) {
  // Community-style layout (deterministic): hubs + local clusters.
  // Implemented via the same force engine (organic + uses full viewport) with group anchors.
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
    isTestMode,
  })

  // Stronger grouping + shorter within-cluster links.
  return applyForceLayout({
    snapshot,
    w,
    h,
    seedKey: 'community-clusters',
    isTestMode,
    groupKeyByNodeId: clusterKeyByNode,
    groupAnchors: anchors,
    groupStrength: 0.12,
    centerStrength: 0.045,
    linkDistanceScaleSameGroup: 0.82,
    linkDistanceScaleCrossGroup: 1.18,
  })
}

function computeLayoutAdminForce(snapshot: GraphSnapshot, w: number, h: number, isTestMode: boolean) {
  return applyForceLayout({ snapshot, w, h, seedKey: 'admin-force', isTestMode, centerStrength: 0.070 })
}

function computeLayoutConstellations(
  snapshot: GraphSnapshot,
  w: number,
  h: number,
  groups: Array<{ key: string; label: string; nodes: GraphNode[] }>,
  flavorKey: string,
  isTestMode: boolean,
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
    isTestMode,
  })

  return applyForceLayout({
    snapshot,
    w,
    h,
    seedKey: `constellations:${flavorKey}`,
    isTestMode,
    groupKeyByNodeId: groupKeyByNode,
    groupAnchors: anchors,
    groupStrength: 0.10,
    centerStrength: 0.040,
    linkDistanceScaleSameGroup: 0.90,
    linkDistanceScaleCrossGroup: 1.20,
  })
}

function computeLayoutTypeSplit(snapshot: GraphSnapshot, w: number, h: number, isTestMode: boolean) {
  // “Constellations by type”: institutions vs people.
  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const business = nodesSorted.filter((n) => String(n.type ?? '') === 'business')
  const other = nodesSorted.filter((n) => String(n.type ?? '') !== 'business')
  return computeLayoutConstellations(
    snapshot,
    w,
    h,
    [
      { key: 'business', label: 'business', nodes: business },
      { key: 'other', label: 'other', nodes: other },
    ],
    'type',
    isTestMode,
  )
}

function computeLayoutStatusSplit(snapshot: GraphSnapshot, w: number, h: number, isTestMode: boolean) {
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

  return computeLayoutConstellations(
    snapshot,
    w,
    h,
    [
      { key: 'active', label: 'active', nodes: active },
      { key: 'suspended', label: 'suspended', nodes: suspended },
      { key: 'inactive', label: 'inactive', nodes: inactive },
    ],
    'status',
    isTestMode,
  )
}

function computeLayoutBalanceSplit(snapshot: GraphSnapshot, w: number, h: number, isTestMode: boolean) {
  // “Constellations by balance”: debtors / neutral / creditors.
  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const debtors = nodesSorted.filter((n) => Number(n.net_sign ?? 0) < 0)
  const neutral = nodesSorted.filter((n) => Number(n.net_sign ?? 0) === 0)
  const creditors = nodesSorted.filter((n) => Number(n.net_sign ?? 0) > 0)

  return computeLayoutConstellations(
    snapshot,
    w,
    h,
    [
      { key: 'debtors', label: 'debtors', nodes: debtors },
      { key: 'neutral', label: 'neutral', nodes: neutral },
      { key: 'creditors', label: 'creditors', nodes: creditors },
    ],
    'balance',
    isTestMode,
  )
}

export function computeLayoutForMode(
  snapshot: GraphSnapshot,
  w: number,
  h: number,
  mode: LayoutMode,
  isTestMode: boolean,
): { nodes: LayoutNode[]; links: LayoutLink[] } {
  if (mode === 'admin-force') return computeLayoutAdminForce(snapshot, w, h, isTestMode)
  if (mode === 'balance-split') return computeLayoutBalanceSplit(snapshot, w, h, isTestMode)
  if (mode === 'type-split') return computeLayoutTypeSplit(snapshot, w, h, isTestMode)
  if (mode === 'status-split') return computeLayoutStatusSplit(snapshot, w, h, isTestMode)
  return computeLayoutCommunityClusters(snapshot, w, h, isTestMode)
}
