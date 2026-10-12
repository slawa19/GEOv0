import type { GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { keyEdge } from '../utils/edgeKey'

/**
 * Reconciles an incrementally loaded snapshot into the layout objects the renderer already holds.
 *
 * Layout nodes/links are copies (see forceLayout.ts spreads base objects). When the scene loader decides an
 * update is incremental (same node IDs), it may skip relayout; the copies then have to be brought up to date
 * here, keeping node positions and the identity of the existing objects.
 *
 * Moved out of `useSimulatorApp.ts` unchanged (039 slice A, R12) so that it can be exercised without mounting
 * the whole app. It does not wake the render loop: the caller does.
 */
export function syncLayoutFromSnapshot(layout: { nodes: LayoutNode[]; links: LayoutLink[] }, snapshot: GraphSnapshot): void {
  const snapNodeById = new Map(snapshot.nodes.map((n) => [n.id, n] as const))
  for (const n of layout.nodes) {
    const src = snapNodeById.get(String(n.id))
    if (!src) continue
    n.name = src.name
    n.type = src.type
    n.status = src.status
    n.links_count = src.links_count
    n.net_balance_atoms = src.net_balance_atoms
    n.net_sign = src.net_sign
    n.net_balance = src.net_balance
    n.viz_color_key = src.viz_color_key
    n.viz_shape_key = src.viz_shape_key
    n.viz_size = src.viz_size
    n.viz_badge_key = src.viz_badge_key
  }

  // Links are more volatile than nodes (preview → run, scenario switch, live run updates).
  // We want to keep node positions/camera stable, but we MUST reflect link add/remove changes.
  const oldLinkByKey = new Map<string, LayoutLink>()
  for (const l of layout.links) {
    const k = String(l.__key ?? keyEdge(String(l.source), String(l.target)))
    oldLinkByKey.set(k, l)
  }

  const nextLinks: LayoutLink[] = []
  for (const src of snapshot.links) {
    const k = keyEdge(String(src.source), String(src.target))
    const existing = oldLinkByKey.get(k)
    if (existing) {
      existing.source = src.source
      existing.target = src.target
      existing.trust_limit = src.trust_limit
      existing.used = src.used
      existing.available = src.available
      existing.status = src.status
      existing.close_requested_at = src.close_requested_at
      existing.viz_color_key = src.viz_color_key
      existing.viz_width_key = src.viz_width_key
      existing.viz_alpha_key = src.viz_alpha_key
      existing.__key = k
      nextLinks.push(existing)
    } else {
      nextLinks.push({ ...src, __key: k })
    }
  }

  // Assign even if length matches: link composition/order may change.
  layout.links = nextLinks
}
