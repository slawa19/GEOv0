/**
 * 039 T3901 - an OPEN route of six legs over seven different nodes, with legs of unequal length. Not a test file.
 *
 * The stand's own six-leg case is the whole ring of six (`motion.qa.ts`): sender and receiver are one node, the chord
 * has no length, and every leg is a side of the same hexagon. That fixture can show neither "constant speed on legs of
 * unequal length" (P-3) nor a chord that is told from the route (P-2, R14). This one can: seven nodes on a ring, and a
 * route that mixes sides of the ring with chords across it - 0 > 1 > 3 > 6 > 5 > 2 > 4.
 *
 * The lengths are whatever the force layout gives; callers assert that they ARE unequal before judging anything.
 */
import type { Page } from '@playwright/test'

import { NODE_CLEAR_PX, namedOccluders, type RouteGeom } from '../oracle.js'
import { distToSegment, ringSnapshot, screenNodes, type QaSnapshot } from '../stand.js'

export const ROUTE7 = ['n0', 'n1', 'n3', 'n6', 'n5', 'n2', 'n4'] as const

export function route7Snapshot(): QaSnapshot {
  const s = ringSnapshot()
  const ids = Array.from({ length: 7 }, (_, i) => `n${i}`)
  const node = (id: string) => ({ ...s.nodes[0]!, id, name: id.toUpperCase() })
  const link = (source: string, target: string) => ({ ...s.links[0]!, source, target })
  const ring = ids.map((id, i) => [id, ids[(i + 1) % 7]!] as const)
  const chords = [['n1', 'n3'], ['n3', 'n6'], ['n5', 'n2'], ['n2', 'n4']] as const
  return { ...s, nodes: ids.map(node), links: [...ring, ...chords].map(([a, b]) => link(a, b)) }
}

export async function routeGeom(page: Page, ids: readonly string[]): Promise<RouteGeom & { lengths: number[]; chordClearPx: number }> {
  const n = await screenNodes(page)
  const nodes = ids.map((id) => {
    const p = n.get(id)
    if (!p) throw new Error(`039 route: node ${id} is not in the layout`)
    return p
  })
  const edges = nodes.slice(0, -1).map((a, k) => ({ name: `${ids[k]}>${ids[k + 1]}`, from: ids[k]!, to: ids[k + 1]!, a, b: nodes[k + 1]! }))
  const xs = nodes.map((p) => p.x)
  const ys = nodes.map((p) => p.y)
  const x = Math.max(0, Math.floor(Math.min(...xs)) - 30)
  const y = Math.max(0, Math.floor(Math.min(...ys)) - 30)
  const first = nodes[0]!
  const last = nodes[nodes.length - 1]!
  const mid = { x: (first.x + last.x) / 2, y: (first.y + last.y) / 2 }
  return {
    ids: [...ids], edges, nodes, occluders: await namedOccluders(page),
    clip: { x, y, width: Math.ceil(Math.max(...xs)) + 30 - x, height: Math.ceil(Math.max(...ys)) + 30 - y },
    lengths: edges.map((e) => Math.hypot(e.b.x - e.a.x, e.b.y - e.a.y)),
    // The oracle's own measure (`pickRingRoute`): how far the middle of the chord sender -> receiver is from every route edge.
    chordClearPx: Math.min(...edges.map((e) => distToSegment(mid, e.a, e.b))),
  }
}

/** The part of a leg the oracle judges: what is left of it outside `NODE_CLEAR_PX` of its two nodes. */
export const judgedLength = (len: number) => Math.max(0, len - 2 * NODE_CLEAR_PX)
