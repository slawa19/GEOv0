import type { GraphNode } from '../types'
import { classifyNode, fillOf, type VizMapping } from '../vizMapping'

/**
 * The fill of a node: the colour of its net role, or of the freeze (039 slice B). `viz_color_key` is not read.
 * The palette is the mapping's resolved one - the object the legend and the node card show.
 */
export function fillForNode(n: Pick<GraphNode, 'net_sign' | 'status' | 'type'>, mapping: VizMapping): string {
  return fillOf(classifyNode(n), mapping.node.palette)
}
