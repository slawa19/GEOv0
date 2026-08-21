import type { Ref } from 'vue'

import type { GraphNode } from '../types'
import type { LayoutLinkLike, LayoutNode } from '../types/layout'

import { useAppViewAndNodeCard } from './useAppViewAndNodeCard'

export function useAppViewWiring(opts: {
  canvasEl: Ref<HTMLCanvasElement | null>
  hostEl: Ref<HTMLDivElement | null>

  getLayoutNodes: () => LayoutNode[]
  getLayoutW: () => number
  getLayoutH: () => number
  isTestMode: () => boolean

  /**
   * Optional: fired after camera zoom/pan is applied (e.g. after a wheel RAF batch).
   */
  onCameraChanged?: () => void

  setClampCameraPan: (fn: () => void) => void

  selectedNodeId: Ref<string | null>
  setSelectedNodeId: (id: string | null) => void

  getNodeById: (id: string) => GraphNode | null
  getLayoutNodeById: (id: string) => LayoutNode | null

  /**
   * Optional: the links of the currently laid-out snapshot.
   *
   * `focusOnEdge` needs it to tell "this edge exists" from "this edge is gone": two
   * endpoints existing does not mean the edge between them does. Without this dependency
   * `focusOnEdge` reports failure rather than framing a segment that may no longer be an
   * edge — it never guesses.
   */
  getLayoutLinks?: () => LayoutLinkLike[]
}) {
  const viewAndNodeCard = useAppViewAndNodeCard({
    canvasEl: opts.canvasEl,
    hostEl: opts.hostEl,

    getLayoutNodes: opts.getLayoutNodes,
    getLayoutW: opts.getLayoutW,
    getLayoutH: opts.getLayoutH,
    isTestMode: opts.isTestMode,

    onCameraChanged: opts.onCameraChanged,

    setClampCameraPan: opts.setClampCameraPan,

    selectedNodeId: opts.selectedNodeId,
    setSelectedNodeId: opts.setSelectedNodeId,

    getNodeById: opts.getNodeById,
    getLayoutNodeById: opts.getLayoutNodeById,
  })

  const cameraSystem = viewAndNodeCard.cameraSystem
  const viewControls = viewAndNodeCard.viewControls
  const nodeCard = viewAndNodeCard.nodeCard

  const camera = cameraSystem.camera
  const panState = cameraSystem.panState

  function findLayoutLink(fromId: string, toId: string): LayoutLinkLike | null {
    const links = opts.getLayoutLinks?.()
    if (!links) return null
    for (const l of links) {
      if (l.source === fromId && l.target === toId) return l
    }
    return null
  }

  /**
   * Point the camera at the edge `fromId → toId` of the current snapshot.
   *
   * Direction matters: edge identity is directed everywhere in this app (`keyEdge`), so
   * `A → B` and `B → A` are different edges and only the requested one counts as present.
   *
   * Returns `true` when the camera moved. Returns `false`, leaving the camera untouched and
   * throwing nothing, when the edge is not in the current snapshot (removed, or the snapshot
   * was replaced between a panel poll and the click) or when its endpoints cannot be placed.
   */
  function focusOnEdge(fromId: string, toId: string): boolean {
    if (!findLayoutLink(fromId, toId)) return false
    return cameraSystem.focusOnEdge(opts.getLayoutNodeById(fromId), opts.getLayoutNodeById(toId))
  }

  return {
    cameraSystem,
    camera,
    panState,

    resetCamera: cameraSystem.resetCamera,
    focusOnEdge,
    worldToScreen: cameraSystem.worldToScreen,
    screenToWorld: cameraSystem.screenToWorld,
    clientToScreen: cameraSystem.clientToScreen,

    viewControls,
    worldToCssTranslateNoScale: viewControls.worldToCssTranslateNoScale,
    resetView: viewControls.resetView,

    nodeCard,
    selectedNode: nodeCard.selectedNode,
    selectedNodeScreenCenter: nodeCard.selectedNodeScreenCenter,
  }
}
