import { toRaw, type Ref } from 'vue'
import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import type { NodeLook } from '../vizMapping'
import { useDragPreview } from './useDragPreview'
import { useDragToPinInteraction } from './useDragToPinInteraction'

export function useAppDragToPinAndPreview(opts: {
  dragPreviewEl: Ref<HTMLDivElement | null>
  isTestMode: () => boolean

  pickNodeAt: (clientX: number, clientY: number) => { id: string } | null
  getLayoutNodeById: (id: string) => LayoutNode | null
  setSelectedNodeId: (id: string | null) => void
  clearHoveredEdge: () => void

  clientToScreen: (clientX: number, clientY: number) => { x: number; y: number }
  screenToWorld: (x: number, y: number) => { x: number; y: number }
  getCanvasEl: () => HTMLCanvasElement | null

  renderOnce: () => void

  pinNodeLive: (id: string, x: number, y: number) => void
  commitPinnedPos: (id: string, x: number, y: number) => void
  reheatPhysics: (alpha?: number) => void

  getNodeById: (id: string) => GraphNode | null
  getCamera: () => { panX: number; panY: number; zoom: number }
  sizeForNode: (n: GraphNode) => { w: number; h: number }
  fillForNode: (n: GraphNode) => string
  /** 039 slice B: the classifier's answer for the dragged node - its sign and freeze are shown on the preview too. */
  lookForNode: (n: GraphNode) => NodeLook
  /** The resolved colour of the hub ring (the current palette). */
  hubColor: () => string
}) {
  let showDragPreviewForNode: (nodeId: string) => void = () => {}
  let scheduleDragPreview: () => void = () => {}
  let hideDragPreview: () => void = () => {}

  const dragToPin = useDragToPinInteraction({
    isEnabled: () => !opts.isTestMode(),
    pickNodeAt: opts.pickNodeAt,
    getLayoutNodeById: opts.getLayoutNodeById,
    setSelectedNodeId: opts.setSelectedNodeId,
    clearHoveredEdge: opts.clearHoveredEdge,
    clientToScreen: opts.clientToScreen,
    screenToWorld: opts.screenToWorld,
    getCanvasEl: opts.getCanvasEl,
    renderOnce: opts.renderOnce,
    pinNodeLive: opts.pinNodeLive,
    commitPinnedPos: opts.commitPinnedPos,
    reheatPhysics: opts.reheatPhysics,
    showDragPreviewForNode: (id) => showDragPreviewForNode(id),
    scheduleDragPreview: () => scheduleDragPreview(),
    hideDragPreview: () => hideDragPreview(),
  })

  const dragPreview = useDragPreview({
    el: opts.dragPreviewEl,
    getDraggedNode: () => dragToPin.dragState.cachedNode,
    getNodeById: opts.getNodeById,
    getCamera: opts.getCamera,
    renderOnce: opts.renderOnce,
    sizeForNode: opts.sizeForNode,
    fillForNode: opts.fillForNode,
    lookForNode: opts.lookForNode,
    hubColor: opts.hubColor,
  })

  hideDragPreview = dragPreview.hideDragPreview
  showDragPreviewForNode = dragPreview.showDragPreviewForNode
  scheduleDragPreview = dragPreview.scheduleDragPreview

  /**
   * Keep a drag honest about the scene it is over. Called on every frame (`useSimulatorApp` `beforeDraw`: every
   * accepted patch, every theme change and every relayout wakes a frame), and it decides by STRUCTURE, not by a list
   * of events:
   *
   *  - the node is still in the shown snapshot AND the layout still holds the very object the drag moves
   *    -> FOLLOW: the preview is painted again from the node's current fields and the current palette. This is a
   *    patch (payment, clearing, topology), an incremental snapshot of the same scene (refresh, replay recovery,
   *    preview -> run with the same participants), a theme change;
   *  - anything else -> CANCEL: the node left the snapshot (removed by a topology event, or the scene was replaced),
   *    or the layout was rebuilt and the dragged object is no longer the one on screen (a full reload, a relayout
   *    after a structural change, a change of layout mode or of the viewport).
   *
   * A scene replaced as a whole is cancelled sooner, by the scene loader itself (`cancelDrag`, wired to its
   * `clearScene`): between that moment and the next frame the old preview must not stand over the new title.
   */
  function syncDragWithScene(): void {
    const ds = dragToPin.dragState
    if (!ds.active) return
    const id = ds.nodeId
    const held = ds.cachedNode
    const current = id ? opts.getLayoutNodeById(id) : null
    if (!id || !held || !current || toRaw(current) !== toRaw(held) || !opts.getNodeById(id)) {
      dragToPin.cancelDrag()
      return
    }
    if (ds.dragging && !dragPreview.refreshDragPreview()) dragToPin.cancelDrag()
  }

  return { dragToPin, hideDragPreview, syncDragWithScene, cancelDrag: dragToPin.cancelDrag }
}
