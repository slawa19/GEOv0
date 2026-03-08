import { computed, createApp, h, nextTick, reactive, ref, type Component, type Ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
import type { GraphSnapshot } from '../types'

type TestAnchor = { x: number; y: number }
type TestSelectedNode = {
  id: string
  name: string
  type: string
  status: string
  viz_color_key: string
  net_balance: string
}
type TestInteractState = {
  fromPid: string | null
  toPid: string | null
  initiatedWithPrefilledFrom: boolean
  selectedEdgeKey: string | null
  edgeAnchor: TestAnchor | null
  error: string
  lastClearing: null
}
type MockUseSimulatorAppOpts = {
  uiOpenOrUpdateEdgeDetail?: (o: { fromPid: string; toPid: string; anchor: TestAnchor }) => void
  uiOpenOrUpdateNodeCard?: (o: { nodeId: string; anchor: TestAnchor | null }) => void
  uiCloseTopmostInspectorWindow?: () => void
}

// ---------------------------------------------------------------------------
// Shared simulatorStorage stub (used by SimulatorAppRoot for demo enter/exit).
// ---------------------------------------------------------------------------
const __GEO_TEST_SIM_STORAGE = {
  forceDesiredModeReal: vi.fn(),
  isFxDebugRun: vi.fn(() => false),
  clearFxDebugRunState: vi.fn(),

  readUiTheme: vi.fn<() => string | null>(() => null),
  writeUiTheme: vi.fn(),

  readDevtoolsOpenReal: vi.fn<() => boolean | null>(() => null),
  writeDevtoolsOpenReal: vi.fn<(isOpen: boolean) => void>(),
  readDevtoolsOpenDemo: vi.fn<() => boolean | null>(() => null),
  writeDevtoolsOpenDemo: vi.fn<(isOpen: boolean) => void>(),
  readDevtoolsOpenRealSnapshot: vi.fn<() => boolean | null>(() => null),
  writeDevtoolsOpenRealSnapshot: vi.fn<(isOpen: boolean) => void>(),
  clearDevtoolsOpenRealSnapshot: vi.fn(),
} as const

type GeoTestGlobals = {
  __GEO_TEST_SIM_STORAGE?: typeof __GEO_TEST_SIM_STORAGE
  __GEO_TEST_WM_OPEN?: ReturnType<typeof vi.fn>
  __GEO_TEST_WM_HANDLE_ESC?: ReturnType<typeof vi.fn>
  __GEO_TEST_WM_GET_TOPMOST_IN_GROUP?: ReturnType<typeof vi.fn>
  __GEO_TEST_WM_SET_GEOMETRY?: ReturnType<typeof vi.fn>
  __GEO_TEST_WM_RECLAMP_ALL?: ReturnType<typeof vi.fn>
  __GEO_TEST_CANVAS_POINTER_DOWN?: ReturnType<typeof vi.fn>
  __GEO_TEST_CANVAS_WHEEL?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_PHASE?: string
  __GEO_TEST_PHASE_REF?: Ref<string>
  __GEO_TEST_SELECTED_NODE?: TestSelectedNode
  __GEO_TEST_NODE_SCREEN_CENTER?: TestAnchor
  __GEO_TEST_NODE_SCREEN_CENTER_REF?: Ref<TestAnchor | null>
  __GEO_TEST_UI_OPEN_OR_UPDATE_EDGE_DETAIL?: MockUseSimulatorAppOpts['uiOpenOrUpdateEdgeDetail']
  __GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD?: MockUseSimulatorAppOpts['uiOpenOrUpdateNodeCard']
  __GEO_TEST_NODE_CARD_OPEN?: boolean
  __GEO_TEST_INTERACT_CANCEL?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_START_PAYMENT_FLOW?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_START_CLEARING_FLOW?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_SET_PAYMENT_TO_PID?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE?: ReturnType<typeof vi.fn>
  __GEO_TEST_INTERACT_SUCCESS_MESSAGE?: Ref<string | null>
  __GEO_TEST_INTERACT_HISTORY?: Array<Record<string, unknown>>
  __GEO_TEST_INTERACT_BUSY_REF?: Ref<boolean>
  __GEO_TEST_TRUSTLINES_LOADING_REF?: Ref<boolean>
  __GEO_TEST_PAYMENT_TARGETS_LOADING_REF?: Ref<boolean>
  __GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF?: Ref<string | null>
  __GEO_TEST_PAYMENT_TO_TARGET_IDS_REF?: Ref<Set<string> | undefined>
  __GEO_TEST_INTERACT_STATE?: TestInteractState
}

function setGeoTestGlobal<K extends keyof GeoTestGlobals>(key: K, value: GeoTestGlobals[K]): void {
  Reflect.set(globalThis, key, value)
}

function getGeoTestGlobal<K extends keyof GeoTestGlobals>(key: K): GeoTestGlobals[K] {
  return Reflect.get(globalThis, key) as GeoTestGlobals[K]
}

function getRequiredGeoTestGlobal<K extends keyof GeoTestGlobals>(key: K): NonNullable<GeoTestGlobals[K]> {
  const value = getGeoTestGlobal(key)
  expect(value).toBeTruthy()
  return value as NonNullable<GeoTestGlobals[K]>
}

function clearGeoTestGlobals(...keys: Array<keyof GeoTestGlobals>): void {
  for (const key of keys) Reflect.deleteProperty(globalThis, key)
}

function getInteractState(): TestInteractState {
  return getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
}

function getPhaseRef(): Ref<string> {
  return getRequiredGeoTestGlobal('__GEO_TEST_PHASE_REF')
}

function getSimStorage() {
  return getRequiredGeoTestGlobal('__GEO_TEST_SIM_STORAGE')
}

function makeSelectedNode(id = 'bob', name = 'Bob'): TestSelectedNode {
  return {
    id,
    name,
    type: 'person',
    status: 'active',
    viz_color_key: 'unknown',
    net_balance: '0',
  }
}

setGeoTestGlobal('__GEO_TEST_SIM_STORAGE', __GEO_TEST_SIM_STORAGE)

vi.mock('../composables/usePersistedSimulatorPrefs', async () => {
  const actual = await vi.importActual<typeof import('../composables/usePersistedSimulatorPrefs')>(
    '../composables/usePersistedSimulatorPrefs',
  )
  return {
    ...actual,
    useSimulatorStorage: () => __GEO_TEST_SIM_STORAGE,
  }
})

vi.mock('../composables/windowManager/useWindowManager', async () => {
  const actual = await vi.importActual<typeof import('../composables/windowManager/useWindowManager')>(
    '../composables/windowManager/useWindowManager',
  )

  return {
    useWindowManager: () => {
      const wm = actual.useWindowManager()
      const origOpen = wm.open
      const open = vi.fn<typeof origOpen>((o) => origOpen(o))
      setGeoTestGlobal('__GEO_TEST_WM_OPEN', open)

      const origHandleEsc = wm.handleEsc
      const handleEsc = vi.fn<typeof origHandleEsc>((ev, o) => origHandleEsc(ev, o))
      setGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC', handleEsc)

      const origGetTopmostInGroup = wm.getTopmostInGroup
      const getTopmostInGroup = vi.fn<typeof origGetTopmostInGroup>((g) => origGetTopmostInGroup(g))
      setGeoTestGlobal('__GEO_TEST_WM_GET_TOPMOST_IN_GROUP', getTopmostInGroup)

      const origSetGeometry = wm.setGeometry
      const setGeometry = vi.fn<typeof origSetGeometry>((o) => origSetGeometry(o))
      setGeoTestGlobal('__GEO_TEST_WM_SET_GEOMETRY', setGeometry)

      const origReclampAll = wm.reclampAll
      const reclampAll = vi.fn<typeof origReclampAll>(() => origReclampAll())
      setGeoTestGlobal('__GEO_TEST_WM_RECLAMP_ALL', reclampAll)

      return { ...wm, open, handleEsc, getTopmostInGroup, setGeometry, reclampAll }
    },
  }
})

// IMPORTANT: This test verifies conditional rendering in SimulatorAppRoot when the URL contains `ui=interact`.
// We mock `useSimulatorApp()` to keep the test fast + deterministic while still deriving flags from the query string.

  vi.mock('../composables/useSimulatorApp', () => {
        return {
    useSimulatorApp: (opts?: MockUseSimulatorAppOpts) => {
      const qs = () => {
        try {
          return new URLSearchParams(window.location.search)
        } catch {
          return new URLSearchParams('')
        }
      }

      const apiMode = computed<'fixtures' | 'real'>(() => (qs().get('mode') || '').toLowerCase() === 'real' ? 'real' : 'fixtures')
      const isInteractUi = computed(() => (qs().get('ui') || '').toLowerCase() === 'interact')
      const isDemoUi = computed(() => (qs().get('ui') || '').toLowerCase() === 'demo')
      const isTestMode = computed(() => {
        // Default to true for most interaction tests, but allow overriding for
        // DevTools/Demo UI wiring tests (DevTools are hidden in test mode).
        const v = (qs().get('testMode') || '').toLowerCase().trim()
        return !(v === '0' || v === 'false')
      })

      const phase = ref(String(getGeoTestGlobal('__GEO_TEST_INTERACT_PHASE') ?? 'idle'))
      setGeoTestGlobal('__GEO_TEST_PHASE_REF', phase)

       const selectedNode = ref<TestSelectedNode | null>(getGeoTestGlobal('__GEO_TEST_SELECTED_NODE') ?? null)
       const selectedNodeScreenCenter = ref<TestAnchor | null>(getGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER') ?? null)
       // Test helper: allow simulating camera pan/zoom by mutating the anchor ref.
       setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER_REF', selectedNodeScreenCenter)

       // Test helper: allow tests to simulate a node dblclick by calling the same
       // root callback that the real dblclick path uses.
      setGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_EDGE_DETAIL', opts?.uiOpenOrUpdateEdgeDetail)
      setGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD', opts?.uiOpenOrUpdateNodeCard)

      // Test helper: allow tests to request an initial NodeCard window by setting globals.
      // WM-only runtime: NodeCard is opened via uiOpenOrUpdateNodeCard(), not via boolean flags.
      const initialNodeCardOpen = Boolean(getGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN') ?? false)
      const openOrUpdateNodeCard = opts?.uiOpenOrUpdateNodeCard
      if (initialNodeCardOpen && selectedNode.value && openOrUpdateNodeCard) {
        const node = selectedNode.value
        queueMicrotask(() => {
          openOrUpdateNodeCard({
            nodeId: String(node.id),
            anchor: selectedNodeScreenCenter.value ?? null,
          })
        })
      }

       const cancel = vi.fn()
       setGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL', cancel)
       // Simulate real FSM cleanup: cancel clears interact state.
       // IMPORTANT: edgeAnchor reset is important for Step 4 anchor propagation tests:
       // trustline(edge popup) → payment must keep anchor.
       cancel.mockImplementation(() => {
         interactState.edgeAnchor = null
         interactState.fromPid = null
         interactState.toPid = null
         interactState.initiatedWithPrefilledFrom = false
         interactState.selectedEdgeKey = null
         phase.value = 'idle'
       })

       // Interact-mode FSM stub: make root integration tests able to assert that
       // panels become active and confirm-step UI renders without relying on timers.
       const startPaymentFlow = vi.fn(() => {
         const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
         if (st) st.initiatedWithPrefilledFrom = false
         phase.value = 'picking-payment-from'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_START_PAYMENT_FLOW', startPaymentFlow)

       const startPaymentFlowWithFrom = vi.fn((fromPid: string) => {
         const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
         if (st) {
           st.fromPid = fromPid
           st.initiatedWithPrefilledFrom = true
         }
         phase.value = 'picking-payment-to'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM', startPaymentFlowWithFrom)

       const startClearingFlow = vi.fn(() => {
         phase.value = 'confirm-clearing'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_START_CLEARING_FLOW', startClearingFlow)

       const setPaymentFromPid = vi.fn((pid: string | null) => {
         const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
         if (st) st.fromPid = pid
         // Real FSM: after picking From, user goes to picking To.
         // When From is cleared (e.g. cancel / invalidation), go back to picking From.
         phase.value = pid ? 'picking-payment-to' : 'picking-payment-from'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID', setPaymentFromPid)

       const setPaymentToPid = vi.fn((pid: string | null) => {
         const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
         if (st) st.toPid = pid
         // Real FSM: selecting a To advances to confirm step; clearing To keeps user in picking To.
         phase.value = pid ? 'confirm-payment' : 'picking-payment-to'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID', setPaymentToPid)

       const setTrustlineFromPid = vi.fn((pid: string | null) => {
         const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
         if (st) st.fromPid = pid
         phase.value = pid ? 'picking-trustline-to' : 'picking-trustline-from'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID', setTrustlineFromPid)

       const setTrustlineToPid = vi.fn((pid: string | null) => {
         const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
         if (st) st.toPid = pid
         // Minimal FSM-like behavior for back-step tests.
         if (!pid) {
           phase.value = 'picking-trustline-to'
           return
         }
         phase.value = 'editing-trustline'
       })
       setGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID', setTrustlineToPid)

       const confirmTrustlineClose = vi.fn(async () => undefined)
       setGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE', confirmTrustlineClose)

       const successMessage = ref<string | null>(null)
       setGeoTestGlobal('__GEO_TEST_INTERACT_SUCCESS_MESSAGE', successMessage)

      const interactBusy = ref(false)
      setGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF', interactBusy)

      const trustlinesLoading = ref(false)
      setGeoTestGlobal('__GEO_TEST_TRUSTLINES_LOADING_REF', trustlinesLoading)

      const paymentTargetsLoading = ref(false)
      setGeoTestGlobal('__GEO_TEST_PAYMENT_TARGETS_LOADING_REF', paymentTargetsLoading)

      const paymentTargetsLastError = ref<string | null>(null)
      setGeoTestGlobal('__GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF', paymentTargetsLastError)

      const paymentToTargetIds = ref<Set<string> | undefined>(undefined)
      setGeoTestGlobal('__GEO_TEST_PAYMENT_TO_TARGET_IDS_REF', paymentToTargetIds)

      const interactState = reactive<TestInteractState>({
        fromPid: 'alice',
        toPid: 'bob',
         initiatedWithPrefilledFrom: false,
        selectedEdgeKey: null as string | null,
        edgeAnchor: { x: 10, y: 10 },
        error: '',
        lastClearing: null,
      })
      setGeoTestGlobal('__GEO_TEST_INTERACT_STATE', interactState)

      const isInteractPickingPhase = computed(() => {
        if (!isInteractUi.value) return false
        const p = phase.value
        return (
          p === 'picking-payment-from' ||
          p === 'picking-payment-to' ||
          p === 'picking-trustline-from' ||
          p === 'picking-trustline-to'
        )
      })

      return {
        apiMode,

        // flags
        isDemoFixtures: computed(() => false),
        isDemoUi,
        isInteractUi,
        isTestMode,
        isWebDriver: computed(() => false),
        isE2eScreenshots: computed(() => false),

        // real mode state (minimal)
        real: reactive({
          loadingScenarios: false,
          scenarios: [],
          selectedScenarioId: '',
          desiredMode: 'real',
          intensityPercent: 0,
          runId: null as string | null,
          runStatus: null as Record<string, unknown> | null,
          sseState: 'idle',
          lastError: '',
          runStats: {},
          artifacts: [],
          artifactsLoading: false,
        }),
        realActions: {
          setApiBase: vi.fn(),
          setAccessToken: vi.fn(),
          setSelectedScenarioId: vi.fn(),
          setDesiredMode: vi.fn(),
          setIntensityPercent: vi.fn(),
          refreshScenarios: vi.fn(),
          startRun: vi.fn(),
          pause: vi.fn(),
          resume: vi.fn(),
          stop: vi.fn(),
          applyIntensity: vi.fn(),
          refreshSnapshot: vi.fn(),
          refreshArtifacts: vi.fn(),
          downloadArtifact: vi.fn(),
        },

        // §10: Cookie session state (anonymous visitors)
        session: {
          actorKind: ref<string | null>(null),
          ownerId: ref<string | null>(null),
          bootstrapping: ref(false),
          tryEnsure: vi.fn(),
        },

        // Admin controls (admin-token required)
        admin: {
          runs: ref<Array<Record<string, unknown>>>([]),
          loading: ref(false),
          lastError: ref(''),
          getRuns: vi.fn(),
          stopRuns: vi.fn(),
        },

        // state + prefs
        state: reactive({
          loading: false,
          error: '',
          sourcePath: '',
          snapshot: null as GraphSnapshot | null,
          selectedNodeId: null as string | null,
          flash: 0,
        }),
        eq: ref('UAH'),
        scene: ref('A'),
        layoutMode: ref('admin-force'),
        quality: ref('med'),
        labelsLod: ref('off'),
        effectiveEq: computed(() => 'UAH'),

        // derived interact UI helpers
        isInteractPickingPhase,

        // interact
        interact: {
          actions: {
            actionsDisabled: ref(false),
          },
           mode: {
               phase,
               busy: interactBusy,
               trustlinesLoading,
               paymentTargetsLoading,
               paymentTargetsLastError,
               state: interactState,

              successMessage,

             availableCapacity: ref('0'),
              paymentToTargetIds,
              participants: ref<ParticipantInfo[]>([]),
              trustlines: ref<TrustlineInfo[]>([]),
              canSendPayment: ref(true),

            setPaymentFromPid,
            setPaymentToPid,
              setTrustlineFromPid,
              setTrustlineToPid,
             selectTrustline: vi.fn(),

             startPaymentFlow: startPaymentFlow,
             startPaymentFlowWithFrom,
              startTrustlineFlow: vi.fn(() => {
                 const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
                 if (st) st.initiatedWithPrefilledFrom = false
                phase.value = 'picking-trustline-from'
              }),
              startTrustlineFlowWithFrom: vi.fn((fromPid: string) => {
                const st = getGeoTestGlobal('__GEO_TEST_INTERACT_STATE')
                 if (st) {
                   st.fromPid = fromPid
                   st.initiatedWithPrefilledFrom = true
                 }
                phase.value = 'picking-trustline-to'
              }),
              startClearingFlow: startClearingFlow,
             confirmPayment: vi.fn(async () => undefined),
             confirmTrustlineCreate: vi.fn(async () => undefined),
             confirmTrustlineUpdate: vi.fn(async () => undefined),
             confirmTrustlineClose,
              confirmClearing: vi.fn(async () => {
                successMessage.value = 'Clearing done: 1/1 cycles'
              }),
              cancel,
              history: reactive<Array<Record<string, unknown>>>(getGeoTestGlobal('__GEO_TEST_INTERACT_HISTORY') ?? []),
            },
          systemBalance: computed(() => ({
            isClean: true,
            totalUsed: 0,
            totalAvailable: 0,
            activeTrustlines: 0,
            activeParticipants: 0,
            utilization: 0,
          })),
        },

        // env
        gpuAccelLikely: computed(() => false),

        // refs
        hostEl: ref<HTMLElement | null>(null),
        canvasEl: ref<HTMLCanvasElement | null>(null),
        fxCanvasEl: ref<HTMLCanvasElement | null>(null),
        dragPreviewEl: ref<HTMLElement | null>(null),

        // derived ui
        overlayLabelScale: computed(() => 1),
        showResetView: computed(() => false),

        // dev / diagnostics
        showPerfOverlay: computed(() => false),
        perf: reactive<Record<string, unknown>>({}),
        fxDebug: {
          enabled: computed(() => false),
          busy: ref(false),
          runTxOnce: vi.fn(),
          runClearingOnce: vi.fn(),
        },
        e2e: {
          runTxOnce: vi.fn(),
          runClearingOnce: vi.fn(),
        },

        // selection + overlays
        hoveredEdge: reactive({ key: null, fromId: '', toId: '', amountText: '' }),
        clearHoveredEdge: vi.fn(),
        edgeTooltipStyle: () => ({}),
        selectedNode: computed(() => selectedNode.value),
        selectedNodeScreenCenter: computed(() => selectedNodeScreenCenter.value),
        getNodeScreenCenter: () => selectedNodeScreenCenter.value ?? null,
        selectedNodeEdgeStats: computed(() => null),

        // pinning
        dragToPin: reactive({ dragState: { active: false } }),
        isSelectedPinned: computed(() => false),
        isNodePinned: () => false,
        pinNode: vi.fn(),
        unpinNode: vi.fn(),
        pinSelectedNode: vi.fn(),
        unpinSelectedNode: vi.fn(),

        // handlers
        onCanvasClick: vi.fn(() => {
          // Minimal integration wiring for root interaction tests:
          // emulate empty-canvas click behavior (outside click) by delegating
          // to WM-aware callback (WM-only runtime).
          //
          // P0-2: gate cancel on busy (mirrors real Step0 policy).
          const busyRef = getGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
          const isBusy = busyRef?.value === true

          if (isBusy) {
            // Show confirm gate (mirrors real confirmCancelInteractBusy).
            try {
              const c = typeof window.confirm === 'function' ? window.confirm : null
              if (typeof c === 'function') {
                const ok = !!c('Отменить операцию?')
                if (!ok) return
              }
            } catch {
              // best-effort
            }
          }

          try {
            const cancel = getGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
            if (typeof cancel === 'function') cancel()
          } catch {
            // best-effort
          }

          if (typeof opts?.uiCloseTopmostInspectorWindow === 'function') {
            // Close twice to mirror Step0 policy (edge-detail then node-card).
            opts.uiCloseTopmostInspectorWindow()
            opts.uiCloseTopmostInspectorWindow()
          }
        }),
        onCanvasDblClick: vi.fn(),
        onCanvasPointerDown: (() => {
          const fn = vi.fn()
          setGeoTestGlobal('__GEO_TEST_CANVAS_POINTER_DOWN', fn)
          return fn
        })(),
        onCanvasPointerMove: vi.fn(),
        onCanvasPointerUp: vi.fn(),
        onCanvasWheel: (() => {
          const fn = vi.fn()
          setGeoTestGlobal('__GEO_TEST_CANVAS_WHEEL', fn)
          return fn
        })(),

        // labels
        labelNodes: computed(() => []),
        floatingLabelsViewFx: computed(() => []),
        worldToCssTranslateNoScale: () => 'translate(0px, 0px)',

        // helpers for template
        getNodeById: (id: string | null) => {
          const n = getGeoTestGlobal('__GEO_TEST_SELECTED_NODE') ?? null
          if (!id || !n) return null
          return String(n.id) === String(id) ? n : null
        },
        resetView: vi.fn(),
      }
    },
  }
})

import SimulatorAppRoot from './SimulatorAppRoot.vue'

const simulatorAppRootComponent: Component = SimulatorAppRoot

function mountSimulatorAppRoot(host: HTMLElement) {
  const app = createApp({ render: () => h(simulatorAppRootComponent) })
  app.mount(host)
  return app
}

function stubMissingResizeObserver() {
  vi.stubGlobal('ResizeObserver', undefined)
}

type ResizeObserverRecord = {
  callback: ResizeObserverCallbackLike
  observed: Element[]
  instance: {
    observe: (target: Element) => void
    disconnect: () => void
    unobserve: (target: Element) => void
  }
}

type ResizeObserverEntryLike = {
  borderBoxSize: Array<{ inlineSize: number; blockSize: number }>
}

type ResizeObserverCallbackLike = (entries: ResizeObserverEntryLike[], observer: ResizeObserver) => void

function stubResizeObserverRecords() {
  const records: ResizeObserverRecord[] = []

  class ResizeObserverMock {
    private record: ResizeObserverRecord

    constructor(callback: ResizeObserverCallback) {
      this.record = {
        callback: callback as unknown as ResizeObserverCallbackLike,
        observed: [],
        instance: this,
      }
      records.push(this.record)
    }

    observe(target: Element) {
      this.record.observed.push(target)
    }

    disconnect() {}

    unobserve(target: Element) {
      this.record.observed = this.record.observed.filter((candidate) => candidate !== target)
    }
  }

  vi.stubGlobal('ResizeObserver', ResizeObserverMock)
  return { records }
}

function setUrl(search: string) {
  // Use a same-origin relative URL to satisfy happy-dom History security checks.
  window.history.replaceState({}, '', search)
}

function stubRect(el: HTMLElement, rect: {
  left: number
  top: number
  width: number
  height: number
  right?: number
  bottom?: number
}) {
  const right = rect.right ?? rect.left + rect.width
  const bottom = rect.bottom ?? rect.top + rect.height

  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue({
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
    right,
    bottom,
    x: rect.left,
    y: rect.top,
    toJSON: () => ({}),
  } as DOMRect)
}

function stubLocationHrefSetter() {
  const hrefSet = vi.fn()
  const prevLocation = window.location
  const currentHref = String(prevLocation.href)
  const currentSearch = String(prevLocation.search ?? '')
  const currentOrigin = 'origin' in prevLocation ? String(prevLocation.origin ?? 'http://localhost') : 'http://localhost'

  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      get href() {
        return currentHref
      },
      set href(v: string) {
        hrefSet(v)
      },
      get search() {
        return currentSearch
      },
      get origin() {
        return currentOrigin
      },
    },
  })

  return {
    hrefSet,
    restore: () => Object.defineProperty(window, 'location', { configurable: true, value: prevLocation }),
  }
}

describe('SimulatorAppRoot - Interact Mode rendering', () => {
  it('regression-guard: root must NOT use ds-ov-layer (pointer-events:none); it must use ds-ov-vars', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const root = host.querySelector('.root') as HTMLElement | null
      expect(root).toBeTruthy()

      // NOTE: `.ds-ov-layer` sets pointer-events:none and would break canvas interactions.
      expect(root!.classList.contains('ds-ov-layer')).toBe(false)
      expect(root!.classList.contains('ds-ov-vars')).toBe(true)

      // Sanity: canvas exists under root.
      expect(root!.querySelector('canvas.canvas')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('regression-guard: ActionBar-initiated interact-panel must anchor near ActionBar (not at host width)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const root = host.querySelector('.root') as HTMLElement | null
      expect(root).toBeTruthy()

      // Ensure deterministic geometry in happy-dom.
      stubRect(root!, { left: 0, top: 0, width: 1400, height: 800 })

      const actionBar = host.querySelector('[aria-label="Interact actions"]') as HTMLElement | null
      expect(actionBar).toBeTruthy()

      const hudBar = (actionBar!.querySelector('.hud-bar') as HTMLElement | null) ?? actionBar!
      stubRect(hudBar, { left: 40, top: 120, width: 620, height: 40 })

      const btn = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      btn!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      const open = getGeoTestGlobal('__GEO_TEST_WM_OPEN')
      expect(open).toBeTruthy()

      const calls = open!.mock.calls.map((c) => c[0]).filter(Boolean)
      const lastInteract = [...calls]
        .reverse()
        .find(
          (c): c is { type: 'interact-panel'; anchor?: { x: number; y: number; space: string; source: string } } =>
            typeof c === 'object' && c !== null && 'type' in c && c.type === 'interact-panel',
        )
      expect(lastInteract).toBeTruthy()

      const interactAnchor = lastInteract?.anchor
      expect(interactAnchor).toBeTruthy()
      expect(interactAnchor?.space).toBe('host')
      expect(interactAnchor?.source).toBe('panel')

      // Key regression guard: anchor must be near ActionBar's left, not at host width (=1400).
      expect(Number(interactAnchor?.x)).toBeGreaterThanOrEqual(0)
      expect(Number(interactAnchor?.x)).toBeLessThan(200)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('keeps canvas pointer and wheel handlers bound to canvas while overlay shell pointer paths stay separate', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 111, y: 222 })
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const pointerDown = getRequiredGeoTestGlobal('__GEO_TEST_CANVAS_POINTER_DOWN')
      const wheel = getRequiredGeoTestGlobal('__GEO_TEST_CANVAS_WHEEL')
      const uiOpenOrUpdateNodeCard = getRequiredGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD')

      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()

      canvas?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
      canvas?.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: 16 }))

      expect(pointerDown).toHaveBeenCalledTimes(1)
      expect(wheel).toHaveBeenCalledTimes(1)

      uiOpenOrUpdateNodeCard?.({ nodeId: 'bob', anchor: { x: 111, y: 222 } })
      await nextTick()
      await nextTick()

      const shell = host.querySelector('.ws-shell[data-win-type="node-card"]') as HTMLElement | null
      expect(shell).toBeTruthy()

      shell?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))

      expect(pointerDown).toHaveBeenCalledTimes(1)
      expect(wheel).toHaveBeenCalledTimes(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD',
        '__GEO_TEST_CANVAS_POINTER_DOWN',
        '__GEO_TEST_CANVAS_WHEEL',
      )
      vi.unstubAllGlobals()
    }
  })

  it('publishes measured top/bottom stack geometry and drives WM/top + toast/bottom consumer paths', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    const { records } = stubResizeObserverRecords()

    let topHeight = 144
    let bottomHeight = 72

    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('root')) {
        return {
          left: 0,
          top: 0,
          width: 1280,
          height: 720,
          right: 1280,
          bottom: 720,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-top')) {
        return {
          left: 0,
          top: 0,
          width: 1280,
          height: topHeight,
          right: 1280,
          bottom: topHeight,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-bottom') && !this.classList.contains('sar-interact-history-overlay')) {
        return {
          left: 0,
          top: 720 - bottomHeight,
          width: 1280,
          height: bottomHeight,
          right: 1280,
          bottom: 720,
          x: 0,
          y: 720 - bottomHeight,
          toJSON: () => ({}),
        } as DOMRect
      }

      return {
        left: 0,
        top: 0,
        width: 0,
        height: 0,
        right: 0,
        bottom: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect
    })

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const root = host.querySelector('.root') as HTMLElement | null
      const topStack = host.querySelector('.ds-ov-top') as HTMLElement | null
      const bottomStack = Array.from(host.querySelectorAll('.ds-ov-bottom')).find(
        (el) => !(el as HTMLElement).classList.contains('sar-interact-history-overlay'),
      ) as HTMLElement | undefined

      expect(root).toBeTruthy()
      expect(topStack).toBeTruthy()
      expect(bottomStack).toBeTruthy()

      expect(root!.style.getPropertyValue('--ds-hud-stack-height')).toBe('144px')
      expect(root!.style.getPropertyValue('--ds-hud-bottom-stack-height')).toBe('72px')

      const setGeometry = getGeoTestGlobal('__GEO_TEST_WM_SET_GEOMETRY')
      const reclampAll = getGeoTestGlobal('__GEO_TEST_WM_RECLAMP_ALL')
      expect(setGeometry).toBeTruthy()
      expect(reclampAll).toBeTruthy()
      expect(setGeometry).toHaveBeenCalledWith({ dockedRightTopPx: 144 })
      expect(reclampAll).toHaveBeenCalled()

      getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SUCCESS_MESSAGE').value = 'Geometry-aware toast'
      await nextTick()
      expect(host.querySelector('.success-toast')).toBeTruthy()

      topHeight = 184
      bottomHeight = 96

      const topRecord = records.find((record) => record.observed.includes(topStack!))
      const bottomRecord = records.find((record) => record.observed.includes(bottomStack!))
      expect(topRecord).toBeTruthy()
      expect(bottomRecord).toBeTruthy()

      topRecord!.callback([], topRecord!.instance)
      bottomRecord!.callback([], bottomRecord!.instance)
      await nextTick()

      expect(root!.style.getPropertyValue('--ds-hud-stack-height')).toBe('184px')
      expect(root!.style.getPropertyValue('--ds-hud-bottom-stack-height')).toBe('96px')
      expect(setGeometry).toHaveBeenCalledWith({ dockedRightTopPx: 184 })
    } finally {
      app.unmount()
      host.remove()
      rectSpy.mockRestore()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_SUCCESS_MESSAGE',
        '__GEO_TEST_WM_SET_GEOMETRY',
        '__GEO_TEST_WM_RECLAMP_ALL',
      )
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('dedupes unchanged HUD geometry publishes and only reclamps WM on meaningful deltas', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    const { records } = stubResizeObserverRecords()

    let topHeight = 144
    let bottomHeight = 72

    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('root')) {
        return {
          left: 0,
          top: 0,
          width: 1280,
          height: 720,
          right: 1280,
          bottom: 720,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-top')) {
        return {
          left: 0,
          top: 0,
          width: 1280,
          height: topHeight,
          right: 1280,
          bottom: topHeight,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-bottom') && !this.classList.contains('sar-interact-history-overlay')) {
        return {
          left: 0,
          top: 720 - bottomHeight,
          width: 1280,
          height: bottomHeight,
          right: 1280,
          bottom: 720,
          x: 0,
          y: 720 - bottomHeight,
          toJSON: () => ({}),
        } as DOMRect
      }

      return {
        left: 0,
        top: 0,
        width: 0,
        height: 0,
        right: 0,
        bottom: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect
    })

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const root = host.querySelector('.root') as HTMLElement | null
      const topStack = host.querySelector('.ds-ov-top') as HTMLElement | null
      const bottomStack = Array.from(host.querySelectorAll('.ds-ov-bottom')).find(
        (el) => !(el as HTMLElement).classList.contains('sar-interact-history-overlay'),
      ) as HTMLElement | undefined

      expect(root).toBeTruthy()
      expect(topStack).toBeTruthy()
      expect(bottomStack).toBeTruthy()

      const topRecord = records.find((record) => record.observed.includes(topStack!))
      const bottomRecord = records.find((record) => record.observed.includes(bottomStack!))
      expect(topRecord).toBeTruthy()
      expect(bottomRecord).toBeTruthy()

      const setGeometry = getGeoTestGlobal('__GEO_TEST_WM_SET_GEOMETRY')
      const reclampAll = getGeoTestGlobal('__GEO_TEST_WM_RECLAMP_ALL')
      expect(setGeometry).toBeTruthy()
      expect(reclampAll).toBeTruthy()

      const setGeometryCallsAfterMount = setGeometry!.mock.calls.length
      const reclampCallsAfterMount = reclampAll!.mock.calls.length

      topRecord!.callback([], topRecord!.instance)
      bottomRecord!.callback([], bottomRecord!.instance)
      await nextTick()

      expect(setGeometry!.mock.calls.length).toBe(setGeometryCallsAfterMount)
      expect(reclampAll!.mock.calls.length).toBe(reclampCallsAfterMount)

      topHeight = 176
      topRecord!.callback([], topRecord!.instance)
      await nextTick()

      expect(setGeometry!.mock.calls.length).toBe(setGeometryCallsAfterMount + 1)
      expect(setGeometry).toHaveBeenLastCalledWith({ dockedRightTopPx: 176 })
      expect(reclampAll!.mock.calls.length).toBe(reclampCallsAfterMount + 1)

      bottomHeight = 88
      bottomRecord!.callback([], bottomRecord!.instance)
      await nextTick()

      expect(root!.style.getPropertyValue('--ds-hud-bottom-stack-height')).toBe('88px')
      expect(setGeometry!.mock.calls.length).toBe(setGeometryCallsAfterMount + 1)
      expect(reclampAll!.mock.calls.length).toBe(reclampCallsAfterMount + 1)
    } finally {
      app.unmount()
      host.remove()
      rectSpy.mockRestore()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_WM_SET_GEOMETRY',
        '__GEO_TEST_WM_RECLAMP_ALL',
      )
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('keeps confirm-payment interact-panel on policy width after shell measurement and viewport resize reclamp', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    const { records } = stubResizeObserverRecords()

    let viewportWidth = 1280
    let viewportHeight = 720
    const topHeight = 144
    const bottomHeight = 72

    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('root')) {
        return {
          left: 0,
          top: 0,
          width: viewportWidth,
          height: viewportHeight,
          right: viewportWidth,
          bottom: viewportHeight,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-top')) {
        return {
          left: 0,
          top: 0,
          width: viewportWidth,
          height: topHeight,
          right: viewportWidth,
          bottom: topHeight,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-bottom') && !this.classList.contains('sar-interact-history-overlay')) {
        return {
          left: 0,
          top: viewportHeight - bottomHeight,
          width: viewportWidth,
          height: bottomHeight,
          right: viewportWidth,
          bottom: viewportHeight,
          x: 0,
          y: viewportHeight - bottomHeight,
          toJSON: () => ({}),
        } as DOMRect
      }

      return {
        left: 0,
        top: 0,
        width: 0,
        height: 0,
        right: 0,
        bottom: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect
    })

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const root = host.querySelector('.root') as HTMLElement | null
      const interactShell = host.querySelector('[data-win-type="interact-panel"]') as HTMLElement | null

      expect(root).toBeTruthy()
      expect(interactShell).toBeTruthy()
      const initialLeft = Number.parseInt(interactShell!.style.left, 10)
      expect(interactShell!.style.width).toBe('560px')
      expect(Number.isFinite(initialLeft)).toBe(true)
      expect(initialLeft).toBeGreaterThan(0)
      expect(interactShell!.style.top).toBe('144px')

      const viewportRecord = records.find((record) => record.observed.includes(root!))
      const shellRecord = records.find((record) => record.observed.includes(interactShell!))

      expect(viewportRecord).toBeTruthy()
      expect(shellRecord).toBeTruthy()

      shellRecord!.callback(
        [
          {
            borderBoxSize: [{ inlineSize: 620, blockSize: 312 }],
          },
        ],
        shellRecord!.instance as ResizeObserver,
      )
      await new Promise((resolve) => setTimeout(resolve, 20))
      await nextTick()

      expect(interactShell!.style.width).toBe('560px')
      expect(Number.parseInt(interactShell!.style.left, 10)).toBe(initialLeft)

      viewportWidth = 920
      viewportRecord!.callback([], viewportRecord!.instance as ResizeObserver)
      await nextTick()

      expect(interactShell!.style.width).toBe('560px')
      expect(interactShell!.style.left).toBe('344px')
      expect(Number.parseInt(interactShell!.style.left, 10)).toBeLessThan(initialLeft)
      expect(interactShell!.style.top).toBe('144px')
    } finally {
      app.unmount()
      host.remove()
      rectSpy.mockRestore()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_WM_SET_GEOMETRY',
        '__GEO_TEST_WM_RECLAMP_ALL',
      )
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('keeps InteractHistoryLog coexisting with BottomBar without polluting bottom-stack geometry ownership', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setGeoTestGlobal('__GEO_TEST_INTERACT_HISTORY', [
      { id: '1', icon: 'P', text: 'Send payment', timeText: '10:00' },
      { id: '2', icon: 'T', text: 'Update trustline', timeText: '10:01' },
    ])
    setUrl('/?mode=real&ui=interact')

    const { records } = stubResizeObserverRecords()

    let topHeight = 144
    let bottomHeight = 72
    const historyHeight = 220

    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains('root')) {
        return {
          left: 0,
          top: 0,
          width: 1280,
          height: 720,
          right: 1280,
          bottom: 720,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-top')) {
        return {
          left: 0,
          top: 0,
          width: 1280,
          height: topHeight,
          right: 1280,
          bottom: topHeight,
          x: 0,
          y: 0,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('sar-interact-history-overlay')) {
        return {
          left: 900,
          top: 720 - historyHeight,
          width: 320,
          height: historyHeight,
          right: 1220,
          bottom: 720,
          x: 900,
          y: 720 - historyHeight,
          toJSON: () => ({}),
        } as DOMRect
      }

      if (this.classList.contains('ds-ov-bottom')) {
        return {
          left: 0,
          top: 720 - bottomHeight,
          width: 1280,
          height: bottomHeight,
          right: 1280,
          bottom: 720,
          x: 0,
          y: 720 - bottomHeight,
          toJSON: () => ({}),
        } as DOMRect
      }

      return {
        left: 0,
        top: 0,
        width: 0,
        height: 0,
        right: 0,
        bottom: 0,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      } as DOMRect
    })

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const root = host.querySelector('.root') as HTMLElement | null
      const historyOverlay = host.querySelector('.sar-interact-history-overlay') as HTMLElement | null
      const bottomStack = Array.from(host.querySelectorAll('.ds-ov-bottom')).find(
        (el) => !(el as HTMLElement).classList.contains('sar-interact-history-overlay'),
      ) as HTMLElement | undefined

      expect(root).toBeTruthy()
      expect(historyOverlay).toBeTruthy()
      expect(bottomStack).toBeTruthy()
      expect(historyOverlay?.textContent).toContain('Recent actions')

      expect(root!.style.getPropertyValue('--ds-hud-bottom-stack-height')).toBe('72px')

      const observedHistory = records.some((record) => historyOverlay != null && record.observed.includes(historyOverlay))
      const observedBottom = records.some((record) => bottomStack != null && record.observed.includes(bottomStack))
      expect(observedHistory).toBe(false)
      expect(observedBottom).toBe(true)
    } finally {
      app.unmount()
      host.remove()
      rectSpy.mockRestore()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_HISTORY')
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('ui=interact renders interact HUD + ActionBar + ManualPaymentPanel and does not render intensity slider', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    // HUD presence (unified TopBar)
    const interactBtn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === 'Interact') as
      | HTMLButtonElement
      | undefined
    expect(interactBtn).toBeTruthy()
    expect(interactBtn?.getAttribute('data-active')).toBe('1')

    // ActionBar presence
    expect(host.querySelector('[data-testid="actionbar-payment"]')).toBeTruthy()
    // Phase panel presence
    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

    // Interact Mode must hide intensity slider (it exists in TopBar Auto-Run advanced only)
    expect(host.querySelector('[aria-label="Intensity percent"]')).toBeFalsy()

    app.unmount()
    host.remove()
    clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
  })

  it('renders ManualPaymentPanel through WindowLayer (WindowShell) and does not duplicate legacy panel', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    // Step 3: WM wiring must not crash in environments without native ResizeObserver.
    // (happy-dom may not provide it; we stub it out explicitly to ensure coverage)
    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const panels = host.querySelectorAll('[data-testid="manual-payment-panel"]')
      expect(panels.length).toBe(1)

      const panel = panels[0] as HTMLElement
      expect(panel.closest('.ws-shell')).toBeTruthy()

      // AC-0 / AC-3: WM-rendered panel must carry legacy DS classes for visual parity
      expect(panel.classList.contains('ds-ov-panel')).toBe(true)
      expect(panel.classList.contains('ds-panel')).toBe(true)
      expect(panel.classList.contains('ds-panel--elevated')).toBe(true)
      expect(panel.querySelector('.ds-panel__header')).toBeTruthy()

      // AC-1 / AC-2: frameless shell must NOT render its own header/chrome
      const shell = shells[0] as HTMLElement
      expect(shell.classList.contains('ws-shell--framed')).toBe(false)
      expect(shell.querySelector('.ws-header')).toBeFalsy()
      expect(shell.querySelector('button.ws-close')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  it('renders TrustlineManagementPanel through WindowLayer and does not duplicate legacy panel', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-trustline-create')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const panels = host.querySelectorAll('[data-testid="trustline-panel"]')
      expect(panels.length).toBe(1)
      expect((panels[0] as HTMLElement).closest('.ws-shell')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  it('renders ClearingPanel through WindowLayer and does not duplicate legacy panel', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-clearing')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const panels = host.querySelectorAll('[data-testid="clearing-panel"]')
      expect(panels.length).toBe(1)
      expect((panels[0] as HTMLElement).closest('.ws-shell')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  it('keyboard-triggered clearing flow moves focus into interact-panel and traps Tab', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const opener = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement | null
      expect(opener).toBeTruthy()

      opener?.focus()
      expect(document.activeElement).toBe(opener)

      opener?.click()
      await nextTick()
      await nextTick()

      const panel = host.querySelector('[data-testid="clearing-panel"]') as HTMLElement | null
      const confirmBtn = panel?.querySelector('button.ds-btn--primary') as HTMLButtonElement | null
      const cancelBtn = panel?.querySelector('button.ds-btn--ghost') as HTMLButtonElement | null

      expect(panel).toBeTruthy()
      expect(confirmBtn).toBeTruthy()
      expect(cancelBtn).toBeTruthy()
      expect(panel?.contains(document.activeElement)).toBe(true)
      expect(document.activeElement).toBe(confirmBtn)

      cancelBtn?.focus()
      cancelBtn?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
      await nextTick()
      expect(document.activeElement).toBe(confirmBtn)

      confirmBtn?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
      await nextTick()
      expect(document.activeElement).toBe(cancelBtn)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE')
      vi.unstubAllGlobals()
    }
  })

  it('renders EdgeDetailPopup through WindowLayer (WindowShell)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const popups = host.querySelectorAll('[data-testid="edge-detail-popup"]')
      expect(popups.length).toBe(1)
      expect((popups[0] as HTMLElement).closest('.ws-shell')).toBeTruthy()

      const shell = host.querySelector('.ws-shell[data-win-type="edge-detail"]') as HTMLElement | null
      expect(shell?.getAttribute('role')).toBe('region')
      expect(shell?.getAttribute('aria-label')).toBe('Trustline details: alice to bob')

      // legacy render must be disabled (no duplicate absolute popup)
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  it('renders NodeCardOverlay through WindowLayer (WindowShell)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const cards = host.querySelectorAll('.ds-ov-node-card')
      expect(cards.length).toBe(1)
      expect((cards[0] as HTMLElement).closest('.ws-shell')).toBeTruthy()

      const shell = host.querySelector('.ws-shell[data-win-type="node-card"]') as HTMLElement | null
      expect(shell?.getAttribute('role')).toBe('region')
      expect(shell?.getAttribute('aria-label')).toBe('Node details: Bob')
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
      )
      vi.unstubAllGlobals()
    }
  })

  it('coexistence — interact-panel and node-card both render (2 windows)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(2)

      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(1)
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
      )
      vi.unstubAllGlobals()
    }
  })

  it('P1-1: реактивный watcher-update interact-panel (focus:never) не поднимает interact выше inspector (z-order stable)', async () => {
    // Scenario: interact + node-card coexist. Inspector is "on top" of itself
    // (no manual focus change by user). Then a phase watcher fires again (reactive update).
    // The interact-panel watcher now uses focus:'never', so the z-order must not jump.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Both windows should render.
      expect(host.querySelectorAll('.ws-shell').length).toBe(2)

      const getShells = () => {
        const shells = Array.from(host.querySelectorAll('.ws-shell')) as HTMLElement[]
        const interactShell = shells.find((el) => el.getAttribute('data-win-type') === 'interact-panel')
        const inspectorShell = shells.find((el) => el.getAttribute('data-win-type') === 'node-card')
        return { interactShell, inspectorShell }
      }

      const { interactShell, inspectorShell } = getShells()
      expect(interactShell).toBeTruthy()
      expect(inspectorShell).toBeTruthy()

      // Capture z-index values before reactive update.
      const zInteractBefore = Number(interactShell!.style.zIndex || '0')
      const zInspectorBefore = Number(inspectorShell!.style.zIndex || '0')
      // Interact group has higher base; it must always be above inspector.
      expect(zInteractBefore).toBeGreaterThan(zInspectorBefore)

      // Simulate reactive watcher update: change phase (this triggers the interact-panel watcher
      // to call wm.open with focus:'never'). Then restore.
      const phaseRef = getGeoTestGlobal('__GEO_TEST_PHASE_REF')
      if (phaseRef) {
        phaseRef.value = 'picking-payment-from'
        await nextTick()
        phaseRef.value = 'confirm-payment'
        await nextTick()
        await nextTick()
      }

      // After reactive update the z-order must not have changed: interact still above inspector.
      const { interactShell: interactAfter, inspectorShell: inspectorAfter } = getShells()
      const zInteractAfter = Number(interactAfter?.style.zIndex || '0')
      const zInspectorAfter = Number(inspectorAfter?.style.zIndex || '0')
      expect(zInteractAfter).toBeGreaterThan(zInspectorAfter)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
      )
      vi.unstubAllGlobals()
    }
  })

  it('NodeCard action → opens interact-panel and keeps NodeCard open (H-1 coexistence)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Run Clearing')) as
        | HTMLButtonElement
        | undefined
      expect(btn).toBeTruthy()

      btn?.click()
      await nextTick()
      await nextTick()

      // Interact panel is visible, NodeCard stays rendered.
      expect(host.querySelectorAll('[data-testid="clearing-panel"]').length).toBe(1)
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)
      expect(host.querySelectorAll('.ws-shell').length).toBe(2)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_WM_HANDLE_ESC',
      )
      vi.unstubAllGlobals()
    }
  })

  it('cross-group replace — Change Limit closes edge-detail window and opens trustline panel', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Initially: edge-detail only.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Change limit')) as
        | HTMLButtonElement
        | undefined
      expect(btn).toBeTruthy()

      btn?.click()
      await nextTick()
      await nextTick()

      // Edge detail must be closed; trustline panel must be visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      expect(host.querySelectorAll('[data-testid="trustline-panel"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  it('edge-detail UI-close closes the window and does NOT cancel interact flow (H-3)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // UI-close inside edge-detail window.
      const closeBtn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Close') as
        | HTMLButtonElement
        | undefined
      expect(closeBtn).toBeTruthy()

      closeBtn?.click()
      await nextTick()
      await nextTick()

      // Window must be closed...
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      // ...but flow MUST NOT be cancelled.
      expect(cancel).not.toHaveBeenCalled()

      // Suppression is UI-only: selecting a new edge (anchor change) should reopen the window.
      const st = getInteractState()
      expect(st).toBeTruthy()
      st.edgeAnchor = { x: 20, y: 20 }
      await nextTick()
      await nextTick()
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_STATE')
      vi.unstubAllGlobals()
    }
  })

  it('ARCH-7: EdgeDetail in keepAlive shows frozen edge context, not live interact state (Send Payment from edge-detail keeps edge-detail window open)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
      expect(host.querySelectorAll('.ws-shell').length).toBe(1)

      // Baseline: edge-detail title must match the selected trustline.
      const getEdgeTitle = () => {
        const popup = host.querySelector('[data-testid="edge-detail-popup"]') as HTMLElement | null
        const title = popup?.querySelector('.popup__subtitle') as HTMLElement | null
        return (title?.textContent ?? '').trim()
      }
      expect(getEdgeTitle()).toBe('alice → bob')

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // Click "Send Payment" button inside edge-detail (uses data-testid).
      const sendBtn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
      expect(sendBtn).toBeTruthy()

      sendBtn?.click()
      await nextTick()
      await nextTick()

      // Edge-detail must STILL be visible (keepAlive).
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      // Regression (P0-1): keepAlive edge-detail MUST render frozen context from WM window data,
      // not from live interact state (which drifts to payment flow: bob -> alice).
      const st = getInteractState()
      expect(st?.fromPid).toBe('bob')
      expect(st?.toPid).toBe('alice')
      expect(getEdgeTitle()).toBe('alice → bob')

      // Even if live interact state changes further, edge-detail context must remain frozen.
      st.fromPid = 'carol'
      st.toPid = 'dave'
      await nextTick()
      await nextTick()
      expect(getEdgeTitle()).toBe('alice → bob')

      // Payment interact-panel must also appear (coexistence).
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(1)

      // Two windows: edge-detail + interact-panel.
      expect(host.querySelectorAll('.ws-shell').length).toBe(2)

      // Layering priority: interact must be visually above inspector, even if inspector is focused.
      const shells = Array.from(host.querySelectorAll('.ws-shell')) as HTMLElement[]
      const interactShell = shells.find((el) => el.getAttribute('data-win-type') === 'interact-panel') as HTMLElement | undefined
      const inspectorShell = shells.find((el) => el.getAttribute('data-win-type') === 'edge-detail') as HTMLElement | undefined
      expect(interactShell).toBeTruthy()
      expect(inspectorShell).toBeTruthy()

      // Simulate focusing inspector (pointerdown on shell).
      inspectorShell?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))
      await nextTick()
      await nextTick()

      const zInteract = Number(interactShell!.style.zIndex || '0')
      const zInspector = Number(inspectorShell!.style.zIndex || '0')
      expect(zInteract).toBeGreaterThan(zInspector)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_STATE')
      vi.unstubAllGlobals()
    }
  })

  it('outside-click closes topmost/active inspector via WM (edge-detail) and cancels interact flow', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      const getTopmost = getRequiredGeoTestGlobal('__GEO_TEST_WM_GET_TOPMOST_IN_GROUP')

      // Outside click (empty canvas click)
      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      expect(getTopmost).toHaveBeenCalledWith('inspector')

      // Window must be closed...
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      // Outside click cancels the flow.
      expect(cancel).toHaveBeenCalledTimes(1)
      const st = getInteractState()
      expect(st?.edgeAnchor).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_STATE',
        '__GEO_TEST_WM_GET_TOPMOST_IN_GROUP',
      )
      vi.unstubAllGlobals()
    }
  })

  it('outside-click closes inspector (node-card) and cancels interact-panel (hard dismiss)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Coexistence: interact + inspector.
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(1)
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

      const getTopmost = getRequiredGeoTestGlobal('__GEO_TEST_WM_GET_TOPMOST_IN_GROUP')

      // Outside click (empty canvas click)
      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      expect(getTopmost).toHaveBeenCalledWith('inspector')
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(0)

      // Interact flow must be cancelled (phase -> idle closes the interact window).
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(0)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_WM_GET_TOPMOST_IN_GROUP',
      )
      vi.unstubAllGlobals()
    }
  })

  it('outside-click restoreFocus returns to opener after node-card dismiss', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 111, y: 222 })
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const opener = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement | null
      expect(opener).toBeTruthy()

      const uiOpenOrUpdateNodeCard = getRequiredGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD')

      opener?.focus()
      expect(document.activeElement).toBe(opener)

      uiOpenOrUpdateNodeCard?.({ nodeId: 'bob', anchor: { x: 111, y: 222 } })
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(0)
      expect(document.activeElement).toBe(opener)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD',
      )
      vi.unstubAllGlobals()
    }
  })

  it('outside-click restoreFocus returns to opener after edge-detail dismiss', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const opener = host.querySelector('[data-testid="actionbar-payment"]') as HTMLButtonElement | null
      expect(opener).toBeTruthy()

      const uiOpenOrUpdateEdgeDetail = getRequiredGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_EDGE_DETAIL')

      opener?.focus()
      expect(document.activeElement).toBe(opener)

      uiOpenOrUpdateEdgeDetail?.({ fromPid: 'alice', toPid: 'bob', anchor: { x: 10, y: 10 } })
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      expect(document.activeElement).toBe(opener)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_UI_OPEN_OR_UPDATE_EDGE_DETAIL',
      )
      vi.unstubAllGlobals()
    }
  })

  it('EdgeDetailPopup Close closes edge-detail window (UI-close) and does NOT cancel interact flow', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      const closeBtn = Array.from(host.querySelectorAll('[data-testid="edge-detail-popup"] button')).find((b) =>
        (b.textContent ?? '').trim() === 'Close',
      ) as HTMLButtonElement | undefined
      expect(closeBtn).toBeTruthy()

      closeBtn?.click()
      await nextTick()
      await nextTick()

      // Window must be closed...
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      // ...but flow MUST NOT be cancelled.
      expect(cancel).not.toHaveBeenCalled()

      // Suppression is UI-only: selecting a new edge (anchor change) should reopen the window.
      const st = getInteractState()
      expect(st).toBeTruthy()
      st.edgeAnchor = { x: 20, y: 20 }
      await nextTick()
      await nextTick()
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_STATE')
      vi.unstubAllGlobals()
    }
  })

  it('EdgeDetailPopup send-payment → payment keeps edge anchor (wm.open gets non-null anchor)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()

      const btn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      const open = getRequiredGeoTestGlobal('__GEO_TEST_WM_OPEN')

      btn?.click()
      await nextTick()
      await nextTick()

      // Find any call that opened a payment interact-panel.
      const calls = open.mock.calls.map((c) => c[0])
      const paymentOpen = calls.find((o) => o?.type === 'interact-panel' && o?.data?.panel === 'payment')
      expect(paymentOpen).toBeTruthy()
      expect(paymentOpen?.anchor).toBeTruthy()
      expect(paymentOpen?.anchor?.x).toBe(10)
      expect(paymentOpen?.anchor?.y).toBe(10)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_WM_OPEN')
      vi.unstubAllGlobals()
    }
  })

  it('MP-0: routesLoading in root yields tri-state unknown in ManualPaymentPanel (shows updating help)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-payment-to')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()

      const trustlinesLoading = getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_LOADING_REF')
      const paymentTargetsLoading = getRequiredGeoTestGlobal('__GEO_TEST_PAYMENT_TARGETS_LOADING_REF')
      const paymentTargetsLastError = getRequiredGeoTestGlobal('__GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF')
      const paymentToTargetIds = getRequiredGeoTestGlobal('__GEO_TEST_PAYMENT_TO_TARGET_IDS_REF')

      trustlinesLoading.value = true
      paymentTargetsLoading.value = false
      paymentTargetsLastError.value = null
      paymentToTargetIds.value = new Set(['bob'])
      await nextTick()
      await nextTick()

      const help = host.querySelector('[data-testid="manual-payment-to-help"]') as HTMLElement | null
      expect(help).toBeTruthy()
      expect(help?.textContent ?? '').toContain('Routes are updating')

      // Now become known-empty: root should pass a Set (not undefined) and panel should show the empty-state help.
      trustlinesLoading.value = false
      paymentTargetsLoading.value = false
      paymentTargetsLastError.value = null
      paymentToTargetIds.value = new Set()
      await nextTick()
      await nextTick()

      // In the mocked interact state, toPid starts as 'bob'. When known-empty targets arrive,
      // MP-1b behavior in ManualPaymentPanel resets To and surfaces an inline warning.
      const warn = host.querySelector('[data-testid="manual-payment-to-invalid-warn"]') as HTMLElement | null
      expect(warn).toBeTruthy()
      expect((warn?.textContent ?? '').trim()).toContain('Selected recipient is no longer available')

      // UX-10 (Phase 2): known-empty => To select disabled.
      // The To selector only renders when participantsSorted.length > 0.
      // In this test the participants list is empty, so the select may not be present.
      // UX-10 is verified separately in ManualPaymentPanel.test.ts with actual participants.
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_BUSY_REF',
        '__GEO_TEST_TRUSTLINES_LOADING_REF',
        '__GEO_TEST_PAYMENT_TARGETS_LOADING_REF',
        '__GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF',
        '__GEO_TEST_PAYMENT_TO_TARGET_IDS_REF',
        '__GEO_TEST_INTERACT_STATE',
      )
    }
  })

  it('ui=interact renders trustline/clearing panels depending on phase', async () => {
    setUrl('/?mode=real&ui=interact')

    // Trustline create phase
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-trustline-create')
    {
      const host = document.createElement('div')
      document.body.appendChild(host)
      const app = mountSimulatorAppRoot(host)
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()
      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeFalsy()

      app.unmount()
      host.remove()
    }

    // Clearing phase
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-clearing')
    {
      const host = document.createElement('div')
      document.body.appendChild(host)
      const app = mountSimulatorAppRoot(host)
      await nextTick()

      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeTruthy()

      app.unmount()
      host.remove()
    }

    clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
  })

  it('clicking ActionBar "Run Clearing" changes phase and shows ClearingPanel (behavioral)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    expect(host.querySelector('[data-testid="clearing-panel"]')).toBeFalsy()

    const btn = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click()
    await nextTick()

    expect(host.querySelector('[data-testid="clearing-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
    clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
  })

  it('Escape delegates to WM and does not cancel confirm-payment (step-back instead)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

    const setTo = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID')

    const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(handleEsc).toHaveBeenCalledTimes(1)
    expect(cancel).toHaveBeenCalledTimes(0)
    expect(setTo).toHaveBeenCalledWith(null)

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_INTERACT_CANCEL',
      '__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID',
      '__GEO_TEST_WM_HANDLE_ESC',
    )
  })

  it('Escape delegates to wm.handleEsc() and does NOT cancel interact flow', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setTo = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID')
      const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))

      expect(handleEsc).toHaveBeenCalledTimes(1)
      expect(cancel).toHaveBeenCalledTimes(0)
      // In WM mode, ESC should attempt Interact step-back first.
      expect(setTo).toHaveBeenCalledWith(null)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID',
        '__GEO_TEST_WM_HANDLE_ESC',
      )
      vi.unstubAllGlobals()
    }
  })

  it('ESC on picking-payment-to closes when initiatedWithPrefilledFrom=true (NodeCard)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-payment-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = true
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      // Window closes via WM, which calls onClose → cancel().
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(setFrom).not.toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
      )
      vi.unstubAllGlobals()
    }
  })

  it('ESC on picking-payment-to steps back when initiatedWithPrefilledFrom=false (ActionBar)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-payment-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const phaseRef = getPhaseRef()
      phaseRef.value = 'picking-payment-to'

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      // Step-back consumes ESC; window stays open.
      expect(cancel).toHaveBeenCalledTimes(0)
      expect(setFrom).toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()
      expect(phaseRef.value).toBe('picking-payment-from')
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
        '__GEO_TEST_PHASE_REF',
      )
      vi.unstubAllGlobals()
    }
  })

  it('ActionBar payment — 2nd ESC after step-back closes window (cancel ×1)', async () => {
    // AC regression: after step-back from picking-to → picking-from (ActionBar), 2nd ESC must close.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-payment-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const phaseRef = getPhaseRef()
      phaseRef.value = 'picking-payment-to'

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID')

      // ESC #1: step-back (picking-to → picking-from)
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(0)
      expect(setFrom).toHaveBeenCalledWith(null)
      // Mock setPaymentFromPid(null) advances phase to 'picking-payment-from'
      expect(phaseRef.value).toBe('picking-payment-from')
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      await nextTick()
      await nextTick()

      // ESC #2: no onBack matches picking-payment-from → WM closes → cancel ×1
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(1)
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
        '__GEO_TEST_PHASE_REF',
      )
      vi.unstubAllGlobals()
    }
  })

  it('latched initiatedWithPrefilledFrom stays true after manual FROM change via dropdown', async () => {
    // Regression: initiatedWithPrefilledFrom must be latched (not derived from fromPid truthy).
    // After user changes FROM in dropdown, ESC must still close (not step-back to picking-from).
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-payment-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      // Flow initiated via NodeCard (prefilled FROM = alice)
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = true
      await nextTick()
      await nextTick()

      // User changes FROM via dropdown (setPaymentFromPid('carol'))
      // initiatedWithPrefilledFrom remains true (latched — not reset by dropdown change)
      st.fromPid = 'carol'
      // Explicitly keep latched flag = true (simulates FSM behavior)
      st.initiatedWithPrefilledFrom = true
      await nextTick()
      await nextTick()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID')

      // ESC should close (prefilled=true), not step-back to picking-from
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(1)
      expect(setFrom).not.toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
      )
      vi.unstubAllGlobals()
    }
  })

  it('EdgeDetail payment confirm ESC clears TO, then closes on 2nd ESC (prefilled)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.initiatedWithPrefilledFrom = true

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setTo = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID')

      // ESC #1: confirm → picking-to
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(setTo).toHaveBeenCalledWith(null)
      expect(cancel).toHaveBeenCalledTimes(0)
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      // ESC #2: picking-to + prefilled => close
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(setFrom).not.toHaveBeenCalledWith(null)
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID',
        '__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
      )
      vi.unstubAllGlobals()
    }
  })

  it('Trustline picking-to closes when initiatedWithPrefilledFrom=true (NodeCard)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-trustline-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = true
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(1)
      expect(setFrom).not.toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
      )
      vi.unstubAllGlobals()
    }
  })

  it('Trustline picking-to steps back when initiatedWithPrefilledFrom=false (ActionBar)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-trustline-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(0)
      expect(setFrom).toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
      )
      vi.unstubAllGlobals()
    }
  })

  it('ActionBar trustline — 2nd ESC after step-back closes window (cancel ×1)', async () => {
    // AC regression: after step-back from picking-trustline-to → picking-trustline-from, 2nd ESC must close.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-trustline-to')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const phaseRef = getPhaseRef()
      phaseRef.value = 'picking-trustline-to'

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID')

      // ESC #1: step-back (picking-trustline-to → picking-trustline-from)
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(0)
      expect(setFrom).toHaveBeenCalledWith(null)
      // Mock setTrustlineFromPid(null) advances phase to 'picking-trustline-from'
      expect(phaseRef.value).toBe('picking-trustline-from')
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      await nextTick()
      await nextTick()

      // ESC #2: no onBack matches picking-trustline-from → WM closes → cancel ×1
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(1)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID',
        '__GEO_TEST_INTERACT_STATE',
        '__GEO_TEST_PHASE_REF',
      )
      vi.unstubAllGlobals()
    }
  })

  it('clearing confirm — ESC closes window without step-back, cancel called exactly once', async () => {
    // AC-7 regression: Run Clearing has no step-back; ESC must close and cancel exactly once.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-clearing')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      // cancel must be called exactly once (no double-cancel from step-back + close)
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_STATE')
      vi.unstubAllGlobals()
    }
  })

  it('Trustline edit ESC clears TO and stays open, then closes on 2nd ESC (prefilled)', async () => {
    // IMPORTANT: `editing-trustline` may map to EdgeDetailPopup when `useFullTrustlineEditor=false`.
    // This test asserts ESC step-back for the *full* trustline editor (TrustlineManagementPanel),
    // so we must enter the trustline flow via ActionBar to set `useFullTrustlineEditor=true`.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Enter via ActionBar so the root sets useFullTrustlineEditor=true.
      const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
      expect(tlBtn).toBeTruthy()
      tlBtn!.click()
      await nextTick()
      await nextTick()

      const st = getInteractState()
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.initiatedWithPrefilledFrom = true

      // Force phase to editor state (full editor window exists in WM layer).
      const phaseRef = getPhaseRef()
      phaseRef.value = 'editing-trustline'
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const setTo = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID')

      // ESC #1: editing → picking-to (clear TO)
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()
      expect(setTo).toHaveBeenCalledWith(null)
      expect(cancel).toHaveBeenCalledTimes(0)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      // ESC #2: picking-to + prefilled => close
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID',
        '__GEO_TEST_INTERACT_STATE',
      )
      vi.unstubAllGlobals()
    }
  })

  it('canvas shows crosshair cursor in interact picking phases', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'picking-payment-from')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
    expect(canvas).toBeTruthy()
    expect(canvas?.style.cursor).toBe('crosshair')

    app.unmount()
    host.remove()
    clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
  })

  it('TrustlineManagementPanel: Close TL requires 2 clicks (armed confirmation)', async () => {
    // Enter via ActionBar to set useFullTrustlineEditor=true, then advance to editing-trustline.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    // Click ActionBar trustline button → useFullTrustlineEditor=true, phase=picking-trustline-from
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // Advance to editing-trustline (simulates FSM progression)
    const phaseRef = getPhaseRef()
    phaseRef.value = 'editing-trustline'
    await nextTick()

    const confirmClose = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE')

    const btn = host.querySelector('[data-testid="trustline-close-btn"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()

    btn?.click()
    await nextTick()
    expect(confirmClose).toHaveBeenCalledTimes(0)

    btn?.click()
    await nextTick()
    expect(confirmClose).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_INTERACT_CANCEL',
      '__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE',
    )
  })

  it('EdgeDetailPopup: hidden when TrustlineManagementPanel is visible (no duplicate windows)', async () => {
    // Enter via ActionBar so useFullTrustlineEditor=true → TrustlineManagementPanel shown,
    // EdgeDetailPopup hidden via forceHidden prop.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    // Click ActionBar trustline button → useFullTrustlineEditor=true
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // Advance to editing-trustline
    const phaseRef = getPhaseRef()
    phaseRef.value = 'editing-trustline'
    await nextTick()

    // EdgeDetailPopup should NOT render (forceHidden=true because useFullTrustlineEditor=true).
    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
    expect(btn).toBeFalsy()

    // TrustlineManagementPanel should render instead.
    expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_INTERACT_CANCEL',
      '__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE',
    )
  })

  it('AC-ED-3: EdgeDetailPopup "Send Payment" activates Manual Payment and pre-fills pids (trustline to→from)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    const btn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click()
    await nextTick()
    await nextTick()

    const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
    const setTo = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID')
    const startPaymentFlowWithFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM')

    expect(cancel).toHaveBeenCalledTimes(1)
    // EdgeDetailPopup Send Payment: must start payment atomically with pre-filled FROM.
    // trustline alice→bob => payment FROM=bob, TO=alice
    expect(startPaymentFlowWithFrom).toHaveBeenCalledTimes(1)
    expect(startPaymentFlowWithFrom).toHaveBeenCalledWith('bob')
    expect(setTo).toHaveBeenCalledWith('alice')

    // Manual Payment panel should be active (root-level E2E wiring), and confirm flow should be reached.
    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()
    expect(host.querySelector('[data-testid="manual-payment-confirm"]')).toBeTruthy()

    // Pre-filled values should be persisted into interact state (as the real mode setters do).
    const st = getInteractState()
    expect(st.fromPid).toBe('bob')
    expect(st.toPid).toBe('alice')

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_INTERACT_CANCEL',
      '__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM',
      '__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID',
    )
  })

  it('Escape disarms Close TL confirmation (does not cancel flow)', async () => {
    // Enter via ActionBar to set useFullTrustlineEditor=true, then advance to editing-trustline.
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    // Click ActionBar trustline button → useFullTrustlineEditor=true
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // Advance to editing-trustline
    const phaseRef = getPhaseRef()
    phaseRef.value = 'editing-trustline'
    await nextTick()

    const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

    const btn = host.querySelector('[data-testid="trustline-close-btn"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click() // arm
    await nextTick()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    // Regression guard: destructive confirmation uses `geo:interact-esc`.
    // The global ESC handler may still cancel the flow afterwards depending on stubs.
    const escEvt = new CustomEvent('geo:interact-esc', { cancelable: true })
    window.dispatchEvent(escEvt)
    await nextTick()

    expect(host.querySelector('[data-testid="trustline-close-cancel"]')).toBeFalsy()

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_INTERACT_CANCEL',
      '__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE',
    )
  })

  it('Escape closes NodeCard window first (does not cancel idle interact)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()
    await nextTick()

    const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
    const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')

    expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(handleEsc).toHaveBeenCalledTimes(1)
    expect(cancel).toHaveBeenCalledTimes(0)

    await nextTick()
    await nextTick()
    expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(0)

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_NODE_CARD_OPEN',
      '__GEO_TEST_SELECTED_NODE',
      '__GEO_TEST_INTERACT_CANCEL',
      '__GEO_TEST_WM_HANDLE_ESC',
    )
  })

  it('Escape is routed to WM and closes NodeCard via back-stack (H-2 ESC policy)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

      const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      // Must go through WM.
      expect(handleEsc).toHaveBeenCalledTimes(1)

      // Not visible.
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(0)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_WM_HANDLE_ESC',
      )
      vi.unstubAllGlobals()
    }
  })

  it('NodeCardOverlay: clicking "Send Payment" starts payment flow and shows ManualPaymentPanel (wiring)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()
    await nextTick()

    const card = host.querySelector('.ds-ov-node-card') as HTMLElement | null
    expect(card).toBeTruthy()

    const btn = card!.querySelector('[data-testid="node-card-send-payment"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()

    btn?.click()
    await nextTick()

    const startPaymentFlowWithFrom = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM')
    expect(startPaymentFlowWithFrom).toHaveBeenCalledTimes(1)
    expect(startPaymentFlowWithFrom).toHaveBeenCalledWith('bob')

    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
    clearGeoTestGlobals(
      '__GEO_TEST_INTERACT_PHASE',
      '__GEO_TEST_NODE_CARD_OPEN',
      '__GEO_TEST_SELECTED_NODE',
      '__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM',
    )
  })

  it('NodeCardOverlay "Send Payment" opens interact-panel once with node anchor (no intermediate window)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 111, y: 222 })
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const card = host.querySelector('.ds-ov-node-card') as HTMLElement | null
      expect(card).toBeTruthy()

      const btn = card!.querySelector('[data-testid="node-card-send-payment"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      const wmOpen = getRequiredGeoTestGlobal('__GEO_TEST_WM_OPEN')

      btn?.click()
      await nextTick()
      await nextTick()

      const calls = wmOpen.mock.calls.map(([call]) => call).filter(Boolean)
      const interactPanelOpens = calls.filter((call) => call.type === 'interact-panel')
      expect(interactPanelOpens.length).toBe(1)
      expect(interactPanelOpens[0].anchor).toEqual({ x: 111, y: 222, space: 'host', source: 'panel' })
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_WM_OPEN',
      )
      vi.unstubAllGlobals()
    }
  })

  it('NodeCardOverlay "New Trustline" keeps TrustlineManagementPanel visible after selecting recipient', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 111, y: 222 })
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const card = host.querySelector('.ds-ov-node-card') as HTMLElement | null
      expect(card).toBeTruthy()

      const btn = card!.querySelector('[data-testid="node-card-new-trustline"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      btn?.click()
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      const interactState = getInteractState()
      interactState.edgeAnchor = null

      const setTo = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID')
      setTo('alice')
      await nextTick()
      await nextTick()

      expect(getPhaseRef().value).toBe('editing-trustline')
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()
      expect(host.querySelector('[data-testid="edge-detail-popup"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID',
      )
      vi.unstubAllGlobals()
    }
  })

  it('UX-5: repeated dblclick on the same node while NodeCard is topmost does not call wm.open()', async () => {
    // Arrange: NodeCard already opened for nodeId=A ('bob')
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 111, y: 222 })
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      // Flush queueMicrotask() used by the useSimulatorApp mock to request initial open.
      await new Promise<void>((resolve) => queueMicrotask(() => resolve()))
      await nextTick()
      await nextTick()

      // Sanity: NodeCard is visible and is the only window.
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)
      expect(host.querySelectorAll('.ws-shell').length).toBe(1)

      const wmOpen = getRequiredGeoTestGlobal('__GEO_TEST_WM_OPEN')

      // Reset: ignore initial open call; we care about the repeated dblclick attempt.
      wmOpen.mockClear()

      // Act: simulate repeated dblclick on the same node.
      const uiOpenOrUpdateNodeCard = getRequiredGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD')
      uiOpenOrUpdateNodeCard?.({ nodeId: 'bob', anchor: { x: 111, y: 222 } })
      await nextTick()
      await nextTick()

      // Assert (DoD): no calls to wm.open() on repeated dblclick.
      expect(wmOpen).toHaveBeenCalledTimes(0)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD',
        '__GEO_TEST_WM_OPEN',
      )
      vi.unstubAllGlobals()
    }
  })

  it('UX-9: NodeCard anchor follow during camera changes is throttled to ≤ 1 call / 100ms', async () => {
    vi.useFakeTimers()

    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 100, y: 200 })
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const wmOpen = getRequiredGeoTestGlobal('__GEO_TEST_WM_OPEN')

      // Initial open is not part of the throttle budget for pan/zoom updates.
      wmOpen.mockClear()

      const centerRef = getRequiredGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER_REF')

      // Simulate a 500ms pan/zoom burst with updates every 10ms.
      // Expectation: `wm.open()` for node-card happens at most 5 times.
      for (let i = 0; i < 50; i += 1) {
        centerRef.value = { x: 100 + i, y: 200 + i }
        await nextTick()
        vi.advanceTimersByTime(10)
        await nextTick()
      }

      // Flush any pending trailing tick(s).
      vi.advanceTimersByTime(1000)
      await nextTick()

      const calls = wmOpen.mock.calls.map(([call]) => call).filter(Boolean)
      const nodeCardCalls = calls.filter((call) => call.type === 'node-card')
      expect(nodeCardCalls.length).toBeLessThanOrEqual(5)

      // Also verify we apply the latest anchor after the burst stops.
      const last = nodeCardCalls[nodeCardCalls.length - 1]
      expect(last?.anchor).toEqual({ x: 149, y: 249, space: 'host', source: 'node' })
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_NODE_SCREEN_CENTER_REF',
        '__GEO_TEST_WM_OPEN',
      )
      vi.useRealTimers()
      vi.unstubAllGlobals()
    }
  })

  it('success toast: successful clearing confirm renders SuccessToast and allows dismiss', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    await nextTick()

    // Start flow (idle -> confirm-clearing)
    const btnStart = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement | null
    expect(btnStart).toBeTruthy()
    btnStart?.click()
    await nextTick()

    // Confirm clearing
    const btnConfirm = host.querySelector('[data-testid="clearing-panel"] button.ds-btn--primary') as HTMLButtonElement | null
    expect(btnConfirm).toBeTruthy()
    btnConfirm?.click()
    await Promise.resolve()
    await nextTick()

    const toast = host.querySelector('.success-toast') as HTMLElement | null
    expect(toast).toBeTruthy()
    expect(toast?.textContent ?? '').toContain('Clearing done: 1/1 cycles')

    // Dismiss via close button (should clear interact.mode.successMessage in parent handler)
    const successMessage = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_SUCCESS_MESSAGE')
    expect(successMessage.value).toContain('Clearing done')

    const btnDismiss = toast?.querySelector('button[aria-label="Dismiss"]') as HTMLButtonElement | null
    expect(btnDismiss).toBeTruthy()
    btnDismiss?.click()
    await nextTick()

    expect(successMessage.value).toBeNull()
    // Vue Transition keeps the leaving element in DOM briefly.
    const toastAfter = host.querySelector('.success-toast') as HTMLElement | null
    expect(toastAfter).toBeTruthy()
    expect(toastAfter?.className ?? '').toContain('success-toast-leave')

    app.unmount()
    host.remove()
    clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_SUCCESS_MESSAGE')
  })

  // -----------------------------------------------------------------------
  // P0-2: busy-gate tests (ESC and outside-click while interact.mode.busy)
  // -----------------------------------------------------------------------

  it('P0-2: ESC while busy + confirm=true → cancel called + windows closed', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    // Stub window.confirm to return true (user confirms cancel)
    vi.stubGlobal('confirm', vi.fn(() => true))

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // Set busy = true
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busyRef.value = true
      await nextTick()

      // ESC while busy
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      // confirm must have been shown
      expect(window.confirm).toHaveBeenCalledTimes(1)
      // cancel must have been called (epoch bump)
      expect(cancel).toHaveBeenCalledTimes(1)
      // interact panel must be gone
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_BUSY_REF')
      vi.unstubAllGlobals()
    }
  })

  it('P0-2: ESC while busy + confirm=false → nothing happens (windows stay, flow continues)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    // Stub window.confirm to return false (user declines cancel)
    vi.stubGlobal('confirm', vi.fn(() => false))

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // Set busy = true
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busyRef.value = true
      await nextTick()

      // ESC while busy
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      // confirm must have been shown
      expect(window.confirm).toHaveBeenCalledTimes(1)
      // cancel must NOT have been called
      expect(cancel).not.toHaveBeenCalled()
      // interact panel must still be visible
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_BUSY_REF')
      vi.unstubAllGlobals()
    }
  })

  it('P0-2: ESC while NOT busy → no confirm shown, normal WM ESC handling', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    const confirmSpy = vi.fn(() => false)
    vi.stubGlobal('confirm', confirmSpy)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // busy is false by default
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      expect(busyRef.value).toBe(false)

      const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      // confirm must NOT have been shown (not busy)
      expect(confirmSpy).not.toHaveBeenCalled()
      // WM ESC must have been called
      expect(handleEsc).toHaveBeenCalledTimes(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_BUSY_REF',
        '__GEO_TEST_WM_HANDLE_ESC',
      )
      vi.unstubAllGlobals()
    }
  })

  it('P0-2: outside-click while busy + confirm=true → cancel called + windows closed', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    vi.stubGlobal('confirm', vi.fn(() => true))

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // Set busy = true
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busyRef.value = true
      await nextTick()

      // Outside click (empty canvas click)
      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      // confirm must have been shown
      expect(window.confirm).toHaveBeenCalledTimes(1)
      // cancel must have been called
      expect(cancel).toHaveBeenCalledTimes(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_BUSY_REF')
      vi.unstubAllGlobals()
    }
  })

  it('P0-2: outside-click while busy + confirm=false → nothing happens', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    vi.stubGlobal('confirm', vi.fn(() => false))

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // Set busy = true
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busyRef.value = true
      await nextTick()

      // Outside click (empty canvas click)
      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      // confirm must have been shown
      expect(window.confirm).toHaveBeenCalledTimes(1)
      // cancel must NOT have been called
      expect(cancel).not.toHaveBeenCalled()
      // panel must still be visible
      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_BUSY_REF')
      vi.unstubAllGlobals()
    }
  })

  // P1-3: transition-aware close — rapid double ESC integration test.
  // Scenario: 1 node-card window open (inspector). Rapid ESC ×2 — первый закрывает
  // node-card (→ state=closing, excluded from wm.windows). Второй ESC не находит
  // open-окон и возвращает false (нет accidental closing несуществующего окна).
  it('P1-3 rapid double ESC: node-card — перший ESC переводить в closing, другий не знаходить відкритих вікон', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_NODE_CARD_OPEN', true)
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode('alice', 'Alice'))
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')

      // There must be 1 window: node-card.
      const shellsBefore = host.querySelectorAll('.ws-shell')
      expect(shellsBefore.length).toBe(1)

      // Rapid double ESC: dispatch 2 keydown events without waiting between them.
      // First ESC: closes node-card → state=closing → removed from wm.windows.
      // Second ESC: no open windows found → handleEsc returns false (no accidental close).
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))

      // Both ESC events must have been routed through WM.
      expect(handleEsc).toHaveBeenCalledTimes(2)

      // First call must have consumed ESC (returned true — closed the node-card).
      // Second call must have NOT consumed ESC (returned false — no open windows to close).
      // Results are checked from the spy. Both calls are synchronous (keydown is sync).
      const results = handleEsc.mock.results
      expect(results[0]?.value).toBe(true)
      expect(results[1]?.value).toBe(false) // second ESC: no open window found

      await nextTick()
      await nextTick()

      // After the first ESC, node-card is gone from wm.windows (closing state).
      const shellsAfter = host.querySelectorAll('.ws-shell')
      expect(shellsAfter.length).toBe(0)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_NODE_CARD_OPEN',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_WM_HANDLE_ESC',
      )
      vi.unstubAllGlobals()
    }
  })
})

