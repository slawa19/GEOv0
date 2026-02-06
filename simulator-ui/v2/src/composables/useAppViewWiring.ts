import type { Ref } from 'vue'

import type { GraphNode } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'

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
  getNodeScreenSize: (n: GraphNode) => { w: number; h: number }

  getIncidentEdges: (nodeId: string) => LayoutLink[]
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
    getNodeScreenSize: opts.getNodeScreenSize,

    getIncidentEdges: opts.getIncidentEdges,
  })

  const cameraSystem = viewAndNodeCard.cameraSystem
  const viewControls = viewAndNodeCard.viewControls
  const nodeCard = viewAndNodeCard.nodeCard

  const camera = cameraSystem.camera
  const panState = cameraSystem.panState

  return {
    cameraSystem,
    camera,
    panState,

    resetCamera: cameraSystem.resetCamera,
    worldToScreen: cameraSystem.worldToScreen,
    screenToWorld: cameraSystem.screenToWorld,
    clientToScreen: cameraSystem.clientToScreen,

    viewControls,
    worldToCssTranslateNoScale: viewControls.worldToCssTranslateNoScale,
    resetView: viewControls.resetView,

    nodeCard,
    selectedNode: nodeCard.selectedNode,
    nodeCardStyle: nodeCard.nodeCardStyle,
  }
}
