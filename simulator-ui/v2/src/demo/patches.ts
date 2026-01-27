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

  function applyNodePatches(patches: NodePatch[] | undefined) {
    const snapshot = getSnapshot()
    if (!patches?.length || !snapshot) return

    const layoutNodes = getLayoutNodes()

    const snapIdx = indexById(snapshot.nodes)
    const layoutIdx = indexById(layoutNodes)

    for (const p of patches) {
      const si = snapIdx.get(p.id)
      if (si !== undefined) {
        const cur = snapshot.nodes[si]!
        snapshot.nodes[si] = {
          ...cur,
          net_balance_atoms: p.net_balance_atoms !== undefined ? p.net_balance_atoms : cur.net_balance_atoms,
          net_sign: p.net_sign !== undefined ? p.net_sign : cur.net_sign,
          viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
          viz_size: p.viz_size !== undefined ? p.viz_size : cur.viz_size,
        }
      }

      const li = layoutIdx.get(p.id)
      if (li !== undefined) {
        const cur = layoutNodes[li]!
        layoutNodes[li] = {
          ...cur,
          net_balance_atoms: p.net_balance_atoms !== undefined ? p.net_balance_atoms : cur.net_balance_atoms,
          net_sign: p.net_sign !== undefined ? p.net_sign : cur.net_sign,
          viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
          viz_size: p.viz_size !== undefined ? p.viz_size : cur.viz_size,
        }
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
        const cur = snapshot.links[si]!
        snapshot.links[si] = {
          ...cur,
          used: p.used !== undefined ? p.used : cur.used,
          available: p.available !== undefined ? p.available : cur.available,
          viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
          viz_width_key: p.viz_width_key !== undefined ? p.viz_width_key : cur.viz_width_key,
          viz_alpha_key: p.viz_alpha_key !== undefined ? p.viz_alpha_key : cur.viz_alpha_key,
        }
      }

      const li = layoutIdx.get(k)
      if (li !== undefined) {
        const cur = layoutLinks[li]!
        layoutLinks[li] = {
          ...cur,
          used: p.used !== undefined ? p.used : cur.used,
          available: p.available !== undefined ? p.available : cur.available,
          viz_color_key: p.viz_color_key !== undefined ? p.viz_color_key : cur.viz_color_key,
          viz_width_key: p.viz_width_key !== undefined ? p.viz_width_key : cur.viz_width_key,
          viz_alpha_key: p.viz_alpha_key !== undefined ? p.viz_alpha_key : cur.viz_alpha_key,
        }
      }
    }
  }

  return { applyNodePatches, applyEdgePatches }
}