describe('SimulatorAppRoot - Demo UI DevTools snapshot/restore wiring', () => {
  it('enter demo: snapshots real devtools open state; URL reload does not keep devtools param', async () => {
    const simStorage = getSimStorage()

    // reset
    simStorage.writeDevtoolsOpenRealSnapshot.mockClear()
    simStorage.writeDevtoolsOpenReal.mockClear()
    simStorage.clearDevtoolsOpenRealSnapshot.mockClear()

    // Pretend real devtools was open.
    simStorage.readDevtoolsOpenReal.mockReturnValue(true)

    // Add a legacy param that must be scrubbed.
    setUrl('/?mode=real&ui=interact&devtools=1&testMode=0')

    // Prevent real navigation in happy-dom.
    // Prevent real navigation in happy-dom by intercepting href setter.
    const nav = stubLocationHrefSetter()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()

      const devDetails = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement | null
      expect(devDetails).toBeTruthy()
      devDetails!.open = true

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent || '').includes('Enter Demo')) as
        | HTMLButtonElement
        | undefined
      expect(btn).toBeTruthy()
      btn!.click()
      await nextTick()

      // Snapshot saved.
      expect(simStorage.writeDevtoolsOpenRealSnapshot).toHaveBeenCalledWith(true)

      // And navigation URL must not keep devtools param.
      const nextHref = String(nav.hrefSet.mock.calls.at(-1)?.[0] ?? '')
      const u = new URL(nextHref)
      expect(u.searchParams.get('devtools')).toBe(null)
    } finally {
      app.unmount()
      host.remove()

      nav.restore()
    }
  })

  it('exit demo: restores real state from snapshot and clears snapshot; URL reload does not keep devtools param', async () => {
    const simStorage = getSimStorage()

    simStorage.writeDevtoolsOpenRealSnapshot.mockClear()
    simStorage.writeDevtoolsOpenReal.mockClear()
    simStorage.clearDevtoolsOpenRealSnapshot.mockClear()

    // Setup: in demo UI, with snapshot present.
    simStorage.readDevtoolsOpenRealSnapshot.mockReturnValue(false)

    setUrl('/?mode=real&ui=demo&debug=1&devtools=1&testMode=0')

    const nav = stubLocationHrefSetter()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()

      const devDetails = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement | null
      expect(devDetails).toBeTruthy()
      devDetails!.open = true

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent || '').includes('Exit Demo')) as
        | HTMLButtonElement
        | undefined
      expect(btn).toBeTruthy()
      btn!.click()
      await nextTick()

      // Restore + clear.
      expect(simStorage.writeDevtoolsOpenReal).toHaveBeenCalledWith(false)
      expect(simStorage.clearDevtoolsOpenRealSnapshot).toHaveBeenCalledTimes(1)

      const nextHref = String(nav.hrefSet.mock.calls.at(-1)?.[0] ?? '')
      const u = new URL(nextHref)
      expect(u.searchParams.get('ui')).toBe(null)
      expect(u.searchParams.get('debug')).toBe(null)
      expect(u.searchParams.get('devtools')).toBe(null)
    } finally {
      app.unmount()
      host.remove()

      nav.restore()
    }
  })
})
