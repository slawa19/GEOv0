import { computed, type Ref } from 'vue'
import type { GraphNode } from '../types'
import type { LayoutNode, LayoutLink } from '../types/layout'
import { useCamera } from './useCamera'
import { useNodeCard } from './useNodeCard'
import { useViewControls } from './useViewControls'

export function useAppViewAndNodeCard(opts: {
  canvasEl: Ref<HTMLCanvasElement | null>
  hostEl: Ref<HTMLDivElement | null>

  getLayoutNodes: () => LayoutNode[]
  getLayoutW: () => number
  getLayoutH: () => number
  isTestMode: () => boolean

  setClampCameraPan: (fn: () => void) => void

  selectedNodeId: Ref<string | null>
  setSelectedNodeId: (id: string | null) => void

  getNodeById: (id: string) => GraphNode | null
  getLayoutNodeById: (id: string) => LayoutNode | null
  getNodeScreenSize: (n: GraphNode) => { w: number; h: number }

  getIncidentEdges: (nodeId: string) => LayoutLink[]
}) {
  const cameraSystem = useCamera({
    canvasEl: opts.canvasEl,
    hostEl: opts.hostEl,
    getLayoutNodes: opts.getLayoutNodes,
    getLayoutW: opts.getLayoutW,
    getLayoutH: opts.getLayoutH,
    isTestMode: opts.isTestMode,
  })

  opts.setClampCameraPan(cameraSystem.clampCameraPan)

  const viewControls = useViewControls({
    worldToScreen: cameraSystem.worldToScreen,
    resetCamera: cameraSystem.resetCamera,
    clampCameraPan: cameraSystem.clampCameraPan,
  })

  const nodeCard = useNodeCard({
    hostEl: opts.hostEl,
    selectedNodeId: computed({
      get: () => opts.selectedNodeId.value,
      set: (v) => opts.setSelectedNodeId(v),
    }),
    getNodeById: (id) => (id ? opts.getNodeById(id) : null),
    getLayoutNodeById: opts.getLayoutNodeById,
    getNodeScreenSize: opts.getNodeScreenSize,
    worldToScreen: cameraSystem.worldToScreen,
    getIncidentEdges: opts.getIncidentEdges,
    getLayoutNodes: opts.getLayoutNodes,
  })

  return {
    cameraSystem,
    viewControls,
    nodeCard,
  }
}
