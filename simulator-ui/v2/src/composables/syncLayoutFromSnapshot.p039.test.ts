/**
 * 039 slice A, R12: an incremental snapshot load must bring EVERY field of an existing layout link up to date.
 *
 * The path is the app's own: `useSceneState.loadScene()` builds the layout through the real `computeLayoutForMode`
 * on the first load, decides the second load is incremental (same node ids, same equivalent) and hands the
 * snapshot to `syncLayoutFromSnapshot` - wired here exactly as `useSimulatorApp.ts` wires it.
 *
 * What this does NOT show: that any screen reads `reverse_used` from a layout link. At the time of writing the
 * edge-detail window reads it from the snapshot / trustline cache (`useSelectedTrustlineLine.ts`), so the stale copy
 * is a latent defect of the layout objects, not a wrong number on screen.
 */
import { computed, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import { computeLayoutForMode } from '../layout/forceLayout'
import type { GraphLink, GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { syncLayoutFromSnapshot } from './syncLayoutFromSnapshot'
import { useSceneState } from './useSceneState'

type LayoutMode = 'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'

/** Every field of the link type. `Required<>` makes `vue-tsc` fail here when a field is added to `GraphLink`. */
const FULL_LINK_BEFORE: Required<GraphLink> = {
  id: 'tl-1',
  source: 'A',
  target: 'B',
  trust_limit: '100',
  used: '10',
  reverse_used: '3',
  available: '90',
  status: 'active',
  close_requested_at: null,
  viz_color_key: 'c-before',
  viz_width_key: 'w-before',
  viz_alpha_key: 'a-before',
}

const FULL_LINK_AFTER: Required<GraphLink> = {
  id: 'tl-1-reissued',
  source: 'A',
  target: 'B',
  trust_limit: '200',
  used: '20',
  reverse_used: '7',
  available: '180',
  status: 'frozen',
  close_requested_at: '2026-10-12T00:00:00Z',
  viz_color_key: 'c-after',
  viz_width_key: 'w-after',
  viz_alpha_key: 'a-after',
}

function snapshotOf(links: GraphLink[], generatedAt: string): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: generatedAt,
    nodes: [
      { id: 'A', name: 'Alice' },
      { id: 'B', name: 'Bob' },
      { id: 'C', name: 'Carol' },
    ],
    links,
  }
}

async function loadTwice(first: GraphSnapshot, second: GraphSnapshot) {
  const eq = ref('UAH')
  const scene = ref<'A' | 'B' | 'C'>('A')
  const layoutMode = ref<LayoutMode>('admin-force')
  const state = reactive({
    loading: false,
    error: '',
    sourcePath: '',
    snapshot: null as GraphSnapshot | null,
    selectedNodeId: null as string | null,
  })
  const layout: { nodes: LayoutNode[]; links: LayoutLink[] } = { nodes: [], links: [] }

  const loadSnapshot = vi.fn()
  loadSnapshot.mockResolvedValueOnce({ snapshot: first, sourcePath: 'run:R1' })
  loadSnapshot.mockResolvedValueOnce({ snapshot: second, sourcePath: 'run:R1' })

  const resizeAndLayout = vi.fn(() => {
    const computed = computeLayoutForMode(state.snapshot as GraphSnapshot, 800, 600, layoutMode.value, true)
    layout.nodes = computed.nodes
    layout.links = computed.links
  })
  const onIncrementalSnapshotLoaded = vi.fn((snapshot: GraphSnapshot) => syncLayoutFromSnapshot(layout, snapshot))

  const s = useSceneState({
    eq,
    scene,
    layoutMode,
    allowEqDeepLink: () => true,
    isEqAllowed: () => true,
    effectiveEq: computed(() => eq.value),
    state,
    loadSnapshot,
    clearScheduledTimeouts: vi.fn(),
    resetCamera: vi.fn(),
    resetLayoutKeyCache: vi.fn(),
    resetOverlays: vi.fn(),
    resizeAndLayout,
    ensureRenderLoop: vi.fn(),
    onIncrementalSnapshotLoaded,
    setupResizeListener: vi.fn(),
    teardownResizeListener: vi.fn(),
    stopRenderLoop: vi.fn(),
  })

  await s.loadScene()
  const before = {
    positions: layout.nodes.map((n) => ({ id: n.id, x: n.__x, y: n.__y })),
    linkObjects: [...layout.links],
  }

  await s.loadScene()

  // Anti-vacuum for every case below: the second load really took the incremental branch, and only that one.
  expect(resizeAndLayout, 'the second load must not relayout').toHaveBeenCalledTimes(1)
  expect(onIncrementalSnapshotLoaded, 'the second load must be incremental').toHaveBeenCalledTimes(1)

  return { layout, before }
}

