import type { GraphNode, GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { quadtree } from 'd3-quadtree'
import { sizeForNode } from '../render/nodePainter'
import { fnv1a } from '../utils/hash'
import { clamp, safeClampToViewport } from '../utils/math'
import { keyEdge } from '../utils/edgeKey'
import { createThrottledWarn } from '../utils/throttledWarn'

const warnDanglingLink = createThrottledWarn(5000)
let danglingLinkFilteredCount = 0

export type LayoutMode = 'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'

export type { LayoutLink, LayoutNode } from '../types/layout'

type ForceGroupAnchors = Map<string, { x: number; y: number }>

export type ForceLayoutOptions = {
  snapshot: GraphSnapshot
  w: number
  h: number
  seedKey: string
  isTestMode: boolean

  /** Barnes–Hut theta for many-body repulsion (speed/accuracy). Default: 0.9. ITEM-6. */
  chargeTheta?: number

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
  const MARGIN_PX = 22
  const JITTER_BUCKET = 1000
  const JITTER_SCALE = 0.08
  const RANDOM_BUCKET = 4096
  const ITERS_TEST = 60
  const ITERS_NORMAL = 90
  const DAMPING = 0.82
  const CENTER_STRENGTH = 0.08
  const REPULSE_K = 0.12
  const REPULSE_MULT = 0.9
  const SOFTENING2 = 64
  const MIN_DIST_MIN_PX = 140
  const MIN_DIST_K = 1.1
  const PUSH_MULT = 0.7

  const margin = MARGIN_PX
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
    const jx = (((seed % JITTER_BUCKET) / JITTER_BUCKET) - 0.5) * Math.min(availW, availH) * JITTER_SCALE
    const jy =
      ((((Math.floor(seed / JITTER_BUCKET) % JITTER_BUCKET) / JITTER_BUCKET) - 0.5) * Math.min(availW, availH) *
        JITTER_SCALE)

    if (m <= 3) {
      const t = m === 1 ? 0.5 : m === 2 ? (i === 0 ? 0.33 : 0.67) : 0.25 + (i * 0.5)
      const x = majorIsX ? margin + availW * t : cx + jx
      const y = majorIsX ? cy + jy : margin + availH * t
      ax[i] = safeClampToViewport(x, margin, w)
      ay[i] = safeClampToViewport(y, margin, h)
      continue
    }

    // More groups: spread through area with deterministic pseudo-random.
    const rx = (seed % RANDOM_BUCKET) / RANDOM_BUCKET
    const ry = (Math.floor(seed / RANDOM_BUCKET) % RANDOM_BUCKET) / RANDOM_BUCKET
    ax[i] = margin + rx * availW + jx
    ay[i] = margin + ry * availH + jy
  }

  const area = availW * availH
  const k = Math.sqrt(area / Math.max(1, m))
  const iters = isTestMode ? ITERS_TEST : ITERS_NORMAL
  const damping = DAMPING
  const centerStrength = CENTER_STRENGTH
  const repulseStrength = Math.max(0.001, k * k) * REPULSE_K
  const softening2 = SOFTENING2
  const minDist = Math.max(MIN_DIST_MIN_PX, k * MIN_DIST_K)

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
        const rawDist2 = dx * dx + dy * dy
        const dist2 = rawDist2 + softening2
        const invDist = 1 / Math.sqrt(dist2)

        // Softened unit vector to avoid NaN/Inf when two anchors are very close.
        let ux = dx * invDist
        let uy = dy * invDist

        // If two anchors are exactly coincident (dx=dy=0), direction is undefined and the
        // repulsion/collision forces become zero. Provide a deterministic direction so
        // coincident anchors can separate.
        if (rawDist2 === 0) {
          const hash = (((i + 1) * 73856093) ^ ((j + 1) * 19349663)) >>> 0
          const angle = ((hash % 1024) / 1024) * Math.PI * 2
          ux = Math.cos(angle)
          uy = Math.sin(angle)
        }

        const fRep = (repulseStrength / dist2) * REPULSE_MULT
        vx[i] += ux * fRep
        vy[i] += uy * fRep
        vx[j] -= ux * fRep
        vy[j] -= uy * fRep

        const rawDist = Math.sqrt(rawDist2)
        if (rawDist < minDist) {
          // Collision/spacing should be based on the real distance, not the softened one.
          const invRawDist = rawDist > 0 ? 1 / rawDist : 0
          const uxPush = rawDist > 0 ? dx * invRawDist : ux
          const uyPush = rawDist > 0 ? dy * invRawDist : uy
          const push = ((minDist - rawDist) / minDist) * PUSH_MULT
          vx[i] += uxPush * push
          vy[i] += uyPush * push
          vx[j] -= uxPush * push
          vy[j] -= uyPush * push
        }
      }
    }

    for (let i = 0; i < m; i++) {
      vx[i] *= damping
      vy[i] *= damping
      ax[i] += vx[i]
      ay[i] += vy[i]
      ax[i] = safeClampToViewport(ax[i], margin, w)
      ay[i] = safeClampToViewport(ay[i], margin, h)
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
    chargeTheta: chargeThetaRaw,
    groupKeyByNodeId,
    groupAnchors,
    groupStrength,
    centerStrength,
    linkDistanceScaleSameGroup,
    linkDistanceScaleCrossGroup,
  } = opts

  // Real mode can render an empty snapshot before a run is started.
  // Handle it explicitly to avoid "cannot read properties of undefined" when the algorithm expects at least 1 node.
  if (!snapshot.nodes.length) return { nodes: [], links: [] }

  // Deterministic Force-Directed Layout:
  // - Gravity (center force)
  // - Optional group attraction (anchor force)
  // - Repulsion (many-body / charge)
  // - Link distance (springs)
  // - Collision
  const MARGIN_PX = 22
  const SEED_BUCKET = 4096
  const NODE_RADIUS_MIN_PX = 8
  const NODE_RADIUS_K = 0.56
  const COLLIDE_PAD_PX = 6
  const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5))
  const SUNFLOWER_MAX_RAD_MIN_PX = 90
  const SUNFLOWER_MAX_RAD_VIEWPORT_K = 0.46
  const SUNFLOWER_MAX_RAD_K = 3.0
  const SUNFLOWER_JITTER_BUCKET = 1024
  const SUNFLOWER_JITTER_ANGLE_K = 0.18
  const SUNFLOWER_JITTER_R_BASE = 0.96
  const SUNFLOWER_JITTER_R_K = 0.10
  const GROUP_SEED_BASE_RAD_MIN_PX = 56
  const GROUP_SEED_BASE_RAD_VIEWPORT_K = 0.34
  const GROUP_SEED_BASE_RAD_K = 2.2
  const ITERS_TEST = 140
  const ITERS_NORMAL = 180
  const DAMPING = 0.88
  const MAX_SPEED_MIN = 0.8
  const MAX_SPEED_K = 0.35
  const CENTER_STRENGTH_DEFAULT = 0.052
  const CHARGE_K = 0.055
  const SPRING_STRENGTH = 0.022
  const LINK_DISTANCE_MIN_PX = 24
  const LINK_DISTANCE_K = 1.25
  const COLLISION_STRENGTH = 0.70
  const SOFTENING2 = 36
  const LINK_DIST_SAME_DEFAULT = 0.90
  const LINK_DIST_CROSS_DEFAULT = 1.12
  const REPULSE_FAR_K = 2.2
  const CHARGE_THETA_DEFAULT = 0.9

  const margin = MARGIN_PX

  const cx = w / 2
  const cy = h / 2

  const nodesSorted = [...snapshot.nodes].sort((a, b) => a.id.localeCompare(b.id))
  const n = nodesSorted.length

  const availW = Math.max(1, w - margin * 2)
  const availH = Math.max(1, h - margin * 2)
  const area = availW * availH
  const k = Math.sqrt(area / n)

  const idxById = new Map<string, number>()
  for (let i = 0; i < nodesSorted.length; i++) idxById.set(nodesSorted[i]!.id, i)

  const r = nodesSorted.map((node) => {
    const s = sizeForNode(node)
    return Math.max(NODE_RADIUS_MIN_PX, Math.max(s.w, s.h) * NODE_RADIUS_K)
  })
  const collidePad = COLLIDE_PAD_PX

  // Initial positions: around group anchors (if provided), else centered disc.
  const x = new Float64Array(n)
  const y = new Float64Array(n)
  const vx = new Float64Array(n)
  const vy = new Float64Array(n)

  const hasGroups = !!groupKeyByNodeId && !!groupAnchors && groupKeyByNodeId.size > 0 && groupAnchors.size > 0

  if (!hasGroups) {
    // Deterministic spacious seed: sunflower spiral over the whole viewport.
    // This prevents the "clump then spread" feel right after start.
    const goldenAngle = GOLDEN_ANGLE
    const a0 = ((fnv1a(`${snapshot.equivalent}:${seedKey}:a0`) % SEED_BUCKET) / SEED_BUCKET) * Math.PI * 2
    const maxRad = Math.max(
      SUNFLOWER_MAX_RAD_MIN_PX,
      Math.min(Math.min(availW, availH) * SUNFLOWER_MAX_RAD_VIEWPORT_K, k * SUNFLOWER_MAX_RAD_K),
    )

    for (let i = 0; i < n; i++) {
      const node = nodesSorted[i]!
      const seed = fnv1a(`${snapshot.equivalent}:${seedKey}:${node.id}`)
      const t = (i + 0.5) / n

      const jitterA = (((seed % SUNFLOWER_JITTER_BUCKET) / SUNFLOWER_JITTER_BUCKET) - 0.5) * SUNFLOWER_JITTER_ANGLE_K
      const jitterR =
        SUNFLOWER_JITTER_R_BASE +
        (((Math.floor(seed / SUNFLOWER_JITTER_BUCKET) % SUNFLOWER_JITTER_BUCKET) / SUNFLOWER_JITTER_BUCKET) - 0.5) *
          SUNFLOWER_JITTER_R_K

      const a = a0 + i * goldenAngle + jitterA
      const rad = Math.sqrt(t) * maxRad * jitterR

      x[i] = cx + Math.cos(a) * rad
      y[i] = cy + Math.sin(a) * rad
      vx[i] = 0
      vy[i] = 0
    }
  } else {
    // Grouped layouts: seed around anchors, but start further apart so clusters don't collapse.
    const baseRad = Math.max(
      GROUP_SEED_BASE_RAD_MIN_PX,
      Math.min(Math.min(availW, availH) * GROUP_SEED_BASE_RAD_VIEWPORT_K, k * GROUP_SEED_BASE_RAD_K),
    )
    for (let i = 0; i < n; i++) {
      const node = nodesSorted[i]!
      const seed = fnv1a(`${snapshot.equivalent}:${seedKey}:${node.id}`)
      const a = ((seed % SEED_BUCKET) / SEED_BUCKET) * Math.PI * 2
      const rr = Math.sqrt((Math.floor(seed / SEED_BUCKET) % SEED_BUCKET) / SEED_BUCKET)
      const rad = baseRad * rr

      const gKey = groupKeyByNodeId?.get(node.id)
      const anchor = gKey && groupAnchors?.get(gKey) ? groupAnchors.get(gKey)! : { x: cx, y: cy }

      x[i] = anchor.x + Math.cos(a) * rad
      y[i] = anchor.y + Math.sin(a) * rad
      vx[i] = 0
      vy[i] = 0
    }
  }

  const linksSorted = [...snapshot.links].sort((a, b) =>
    keyEdge(a.source, a.target).localeCompare(keyEdge(b.source, b.target)),
  )

  const iterations = isTestMode ? ITERS_TEST : ITERS_NORMAL
  const damping = DAMPING
  const maxSpeed = Math.max(MAX_SPEED_MIN, k * MAX_SPEED_K)

  const cStrength = centerStrength ?? CENTER_STRENGTH_DEFAULT
  const gStrength = groupStrength ?? 0
  // Increased spacing: stronger repulsion + longer link distances
  const chargeStrength = Math.max(0.001, k * k) * CHARGE_K
  const springStrength = SPRING_STRENGTH
  const linkDistanceBase = Math.max(LINK_DISTANCE_MIN_PX, k * LINK_DISTANCE_K)
  const collisionStrength = COLLISION_STRENGTH
  const softening2 = SOFTENING2

  // ITEM-6: Barnes–Hut theta (d3-quadtree). Clamp to a sane range to avoid pathological configs.
  const chargeTheta = clamp(Number.isFinite(chargeThetaRaw) ? (chargeThetaRaw as number) : CHARGE_THETA_DEFAULT, 0.2, 1.4)

  const sameK = linkDistanceScaleSameGroup ?? LINK_DIST_SAME_DEFAULT
  const crossK = linkDistanceScaleCrossGroup ?? LINK_DIST_CROSS_DEFAULT

  const indices = new Array<number>(n)
  for (let i = 0; i < n; i++) indices[i] = i

  type QuadExt = {
    mass?: number
    cx?: number
    cy?: number
    rMax?: number
  }

  function buildRepulsionQuadtree() {
    // Note: use indices rather than node objects to keep the quadtree lean & deterministic.
    const qt = quadtree<number>()
      .x((i: number) => x[i] ?? 0)
      .y((i: number) => y[i] ?? 0)
      .addAll(indices)

    // Precompute center-of-mass & mass per quad (Barnes–Hut), and max radius for collision pruning.
    qt.visitAfter((quad: unknown) => {
      const q = quad as unknown as QuadExt & { data?: number; next?: unknown } & Array<unknown>

      if (!q.length) {
        // Leaf: may hold a linked list of points with identical coordinates.
        let m = 0
        let cxAcc = 0
        let cyAcc = 0
        let rMax = 0
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let cur: any = quad
        while (cur) {
          const idx = cur.data as number
          m += 1
          cxAcc += x[idx] ?? 0
          cyAcc += y[idx] ?? 0
          rMax = Math.max(rMax, r[idx] ?? 0)
          cur = cur.next
        }

        q.mass = m
        q.cx = m ? cxAcc / m : 0
        q.cy = m ? cyAcc / m : 0
        q.rMax = rMax
        return
      }

      // Internal node: aggregate children.
      let m = 0
      let cxAcc = 0
      let cyAcc = 0
      let rMax = 0
      for (let kChild = 0; kChild < 4; kChild++) {
        const child = (quad as unknown as Array<unknown>)[kChild] as (QuadExt & { length?: number }) | undefined
        if (!child || !child.mass) continue
        m += child.mass
        cxAcc += (child.cx ?? 0) * child.mass
        cyAcc += (child.cy ?? 0) * child.mass
        rMax = Math.max(rMax, child.rMax ?? 0)
      }
      q.mass = m
      q.cx = m ? cxAcc / m : 0
      q.cy = m ? cyAcc / m : 0
      q.rMax = rMax
    })

    return qt
  }

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

    // ITEM-6: Many-body repulsion via Barnes–Hut (d3-quadtree) + leaf-level collision.
    // We accumulate forces per node (like d3-force): each pair influences both nodes, but through
    // two symmetric visits (i visits j, then j visits i). This avoids an explicit O(N²) double loop.
    const qt = buildRepulsionQuadtree()
    for (let i = 0; i < n; i++) {
      const xi = x[i] ?? 0
      const yi = y[i] ?? 0
      const ri = r[i] ?? 0

      qt.visit((quad: unknown, x0: number, y0: number, x1: number, y1: number) => {
        const q = quad as unknown as QuadExt & { data?: number; next?: unknown } & Array<unknown>
        const m = q.mass ?? 0
        if (!m) return true

        // Internal node: Barnes–Hut approximation.
        if (q.length) {
          const dx = xi - (q.cx ?? 0)
          const dy = yi - (q.cy ?? 0)
          const dist2 = dx * dx + dy * dy + softening2
          const dist = Math.sqrt(dist2)

          const s = x1 - x0
          if (s / dist < chargeTheta) {
            const far = dist / (k * REPULSE_FAR_K)
            const falloff = 1 / (1 + far * far)
            const fRep = ((chargeStrength * m) / dist2) * falloff
            const ux = dx / dist
            const uy = dy / dist
            vx[i] += ux * fRep
            vy[i] += uy * fRep
            return true
          }
          return false
        }

        // Leaf node: exact interactions with each point in the leaf's linked list.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        let cur: any = quad
        while (cur) {
          const j = cur.data as number
          if (j !== i) {
            const dx = xi - (x[j] ?? 0)
            const dy = yi - (y[j] ?? 0)
            const dist2 = dx * dx + dy * dy + softening2
            const dist = Math.sqrt(dist2)
            const ux = dx / dist
            const uy = dy / dist

            const far = dist / (k * REPULSE_FAR_K)
            const falloff = 1 / (1 + far * far)
            const fRep = (chargeStrength / dist2) * falloff
            vx[i] += ux * fRep
            vy[i] += uy * fRep

            const minDist = ri + (r[j] ?? 0) + collidePad
            if (dist < minDist) {
              const push = ((minDist - dist) / minDist) * collisionStrength
              vx[i] += ux * push
              vy[i] += uy * push
            }
          }
          cur = cur.next
        }

        return false
      })
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
    // Keep aspect ratio: avoid stretching the whole layout to match viewport ratio.
    // This is critical for stable Playwright snapshots.
    const scale = Math.max(0.70, Math.min(2.20, Math.min(targetHalfW / halfW, targetHalfH / halfH)))
    for (let i = 0; i < n; i++) {
      x[i] = cx + (x[i] - cx) * scale
      y[i] = cy + (y[i] - cy) * scale
    }
  }

  const nodes: LayoutNode[] = nodesSorted.map((base) => {
    const i = idxById.get(base.id) ?? 0
    return {
      ...base,
      __x: safeClampToViewport(x[i] ?? w / 2, margin, w),
      __y: safeClampToViewport(y[i] ?? h / 2, margin, h),
    }
  })

  // ITEM-17: filter dangling links before returning – links whose source/target is absent from
  // the current snapshot must not reach the renderer. The simulation loop already soft-skips
  // them (continue); here we ensure the output array is also clean.
  const links: LayoutLink[] = snapshot.links
    .filter((l) => {
      if (!idxById.has(l.source) || !idxById.has(l.target)) {
        danglingLinkFilteredCount++
        // Dev-only throttled warn: at most once every 5 s (mirrors ITEM-14 pattern).
        warnDanglingLink(
          import.meta.env.DEV && !isTestMode,
          `[forceLayout] dangling link filtered (total ${danglingLinkFilteredCount}): ${l.source}→${l.target}`,
        )
        return false
      }
      return true
    })
    .map((l) => ({
      ...l,
      __key: keyEdge(l.source, l.target),
    }))

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
  return applyForceLayout({ snapshot, w, h, seedKey: 'admin-force', isTestMode, centerStrength: 0.055 })
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
