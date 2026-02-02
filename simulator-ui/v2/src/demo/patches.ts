import type { EdgePatch, GraphSnapshot, NodePatch } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'

type KeyEdge = (a: string, b: string) => string

const __indexCacheByArrayRef = new WeakMap<object, Map<string, number>>()

function indexById<T extends { id: string }>(items: readonly T[]) {
  const key = items as unknown as object
  const cached = __indexCacheByArrayRef.get(key)
  if (cached && cached.size === items.length) return cached

  const m = new Map<string, number>()
  for (let i = 0; i < items.length; i++) m.set(items[i]!.id, i)
  __indexCacheByArrayRef.set(key, m)
  return m
}

function indexByEdgeKey<T extends { source: string; target: string }>(items: readonly T[], keyEdge: KeyEdge) {
  const key = items as unknown as object
  const cached = __indexCacheByArrayRef.get(key)
  if (cached && cached.size === items.length) return cached

  const m = new Map<string, number>()
  for (let i = 0; i < items.length; i++) {
    const it = items[i]!
    m.set(keyEdge(it.source, it.target), i)
  }
  __indexCacheByArrayRef.set(key, m)
  return m
}

export function createPatchApplier(opts: {
  getSnapshot: () => GraphSnapshot | null
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  keyEdge: KeyEdge
}) {
  const { getSnapshot, getLayoutNodes, getLayoutLinks, keyEdge } = opts

  function applyNodePatchInPlace(target: any, p: NodePatch) {
    if (!target) return
    if (p.net_balance_atoms !== undefined) target.net_balance_atoms = p.net_balance_atoms
    if (p.net_sign !== undefined) target.net_sign = p.net_sign
    if (p.net_balance !== undefined) target.net_balance = p.net_balance
    if (p.viz_color_key !== undefined) target.viz_color_key = p.viz_color_key
    if (p.viz_size !== undefined) target.viz_size = p.viz_size
  }

  function applyEdgePatchInPlace(target: any, p: EdgePatch) {
    if (!target) return
    if (p.used !== undefined) target.used = p.used
    if (p.available !== undefined) target.available = p.available
    if (p.viz_color_key !== undefined) target.viz_color_key = p.viz_color_key
    if (p.viz_width_key !== undefined) target.viz_width_key = p.viz_width_key
    if (p.viz_alpha_key !== undefined) target.viz_alpha_key = p.viz_alpha_key
  }

  function applyNodePatches(patches: NodePatch[] | undefined) {
    const snapshot = getSnapshot()
    if (!patches?.length || !snapshot) return

    const layoutNodes = getLayoutNodes()

    const snapIdx = indexById(snapshot.nodes)
    const layoutIdx = indexById(layoutNodes)

    for (const p of patches) {
      const si = snapIdx.get(p.id)
      if (si !== undefined) {
        // IMPORTANT: mutate in-place to preserve node identity.
        // This keeps drag/physics references valid during scenario playback and reduces GC.
        applyNodePatchInPlace(snapshot.nodes[si] as any, p)
      }

      const li = layoutIdx.get(p.id)
      if (li !== undefined) {
        applyNodePatchInPlace(layoutNodes[li] as any, p)
      }
    }
  }

  function applyEdgePatches(patches: EdgePatch[] | undefined) {
    const snapshot = getSnapshot()
    if (!patches?.length || !snapshot) return

    const layoutLinks = getLayoutLinks()

    const snapIdx = indexByEdgeKey(snapshot.links, keyEdge)
    const layoutIdx = indexByEdgeKey(layoutLinks, keyEdge)

    for (const p of patches) {
      const k = keyEdge(p.source, p.target)

      const si = snapIdx.get(k)
      if (si !== undefined) {
        applyEdgePatchInPlace(snapshot.links[si] as any, p)
      }

      const li = layoutIdx.get(k)
      if (li !== undefined) {
        applyEdgePatchInPlace(layoutLinks[li] as any, p)
      }
    }
  }

  return { applyNodePatches, applyEdgePatches }
}
