/**
 * 039 slice B, PR 1, fix-delta of the external review of 2026-10-12 (class 1): a drag in progress and the scene
 * under it. While a node is dragged, the canvas does not draw it and a DOM preview stands for it; that preview used
 * to be painted once, when the drag began.
 *
 * ONE stand, production parts: the real drag owner (`useAppDragToPinAndPreview`: the real pointer state machine and
 * the real preview), the real event pipeline and patch applier, the real scene loader (`useSceneState`), the real
 * incremental sync of the layout (`syncLayoutFromSnapshot`), the real classifier and fill. The scene is replaced and
 * patched WHILE the drag of this same stand is active - not in a second stand.
 *
 * What stands in for the app here, and is named so: `frame()` is what `useSimulatorApp` does at the start of every
 * frame (`beforeDraw` -> `syncDragWithScene`), and the scene loader's `resetOverlays` dependency ends the drag as
 * the app wires it (`cancelDrag`). That wiring itself runs in a browser in
 * `e2e/qa/p039-repro/drag-preview-stale.p039-repro.ts` (debug-only): a patch during a drag, a scenario change
 * during a drag.
 *
 * EVERY WAY A SCENE CHANGES UNDER A DRAG, and the decision - by structure, see `syncDragWithScene`:
 *   patch of the dragged node (payment / clearing / topology)       FOLLOW   the preview is painted again
 *   freeze of the dragged node (topology `frozen_nodes`)            FOLLOW
 *   theme change (the palette object is replaced)                   FOLLOW
 *   incremental snapshot: refresh, replay recovery, preview -> run  FOLLOW   same participants, same equivalent
 *   equivalent switch (also with the very same participants)        CANCEL   at once, by the scene loader
 *   scenario / run change, demo fixture with other participants     CANCEL   at once, by the scene loader
 *   failed load of another source (the scene is cleared)            CANCEL   at once, by the scene loader
 *   the dragged node removed by a topology event                    CANCEL   on the next frame
 *   a relayout that rebuilt the layout objects                      CANCEL   on the next frame
 * After a cancel: no preview, no pin written at release, and the rest of that pointer's gesture does nothing.
 *
 * NOT seen here: pixels; the real `requestAnimationFrame` cadence (a hidden tab draws no frame - the preview then
 * waits for the next one, as the canvas does); physics (the live pin of a cancelled drag is not undone here - a
 * cancel always comes with a rebuilt layout, which re-applies only COMMITTED pins, `usePhysicsManager.ts`).
 */
