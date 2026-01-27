import type { DemoEvent, GraphSnapshot } from '../types'
import { keyEdge } from '../utils/edgeKey'

export function assertPlaylistEdgesExistInSnapshot(opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) {
  const { snapshot, events, eventsPath } = opts
  const ok = new Set(snapshot.links.map((l) => keyEdge(l.source, l.target)))

  const assertEdge = (from: string, to: string, ctx: string) => {
    const k = keyEdge(from, to)
    if (!ok.has(k)) {
      throw new Error(`Unknown edge '${k}' referenced by ${ctx} (${eventsPath})`)
    }
  }

  for (let ei = 0; ei < events.length; ei++) {
    const evt = events[ei]!
    const baseCtx = `event[${ei}] ${evt.type} ${'event_id' in evt ? String((evt as any).event_id ?? '') : ''}`.trim()

    if (evt.type === 'tx.updated') {
      for (let i = 0; i < evt.edges.length; i++) {
        const e = evt.edges[i]!
        assertEdge(e.from, e.to, `${baseCtx} edges[${i}]`)
      }
      continue
    }

    if (evt.type === 'clearing.plan') {
      for (let si = 0; si < evt.steps.length; si++) {
        const step = evt.steps[si]!
        const he = step.highlight_edges ?? []
        const pe = step.particles_edges ?? []
        for (let i = 0; i < he.length; i++) {
          const e = he[i]!
          assertEdge(e.from, e.to, `${baseCtx} steps[${si}].highlight_edges[${i}]`)
        }
        for (let i = 0; i < pe.length; i++) {
          const e = pe[i]!
          assertEdge(e.from, e.to, `${baseCtx} steps[${si}].particles_edges[${i}]`)
        }
      }
    }
  }
}
