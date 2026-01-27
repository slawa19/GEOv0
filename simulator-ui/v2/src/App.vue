<script setup lang="ts">
import DemoHudBottom from './components/DemoHudBottom.vue'
import DemoHudTop from './components/DemoHudTop.vue'
import EdgeTooltip from './components/EdgeTooltip.vue'
import LabelsOverlayLayers from './components/LabelsOverlayLayers.vue'
import NodeCardOverlay from './components/NodeCardOverlay.vue'
import { computed, reactive, ref } from 'vue'
import type {
  ClearingDoneEvent,
  ClearingPlanEvent,
  GraphSnapshot,
  TxUpdatedEvent,
} from './types'
import { loadEvents, loadSnapshot } from './fixtures'
import { VIZ_MAPPING } from './vizMapping'
import { fillForNode, sizeForNode } from './render/nodePainter'
import { assertPlaylistEdgesExistInSnapshot } from './demo/playlistValidation'
import { useAppDemoPlayerSetup } from './composables/useAppDemoPlayerSetup'
import { createAppComputeLayout } from './composables/useAppComputeLayout'
import { useAppFxOverlays } from './composables/useAppFxOverlays'
import { useAppPhysicsAndPinning } from './composables/useAppPhysicsAndPinning'
import { useAppUiDerivedState } from './composables/useAppUiDerivedState'
import { useAppLifecycle } from './composables/useAppLifecycle'
import { useAppDemoControls } from './composables/useAppDemoControls'
import { useAppRenderLoop } from './composables/useAppRenderLoop'
import { useAppSceneState } from './composables/useAppSceneState'
import { useCanvasInteractions } from './composables/useCanvasInteractions'
import { useAppViewAndNodeCard } from './composables/useAppViewAndNodeCard'
import { useGeoSimDevHookSetup } from './composables/useGeoSimDevHookSetup'
import { useLayoutCoordinator } from './composables/useLayoutCoordinator'
import { usePicking } from './composables/usePicking'
import { useEdgeHover } from './composables/useEdgeHover'
import { useEdgeTooltip } from './composables/useEdgeTooltip'
import { useAppDragToPinAndPreview } from './composables/useAppDragToPinAndPreview'
import { useLabelNodes } from './composables/useLabelNodes'
import { useLayoutIndex } from './composables/useLayoutIndex'
import { usePersistedSimulatorPrefs } from './composables/usePersistedSimulatorPrefs'
import { useSnapshotIndex } from './composables/useSnapshotIndex'
import { useSelectedNodeEdgeStats } from './composables/useSelectedNodeEdgeStats'
import { computeLayoutForMode, type LayoutMode } from './layout/forceLayout'
import { clamp } from './utils/math'
import type { SceneId } from './scenes'
import type { LayoutLink, LayoutNode } from './types/layout'
import type { LabelsLod, Quality } from './types/uiPrefs'

const eq = ref('UAH')
const scene = ref<SceneId>('A')

const layoutMode = ref<LayoutMode>('admin-force')

const isDemoFixtures = computed(() => String(import.meta.env.VITE_DEMO_FIXTURES ?? '1') === '1')
const isTestMode = computed(() => String(import.meta.env.VITE_TEST_MODE ?? '0') === '1')

// Playwright sets navigator.webdriver=true. Use it to keep screenshot tests stable even if
// someone runs the dev server with VITE_TEST_MODE=1.
const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true

const ALLOWED_EQS = new Set(['UAH', 'HOUR', 'EUR'])

const state = reactive({
  loading: true,
  error: '' as string,
  sourcePath: '' as string,
  eventsPath: '' as string,
  snapshot: null as GraphSnapshot | null,
  demoTxEvents: [] as TxUpdatedEvent[],
  demoClearingPlan: null as ClearingPlanEvent | null,
  demoClearingDone: null as ClearingDoneEvent | null,
  selectedNodeId: null as string | null,
  flash: 0 as number,
})

const canvasEl = ref<HTMLCanvasElement | null>(null)
const fxCanvasEl = ref<HTMLCanvasElement | null>(null)
const hostEl = ref<HTMLDivElement | null>(null)
const dragPreviewEl = ref<HTMLDivElement | null>(null)

const snapshotIndex = useSnapshotIndex({
  getSnapshot: () => state.snapshot,
})

const getNodeById = snapshotIndex.getNodeById

function fxColorForNode(nodeId: string, fallback: string): string {
  const n = getNodeById(nodeId)
  if (!n) return fallback
  return fillForNode(n, VIZ_MAPPING)
}

const labelsLod = ref<LabelsLod>('selection')
const quality = ref<Quality>('high')

const uiDerived = useAppUiDerivedState({
  eq,
  scene,
  quality,
  isTestMode,
  isWebDriver,
  getCameraZoom: () => camera.zoom,
})

const effectiveEq = uiDerived.effectiveEq
const dprClamp = uiDerived.dprClamp
const showDemoControls = uiDerived.showDemoControls
const showResetView = uiDerived.showResetView
const overlayLabelScale = uiDerived.overlayLabelScale

const baselineLayoutPos = new Map<string, { x: number; y: number }>()
const pinnedPos = reactive(new Map<string, { x: number; y: number }>())

const snapshotRef = computed(() => state.snapshot)

const layoutCoordinator = useLayoutCoordinator<LayoutNode, LayoutLink, LayoutMode, GraphSnapshot>({
  canvasEl,
  fxCanvasEl,
  hostEl,
  snapshot: snapshotRef,
  layoutMode,
  import SimulatorAppRoot from './components/SimulatorAppRoot.vue'
      :is-web-driver="isWebDriver"
      :nodes-count="state.snapshot?.nodes.length"
      :links-count="state.snapshot?.links.length"
    <SimulatorAppRoot />

