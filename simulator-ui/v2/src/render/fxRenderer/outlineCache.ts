import type { LayoutNode } from '../nodePainter'
import { getNodeShape } from '../../types/nodeShape'
import { LruCache } from '../../utils/lruCache'
import { getNodeScaledGeometry } from '../nodeGeometry'
import { roundedRectPath2D } from '../roundedRect'

const OUTLINE_CACHE_SCALE_QUANT = 100
const OUTLINE_CACHE_INVZOOM_QUANT = 1000

const MAX_NODE_OUTLINE_CACHE = 512
const nodeOutlinePath2DCache = new LruCache<string, Path2D>({ max: MAX_NODE_OUTLINE_CACHE })
let _nodeOutlineCacheSnapshotKey: string | null | undefined = undefined

export function resetFxRendererCaches(): void {
  nodeOutlinePath2DCache.clear()
  _nodeOutlineCacheSnapshotKey = undefined
}

export function invalidateNodeOutlineCacheForSnapshotKey(sk: string | null | undefined): void {
  if (sk !== undefined && sk !== _nodeOutlineCacheSnapshotKey) {
    nodeOutlinePath2DCache.clear()
    _nodeOutlineCacheSnapshotKey = sk ?? null
  }
}

export function nodeOutlinePath2D(n: LayoutNode, scale = 1, invZoom = 1): Path2D {
  // Cache across frames: key uses rounded positions (1px grid) to maximise hit rate
  // during physics micro-movements while still detecting real positional changes.
  const shapeKeyForCache = getNodeShape(n) ?? ''
  const cacheKey = `${n.id}|${Math.round(n.__x)}|${Math.round(n.__y)}|${shapeKeyForCache}|${Math.round(scale * OUTLINE_CACHE_SCALE_QUANT)}|${Math.round(invZoom * OUTLINE_CACHE_INVZOOM_QUANT)}`

  const cached = nodeOutlinePath2DCache.get(cacheKey)
  if (cached) return cached

  const g = getNodeScaledGeometry(n, scale, invZoom)

  let p: Path2D
  if (g.shape === 'rounded-rect') {
    p = roundedRectPath2D(g.x, g.y, g.w, g.h, g.rr)
  } else {
    p = new Path2D()
    p.arc(n.__x, n.__y, g.r, 0, Math.PI * 2)
  }

  nodeOutlinePath2DCache.set(cacheKey, p)
  return p
}

export const __testing = {
  _nodeOutlinePath2DCacheSize(): number {
    return nodeOutlinePath2DCache.size()
  },
  _nodeOutlineCacheSnapshotKey(): string | null | undefined {
    return _nodeOutlineCacheSnapshotKey
  },
  _warmNodeOutlinePath2DCache(n: LayoutNode, scale = 1, invZoom = 1): void {
    void nodeOutlinePath2D(n, scale, invZoom)
  },
}
