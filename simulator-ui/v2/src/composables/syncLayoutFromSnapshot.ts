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
 * Moved out of `useSimulatorApp.ts` (039 slice A, R12) so that it can be exercised without mounting the whole
 * app. It does not wake the render loop: the caller does.
 *
 * Nodes are still synced field by field; that list is complete for today's `GraphNode` but is not guarded.
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
      // The snapshot is authoritative for every data field of a link, whatever the field is called: no list of
      // names here (039 R12 - the list that used to be here had drifted and left `id` and `reverse_used` stale).
      // Only what the layout itself owns (`__`-prefixed, see `types/layout.ts`) is kept; a data field the new
      // snapshot no longer carries is reset, as the per-field assignments did.
      const fields = existing as unknown as Record<string, unknown>
      for (const name of Object.keys(fields)) {
        if (!name.startsWith('__') && !(name in src)) fields[name] = undefined
      }
      Object.assign(existing, src)
      existing.__key = k
      nextLinks.push(existing)
    } else {
      nextLinks.push({ ...src, __key: k })
    }
  }

  // Assign even if length matches: link composition/order may change.
  layout.links = nextLinks
}
