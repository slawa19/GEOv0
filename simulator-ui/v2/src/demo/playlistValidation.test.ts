import { describe, expect, it } from 'vitest'
import type { DemoEvent, GraphSnapshot } from '../types'
import { assertPlaylistEdgesExistInSnapshot } from './playlistValidation'

function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-27T00:00:00Z',
    nodes: [{ id: 'A' }, { id: 'B' }, { id: 'C' }],
    links: [
      { source: 'A', target: 'B' },
      { source: 'B', target: 'C' },
    ],
  }
}

describe('demo/playlistValidation', () => {
  it('passes when all edges exist', () => {
    const snapshot = makeSnapshot()

    const events: DemoEvent[] = [
      {
        event_id: 'e1',
        ts: 't',
        type: 'tx.updated',
        equivalent: 'UAH',
        ttl_ms: 1000,
        edges: [
          { from: 'A', to: 'B' },
          { from: 'B', to: 'C' },
        ],
      },
      {
        event_id: 'e2',
        ts: 't',
        type: 'clearing.plan',
        equivalent: 'UAH',
        plan_id: 'p1',
        steps: [
          {
            at_ms: 0,
            highlight_edges: [{ from: 'A', to: 'B' }],
            particles_edges: [{ from: 'B', to: 'C' }],
          },
        ],
      },
    ]

    expect(() =>
      assertPlaylistEdgesExistInSnapshot({ snapshot, events, eventsPath: 'fixtures/demo.json' }),
    ).not.toThrow()
  })

  it('throws with a helpful message when an edge is unknown', () => {
    const snapshot = makeSnapshot()

    const events: DemoEvent[] = [
      {
        event_id: 'e1',
        ts: 't',
        type: 'tx.updated',
        equivalent: 'UAH',
        ttl_ms: 1000,
        edges: [{ from: 'C', to: 'A' }],
      },
    ]

    expect(() => assertPlaylistEdgesExistInSnapshot({ snapshot, events, eventsPath: 'fixtures/demo.json' })).toThrow(
      "Unknown edge 'C→A'",
    )
  })

  it('checks both highlight_edges and particles_edges for clearing.plan', () => {
    const snapshot = makeSnapshot()

    const events: DemoEvent[] = [
      {
        event_id: 'e2',
        ts: 't',
        type: 'clearing.plan',
        equivalent: 'UAH',
        plan_id: 'p1',
        steps: [
          {
            at_ms: 0,
            highlight_edges: [{ from: 'A', to: 'B' }],
            particles_edges: [{ from: 'C', to: 'A' }],
          },
        ],
      },
    ]

    expect(() => assertPlaylistEdgesExistInSnapshot({ snapshot, events, eventsPath: 'fixtures/demo.json' })).toThrow(
      "Unknown edge 'C→A'",
    )
  })
})