function linkAt(layout: { links: LayoutLink[] }, source: string, target: string): LayoutLink {
  const found = layout.links.find((l) => l.source === source && l.target === target)
  if (!found) throw new Error(`layout has no link ${source}->${target}`)
  return found
}

describe('R12: incremental snapshot load updates the layout copy of a link', () => {
  it("an EXISTING edge with reverse_used '3' is updated to '7'", async () => {
    const { layout } = await loadTwice(
      snapshotOf([{ source: 'A', target: 'B', used: '10', reverse_used: '3' }], '2026-10-12T00:00:00Z'),
      snapshotOf([{ source: 'A', target: 'B', used: '10', reverse_used: '7' }], '2026-10-12T00:00:01Z'),
    )

    expect(linkAt(layout, 'A', 'B').reverse_used).toBe('7')
  })

  it('every field of the link type is carried onto the existing layout link', async () => {
    const { layout, before } = await loadTwice(
      snapshotOf([FULL_LINK_BEFORE], '2026-10-12T00:00:00Z'),
      snapshotOf([FULL_LINK_AFTER], '2026-10-12T00:00:01Z'),
    )

    const link = linkAt(layout, 'A', 'B')
    expect(link).toBe(before.linkObjects[0])
    expect(link).toEqual({ ...FULL_LINK_AFTER, __key: before.linkObjects[0]!.__key })
    // Anti-vacuum: the two fixtures differ in every field that is not part of the edge identity.
    for (const key of Object.keys(FULL_LINK_BEFORE) as Array<keyof GraphLink>) {
      if (key === 'source' || key === 'target') continue
      expect(FULL_LINK_AFTER[key], `fixture field ${key} must differ`).not.toEqual(FULL_LINK_BEFORE[key])
    }
  })

  it('a field the link type does not know yet is carried too (no hand-picked list)', async () => {
    const withFuture = (value: string) => ({ source: 'A', target: 'B', future_field: value }) as GraphLink
    const { layout } = await loadTwice(
      snapshotOf([withFuture('old')], '2026-10-12T00:00:00Z'),
      snapshotOf([withFuture('new')], '2026-10-12T00:00:01Z'),
    )

    expect((linkAt(layout, 'A', 'B') as unknown as Record<string, unknown>).future_field).toBe('new')
  })

  it('a field the new snapshot no longer carries does not survive as a stale value', async () => {
    const { layout } = await loadTwice(
      snapshotOf([{ source: 'A', target: 'B', used: '10', reverse_used: '3' }], '2026-10-12T00:00:00Z'),
      snapshotOf([{ source: 'A', target: 'B', used: '10' }], '2026-10-12T00:00:01Z'),
    )

    const link = linkAt(layout, 'A', 'B')
    expect(link.reverse_used).toBeUndefined()
    // Counter-check: clearing is not indiscriminate - what the snapshot does carry is still there.
    expect(link.used).toBe('10')
    expect(link.__key).toBeTruthy()
  })

  it('CONTRACT: an ADDED edge keeps its fields, a removed edge leaves the layout', async () => {
    const { layout } = await loadTwice(
      snapshotOf(
        [
          { source: 'A', target: 'B', reverse_used: '3' },
          { source: 'B', target: 'C', reverse_used: '1' },
        ],
        '2026-10-12T00:00:00Z',
      ),
      snapshotOf(
        [
          { source: 'A', target: 'B', reverse_used: '3' },
          { source: 'C', target: 'A', used: '4', reverse_used: '5', id: 'tl-new' },
        ],
        '2026-10-12T00:00:01Z',
      ),
    )

    expect(layout.links.map((l) => `${l.source}->${l.target}`)).toEqual(['A->B', 'C->A'])
    const added = linkAt(layout, 'C', 'A')
    expect(added).toMatchObject({ used: '4', reverse_used: '5', id: 'tl-new' })
    expect(added.__key).toBeTruthy()
  })

  it('CONTRACT: node positions and the identity of existing link objects are preserved', async () => {
    const { layout, before } = await loadTwice(
      snapshotOf([{ source: 'A', target: 'B', reverse_used: '3' }], '2026-10-12T00:00:00Z'),
      snapshotOf([{ source: 'A', target: 'B', reverse_used: '7' }], '2026-10-12T00:00:01Z'),
    )

    expect(before.positions).toHaveLength(3)
    for (const p of before.positions) {
      expect(Number.isFinite(p.x) && Number.isFinite(p.y), `node ${p.id} has a position`).toBe(true)
    }
    expect(layout.nodes.map((n) => ({ id: n.id, x: n.__x, y: n.__y }))).toEqual(before.positions)
    expect(linkAt(layout, 'A', 'B')).toBe(before.linkObjects[0])
  })
})
