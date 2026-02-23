import { computed } from 'vue'

import type { LayoutLinkLike, LayoutNodeWithId } from '../types/layout'

export type { LayoutLinkLike }

export type EdgeSeg = { key: string; fromId: string; toId: string; ax: number; ay: number; bx: number; by: number }

export function dist2PointToSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const abx = bx - ax
  const aby = by - ay
  const apx = px - ax
  const apy = py - ay
  const abLen2 = abx * abx + aby * aby
  if (abLen2 < 1e-9) {
    const dx = px - ax
    const dy = py - ay
    return dx * dx + dy * dy
  }
  const t = Math.max(0, Math.min(1, (apx * abx + apy * aby) / abLen2))
  const cx = ax + abx * t
  const cy = ay + aby * t
  const dx = px - cx
  const dy = py - cy
  return dx * dx + dy * dy
}

export function closestPointOnSegment(px: number, py: number, ax: number, ay: number, bx: number, by: number) {
  const abx = bx - ax
  const aby = by - ay
  const apx = px - ax
  const apy = py - ay
  const abLen2 = abx * abx + aby * aby
  if (abLen2 < 1e-9) return { x: ax, y: ay, t: 0 }
  const t = Math.max(0, Math.min(1, (apx * abx + apy * aby) / abLen2))
  return { x: ax + abx * t, y: ay + aby * t, t }
}

type UsePickingDeps<N extends LayoutNodeWithId, L extends LayoutLinkLike> = {
  getLayoutNodes: () => N[]
  getLayoutLinks: () => L[]
  getCameraZoom: () => number
  sizeForNode: (n: N) => { w: number; h: number }
  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
  screenToWorld: (screenX: number, screenY: number) => { x: number; y: number }
  isReady?: () => boolean
}

export function usePicking<N extends LayoutNodeWithId, L extends LayoutLinkLike>(deps: UsePickingDeps<N, L>) {
  const nodePickGrid = computed(() => {
    const cellSizeW = 180
    const cells = new Map<string, N[]>()
    for (const n of deps.getLayoutNodes()) {
      const cx = Math.floor(n.__x / cellSizeW)
      const cy = Math.floor(n.__y / cellSizeW)
      const k = `${cx},${cy}`
      const arr = cells.get(k)
      if (arr) arr.push(n)
      else cells.set(k, [n])
    }
    return cells
  })

  const layoutNodeMap = computed(() => {
    const m = new Map<string, N>()
    for (const n of deps.getLayoutNodes()) m.set(n.id, n)
    return m
  })

  const edgePickGrid = computed(() => {
    const cellSizeW = 220
    const cells = new Map<string, EdgeSeg[]>()
    const nodes = layoutNodeMap.value

    const push = (cx: number, cy: number, seg: EdgeSeg) => {
      const k = `${cx},${cy}`
      const arr = cells.get(k)
      if (arr) arr.push(seg)
      else cells.set(k, [seg])
    }

    for (const l of deps.getLayoutLinks()) {
      const a = nodes.get(l.source)
      const b = nodes.get(l.target)
      if (!a || !b) continue

      const ax = a.__x
      const ay = a.__y
      const bx = b.__x
      const by = b.__y
      const seg: EdgeSeg = { key: l.__key, fromId: l.source, toId: l.target, ax, ay, bx, by }

      const minX = Math.min(ax, bx)
      const maxX = Math.max(ax, bx)
      const minY = Math.min(ay, by)
      const maxY = Math.max(ay, by)

      const cx0 = Math.floor(minX / cellSizeW)
      const cx1 = Math.floor(maxX / cellSizeW)
      const cy0 = Math.floor(minY / cellSizeW)
      const cy1 = Math.floor(maxY / cellSizeW)

      for (let cy = cy0; cy <= cy1; cy++) {
        for (let cx = cx0; cx <= cx1; cx++) {
          push(cx, cy, seg)
        }
      }
    }

    return { cellSizeW, cells }
  })

  function pickNodeAt(clientX: number, clientY: number): N | null {
    if (deps.isReady && !deps.isReady()) return null

    const s = deps.clientToScreen(clientX, clientY)
    const p = deps.screenToWorld(s.x, s.y)

    const cellSizeW = 180
    const cx0 = Math.floor(p.x / cellSizeW)
    const cy0 = Math.floor(p.y / cellSizeW)

    let best: N | null = null
    let bestD2 = Infinity

    const key = (cx: number, cy: number) => `${cx},${cy}`
    const cells = nodePickGrid.value

    for (let dyc = -1; dyc <= 1; dyc++) {
      for (let dxc = -1; dxc <= 1; dxc++) {
        const bucket = cells.get(key(cx0 + dxc, cy0 + dyc))
        if (!bucket) continue
        for (const n of bucket) {
          const { w, h } = deps.sizeForNode(n)
          const r = (Math.max(w, h) * 0.8) / Math.max(0.01, deps.getCameraZoom())
          const dx = p.x - n.__x
          const dy = p.y - n.__y
          const d2 = dx * dx + dy * dy
          if (d2 <= r * r && d2 < bestD2) {
            best = n
            bestD2 = d2
          }
        }
      }
    }

    return best
  }

  function pickEdgeAt(clientX: number, clientY: number): EdgeSeg | null {
    if (deps.isReady && !deps.isReady()) return null

    const s = deps.clientToScreen(clientX, clientY)
    const p = deps.screenToWorld(s.x, s.y)

    const { cellSizeW, cells } = edgePickGrid.value
    const cx0 = Math.floor(p.x / cellSizeW)
    const cy0 = Math.floor(p.y / cellSizeW)

    const hitPx = 10
    const hitW = hitPx / Math.max(0.01, deps.getCameraZoom())
    const hit2 = hitW * hitW

    let best: EdgeSeg | null = null
    let bestD2 = Infinity

    for (let dy = -1; dy <= 1; dy++) {
      for (let dx = -1; dx <= 1; dx++) {
        const bucket = cells.get(`${cx0 + dx},${cy0 + dy}`)
        if (!bucket) continue
        for (const seg of bucket) {
          const d2 = dist2PointToSegment(p.x, p.y, seg.ax, seg.ay, seg.bx, seg.by)
          if (d2 <= hit2 && d2 < bestD2) {
            best = seg
            bestD2 = d2
          }
        }
      }
    }

    return best
  }

  return {
    nodePickGrid,
    edgePickGrid,
    pickNodeAt,
    pickEdgeAt,
  }
}
