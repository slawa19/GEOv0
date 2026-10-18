/**
 * 038 S3 - the NAMED load of the performance stand. Not a test file.
 *
 * `S3-MIXED-V1`, a pure function of the snapshot and the slot number (no randomness, no clock):
 *   - one SSE event every 150 ms; slots repeat with a period of 24 (3.6 s);
 *   - slot 12 of every period: `clearing.done` over 20 trust lines of the fixture (not a closed cycle: the FX does not
 *     read the topology), cleared amount 5.00. Since 039 slice C (2026-10-18) the product draws a spark round each cycle
 *     it can rebuild from these lines (an arbitrary sample is mostly none), then 20 edge pulses and one amount label;
 *     until then 20 pulses, up to 40 node bursts and a full-screen flash at once. So this load does NOT measure the
 *     cost of a multi-cycle clearing traversal (Codex review of 039 C);
 *   - the other 23 slots: `tx.updated` with `amount_flyout: true`, `ttl_ms: 1200`, over a CONNECTED simple route along
 *     real trust lines, of 1, 3 and 6 edges in turn (6 is the cap of the beam, `REAL_TX_MAX_ROUTE_EDGES`) - a sender
 *     label at once, a receiver label when the beam arrives (039 slice C: its route plan, `ttl_ms` times nothing), and
 *     the beam when the product's limiter admits it.
 *
 * What the product makes of it is NOT part of the definition and is recorded by the measurement: the limiter of
 * `useRealTxFx.ts` (3 beams per second, burst 2, 120 ms gap) admits fewer than half of 6.7 payments per second, and a
 * beam is one live spark at a time (038 S1). "N sparks at once" is not reachable by events and is not faked by
 * writing into FX state.
 *
 * It does not model: a real backend's patches of balances (`node_patch`/`edge_patch`), topology changes, snapshot
 * refreshes, user interaction (drag, pan, zoom, hover), open windows.
 */
import { createHash } from 'node:crypto'

import { EQ, RUN_ID, type QaSnapshot } from './stand.js'

export const LOAD_NAME = 'S3-MIXED-V1'
export const LOAD_EVERY_MS = 150
const PERIOD_SLOTS = 24
const CLEARING_SLOT = 12
const CLEARING_EDGES = 20
export const ROUTE_LENGTHS = [1, 3, 6] as const

type Edge = { from: string; to: string }

/** A simple path of `k` edges along trust lines (either direction of a line), found by a deterministic DFS from `start`. */
function routeFrom(adj: Map<string, string[]>, start: string, k: number): string[] | null {
  const path = [start]
  const seen = new Set(path)
  const walk = (): boolean => {
    if (path.length === k + 1) return true
    for (const next of adj.get(path[path.length - 1]!) ?? []) {
      if (seen.has(next)) continue
      path.push(next)
      seen.add(next)
      if (walk()) return true
      path.pop()
      seen.delete(next)
    }
    return false
  }
  return walk() ? path : null
}

export type PerfLoad = {
  name: string
  everyMs: number
  /** `frames(firstSlot, count, idTag)`: SSE frames of the slots `firstSlot .. firstSlot + count - 1`. */
  frames: (firstSlot: number, count: number, idTag: string) => string[]
  /** What the definition contains for `count` slots from 0: counts by kind and by route length. */
  composition: (count: number) => { events: number; clearings: number; payments: number; byRouteEdges: Record<string, number> }
  /** sha256 of the first 240 slots (ten periods) with a fixed id tag: the same load on any machine. */
  fingerprint: string
}

export function buildPerfLoad(snapshot: QaSnapshot): PerfLoad {
  const adj = new Map<string, string[]>()
  const add = (a: string, b: string) => {
    const l = adj.get(a) ?? []
    if (!l.includes(b)) l.push(b)
    adj.set(a, l)
  }
  for (const l of snapshot.links) {
    add(l.source, l.target)
    add(l.target, l.source)
  }
  const ids = snapshot.nodes.map((n) => n.id)

  const routeOf = (slot: number): { edges: Edge[]; k: number } => {
    const k = ROUTE_LENGTHS[slot % ROUTE_LENGTHS.length]!
    // Start nodes walk the participant list with a stride co-prime to its length; a start with no route of `k` edges
    // (a leaf, an isolated node) is skipped to the next one - deterministically.
    for (let probe = 0; probe < ids.length; probe += 1) {
      const path = routeFrom(adj, ids[(slot * 7 + probe) % ids.length]!, k)
      if (path) return { k, edges: path.slice(0, -1).map((from, i) => ({ from, to: path[i + 1]! })) }
    }
    throw new Error(`perf load ${LOAD_NAME}: the snapshot has no simple route of ${k} edges`)
  }

  const eventOf = (slot: number, idTag: string) => {
    const id = `evt_${RUN_ID}_${idTag}_${String(slot).padStart(6, '0')}`
    const base = { event_id: id, ts: '2026-02-01T00:00:01.000Z', equivalent: EQ }
    if (slot % PERIOD_SLOTS === CLEARING_SLOT) {
      const links = snapshot.links
      const cycle_edges = Array.from({ length: CLEARING_EDGES }, (_, k) => {
        const l = links[(slot * 7 + k * 13) % links.length]!
        return { from: l.source, to: l.target }
      })
      return { id, evt: { ...base, type: 'clearing.done', plan_id: `plan-${idTag}-${slot}`, cleared_cycles: 1, cleared_amount: '5.00', cycle_edges } }
    }
    const { edges } = routeOf(slot)
    return {
      id,
      evt: { ...base, type: 'tx.updated', from: edges[0]!.from, to: edges[edges.length - 1]!.to, amount: '10.00', amount_flyout: true, ttl_ms: 1200, edges },
    }
  }

  const frames = (firstSlot: number, count: number, idTag: string) =>
    Array.from({ length: count }, (_, i) => {
      const { id, evt } = eventOf(firstSlot + i, idTag)
      return `id: ${id}\nevent: ${evt.type}\ndata: ${JSON.stringify(evt)}\n\n`
    })

  const composition = (count: number) => {
    const byRouteEdges: Record<string, number> = {}
    let clearings = 0
    for (let s = 0; s < count; s += 1) {
      if (s % PERIOD_SLOTS === CLEARING_SLOT) clearings += 1
      else {
        const k = String(routeOf(s).k)
        byRouteEdges[k] = (byRouteEdges[k] ?? 0) + 1
      }
    }
    return { events: count, clearings, payments: count - clearings, byRouteEdges }
  }

  const fingerprint = createHash('sha256').update(frames(0, PERIOD_SLOTS * 10, 'fp').join('')).digest('hex').slice(0, 16)
  return { name: LOAD_NAME, everyMs: LOAD_EVERY_MS, frames, composition, fingerprint }
}
