export type NodeShapeKey = 'circle' | 'rounded-rect'

export function normalizeNodeShapeKey(v: unknown): NodeShapeKey | null {
  // Hot-path friendly: strict comparisons only, no allocations.
  if (v === 'circle' || v === 'rounded-rect') return v
  return null
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null
}

export function getNodeShape(node: unknown): NodeShapeKey | null {
  if (!isRecord(node)) return null
  // Safe access without `any`.
  return normalizeNodeShapeKey(node['viz_shape_key'])
}