import { computed, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import { fillForNode } from '../render/nodeFill'
import type { GraphNode, GraphSnapshot, NodePatch } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import { keyEdge } from '../utils/edgeKey'
import { classifyNode, createVizMapping, type DataPalette, type VizMapping } from '../vizMapping'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'
import { syncLayoutFromSnapshot } from './syncLayoutFromSnapshot'
import { useAppDragToPinAndPreview } from './useAppDragToPinAndPreview'
import { useSceneState } from './useSceneState'

const TS = '2026-10-12T00:00:00Z'
const PALETTE: DataPalette = { debtor: '#a10000', creditor: '#0000b2', neutral: '#c3c3c3', frozen: '#444444', hub: '#00e500' }
const MINUS = '−'

const person = (id: string, over: Partial<GraphNode> = {}): GraphNode => ({ id, name: id, type: 'person', status: 'active', net_sign: 1, net_balance_atoms: '500', viz_shape_key: 'circle', viz_size: { w: 20, h: 20 }, ...over })
const snapshotOf = (equivalent: string, nodes: GraphNode[]): GraphSnapshot => ({ equivalent, generated_at: TS, nodes, links: nodes.length > 1 ? [{ source: nodes[0]!.id, target: nodes[1]!.id, trust_limit: '100', used: '5', available: '95', status: 'active' }] : [] })

function stand(first: GraphSnapshot = snapshotOf('UAH', [person('A'), person('B', { net_sign: -1 })])) {
  const state = reactive({ loading: false, error: '', sourcePath: '', snapshot: null as GraphSnapshot | null, selectedNodeId: null as string | null })
  const layout = { nodes: [] as LayoutNode[], links: [] as LayoutLink[] }
  /** As `forceLayout` does: NEW layout objects, copies of the snapshot's. */
  const relayout = () => {
    const snap = state.snapshot
    layout.nodes = (snap?.nodes ?? []).map((n, i) => ({ ...n, __x: 100 + i * 80, __y: 200 }))
    layout.links = (snap?.links ?? []).map((l) => ({ ...l, __key: keyEdge(l.source, l.target) }))
  }
  const mapping: VizMapping = { ...createVizMapping(), node: { palette: PALETTE } }
  const el = ref<HTMLDivElement | null>(document.createElement('div'))
  const pins = new Map<string, { x: number; y: number }>()
  const releasePointerCapture = vi.fn()

  const drag = useAppDragToPinAndPreview({
    dragPreviewEl: el,
    isTestMode: () => false,
    pickNodeAt: (x) => { const hit = layout.nodes.find((n) => Math.abs(n.__x - x) < 12); return hit ? { id: hit.id } : null },
    getLayoutNodeById: (id) => layout.nodes.find((n) => n.id === id) ?? null,
    setSelectedNodeId: (id) => { state.selectedNodeId = id },
    clearHoveredEdge: () => undefined,
    clientToScreen: (x, y) => ({ x, y }),
    screenToWorld: (x, y) => ({ x, y }),
    getCanvasEl: () => ({ setPointerCapture: () => undefined, releasePointerCapture } as unknown as HTMLCanvasElement),
    renderOnce: () => undefined,
    pinNodeLive: () => undefined,
    commitPinnedPos: (id, x, y) => pins.set(id, { x, y }),
    reheatPhysics: () => undefined,
    getNodeById: (id) => state.snapshot?.nodes.find((n) => n.id === id) ?? null,
    getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
    sizeForNode: (n) => n.viz_size ?? { w: 12, h: 12 },
    fillForNode: (n) => fillForNode(n, mapping),
    lookForNode: (n) => classifyNode(n),
    hubColor: () => mapping.node.palette.hub,
  })

  // The real scene loader. Its `resetOverlays` is where the app ends a drag with the scene (`useSimulatorApp.ts`).
  let next: { snapshot: GraphSnapshot; sourcePath: string } | Error = { snapshot: first, sourcePath: '/simulator/runs/R1/graph/snapshot' }
  let requested = ''
  const eq = ref('UAH')
  const scene = useSceneState({
    eq, scene: ref('A'), layoutMode: ref('admin-force'),
    allowEqDeepLink: () => true, isEqAllowed: () => true, effectiveEq: computed(() => eq.value),
    state,
    loadSnapshot: async () => { if (next instanceof Error) throw next; return next },
    requestedSceneSource: () => requested,
    loadRecoverySnapshot: async () => { if (next instanceof Error) throw next; return next },
    clearScheduledTimeouts: () => undefined, resetCamera: () => undefined, resetLayoutKeyCache: () => undefined,
    resetOverlays: () => drag.cancelDrag(),
    resizeAndLayout: relayout,
    ensureRenderLoop: () => undefined,
    onIncrementalSnapshotLoaded: (snapshot: GraphSnapshot) => syncLayoutFromSnapshot(layout, snapshot),
    setupResizeListener: () => undefined, teardownResizeListener: () => undefined, stopRenderLoop: () => undefined,
    onSceneContextChange: async () => undefined,
  } as unknown as Parameters<typeof useSceneState>[0])

  const load = async (snapshot: GraphSnapshot | Error, sourcePath = '/simulator/runs/R1/graph/snapshot') => {
    next = snapshot instanceof Error ? snapshot : { snapshot, sourcePath }
    requested = sourcePath
    await scene.loadScene()
  }

  // The real pipeline over the real applier.
  const draft = { real: { runId: 'R1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } }, state } as unknown as RealEventDraft
  const deps = {
    patchApplier: createPatchApplier({ getSnapshot: () => state.snapshot, getLayoutNodes: () => layout.nodes, getLayoutLinks: () => layout.links, keyEdge }),
    isUserFacingRunError: () => false, inc: () => undefined,
  }
  let seq = 0
  const send = (event: Record<string, unknown>) => applyAcceptedRealEvent({ event_id: `evt_R1_${++seq}`, ts: TS, equivalent: state.snapshot?.equivalent ?? 'UAH', ...event } as AcceptedSimulatorEvent, 'R1', draft, deps)
  const payment = (node_patch: NodePatch[]) => send({ type: 'tx.updated', from: 'A', to: 'B', amount: '1', ttl_ms: 10, edges: [{ from: 'A', to: 'B' }], node_patch })
  const topology = (payload: Record<string, unknown>) => send({ type: 'topology.changed', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], ...payload } })

  const pointer = (clientX: number, clientY = 200) => ({ pointerId: 7, clientX, clientY }) as unknown as PointerEvent
  /** Press on the node and move: the drag is active, the button is still down. */
  const beginDrag = (id: string) => {
    const n = layout.nodes.find((x) => x.id === id)!
    expect(drag.dragToPin.onPointerDown(pointer(n.__x)), 'the press hit the node').toBe(true)
    expect(drag.dragToPin.onPointerMove(pointer(n.__x + 30))).toBe(true)
    expect(drag.dragToPin.dragState.dragging, 'counter-check: the drag is active').toBe(true)
  }
  /** What the app does at the start of every frame (`useSimulatorApp.ts` `beforeDraw`). */
  const frame = () => drag.syncDragWithScene()
  const preview = () => ({ shown: el.value!.style.display !== 'none', sign: el.value!.textContent, background: el.value!.style.background, border: el.value!.style.borderStyle, outline: el.value!.style.outline })

  return { state, layout, drag, mapping, pins, releasePointerCapture, load, payment, topology, pointer, beginDrag, frame, preview, relayout }
}

async function loaded(first?: GraphSnapshot) {
  const s = stand(first)
  await s.load(first ?? snapshotOf('UAH', [person('A'), person('B', { net_sign: -1 })]))
  expect(s.layout.nodes.length, 'counter-check: the scene is loaded and laid out').toBeGreaterThan(0)
  return s
}

describe('039 B1 review, class 1 - a drag FOLLOWS its node while the scene is the same', () => {
  it('(a) a payment accepted during the drag flips net_sign: the preview shows the new role on the next frame; an explicit null shows "?"', async () => {
    const s = await loaded()
    s.beginDrag('A')
    expect(s.preview()).toMatchObject({ shown: true, sign: '+' })
    expect(s.preview().background).toContain(PALETTE.creditor)

    s.payment([{ id: 'A', net_sign: -1 }])
    s.frame()
    expect(s.preview(), 'a creditor that became a net debtor is not dragged as a creditor').toMatchObject({ shown: true, sign: MINUS })
    expect(s.preview().background).toContain(PALETTE.debtor)
    expect(s.preview().background).not.toContain(PALETTE.creditor)

    s.payment([{ id: 'A', net_sign: null }])
    s.frame()
    expect(s.preview()).toMatchObject({ shown: true, sign: '?' })
    expect(s.preview().background).toContain(PALETTE.neutral)

    // The drag was never interrupted: its drop pins the node.
    expect(s.drag.dragToPin.onPointerUp(s.pointer(160))).toBe(true)
    expect([...s.pins.keys()]).toEqual(['A'])
    expect(s.preview().shown).toBe(false)
  })

  it('a patch of ANOTHER node, and an incomplete patch of the dragged one, leave the preview as it is', async () => {
    const s = await loaded()
    s.beginDrag('A')
    s.payment([{ id: 'B', net_sign: 1 }, { id: 'A', net_balance_atoms: '900' }])
    s.frame()
    expect(s.preview()).toMatchObject({ shown: true, sign: '+' })
    expect(s.preview().background).toContain(PALETTE.creditor)
  })

  it('a freeze of the dragged node (topology, no patch): dashed outline and the freeze colour, the sign kept', async () => {
    const s = await loaded()
    s.beginDrag('A')
    expect(s.preview().border).toBe('solid')
    s.topology({ frozen_nodes: ['A'] })
    s.frame()
    expect(s.preview()).toMatchObject({ shown: true, sign: '+', border: 'dashed' })
    expect(s.preview().background).toContain(PALETTE.frozen)
  })

  it('a theme change replaces the palette object: the preview takes the new colours', async () => {
    const s = await loaded()
    s.beginDrag('A')
    s.mapping.node.palette = { ...PALETTE, creditor: '#123456' }
    s.frame()
    expect(s.preview().background).toContain('#123456')
    expect(s.preview().background).not.toContain(PALETTE.creditor)
  })

  it('an incremental snapshot of the same scene (refresh, replay recovery, preview -> run): the drag goes on and shows the new sign', async () => {
    const s = await loaded()
    const held = s.layout.nodes[0]
    s.beginDrag('A')
    await s.load(snapshotOf('UAH', [person('A', { net_sign: 0, net_balance_atoms: '0' }), person('B', { net_sign: -1 })]))
    expect(s.layout.nodes[0], 'counter-check: an increment keeps the layout objects').toBe(held)
    expect(s.drag.dragToPin.dragState.dragging, 'the drag is not cancelled by an increment').toBe(true)
    s.frame()
    expect(s.preview()).toMatchObject({ shown: true, sign: '0' })
    expect(s.drag.dragToPin.onPointerUp(s.pointer(160))).toBe(true)
    expect([...s.pins.keys()]).toEqual(['A'])
  })

  it('P3: a dragged hub keeps its ring, in the hub colour of the current palette; another participant has none', async () => {
    const s = await loaded(snapshotOf('UAH', [person('A', { type: 'hub', net_sign: 0 }), person('B', { links_count: 99, name: 'Hub' })]))
    s.beginDrag('A')
    expect(s.preview().outline).toContain(PALETTE.hub)
    expect(s.preview().outline).toContain('solid')
    s.mapping.node.palette = { ...PALETTE, hub: '#654321' }
    s.frame()
    expect(s.preview().outline).toContain('#654321')
    expect(s.drag.dragToPin.onPointerUp(s.pointer(160))).toBe(true)

    s.beginDrag('B')
    expect(s.preview().outline, 'a busy participant named Hub is not a hub: no ring').not.toContain('solid')
    expect(s.preview().outline).not.toContain(PALETTE.hub)
  })
})

describe('039 B1 review, class 1 - a drag is CANCELLED with the scene it was over', () => {
  /** After a cancel, with the button still down: nothing is shown, nothing moves, and the release pins nothing. */
  function expectCancelled(s: Awaited<ReturnType<typeof loaded>>, why: string) {
    expect(s.drag.dragToPin.dragState, why).toMatchObject({ active: false, dragging: false, nodeId: null, pointerId: null, cachedNode: null })
    expect(s.preview().shown, `${why}: no preview`).toBe(false)
    expect(s.releasePointerCapture, `${why}: the pointer is released`).toHaveBeenCalledWith(7)
    // The rest of the gesture is taken and does nothing - it must not fall through to panning or to a click.
    expect(s.drag.dragToPin.onPointerMove(s.pointer(400)), `${why}: a later move is swallowed`).toBe(true)
    s.frame()
    expect(s.preview().shown, `${why}: a later move does not show the preview again`).toBe(false)
    expect(s.drag.dragToPin.onPointerUp(s.pointer(400)), `${why}: the release is swallowed`).toBe(true)
    expect([...s.pins.keys()], `${why}: no pin is written for a node of the old scene`).toEqual([])
    // And the next gesture is an ordinary one.
    expect(s.drag.dragToPin.onPointerMove(s.pointer(400)), `${why}: nothing is swallowed after the release`).toBe(false)
  }

  it('(b) an equivalent switch that lands during the drag - the SAME participants in another equivalent - ends it at once, before any frame', async () => {
    const s = await loaded()
    s.beginDrag('A')
    expect(s.preview()).toMatchObject({ shown: true, sign: '+' })
    // EUR arrives: the same ids, other positions. The title of the legend is now EUR.
    await s.load(snapshotOf('EUR', [person('A', { net_sign: -1 }), person('B', { net_sign: 1 })]), '/simulator/runs/R1/graph/snapshot?eq=EUR')
    expect(s.state.snapshot!.equivalent).toBe('EUR')
    expectCancelled(s, 'equivalent switch')
  })

  it('a scenario / run change (other participants) ends it at once', async () => {
    const s = await loaded()
    s.beginDrag('A')
    await s.load(snapshotOf('UAH', [person('X'), person('Y')]), '/simulator/runs/R2/graph/snapshot')
    expectCancelled(s, 'scenario change')
  })

  it('a failed load of another source clears the scene and ends it at once', async () => {
    const s = await loaded()
    s.beginDrag('A')
    await s.load(new Error('HTTP 503'), '/simulator/scenarios/other/graph/preview')
    expect(s.state.snapshot, 'counter-check: the scene model was cleared').toBeNull()
    expectCancelled(s, 'failed load of another source')
  })

  it('the dragged node removed by a topology event: ended on the next frame', async () => {
    const s = await loaded()
    s.beginDrag('A')
    s.topology({ removed_nodes: ['A'] })
    expect(s.state.snapshot!.nodes.map((n) => n.id), 'counter-check: the pipeline removed the node').toEqual(['B'])
    s.frame()
    expectCancelled(s, 'dragged node removed')
  })

  it('a relayout that rebuilt the layout objects (a structural change elsewhere, a layout mode, a resize): ended on the next frame', async () => {
    const s = await loaded()
    s.beginDrag('A')
    s.relayout()
    s.frame()
    expectCancelled(s, 'relayout')
  })

  it('a failed load of the SAME source keeps the scene - and the drag', async () => {
    const s = await loaded()
    s.beginDrag('A')
    await s.load(new Error('HTTP 503'))
    expect(s.state.snapshot, 'counter-check: the scene is kept under the error').not.toBeNull()
    s.frame()
    expect(s.drag.dragToPin.dragState.dragging).toBe(true)
    expect(s.preview()).toMatchObject({ shown: true, sign: '+' })
  })

  it('cancelDrag with no drag in progress does nothing', async () => {
    const s = await loaded()
    s.drag.cancelDrag()
    s.frame()
    expect(s.releasePointerCapture).not.toHaveBeenCalled()
    expect(s.drag.dragToPin.onPointerMove(s.pointer(400)), 'nothing is swallowed').toBe(false)
  })
})
