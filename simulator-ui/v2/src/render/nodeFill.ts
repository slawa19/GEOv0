import type { GraphNode } from '../types'
import type { VizMapping } from '../vizMapping'

export function fillForNode(n: GraphNode, mapping: VizMapping): string {
  // Backend-first: UI only interprets viz keys; never derives colors from `type`.
  const key = String(n.viz_color_key ?? 'unknown')
  return mapping.node.color[key]?.fill ?? mapping.node.color.unknown.fill
}
