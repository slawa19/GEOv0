import { computed, createApp, h, nextTick, reactive, ref, type Component, type Ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'

import type {
  BottlenecksResponse,
  MetricsResponse,
  ParticipantInfo,
  TrustlineInfo,
} from '../api/simulatorTypes'
import type { MetricsStreamPhase } from '../composables/useMetricsPolling'
import type { GraphSnapshot } from '../types'
import type { LayoutLinkLike, LayoutNode } from '../types/layout'
import { useAppViewWiring } from '../composables/useAppViewWiring'
import { useOverlayState } from '../composables/useOverlayState'
import { keyEdge } from '../utils/edgeKey'
import type { TrustlinesFetchState } from '../composables/interact/trustlinesSourceState'
import { resolveOverlayDockStyle } from '../ui-kit/overlaySurfaceCatalog'
import { t } from '../i18n/t'
import { setUiLocale } from '../i18n/locale'
import { renderRunErrorLine, runStatusErrorLine } from '../utils/runErrorClassification'

/**
 * Layout fixture behind the mocked app: a 1000x600 viewport, two placed participants, and by
 * default the single edge between them. Same geometry as `useAppViewWiring.test.ts`, so the
 * framing a test asserts is the camera's documented framing and not an artefact of pan clamping.
 */
const TEST_LAYOUT_NODES: LayoutNode[] = [
  { id: 'alice', __x: 100, __y: 100 },
  { id: 'bob', __x: 300, __y: 300 },
]
const TEST_LAYOUT_LINK_ALICE_BOB: LayoutLinkLike = {
  __key: keyEdge('alice', 'bob'),
  source: 'alice',
  target: 'bob',
}

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
  isAnalyticsPanelOpen?: () => boolean
}

// ---------------------------------------------------------------------------
// Shared simulatorStorage stub (the preference flags SimulatorAppRoot reads and writes).
// ---------------------------------------------------------------------------
const __GEO_TEST_SIM_STORAGE = {
  readUiTheme: vi.fn<() => string | null>(() => null),
  writeUiTheme: vi.fn(),

  readAnalyticsPanelOpen: vi.fn<() => boolean | null>(() => null),
  writeAnalyticsPanelOpen: vi.fn<(isOpen: boolean) => void>(),

  // 043 S1: the "Подробнее" toggle. `null` = a first visit (Details off).
  readDetailsOpen: vi.fn<() => boolean | null>(() => null),
  writeDetailsOpen: vi.fn<(isOpen: boolean) => void>(),
  readOnboardingDismissed: vi.fn<() => boolean | null>(() => null),
  writeOnboardingDismissed: vi.fn<(dismissed: boolean) => void>(),
  // 049 S2: «Показывать события». `null` = never chosen (off).
  readEventsFeedOn: vi.fn<() => boolean | null>(() => null),
  writeEventsFeedOn: vi.fn<(on: boolean) => void>(),
} as const

// Vitest 4 widened the bare `vi.fn()` type to `Mock<Procedure | Constructable>`, which is not
// callable; this alias is the Vitest 3 `ReturnType<typeof vi.fn>` (`Mock<Procedure>`).
type TestFnMock = Mock<(...args: any[]) => any>

type GeoTestGlobals = {
  __GEO_TEST_SIM_STORAGE?: typeof __GEO_TEST_SIM_STORAGE
  __GEO_TEST_WM_OPEN?: TestFnMock
  __GEO_TEST_WM_HANDLE_ESC?: TestFnMock
  __GEO_TEST_WM_GET_TOPMOST_IN_GROUP?: TestFnMock
  __GEO_TEST_WM_SET_GEOMETRY?: TestFnMock
  __GEO_TEST_WM_RECLAMP_ALL?: TestFnMock
  __GEO_TEST_CANVAS_POINTER_DOWN?: TestFnMock
  __GEO_TEST_CANVAS_WHEEL?: TestFnMock
  __GEO_TEST_INTERACT_PHASE?: string
  __GEO_TEST_PHASE_REF?: Ref<string>
  __GEO_TEST_SELECTED_NODE?: TestSelectedNode
  __GEO_TEST_NODE_SCREEN_CENTER?: TestAnchor
  __GEO_TEST_NODE_SCREEN_CENTER_REF?: Ref<TestAnchor | null>
  __GEO_TEST_UI_OPEN_OR_UPDATE_EDGE_DETAIL?: MockUseSimulatorAppOpts['uiOpenOrUpdateEdgeDetail']
  __GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD?: MockUseSimulatorAppOpts['uiOpenOrUpdateNodeCard']
  __GEO_TEST_NODE_CARD_OPEN?: boolean
  __GEO_TEST_INTERACT_CANCEL?: TestFnMock
  __GEO_TEST_INTERACT_START_PAYMENT_FLOW?: TestFnMock
  __GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM?: TestFnMock
  __GEO_TEST_INTERACT_START_CLEARING_FLOW?: TestFnMock
  __GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID?: TestFnMock
  __GEO_TEST_INTERACT_SET_PAYMENT_TO_PID?: TestFnMock
  __GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID?: TestFnMock
  __GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID?: TestFnMock
  __GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE?: TestFnMock
  __GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CREATE?: TestFnMock
  __GEO_TEST_INTERACT_HISTORY?: Array<Record<string, unknown>>
  __GEO_TEST_INTERACT_BUSY_REF?: Ref<boolean>
  __GEO_TEST_TRUSTLINES_LOADING_REF?: Ref<boolean>
  __GEO_TEST_TRUSTLINES_LAST_ERROR_REF?: Ref<string | null>
  __GEO_TEST_TRUSTLINES_REF?: Ref<TrustlineInfo[]>
  /**
   * `F-013-7`: чем именно источник trustlines ответил, КАК СОСТОЯНИЕ. Пока мок умел только
   * `loading` + `lastError`, он не мог выразить «не спрашивали» и «ответил пусто» — то есть не мог
   * выразить ровно ту пару, которую находка называет неразличимой. `null` — состояние выводится
   * из `loading`/`lastError` (см. мок ниже), чтобы соседние тесты не переписывались.
   */
  __GEO_TEST_TRUSTLINES_FETCH_STATE_REF?: Ref<TrustlinesFetchState | null>
  __GEO_TEST_PAYMENT_TARGETS_LOADING_REF?: Ref<boolean>
  __GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF?: Ref<string | null>
  __GEO_TEST_PAYMENT_TO_TARGET_IDS_REF?: Ref<Set<string> | undefined>
  __GEO_TEST_INTERACT_STATE?: TestInteractState
  __GEO_TEST_SNAPSHOT?: GraphSnapshot | null
  __GEO_TEST_STATE_LOADING?: boolean
  __GEO_TEST_STATE_ERROR?: string

  // Analytics overlay surface (spec 007, T705).
  __GEO_TEST_ANALYTICS_METRICS_PHASE_REF?: Ref<MetricsStreamPhase>
  __GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF?: Ref<MetricsStreamPhase>
  __GEO_TEST_ANALYTICS_METRICS_REF?: Ref<MetricsResponse | null>
  __GEO_TEST_ANALYTICS_BOTTLENECKS_REF?: Ref<BottlenecksResponse | null>
  __GEO_TEST_ANALYTICS_METRICS_ERROR_REF?: Ref<string>
  __GEO_TEST_ANALYTICS_BOTTLENECKS_ERROR_REF?: Ref<string>
  __GEO_TEST_ANALYTICS_METRICS_REASON_REF?: Ref<string>
  __GEO_TEST_ANALYTICS_BOTTLENECKS_REASON_REF?: Ref<string>
  __GEO_TEST_ANALYTICS_IS_VISIBLE?: () => boolean
  __GEO_TEST_FOCUS_ON_EDGE?: TestFnMock
  /** 039 slice B: the app's re-resolution of the colour tokens (on mount, after a theme change). */
  __GEO_TEST_RESOLVE_VIZ_COLORS?: TestFnMock
  /** 043 S1 (review): the equivalent of the scene on screen, as the real app's `sceneEq` (the mock lacked the field). */
  __GEO_TEST_SCENE_EQ_REF?: Ref<string>
  /** 048 S1: the access token of the mocked app at mount (an admin token: the administrator's tools and the dock). */
  __GEO_TEST_ACCESS_TOKEN?: string
  /** 048 S1: `?debug=1` of the mocked app at mount. */
  __GEO_TEST_DEBUG_UI?: boolean

  // Camera + highlight, wired to the real composables rather than to spies, so a test can ask
  // what the graph is showing instead of asking which functions ran (spec 007, §4.2 item 4).
  __GEO_TEST_LAYOUT_LINKS?: LayoutLinkLike[]
  __GEO_TEST_CAMERA?: { panX: number; panY: number; zoom: number }
  __GEO_TEST_WORLD_TO_SCREEN?: (x: number, y: number) => { x: number; y: number }
  __GEO_TEST_ACTIVE_EDGES?: Map<string, number>
  __GEO_TEST_PRUNE_ACTIVE_EDGES?: (nowMs: number) => void
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

/** 043 S1: what the browser remembers of the "Подробнее" toggle at mount (`null`: a first visit, Details off). */
function storedDetails(open: boolean | null): void {
  getSimStorage().readDetailsOpen.mockImplementation(() => open)
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

  vi.mock('../composables/useSimulatorApp', async () => {
    // The one thing this mock does NOT re-invent: the rule that decides whether the analytics
    // surface exists. A mock that re-states a rule turns every test over it into a test of the
    // mock — which is how "never shows over fixtures" stayed green while the real-mode half of
    // the app's condition was deleted. The rule is imported, not repeated.
    const actual =
      await vi.importActual<typeof import('../composables/useSimulatorApp')>(
        '../composables/useSimulatorApp',
      )
    // 039 slice B: the real default palette (not re-stated here) and a spy on the re-resolution of the colours.
    const mockDataPalette = (await vi.importActual<typeof import('../vizMapping')>('../vizMapping')).DATA_PALETTE_DEFAULT
    const mockResolveVizColors = vi.fn()
    setGeoTestGlobal('__GEO_TEST_RESOLVE_VIZ_COLORS', mockResolveVizColors)

        return {
    useSimulatorApp: (opts?: MockUseSimulatorAppOpts) => {
      const qs = () => {
        try {
          return new URLSearchParams(window.location.search)
        } catch {
          return new URLSearchParams('')
        }
      }

      const apiMode = computed<'fixtures' | 'real'>(() => (qs().get('mode') || '').toLowerCase() === 'fixtures' ? 'fixtures' : 'real')
      // 043 S2/S3: the one screen is the server source (`useSimulatorApp.ts` isLiveUi = isRealMode); `ui=interact` and
      // `ui=demo` are ignored by the product.
      const isLiveUi = computed(() => apiMode.value === 'real')
      const isTestMode = computed(() => {
        // Default to true for most interaction tests; `testMode=0` overrides it.
        const v = (qs().get('testMode') || '').toLowerCase().trim()
        return !(v === '0' || v === 'false')
      })

      const phase = ref(String(getGeoTestGlobal('__GEO_TEST_INTERACT_PHASE') ?? 'idle'))
      setGeoTestGlobal('__GEO_TEST_PHASE_REF', phase)
      // 043 S1 (review): `sceneEq` is a field of the real `SimulatorAppApi` that this mock never had (it rendered as
      // undefined); a ref the tests can move, defaulting to the fixtures' UAH.
      const sceneEqRef = ref('UAH')
      setGeoTestGlobal('__GEO_TEST_SCENE_EQ_REF', sceneEqRef)

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

       const interactBusy = ref(false)
       setGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF', interactBusy)

       const selectEdge = vi.fn((edgeKey: string, anchor?: TestAnchor | null) => {
         if (interactBusy.value) return false
         interactState.selectedEdgeKey = edgeKey
         interactState.edgeAnchor = anchor ?? null
         const [fromPid, toPid] = edgeKey.split('→')
         interactState.fromPid = fromPid || null
         interactState.toPid = toPid || null
         phase.value = 'editing-trustline'
         return true
       })

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

       // `F-013-7` / внешнее ревью 013: создание линии — единственное действие, которое ответ
       // «линии у этой пары нет» РАЗРЕШАЕТ. Чтобы это судилось по вызову, а не только по виду
       // кнопки, мок отдаёт его наружу так же, как закрытие.
       const confirmTrustlineCreate = vi.fn(async () => undefined)
       setGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CREATE', confirmTrustlineCreate)
       const historyList = reactive<Array<Record<string, unknown>>>(getGeoTestGlobal('__GEO_TEST_INTERACT_HISTORY') ?? [])

      const trustlinesLoading = ref(false)
      setGeoTestGlobal('__GEO_TEST_TRUSTLINES_LOADING_REF', trustlinesLoading)

      // `F-013-7`: the REST trustlines cache has three states, not two.  The root reads the error
      // through `interact.mode.trustlinesLastError?.value`, so without this ref the mock could
      // only ever express "settled and empty" -- the exact conflation the finding is about.
      const trustlinesLastError = ref<string | null>(null)
      setGeoTestGlobal('__GEO_TEST_TRUSTLINES_LAST_ERROR_REF', trustlinesLastError)

      const trustlines = ref<TrustlineInfo[]>([])
      setGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF', trustlines)

      // `F-013-7`. `trustlines` выше — СЛИТЫЙ список: в проде это `fetched ?? снапшот`, и по нему
      // нельзя узнать, кто именно его наполнил. Состояние источника — отдельная величина; тест,
      // которому нужно «не спрашивали» или «ответил пусто», выставляет её явно, а по умолчанию она
      // выводится из тех же двух флагов, что и раньше, чтобы поведение остальных тестов не менялось.
      const trustlinesFetchStateOverride = ref<TrustlinesFetchState | null>(null)
      setGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF', trustlinesFetchStateOverride)
      const trustlinesFetchState = computed<TrustlinesFetchState>(() => {
        const override = trustlinesFetchStateOverride.value
        if (override) return override
        if (trustlinesLoading.value) return { kind: 'loading' }
        const err = trustlinesLastError.value
        if (err) return { kind: 'failed', message: err }
        return { kind: 'answered' }
      })
      // Когда источник ответил, слитый список И ЕСТЬ его ответ; когда не ответил — ответа нет
      // вовсе, и найденная в списке строка приехала из снапшота.
      const findAnsweredTrustline = (from: string | null, to: string | null): TrustlineInfo | null => {
        if (trustlinesFetchState.value.kind !== 'answered') return null
        if (!from || !to) return null
        return trustlines.value.find((tl) => tl.from_pid === from && tl.to_pid === to) ?? null
      }

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
        if (!isLiveUi.value) return false
        const p = phase.value
        return (
          p === 'picking-payment-from' ||
          p === 'picking-payment-to' ||
          p === 'picking-trustline-from' ||
          p === 'picking-trustline-to'
        )
      })

      // --- camera + active-edge highlight: real composables, not spies ---------------------
      //
      // `focusOnEdge` and `addActiveEdge` are the two halves of "show me this bottleneck", and
      // both are only meaningful as state. Stubbing them would make the tests below assert that
      // the component called something; wiring the real ones makes them assert that the camera
      // moved and that the graph is highlighting that edge — and, crucially, lets the real
      // wiring decide on its own that an edge is absent, instead of a test declaring it absent.
      const fxOverlayState = useOverlayState({
        getLayoutNodeById: (id: string) => TEST_LAYOUT_NODES.find((n) => n.id === id),
        sizeForNode: () => ({ w: 40, h: 40 }),
        getCameraZoom: () => 1,
        resetFxState: () => undefined,
        nowMs: () => 0,
      })
      setGeoTestGlobal('__GEO_TEST_ACTIVE_EDGES', fxOverlayState.activeEdges)
      setGeoTestGlobal('__GEO_TEST_PRUNE_ACTIVE_EDGES', fxOverlayState.pruneActiveEdges)

      const viewWiringSelectedNodeId = ref<string | null>(null)
      const viewWiring = useAppViewWiring({
        canvasEl: ref(null),
        hostEl: ref(null),

        getLayoutNodes: () => TEST_LAYOUT_NODES,
        getLayoutW: () => 1000,
        getLayoutH: () => 600,
        isTestMode: () => false,

        setClampCameraPan: () => undefined,

        selectedNodeId: viewWiringSelectedNodeId,
        setSelectedNodeId: (id) => {
          viewWiringSelectedNodeId.value = id
        },

        getNodeById: () => null,
        getLayoutNodeById: (id) => TEST_LAYOUT_NODES.find((n) => n.id === id) ?? null,

        getLayoutLinks: () =>
          getGeoTestGlobal('__GEO_TEST_LAYOUT_LINKS') ?? [TEST_LAYOUT_LINK_ALICE_BOB],
      })
      setGeoTestGlobal('__GEO_TEST_CAMERA', viewWiring.camera)
      setGeoTestGlobal('__GEO_TEST_WORLD_TO_SCREEN', viewWiring.worldToScreen)

      return {
        apiMode,

        // flags
        isDemoFixtures: computed(() => false),
        isLiveUi,
        // 048 S1: `?debug=1` (the real app reads it once per page; a test sets the global before the mount).
        isDebugUi: Boolean(getGeoTestGlobal('__GEO_TEST_DEBUG_UI') ?? false),
        isTestMode,
        isWebDriver: computed(() => false),
        isE2eScreenshots: computed(() => false),

        // real mode state (minimal)
        real: reactive({
          loadingScenarios: false,
          scenarios: [],
          selectedScenarioId: '',
          intensityPercent: 0,
          // 043 S2 (review B4): the one screen has a run to act on; without one the actions are disabled. Tests of the
          // "no run" state set it to null themselves.
          runId: 'run-mock' as string | null,
          runStatus: null as Record<string, unknown> | null,
          sseState: 'idle',
          lastError: '',
          runStats: {},
          artifacts: [],
          artifactsLoading: false,
          accessToken: String(getGeoTestGlobal('__GEO_TEST_ACCESS_TOKEN') ?? ''),
        }),
        realActions: {
          setApiBase: vi.fn(),
          setAccessToken: vi.fn(),
          setSelectedScenarioId: vi.fn(),
          setIntensityPercent: vi.fn(),
          startRun: vi.fn(),
          pause: vi.fn(),
          pauseForManualAction: vi.fn(),
          resume: vi.fn(),
          stop: vi.fn(),
          applyIntensity: vi.fn(),
          refreshSnapshot: vi.fn(),
        },

        // §10: Cookie session state (anonymous visitors)
        session: {
          actorKind: ref<string | null>(null),
          ownerId: ref<string | null>(null),
          bootstrapping: ref(false),
          tryEnsure: vi.fn(),
        },

        // 048 S2: the attach by id is all that is left of the admin facade; the root does not read it.
        admin: {
          attachRun: vi.fn(),
        },

        // state + prefs
        state: reactive({
          loading: Boolean(getGeoTestGlobal('__GEO_TEST_STATE_LOADING') ?? false),
          error: String(getGeoTestGlobal('__GEO_TEST_STATE_ERROR') ?? ''),
          sourcePath: '',
          snapshot: getGeoTestGlobal('__GEO_TEST_SNAPSHOT') ?? null,
          selectedNodeId: null as string | null,
        }),
        eq: ref('UAH'),
        sceneEq: computed(() => sceneEqRef.value),
        // 048 S1: a field of the real `SimulatorAppApi` the mock lacked; the bottom bar now reads it in the root.
        equivalentOptions: computed(() => ['UAH']),
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
               trustlinesLastError,
               trustlinesFetchState,
               findAnsweredTrustline,
               paymentTargetsLoading,
               paymentTargetsLastError,
               state: interactState,

             availableCapacity: ref('0'),
              paymentToTargetIds,
              participants: ref<ParticipantInfo[]>([]),
              trustlines,
              canSendPayment: ref(true),

            setPaymentFromPid,
            setPaymentToPid,
              setTrustlineFromPid,
              setTrustlineToPid,
             selectTrustline: vi.fn(),
             selectEdge,

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
             retryPayment: vi.fn(async () => undefined),
             dismissPaymentResult: vi.fn(),
             discardUnresolvedPayment: vi.fn(),
             paymentOutcome: computed(() => null),
             paymentTargetEstimate: computed(() => null),
             confirmTrustlineCreate,
             confirmTrustlineUpdate: vi.fn(async () => undefined),
             confirmTrustlineClose,
              confirmClearing: vi.fn(async () => undefined),
              cancel,
              history: historyList,
              // 049 S2: the root shows `shownHistory` (own lines; own and network with «Показывать события» on).
              shownHistory: computed(() => historyList),
              feed: [],
              pushFeedItem: vi.fn(),
            },
          systemBalance: computed(() => ({
            totalUsed: 0,
            totalAvailable: 0,
            activeTrustlines: 0,
            activeParticipants: 0,
            utilization: 0,
          })),
        },

        // env
        gpuAccelLikely: computed(() => false),

        // 039 slice B: the resolved data colours and their re-resolution (mount, theme change).
        dataPalette: ref(mockDataPalette),
        resolveVizColors: mockResolveVizColors,

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
        e2e: {
          runTxOnce: vi.fn(),
          runClearingOnce: vi.fn(),
        },

        // selection + overlays
        hoveredEdge: reactive({ key: null, fromId: '', toId: '', amountText: '' }),
        clearHoveredEdge: vi.fn(),
        addActiveEdge: fxOverlayState.addActiveEdge,
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
          // P0-2 (mirrors the real Step0 policy, 046 wave 3): while busy an outside click is ignored - no question, no cancel.
          const busyRef = getGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
          if (busyRef?.value === true) return

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

        // analytics overlay surface (spec 007, T705)
        //
        // `isVisible` IS the production rule — `__analyticsPanelVisibilityPolicy`, imported above
        // — applied to the two facts this mock does synthesise: the mode taken from the query
        // string, and the toggle owned by the mount point. Stream state is exposed per stream,
        // through refs, so a test can drive `/metrics` and `/bottlenecks` independently and watch
        // what reaches the surface.
        analytics: (() => {
          const analyticsMetricsPhase = ref<MetricsStreamPhase>('idle')
          const analyticsBottlenecksPhase = ref<MetricsStreamPhase>('idle')
          const analyticsMetrics = ref<MetricsResponse | null>(null)
          const analyticsBottlenecks = ref<BottlenecksResponse | null>(null)
          const analyticsMetricsError = ref('')
          const analyticsBottlenecksError = ref('')
          const analyticsMetricsUnavailableReason = ref('')
          const analyticsBottlenecksUnavailableReason = ref('')

          setGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_PHASE_REF', analyticsMetricsPhase)
          setGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF', analyticsBottlenecksPhase)
          setGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_REF', analyticsMetrics)
          setGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF', analyticsBottlenecks)
          setGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_ERROR_REF', analyticsMetricsError)
          setGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_ERROR_REF', analyticsBottlenecksError)
          setGeoTestGlobal(
            '__GEO_TEST_ANALYTICS_METRICS_REASON_REF',
            analyticsMetricsUnavailableReason,
          )
          setGeoTestGlobal(
            '__GEO_TEST_ANALYTICS_BOTTLENECKS_REASON_REF',
            analyticsBottlenecksUnavailableReason,
          )

          const isVisible = computed(() =>
            actual.__analyticsPanelVisibilityPolicy(
              apiMode.value === 'real',
              Boolean(opts?.isAnalyticsPanelOpen?.()),
            ),
          )
          setGeoTestGlobal('__GEO_TEST_ANALYTICS_IS_VISIBLE', () => isVisible.value)

          return {
            isVisible,
            metricsPhase: computed(() => analyticsMetricsPhase.value),
            bottlenecksPhase: computed(() => analyticsBottlenecksPhase.value),
            metrics: analyticsMetrics,
            bottlenecks: analyticsBottlenecks,
            metricsError: analyticsMetricsError,
            bottlenecksError: analyticsBottlenecksError,
            metricsUnavailableReason: analyticsMetricsUnavailableReason,
            bottlenecksUnavailableReason: analyticsBottlenecksUnavailableReason,
            isPolling: computed(() => false),
          }
        })(),

        // helpers for template
        getNodeById: (id: string | null) => {
          const n = getGeoTestGlobal('__GEO_TEST_SELECTED_NODE') ?? null
          if (!id || !n) return null
          return String(n.id) === String(id) ? n : null
        },
        focusOnEdge: (() => {
          const fn = vi.fn((fromId: string, toId: string) => viewWiring.focusOnEdge(fromId, toId))
          setGeoTestGlobal('__GEO_TEST_FOCUS_ON_EDGE', fn)
          return fn
        })(),
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

  it('043 S2 (B2): an ActionBar-initiated window is anchored in the right rail (host width), under the top stack', async () => {
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

      // 046 wave 1, full (2026-10-19): by test id - the accessible name is the dictionary's, in the interface language.
      const actionBar = host.querySelector('[data-testid="action-bar"]') as HTMLElement | null
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

      // 043 S2 (owner's decision on B2, mockup-s3): the right rail - the anchor is the host's right edge (=1400), and the
      // window manager clamps the window inside it. Until then the guard held the opposite (near the ActionBar's left).
      expect(Number(interactAnchor?.x)).toBe(1400)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  it('043 S1: with Details on, an ActionBar panel opens under the whole top stack, not over its Details rows', async () => {
    storedDetails(true)
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()
      const root = host.querySelector('.root') as HTMLElement
      stubRect(root, { left: 0, top: 0, width: 1400, height: 800 })
      const actionBar = host.querySelector('[data-testid="action-bar"]') as HTMLElement
      stubRect((actionBar.querySelector('.hud-bar') as HTMLElement | null) ?? actionBar, { left: 400, top: 14, width: 404, height: 40 })
      const stack = host.querySelector('.ds-ov-top-stack') as HTMLElement
      expect(stack.contains(actionBar), 'counter-check: the ActionBar is a row of the stack').toBe(true)
      stubRect(stack, { left: 14, top: 14, width: 1372, height: 132 })

      host.querySelector<HTMLButtonElement>('[data-testid="actionbar-payment"]')!.click()
      await nextTick()
      await nextTick()
      const calls = getGeoTestGlobal('__GEO_TEST_WM_OPEN')!.mock.calls.map((c) => c[0] as { type?: string; anchor?: { x: number; y: number } })
      const anchor = [...calls].reverse().find((c) => c?.type === 'interact-panel')?.anchor
      expect(anchor, 'the panel was opened with an anchor').toBeTruthy()
      expect(anchor!.x, '043 S2 (B2): the right rail (the host width)').toBe(1400)
      expect(anchor!.y, 'under the stack (146), not under the first row (54)').toBe(146)
    } finally {
      app.unmount()
      host.remove()
      storedDetails(null)
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
      vi.restoreAllMocks()
    }
  })

  /**
   * 046 wave 1, full (2026-10-19): the root finds the ActionBar to anchor a window by its test id, not by its accessible
   * name - in Russian that name is «Действия», and a lookup by the English «Interact actions» found nothing and fell
   * back to the stack's published height. Observed by the anchor: the bar is stubbed BELOW the stack, so only a found bar
   * gives its bottom (300). Mutation (the lookup by the English name) - red here, green in the English tests above.
   */
  it('046 wave 1: in Russian the window is anchored under the ActionBar the root found (by test id, not by its name)', async () => {
    setUiLocale('ru')
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()
      stubRect(host.querySelector('.root') as HTMLElement, { left: 0, top: 0, width: 1400, height: 800 })
      const actionBar = host.querySelector('[data-testid="action-bar"]') as HTMLElement
      expect(actionBar.getAttribute('aria-label'), 'precondition: the name is Russian').toBe('Действия')
      stubRect((actionBar.querySelector('.hud-bar') as HTMLElement | null) ?? actionBar, { left: 400, top: 260, width: 404, height: 40 })
      stubRect(host.querySelector('.ds-ov-top-stack') as HTMLElement, { left: 14, top: 14, width: 1372, height: 132 })
      host.querySelector<HTMLButtonElement>('[data-testid="actionbar-payment"]')!.click()
      await nextTick()
      await nextTick()
      const calls = getGeoTestGlobal('__GEO_TEST_WM_OPEN')!.mock.calls.map((c) => c[0] as { type?: string; anchor?: { x: number; y: number } })
      const anchor = [...calls].reverse().find((c) => c?.type === 'interact-panel')?.anchor
      expect(anchor, 'the panel was opened with an anchor').toBeTruthy()
      expect(anchor!.y, 'under the ActionBar the root found (its bottom 300), not the fallback').toBe(300)
    } finally {
      app.unmount()
      host.remove()
      setUiLocale('en')
      window.localStorage.clear()
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
      expect(host.querySelectorAll('canvas[aria-hidden="true"]')).toHaveLength(2)

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

  it('keeps confirm-payment interact-panel on payment policy width after shell measurement and viewport resize reclamp', async () => {
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
      expect(interactShell!.style.width).toBe('440px')
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

      expect(interactShell!.style.width).toBe('440px')
      expect(Number.parseInt(interactShell!.style.left, 10)).toBe(initialLeft)

      viewportWidth = 920
      viewportRecord!.callback([], viewportRecord!.instance as ResizeObserver)
      await nextTick()

      expect(interactShell!.style.width).toBe('440px')
      expect(interactShell!.style.left).toBe('464px')
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
      // 046 wave 3 (2026-10-19): a history line is kept as its key and data (BACKLOG 046-3).
      { id: '1', icon: 'P', line: { key: 'history.payment', vars: { amount: '1.00', unit: 'UAH', from: 'Alice', to: 'Bob' } }, timeText: '10:00' },
      { id: '2', icon: 'T', line: { key: 'history.trustlineUpdated', vars: { from: 'Alice', to: 'Bob', limit: '20.00', unit: 'UAH' } }, timeText: '10:01' },
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
      expect(historyOverlay?.querySelector('[data-testid="recent-actions"] .interact-history__header')?.textContent).toBe('Recent actions')

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

  it('043 S2: one screen - no mode segment; the ActionBar, the payment panel and (under Details) the run controls with the intensity', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    // The old Interact address: `ui=interact` is ignored, the same screen opens.
    setUrl('/?mode=real&ui=interact')
    storedDetails(true)
    // 048 S2 (amendment 8): the run controls and the tempo are the administrator's row - an admin token (read at the mount).
    setGeoTestGlobal('__GEO_TEST_ACCESS_TOKEN', 'admin-token')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    clearGeoTestGlobals('__GEO_TEST_ACCESS_TOKEN')
    await nextTick()

    // No mode segment any more (decision 2), with Details on as well.
    expect(host.querySelectorAll('button.ds-segment'), 'the Sandbox / Auto-Run / Interact segment is gone').toHaveLength(0)

    // ActionBar presence
    expect(host.querySelector('[data-testid="actionbar-payment"]')).toBeTruthy()
    // Phase panel presence
    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

    // The Interact contract "intensity forced to 0, no intensity control" is gone (decision 3 replaces it with the pause
    // on a manual action): the run controls and the intensity are under Details on the one screen.
    expect(host.querySelector('[data-testid="intensity-input"]'), 'the intensity control is under Details').toBeTruthy()
    expect(host.querySelector('select[data-testid="scenario-select"]'), 'the scenario selector is under Details').toBeTruthy()

    app.unmount()
    host.remove()
    storedDetails(null)
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
      // 045 S2: the row is [Cancel][Confirm] in the DOM as on screen; the first focus is still Confirm (`data-initial-focus`).
      expect(document.activeElement).toBe(confirmBtn)

      // Confirm is now the LAST focusable: Tab wraps to Cancel, Shift+Tab from Cancel wraps back to Confirm.
      confirmBtn?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
      await nextTick()
      expect(document.activeElement).toBe(cancelBtn)

      cancelBtn?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true }))
      await nextTick()
      expect(document.activeElement).toBe(confirmBtn)

      cancelBtn?.click()
      await nextTick()
      await nextTick()
      expect(getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')).toHaveBeenCalledTimes(1)
      expect(document.activeElement).toBe(opener)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  it('restores keyboard focus to the ActionBar opener after a successful interact flow closes', async () => {
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
      opener?.click()
      await nextTick()
      await nextTick()

      const confirmBtn = host.querySelector('[data-testid="clearing-panel"] button.ds-btn--primary') as
        | HTMLButtonElement
        | null
      expect(confirmBtn).toBeTruthy()
      confirmBtn?.focus()
      expect(document.activeElement).toBe(confirmBtn)

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      getPhaseRef().value = 'idle'
      await nextTick()
      await nextTick()
      expect(document.activeElement).not.toBe(opener)

      busy.value = false
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeNull()
      expect(document.activeElement).toBe(opener)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE')
      vi.unstubAllGlobals()
    }
  })

  it('does not steal focus from an unrelated control when a successful interact flow closes', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()

    const host = document.createElement('div')
    const unrelated = document.createElement('button')
    unrelated.textContent = 'Unrelated control'
    document.body.append(host, unrelated)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      const opener = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement | null
      opener?.click()
      await nextTick()
      await nextTick()

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      getPhaseRef().value = 'idle'
      await nextTick()
      unrelated.focus()
      expect(document.activeElement).toBe(unrelated)

      busy.value = false
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeNull()
      expect(document.activeElement).toBe(unrelated)
    } finally {
      app.unmount()
      host.remove()
      unrelated.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE')
      vi.unstubAllGlobals()
    }
  })

  it('opens node and edge inspectors from the labelled DOM graph navigator', async () => {
    // 043 S1: "Inspect graph" is a button of the bottom row, behind "Подробнее".
    storedDetails(true)
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SELECTED_NODE', makeSelectedNode())
    setGeoTestGlobal('__GEO_TEST_NODE_SCREEN_CENTER', { x: 100, y: 160 })
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', {
      equivalent: 'UAH',
      generated_at: '2026-08-09T00:00:00Z',
      nodes: [makeSelectedNode('alice', 'Alice'), makeSelectedNode('bob', 'Bob')],
      links: [{ source: 'alice', target: 'bob', trust_limit: '100', used: '25', available: '75' }],
    })
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // 046 wave 1, full (2026-10-19): by test id - the accessible name is the dictionary's, in the interface language.
      const navigator = host.querySelector('[role="region"][data-testid="graph-navigator"]') as HTMLElement | null
      expect(navigator).toBeTruthy()
      const disclosure = navigator?.querySelector('details') as HTMLDetailsElement | null
      expect(disclosure).toBeTruthy()
      disclosure!.open = true

      const nodeSelect = host.querySelector('#graph-navigator-node') as HTMLSelectElement
      nodeSelect.value = 'bob'
      nodeSelect.dispatchEvent(new Event('change', { bubbles: true }))
      await nextTick()
      const nodeButton = host.querySelector<HTMLButtonElement>('[data-testid="graph-navigator-open-node"]') ?? undefined
      expect(nodeButton, 'counter-check: the button is found').toBeTruthy()
      nodeButton?.focus()
      nodeButton?.click()
      await nextTick()
      await nextTick()
      expect(host.querySelector('.ws-shell[data-win-type="node-card"]')).toBeTruthy()
      expect(host.querySelector('[data-testid="graph-navigator-status"]')?.textContent).toContain('Participant opened: Bob')

      const edgeButton = host.querySelector<HTMLButtonElement>('[data-testid="graph-navigator-open-edge"]') ?? undefined
      expect(edgeButton, 'counter-check: the button is found').toBeTruthy()
      expect(edgeButton?.disabled).toBe(false)
      edgeButton?.focus()
      edgeButton?.click()
      await nextTick()
      await nextTick()
      expect(host.querySelector('.ws-shell[data-win-type="edge-detail"]')).toBeTruthy()
      expect(host.querySelector('[data-testid="graph-navigator-status"]')?.textContent).toContain(
        'Line opened: Alice → Bob', // 044 `T4405`: names, not ids
      )

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      await nextTick()
      expect(edgeButton?.disabled).toBe(true)
      const selectedEdgeBeforeRejectedClick = getInteractState().selectedEdgeKey
      edgeButton?.click()
      await nextTick()
      expect(getInteractState().selectedEdgeKey).toBe(selectedEdgeBeforeRejectedClick)
      expect(host.querySelectorAll('.ws-shell[data-win-type="edge-detail"]')).toHaveLength(1)
      // 043 S1: it is a button of the bottom row now, not a panel under the top bar.
      expect(navigator!.closest('.ds-ov-bottom'), 'the navigator is in the bottom bar').not.toBeNull()
      expect(navigator!.closest('.ds-ov-top'), 'and not in the top bar').toBeNull()
    } finally {
      app.unmount()
      host.remove()
      storedDetails(null)
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_SELECTED_NODE',
        '__GEO_TEST_NODE_SCREEN_CENTER',
        '__GEO_TEST_SNAPSHOT',
      )
      vi.unstubAllGlobals()
    }
  })

  it('exposes root loading and error lifecycle semantics without duplicating toast roles', async () => {
    setGeoTestGlobal('__GEO_TEST_STATE_LOADING', true)
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()

    const loadingHost = document.createElement('div')
    document.body.appendChild(loadingHost)
    const loadingApp = mountSimulatorAppRoot(loadingHost)
    await nextTick()
    expect(loadingHost.querySelector('.root')?.getAttribute('aria-busy')).toBe('true')
    const loading = loadingHost.querySelector('.ds-ov-inset[role="status"]')
    expect(loading?.getAttribute('aria-live')).toBe('polite')
    expect(loading?.getAttribute('aria-atomic')).toBe('true')
    loadingApp.unmount()
    loadingHost.remove()
    clearGeoTestGlobals('__GEO_TEST_STATE_LOADING')

    // 048 S3 (decision 3): ONE place of a scene-load error - the overlay over the canvas, in both sources; one sentence, no
    // «Error» title. The run's error line carries no copy (the holder's side: `useSimulatorApp.sceneLoadFailure.p034.test.ts`).
    setGeoTestGlobal('__GEO_TEST_STATE_ERROR', 'Snapshot unavailable')
    const errorHost = document.createElement('div')
    document.body.appendChild(errorHost)
    const errorApp = mountSimulatorAppRoot(errorHost)
    await nextTick()
    const error = errorHost.querySelector('[data-testid="scene-error"]')
    expect(error?.getAttribute('role')).toBe('alert')
    expect(error?.getAttribute('aria-atomic')).toBe('true')
    expect(error?.textContent).toContain('Snapshot unavailable')
    expect(error?.textContent, 'no «Error» title over the sentence').not.toMatch(/\bError\b|Ошибка/)
    errorApp.unmount()
    errorHost.remove()

    setUrl('/?mode=fixtures&ui=interact')
    const offlineHost = document.createElement('div')
    document.body.appendChild(offlineHost)
    const offlineApp = mountSimulatorAppRoot(offlineHost)
    await nextTick()
    await nextTick()
    const overlay = offlineHost.querySelector('[data-testid="scene-error"]')
    expect(overlay?.getAttribute('role')).toBe('alert')
    expect(overlay?.getAttribute('aria-atomic')).toBe('true')
    expect(overlay?.textContent).toContain('Snapshot unavailable')
    expect(overlay?.textContent, 'no «Error» title over the sentence').not.toMatch(/\bError\b|Ошибка/)
    offlineApp.unmount()
    offlineHost.remove()
    clearGeoTestGlobals('__GEO_TEST_STATE_ERROR')
    vi.unstubAllGlobals()
  })

  // 034 S8, review C3 (the half F-034-D2 is named after): with no snapshot the debts are UNKNOWN, not zero. The
  // balance bar computes zeros and "clean" for a null snapshot, so it is not shown until a snapshot exists - the
  // same as while loading. Not judged here: the wording of the error (the existing error surface shows it).
  it('a failed first load shows no balance bar (no "clean", no zero Total Debt); a loaded snapshot shows it', async () => {
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()
    setGeoTestGlobal('__GEO_TEST_STATE_ERROR', 'HTTP 503 Service Unavailable for /simulator/scenarios/sc1/graph/preview')

    const failedHost = document.createElement('div')
    document.body.appendChild(failedHost)
    const failedApp = mountSimulatorAppRoot(failedHost)
    await nextTick()
    // 048 S3: the drawn text is the scene state's text as it is (here a test global; the holder makes it a sentence).
    expect(failedHost.querySelector('[data-testid="scene-error"]')?.textContent).toContain('HTTP 503')
    expect(failedHost.querySelector('.system-balance-bar'), 'a balance is shown for data that was never read').toBeNull()
    expect(failedHost.textContent).not.toContain('System is clean')
    failedApp.unmount()
    failedHost.remove()
    clearGeoTestGlobals('__GEO_TEST_STATE_ERROR')

    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', {
      equivalent: 'UAH',
      generated_at: '2026-10-11T00:00:00Z',
      nodes: [makeSelectedNode('alice', 'Alice'), makeSelectedNode('bob', 'Bob')],
      links: [{ source: 'alice', target: 'bob', trust_limit: '100', used: '25', available: '75' }],
    })
    const loadedHost = document.createElement('div')
    document.body.appendChild(loadedHost)
    const loadedApp = mountSimulatorAppRoot(loadedHost)
    await nextTick()
    expect(loadedHost.querySelector('.system-balance-bar'), 'anti-vacuum: with a snapshot the bar is shown').not.toBeNull()
    loadedApp.unmount()
    loadedHost.remove()
    clearGeoTestGlobals('__GEO_TEST_SNAPSHOT')
    vi.unstubAllGlobals()
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

      // 046 wave 1, full (2026-10-19): by test id - the accessible name is the dictionary's, in the interface language.
      const btn = host.querySelector<HTMLButtonElement>('[data-testid="actionbar-clearing"]') ?? undefined
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

      // 043 S2 (external review 2026-10-17): the full editor is a manual action without a flow transition - the root asks
      // for the pause itself.
      const actions = (app as unknown as { _instance: { subTree: { component: { setupState: Record<string, any> } } } })._instance
        .subTree.component.setupState.realActions as Record<string, Mock>
      actions.pauseForManualAction.mockClear()

      btn?.focus()
      expect(document.activeElement).toBe(btn)
      btn?.click()
      await nextTick()
      await nextTick()
      expect(actions.pauseForManualAction, 'Change Limit pauses the run').toHaveBeenCalledTimes(1)

      // Edge detail must be closed; trustline panel must be visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      expect(host.querySelectorAll('[data-testid="trustline-panel"]').length).toBe(1)

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      getPhaseRef().value = 'idle'
      await nextTick()
      busy.value = false
      await nextTick()
      await nextTick()
      expect(document.activeElement).toBe(host.querySelector('[data-testid="actionbar-trustline"]'))
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL')
      vi.unstubAllGlobals()
    }
  })

  // 043 S2 review round (Gemini C1/C2, 2026-10-17): the root's own entries of a manual action - the ones that are not a
  // flow transition of the composable - ask for the pause themselves.
  const rootActions = (app: ReturnType<typeof mountSimulatorAppRoot>) =>
    (app as unknown as { _instance: { subTree: { component: { setupState: Record<string, any> } } } })._instance.subTree.component.setupState

  it('043 S2 (C2): opening a node card (double click, the dev hook, the navigator) pauses, also for the node already selected', async () => {
    setUrl('/?mode=real')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.append(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      const actions = rootActions(app).realActions as Record<string, Mock>
      actions.pauseForManualAction.mockClear()
      const open = getRequiredGeoTestGlobal('__GEO_TEST_UI_OPEN_OR_UPDATE_NODE_CARD')
      open?.({ nodeId: 'bob', anchor: { x: 111, y: 222 } })
      open?.({ nodeId: 'bob', anchor: { x: 111, y: 222 } })
      expect(actions.pauseForManualAction, 'each opening of a card is an act').toHaveBeenCalledTimes(2)
    } finally {
      app.unmount()
      host.remove()
      vi.unstubAllGlobals()
    }
  })

  it('043 S2 (C1): "Inspect graph" -> a node selects it (the same selection path as a click) and pauses', async () => {
    setUrl('/?mode=real')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.append(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      const setup = rootActions(app)
      const actions = setup.realActions as Record<string, Mock>
      actions.pauseForManualAction.mockClear()
      setup.inspectNodeFromNavigator('bob')
      expect((setup.state as { selectedNodeId: string | null }).selectedNodeId, 'the node is selected').toBe('bob')
      expect(actions.pauseForManualAction).toHaveBeenCalled()
    } finally {
      app.unmount()
      host.remove()
      vi.unstubAllGlobals()
    }
  })

  it('043 S2 (B4): without a run the three actions are disabled, with their reason; with a run they are enabled', async () => {
    setUrl('/?mode=real')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.append(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      const real = rootActions(app).real as { runId: string | null }
      const buttons = () => ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing'].map((id) => host.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`)!)
      expect(buttons().map((b) => b.disabled), 'a run: enabled').toEqual([false, false, false])
      real.runId = null
      await nextTick()
      expect(buttons().map((b) => b.disabled), 'no run: disabled').toEqual([true, true, true])
      // 048 S2 hand-over P2-2 (046 wave 1, full): the reason names the way back every viewer has (a reload), not Start.
      expect(buttons()[0]!.title).toBe(t('actions.noRun'))
      expect(buttons()[0]!.title).not.toMatch(/\bStart\b/)
    } finally {
      app.unmount()
      host.remove()
      vi.unstubAllGlobals()
    }
  })

  it('043 S2 (C2): "Close line" of a line\'s popup pauses', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.append(host)
    const app = mountSimulatorAppRoot(host)
    try {
      answerWithTrustlineForSelectedPair()
      await nextTick()
      await nextTick()
      const actions = rootActions(app).realActions as Record<string, Mock>
      actions.pauseForManualAction.mockClear()
      const closeLine = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
      closeLine?.click()
      await nextTick()
      closeLine?.click()
      expect(getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE')).toHaveBeenCalledOnce()
      expect(actions.pauseForManualAction, 'Close line pauses').toHaveBeenCalledTimes(1)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE')
      vi.unstubAllGlobals()
    }
  })

  it('restores Close Line focus to the trustline action after the original edge detail closes', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.append(host)
    const app = mountSimulatorAppRoot(host)
    try {
      // Закрывать можно СУЩЕСТВУЮЩУЮ линию: без этого основание — `no-row`, и кнопка выключена
      // по существу дела, а не по тому, что судит этот тест (внешнее ревью 013, P2).
      answerWithTrustlineForSelectedPair()
      await nextTick()
      await nextTick()

      const closeLine = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
      expect(closeLine?.disabled).toBe(false)
      closeLine?.focus()
      closeLine?.click()
      await nextTick()
      closeLine?.click()
      expect(getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE')).toHaveBeenCalledOnce()

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      getPhaseRef().value = 'idle'
      await nextTick()
      busy.value = false
      await nextTick()
      await nextTick()

      expect(document.activeElement).toBe(host.querySelector('[data-testid="actionbar-trustline"]'))
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE')
      vi.unstubAllGlobals()
    }
  })

  it('does not capture body as the strict Close Line owner or steal later focus', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()

    const host = document.createElement('div')
    const otherOwner = document.createElement('div')
    otherOwner.dataset.winType = 'edge-detail'
    const otherButton = document.createElement('button')
    otherOwner.appendChild(otherButton)
    document.body.append(host, otherOwner)
    const app = mountSimulatorAppRoot(host)
    try {
      // См. соседний тест: закрывать можно только подтверждённую бэкендом линию.
      answerWithTrustlineForSelectedPair()
      await nextTick()
      await nextTick()

      const closeLine = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
      expect(closeLine?.disabled).toBe(false)
      expect(document.activeElement).toBe(document.body)
      closeLine?.click()
      await nextTick()
      closeLine?.click()
      expect(getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE')).toHaveBeenCalledOnce()

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      getPhaseRef().value = 'idle'
      await nextTick()
      otherButton.focus()

      busy.value = false
      await nextTick()
      await nextTick()

      expect(document.activeElement).toBe(otherButton)
    } finally {
      app.unmount()
      host.remove()
      otherOwner.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE')
      vi.unstubAllGlobals()
    }
  })

  // 048 `T4840` (advisor's decision 2026-10-19) replaces H-3 ("the x closes the window and does NOT cancel the flow"): with
  // the window gone the phase stayed `editing-trustline` - the action bar locked, Escape had no window to close, and from
  // the navigator (no canvas to click) only a reload released it. Closing the line window now ends the "trust line" action.
  it('048 T4840: the x of the line window closes it AND ends the "trust line" action - the action bar is not locked', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible, and the action bar is locked by the open action.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
      expect(host.querySelector('[data-testid="actionbar-locked-hint"]'), 'counter-check: the action locks the bar').not.toBeNull()
      expect(host.querySelector('[data-testid="actionbar-trustline"]')?.getAttribute('data-active')).toBe('1')

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      // UI-close inside edge-detail window (045 S3: the × of its title, named "Close").
      const closeBtn = Array.from(host.querySelectorAll('[data-testid="edge-detail-popup"] button')).find((b) => b.getAttribute('aria-label') === 'Close') as
        | HTMLButtonElement
        | undefined
      expect(closeBtn).toBeTruthy()

      closeBtn?.click()
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(getPhaseRef().value).toBe('idle')
      expect(host.querySelector('[data-testid="actionbar-locked-hint"]')).toBeNull()
      expect(host.querySelector('[data-testid="actionbar-trustline"]')?.getAttribute('data-active')).toBe('0')

      // A line chosen again (the selection and the phase, as `selectEdge`) opens the window again.
      const st = getInteractState()
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.edgeAnchor = { x: 20, y: 20 }
      getPhaseRef().value = 'editing-trustline'
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

      sendBtn?.focus()
      expect(document.activeElement).toBe(sendBtn)
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

      const busy = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busy.value = true
      getPhaseRef().value = 'idle'
      await nextTick()
      busy.value = false
      await nextTick()
      await nextTick()
      expect(document.activeElement).toBe(host.querySelector('[data-testid="actionbar-payment"]'))
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

  // 048 `T4840`: was a second copy of H-3 through the x (the test above holds the x now); the route it did not cover is
  // Escape - the window manager closes the window itself, bypassing the line-window state machine.
  it('048 T4840: Escape on the line window closes it AND ends the "trust line" action - the action bar is not locked', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
      expect(host.querySelector('[data-testid="actionbar-locked-hint"]'), 'counter-check: the action locks the bar').not.toBeNull()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true }))
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(0)
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(getPhaseRef().value).toBe('idle')
      expect(host.querySelector('[data-testid="actionbar-locked-hint"]')).toBeNull()

      // The same line at the same anchor chosen again opens the window again. What this does NOT hold: the stale window id
      // an Escape left in the line-window state machine - here the window was opened by the phase (driver `auto`), and
      // the cancel heals that path by itself; the navigator's manual open is held by `useWindowController.p048t4840.test.ts`
      // ("after Escape the same line opens again") and the e2e (internal adversarial pass, 2026-10-19).
      const st = getInteractState()
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.edgeAnchor = { x: 10, y: 10 }
      getPhaseRef().value = 'editing-trustline'
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

  /**
   * ЛИНИЯ, КОТОРУЮ БЭКЕНД ПОДТВЕРДИЛ (внешнее ревью 013, находка P2).
   *
   * По умолчанию мок отдаёт пустой список при состоянии «источник ответил» — то есть основание
   * `no-row`: «линии у этой пары нет». Закрывать и обновлять в этом состоянии нечего, и панель
   * теперь честно этого не предлагает. Поэтому тест, который судит ЗАКРЫТИЕ СУЩЕСТВУЮЩЕЙ линии,
   * обязан начинаться с линии — иначе он судит закрытие того, чего нет.
   */
  function answerWithTrustlineForSelectedPair(): void {
    getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent: 'UAH',
        limit: '100',
        used: '0',
        available: '100',
        status: 'active',
      } as TrustlineInfo,
    ]
  }

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

    // Судится подтверждение закрытия СУЩЕСТВУЮЩЕЙ линии, поэтому она должна существовать.
    answerWithTrustlineForSelectedPair()

    // Advance to editing-trustline (simulates FSM progression)
    const phaseRef = getPhaseRef()
    phaseRef.value = 'editing-trustline'
    await nextTick()
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
    // and no edge-detail window is opened (the root never mounts EdgeDetailPopup outside one).
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

    // EdgeDetailPopup should NOT render (no edge-detail window while the full editor is open).
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

  // -------------------------------------------------------------------------
  // RT-013-7 -- `F-013-7`: empty and absent are indistinguishable for a mutating control.
  //
  // `interactSelectedLink` (`SimulatorAppRoot.vue:627`) prefers the REST trustlines cache only
  // when the array is non-empty and otherwise silently falls back to the SSE snapshot link.  A
  // failed fetch and a still-in-flight fetch therefore look exactly like an authoritative
  // "this pair has no trustline", and the numbers the snapshot happens to carry are handed to
  // `TrustlineManagementPanel` as `used` / `current-limit` / `available` (`:1403-1405`) -- the
  // panel whose buttons UPDATE and CLOSE a trustline.  Viewing on snapshot grounds is allowed by
  // the spec; permitting a mutation on them is not.
  //
  // WHAT THESE TWO TESTS CAN AND CANNOT TELL APART
  //  - CAN distinguish: the pre-fix behaviour (snapshot numbers shown, both mutations enabled);
  //    a fix that hides the numbers but still permits the mutation; a fix that disables the
  //    buttons but keeps presenting the snapshot numbers as the trustline's; a fix that goes
  //    quiet instead of naming the state (an explicit notice is required); and the degenerate
  //    fix that disables the panel unconditionally -- the settled-and-authoritative control in
  //    the same test requires Update and Close to work.
  //  - CANNOT distinguish: whether the ROOT or the PANEL owns the decision, only that the pair
  //    root+panel behaves; nor anything about `EdgeDetailPopup`, which carries its own Close TL
  //    button off the same `interactSelectedLink` and is left as found.
  // -------------------------------------------------------------------------
  async function openFullTrustlineEditor(host: HTMLElement, beforeEditing?: () => void) {
    const app = mountSimulatorAppRoot(host)
    await nextTick()

    // Entering through the ActionBar is what sets `useFullTrustlineEditor = true`, i.e. what makes
    // the mutating panel (rather than the read-only EdgeDetailPopup) the visible surface.
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // The refs the mocked interact composable exposes only exist after mount, so a test that needs
    // the source to be unsettled BEFORE the edit phase opens sets it here.
    beforeEditing?.()
    await nextTick()

    getPhaseRef().value = 'editing-trustline'
    await nextTick()
    await nextTick()
    return app
  }

  function typeNewLimit(panel: HTMLElement, value: string) {
    const input = panel.querySelector('#tl-new-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()
    input!.value = value
    input!.dispatchEvent(new Event('input'))
    return input!
  }

  function trustlineStatsText(host: HTMLElement): string {
    const stats = host.querySelector('[data-testid="trustline-panel"] [aria-label="Trustline stats"]') as HTMLElement | null
    expect(stats).toBeTruthy()
    return (stats?.textContent ?? '').replace(/\s+/g, ' ').trim()
  }

  function trustlineMutationButtons(host: HTMLElement) {
    const panel = host.querySelector('[data-testid="trustline-panel"]') as HTMLElement | null
    expect(panel).toBeTruthy()
    const update = Array.from(panel?.querySelectorAll('button') ?? []).find(
      (b) => (b.textContent ?? '').trim() === 'Update',
    ) as HTMLButtonElement | undefined
    const closeBtn = panel?.querySelector('[data-testid="trustline-close-btn"]') as HTMLButtonElement | null
    return { panel: panel as HTMLElement, update, closeBtn }
  }

  // A snapshot link with `used: '0'` so that BOTH mutating controls are reachable on snapshot
  // grounds: Update is enabled (new limit >= used) and Close is not blocked by outstanding debt.
  const RT_013_7_SNAPSHOT = {
    equivalent: 'UAH',
    generated_at: '2026-09-10T00:00:00Z',
    nodes: [makeSelectedNode('alice', 'Alice'), makeSelectedNode('bob', 'Bob')],
    links: [{ source: 'alice', target: 'bob', trust_limit: '100', used: '0', available: '100' }],
  } as unknown as GraphSnapshot

  const RT_013_7_TRUSTLINE = (limit: string): TrustlineInfo => ({
    from_pid: 'alice',
    from_name: 'Alice',
    to_pid: 'bob',
    to_name: 'Bob',
    equivalent: 'UAH',
    limit,
    used: '0',
    available: limit,
    status: 'active',
  })

  it('RT-013-7a: a FAILED trustlines fetch must not let the snapshot stand in as the trustline, nor permit the mutation', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    // The REST trustlines fetch failed before the operator opened the editor.  The snapshot still
    // carries a link alice->bob with limit 100 / used 0, so the fallback has something to show --
    // and that is exactly the situation in which it must not.
    const app = await openFullTrustlineEditor(host, () => {
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_LAST_ERROR_REF').value = 'GET /runs/r1/trustlines failed: 503'
    })
    try {
      const { panel, update, closeBtn } = trustlineMutationButtons(host)

      // 1. The snapshot's numbers must not be presented as this trustline's numbers.
      const stats = trustlineStatsText(host)
      expect(stats).not.toContain('100')
      expect(stats).toContain('\u2014')

      // 2. The panel must SAY what happened rather than going quiet.
      const notice = panel.querySelector('[data-testid="tl-source-unavailable"]') as HTMLElement | null
      expect(notice).toBeTruthy()
      expect(notice?.textContent ?? '').toContain('GET /runs/r1/trustlines failed: 503')

      // 3. The edit field must not be pre-filled with a number nobody vouched for.
      const newLimit = panel.querySelector('#tl-new-limit') as HTMLInputElement | null
      expect(newLimit?.value ?? '').toBe('')

      // 4. Neither mutation may be offered on that basis.
      expect(update?.disabled).toBe(true)
      expect(closeBtn?.disabled).toBe(true)

      // 5. Nor may the operator type their way past it: 50 cannot be checked against a `used` that
      //    nobody reported, so Update stays refused.  This is what separates a real fail-closed
      //    source from a fix that merely stopped pre-filling the field.
      typeNewLimit(panel, '50')
      await nextTick()
      expect(trustlineMutationButtons(host).update?.disabled).toBe(true)

      // 6. Recovery: an authoritative answer arrives and the panel becomes usable again, with the
      //    backend's numbers -- not the snapshot's.
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = [RT_013_7_TRUSTLINE('42')]
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_LAST_ERROR_REF').value = null
      await nextTick()
      await nextTick()

      expect(trustlineStatsText(host)).toContain('42')
      expect(panel.querySelector('[data-testid="tl-source-unavailable"]')).toBeFalsy()
      {
        const settled = trustlineMutationButtons(host)
        expect(settled.update?.disabled).toBe(false)
        expect(settled.closeBtn?.disabled).toBe(false)
      }
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_LAST_ERROR_REF',
      )
    }
  })

  it('RT-013-7b: a trustlines fetch STILL IN FLIGHT must not permit the mutation either', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = await openFullTrustlineEditor(host)
    try {
      const trustlinesLoading = getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_LOADING_REF')
      trustlinesLoading.value = true
      await nextTick()
      await nextTick()

      const { panel, update, closeBtn } = trustlineMutationButtons(host)

      const stats = trustlineStatsText(host)
      expect(stats).not.toContain('100')
      expect(stats).toContain('\u2014')

      const notice = panel.querySelector('[data-testid="tl-source-unavailable"]') as HTMLElement | null
      expect(notice).toBeTruthy()
      expect((notice?.textContent ?? '').toLowerCase()).toContain('loading')

      expect(update?.disabled).toBe(true)
      expect(closeBtn?.disabled).toBe(true)

      // And once the fetch settles with an authoritative answer, the panel is usable again.
      const trustlines = getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF')
      trustlines.value = [RT_013_7_TRUSTLINE('7')]
      trustlinesLoading.value = false
      await nextTick()
      await nextTick()

      expect(trustlineStatsText(host)).toContain('7')
      expect(panel.querySelector('[data-testid="tl-source-unavailable"]')).toBeFalsy()
      typeNewLimit(panel, '9')
      await nextTick()
      {
        const settled = trustlineMutationButtons(host)
        expect(settled.update?.disabled).toBe(false)
        expect(settled.closeBtn?.disabled).toBe(false)
      }
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_LOADING_REF',
      )
    }
  })

  /**
   * RT-013-7c — СОСТОЯНИЕ «НЕ СПРАШИВАЛИ», начальное состояние `useInteractDataCache`
   * (`trustlinesLoading=false`, `trustlinesLastError=null`, `trustlines=[]`). Ровно в нём панель
   * находится до первого запроса — и старый гард `(loading || error) && нет строки` его не видел:
   * `unsettled` ложно, фоллбэк на снапшот молчалив, обе мутации доступны.
   *
   * ЧЕМ ЭТОТ ТЕСТ ОТЛИЧАЕТСЯ ОТ RT-013-7d НИЖЕ: данными — ничем. Тот же снапшот, тот же пустой
   * список, те же числа на экране. Различается ТОЛЬКО состояние источника, и в этом вся находка.
   */
  it('RT-013-7c: a source that was NEVER ASKED must not let the snapshot stand in as the trustline, nor permit the mutation', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = await openFullTrustlineEditor(host, () => {
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'never-asked' }
    })
    try {
      const { panel, update, closeBtn } = trustlineMutationButtons(host)

      // 1. Числа снапшота не выдаются за числа этой линии.
      const stats = trustlineStatsText(host)
      expect(stats).not.toContain('100')
      expect(stats).toContain('\u2014')

      // 2. Состояние названо: «не спрашивали» — это не «нет линии».
      const notice = panel.querySelector('[data-testid="tl-source-unavailable"]') as HTMLElement | null
      expect(notice).toBeTruthy()
      expect((notice?.textContent ?? '').toLowerCase()).toContain('has not been requested')

      // 3. Поле правки не подставляет число, за которое никто не ручался.
      expect((panel.querySelector('#tl-new-limit') as HTMLInputElement | null)?.value ?? '').toBe('')

      // 4. Ни одна из мутаций не предлагается на этом основании.
      expect(update?.disabled).toBe(true)
      expect(closeBtn?.disabled).toBe(true)

      // 5. И вручную мимо гарда не пройти: 50 не с чем сверить.
      typeNewLimit(panel, '50')
      await nextTick()
      expect(trustlineMutationButtons(host).update?.disabled).toBe(true)

      // 6. Ответ приходит — панель снова рабочая, и с числами бэкенда, а не снапшота.
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = [RT_013_7_TRUSTLINE('42')]
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'answered' }
      await nextTick()
      await nextTick()

      expect(trustlineStatsText(host)).toContain('42')
      expect(panel.querySelector('[data-testid="tl-source-unavailable"]')).toBeFalsy()
      {
        const settled = trustlineMutationButtons(host)
        expect(settled.update?.disabled).toBe(false)
        expect(settled.closeBtn?.disabled).toBe(false)
      }
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_FETCH_STATE_REF',
      )
    }
  })

  /**
   * RT-013-7d — ВТОРАЯ ПОЛОВИНА ТОЙ ЖЕ ГРАНИЦЫ.
   *
   * ЧТО ЗДЕСЬ ИЗМЕНИЛОСЬ 2026-09-10 И ПОЧЕМУ (внешнее ревью программы 013, находка P2).
   * Прежняя редакция этого теста требовала после АВТОРИТЕТНОГО ПУСТОГО ответа: (1) чтобы на
   * экране осталось снапшотное `limit 100`, и (2) чтобы Update и Close были доступны. Обоснование
   * над ней говорило про право оператора СОЗДАТЬ линию — и это верно; вывод про Update/Close из
   * него не следует. «Источник ответил, и линии у этой пары нет» разрешает СОЗДАНИЕ; обновлять и
   * закрывать в этом состоянии нечего, а числа, которые в нём показывала панель, приехали из
   * снапшота — то есть ровно из того источника, который авторитетный ответ только что опроверг.
   * Выдавать их за состояние существующей линии — та же ложь, ради устранения которой заведена
   * вся эта программа, только на одно состояние в сторону.
   *
   * ЧТО ИЗ ПРЕЖНЕЙ РЕДАКЦИИ СОХРАНЕНО ДОСЛОВНО: пустой ответ НЕ ДОЛЖЕН ГАСИТЬ СОЗДАНИЕ. Это
   * судит RT-013-7d2 ниже, и именно оно краснеет на неверной починке «на `no-row` запретить всё».
   *
   * НЕ «ВОССТАНАВЛИВАТЬ» ПРЕЖНИЙ ВИД. Если следующий читатель увидит здесь запрет на Update/Close
   * и решит, что починка выродилась в «запретить всё», — соседний RT-013-7d2 показывает, что не
   * выродилась: при том же самом `no-row` панель создаёт линию.
   */
  it('RT-013-7d: a source that ANSWERED with no row for this pair authorises CREATING one, not updating or closing one', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = await openFullTrustlineEditor(host, () => {
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'answered' }
    })
    try {
      const { panel } = trustlineMutationButtons(host)

      // 1. Числа снапшота не выдаются за числа существующей линии: авторитетный ответ сказал,
      //    что линии нет, а `100` на экране в этот момент — снапшотное.
      const stats = trustlineStatsText(host)
      expect(
        stats,
        'после ответа «линии у этой пары нет» панель всё ещё показывает снапшотный лимит как ' +
          'состояние существующей линии — при том что ответ бэкенда его прямо опроверг',
      ).not.toContain('100')
      expect(stats).toContain('\u2014')

      // 2. Состояние НАЗВАНО, и названо именно как ответ, а не как отсутствие ответа.
      const noRow = panel.querySelector('[data-testid="tl-no-trustline"]') as HTMLElement | null
      expect(noRow, 'ответ «линии у этой пары нет» не назван оператору вовсе').toBeTruthy()
      expect((noRow?.textContent ?? '').toLowerCase()).toContain('no trustline')
      expect(
        panel.querySelector('[data-testid="tl-source-unavailable"]'),
        'ответ источника объявлен его отсутствием: бэкенд ответил, и это разные вещи',
      ).toBeFalsy()

      // 3. Мутации существующей линии не предлагаются: обновлять и закрывать нечего.
      typeNewLimit(panel, '50')
      await nextTick()
      const settled = trustlineMutationButtons(host)
      expect(
        settled.update?.disabled,
        'Update доступен, хотя обновлять нечего: линии у пары нет, а `50` сверялось бы со ' +
          'снапшотным `used`, который этот же ответ опроверг',
      ).toBe(true)
      expect(
        settled.closeBtn?.disabled,
        'Close доступен, хотя закрывать нечего: `closeBlocked` («есть долг») на отсутствующих ' +
          'числах ложно — то есть каскад разрешает мутацию именно там, где предмета мутации нет',
      ).toBe(true)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_FETCH_STATE_REF',
      )
    }
  })

  /**
   * RT-013-7d2 — ТО ВЕРНОЕ, ЧТО БЫЛО В ПРЕЖНЕЙ РЕДАКЦИИ RT-013-7d, вынесено сюда целиком.
   *
   * Пустой АВТОРИТЕТНЫЙ ответ — это ответ, и он разрешает создать линию. Ровно этот тест краснеет
   * на неверной починке «на `no-row` запретить всё»: там, где предмета мутации нет, нет и повода
   * мешать оператору его завести.
   */
  it('RT-013-7d2: a source that ANSWERED with no row must NOT block creating the line', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
      expect(tlBtn).toBeTruthy()
      tlBtn?.click()
      await nextTick()

      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'answered' }
      await nextTick()

      getPhaseRef().value = 'confirm-trustline-create'
      await nextTick()
      await nextTick()

      const panel = host.querySelector('[data-testid="trustline-panel"]') as HTMLElement | null
      expect(panel).toBeTruthy()

      const input = panel?.querySelector('#tl-limit') as HTMLInputElement | null
      expect(input).toBeTruthy()
      input!.value = '25'
      input!.dispatchEvent(new Event('input'))
      await nextTick()

      const createBtn = Array.from(panel?.querySelectorAll('button') ?? []).find(
        (b) => (b.textContent ?? '').trim() === 'Create',
      ) as HTMLButtonElement | undefined
      expect(createBtn).toBeTruthy()
      expect(
        createBtn?.disabled,
        'починка выродилась в «на `no-row` запретить всё»: пустой ОТВЕТ источника — это ответ, и ' +
          'единственное, что он разрешает, — создать линию',
      ).toBe(false)

      createBtn?.click()
      await nextTick()
      expect(
        getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CREATE'),
      ).toHaveBeenCalledWith('25')
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CREATE',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_FETCH_STATE_REF',
      )
    }
  })

  /**
   * RT-013-7e — НАЙДЕННАЯ СТРОКА ЕЩЁ НЕ ЗНАЧИТ ОТВЕТ. Про это брифинг не говорил, а дефект есть:
   * гард ищет «авторитетную строку» в `interact.mode.trustlines`, а этот список в
   * `useInteractDataCache` равен `fetched ?? строки из снапшота`. Пока ответа нет, он ПОЛОН
   * снапшотных строк — то есть в проде (в отличие от прежних тестов, где список был пуст)
   * `authoritative` находилась всегда, и гард не срабатывал даже в тех двух состояниях, которые
   * он якобы покрывал.
   */
  it('RT-013-7e: a row that came from the snapshot fallback is not an authoritative answer', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'idle')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = await openFullTrustlineEditor(host, () => {
      // Ровно то, что делает кэш до первого ответа: список НЕПУСТ и собран из снапшота.
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = [RT_013_7_TRUSTLINE('100')]
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_LOADING_REF').value = true
    })
    try {
      const { panel, update, closeBtn } = trustlineMutationButtons(host)

      const notice = panel.querySelector('[data-testid="tl-source-unavailable"]') as HTMLElement | null
      expect(
        notice,
        'строка, поднятая из снапшотного фоллбэка, зачтена за ответ источника: гард ищет её в ' +
          'слитом списке, а тот до ответа состоит из снапшота целиком',
      ).toBeTruthy()
      expect(update?.disabled).toBe(true)
      expect(closeBtn?.disabled).toBe(true)
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_LOADING_REF',
      )
    }
  })

  /**
   * RT-013-7f — та же проводка, но до ТРЕТЬЕГО мутирующего контрола: кнопки «Close line» в
   * edge-detail. Компонентный тест (`EdgeDetailPopup.sourceUnavailable.test.ts`) судит попап при
   * заданном основании; здесь судится то, что корень это основание ему действительно передаёт, и
   * что мимо выключенной кнопки не проходит сам вызов действия.
   */
  it('RT-013-7f: edge-detail Close line is refused when the source was never asked', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'never-asked' }
      await nextTick()
      await nextTick()

      const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()
      expect(btn?.disabled).toBe(true)
      expect(host.querySelector('[data-testid="edge-source-unavailable"]')).toBeTruthy()

      btn?.click()
      await nextTick()
      const confirmClose = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE')
      expect(confirmClose).not.toHaveBeenCalled()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_FETCH_STATE_REF',
      )
    }
  })

  /**
   * RT-013-7g — `keepAlive` НЕ ДОЛЖЕН ПОПАСТЬ ПОД ГАРД, КОГДА ЗАМОРОЖЕН НАСТОЯЩИЙ ОТВЕТ.
   *
   * ЧТО ЗДЕСЬ ИЗМЕНИЛОСЬ 2026-09-10 И ПОЧЕМУ (внешнее ревью программы 013, находка P3).
   * Прежняя редакция конструировала состояние `never-asked`, замораживала его нажатием
   * «Send Payment» и требовала, чтобы предупреждение о недоступном источнике ИСЧЕЗЛО. Тем самым
   * она требовала, чтобы заморозка ПОВЫШАЛА происхождение чисел: ответа не приходило, а окно
   * начинало молчать так, будто пришёл. Заморозка — это снимок; она сохраняет то, что было, и
   * не превращает молчание в ответ.
   *
   * ЧТО ИЗ ПРЕЖНЕЙ РЕДАКЦИИ ВЕРНО И СОХРАНЕНО: если заморожен НАСТОЯЩИЙ ответ, объявлять его
   * отсутствием источника нельзя — это вопрос устаревания, а не отсутствия. Поэтому здесь то же
   * самое сравнение «до заморозки / после заморозки», но на состоянии `row`; а состояние
   * `never-asked` перенесено в RT-013-7g2, где предупреждение обязано ОСТАТЬСЯ.
   *
   * ПОЧЕМУ СРАВНЕНИЕ ИМЕННО ЧЕРЕЗ СООБЩЕНИЕ. Кнопки в `keepAlive` и так выключены через
   * `wmEdgeDetailEffectiveBusy`, поэтому единственное наблюдаемое следствие происхождения чисел
   * в этом режиме — то, что окно о них говорит.
   */
  it('RT-013-7g: a keepAlive edge-detail that froze a REAL answer must not be reported as an absent source', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = [RT_013_7_TRUSTLINE('42')]
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'answered' }
      await nextTick()
      await nextTick()

      // База для сравнения: живое окно на отвечающем источнике молчит — и про заморозку тоже.
      expect(host.querySelector('[data-testid="edge-source-unavailable"]')).toBeFalsy()
      expect(host.querySelector('[data-testid="edge-frozen-figures"]')).toBeFalsy()

      // Send Payment замораживает текущую линию и оставляет окно как контекст.
      const sendBtn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
      expect(sendBtn).toBeTruthy()
      sendBtn?.click()
      await nextTick()
      await nextTick()

      const popup = host.querySelector('[data-testid="edge-detail-popup"]') as HTMLElement | null
      expect(popup).toBeTruthy()
      expect((popup?.querySelector('.popup__subtitle')?.textContent ?? '').trim()).toBe('alice \u2192 bob')
      expect(
        popup?.querySelector('[data-testid="edge-source-unavailable"]'),
        'замороженный ОТВЕТ объявлен отсутствием источника: это устаревание, а не молчание, и ' +
          'окно здесь вообще показывает не ту пару, что в живом interact-состоянии',
      ).toBeFalsy()
      expect(popup?.querySelector('[data-testid="edge-no-trustline"]')).toBeFalsy()

      // ...но и живым этот ответ больше не является, и об этом окно говорит. Без этого ассерта
      // `frozen` и `row` наблюдаемо неразличимы, то есть заморозка основания ничем не судится:
      // реализация, которая просто проносит прежнее основание насквозь, оставалась зелёной.
      const frozen = popup?.querySelector('[data-testid="edge-frozen-figures"]') as HTMLElement | null
      expect(
        frozen,
        'замороженная копия выдана за живой ответ: она не обновляется и относится к паре, ' +
          'выбранной ДО начала платёжного потока',
      ).toBeTruthy()
      expect((frozen?.textContent ?? '').toLowerCase()).toContain('frozen copy')
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_FETCH_STATE_REF',
      )
      vi.unstubAllGlobals()
    }
  })

  /**
   * RT-013-7g2 — ЗАМОРОЗКА НЕ ПОВЫШАЕТ ПРОИСХОЖДЕНИЕ ЧИСЕЛ (внешнее ревью 013, находка P3).
   *
   * Состояние: у источника НИЧЕГО НЕ СПРАШИВАЛИ, окно показывает снапшотный фоллбэк и честно об
   * этом говорит. Оператор нажимает «Send Payment» — попап замораживается как контекст платежа.
   * Ответа за это время не приходило; значит, замораживается МОЛЧАНИЕ, и говорить о нём окно
   * обязано ровно теми же словами, что и секунду назад.
   *
   * ЧТО ЭТОТ ТЕСТ ЛОВИТ, А RT-013-7g — НЕТ: `keepAlive` как БЕЗУСЛОВНОЕ основание («заморожена
   * линия ⇒ основание `frozen`»). При такой реализации RT-013-7g остаётся зелёным (там и правда
   * был ответ), а здесь предупреждение исчезает — при том что бэкенд так и не сказал ни слова.
   */
  it('RT-013-7g2: freezing a NEVER-ASKED source keeps saying so; the freeze does not turn silence into an answer', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', RT_013_7_SNAPSHOT)
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_FETCH_STATE_REF').value = { kind: 'never-asked' }
      await nextTick()
      await nextTick()

      // База для сравнения: живое окно при неспрошенном источнике говорит об этом.
      const before = host.querySelector('[data-testid="edge-source-unavailable"]') as HTMLElement | null
      expect(before).toBeTruthy()
      const beforeText = (before?.textContent ?? '').toLowerCase()
      expect(beforeText).toContain('has not been requested')

      const sendBtn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
      expect(sendBtn).toBeTruthy()
      sendBtn?.click()
      await nextTick()
      await nextTick()

      const popup = host.querySelector('[data-testid="edge-detail-popup"]') as HTMLElement | null
      expect(popup).toBeTruthy()

      // И «замороженным ОТВЕТОМ» это молчание не объявлено.
      //
      // ЧТО ЗДЕСЬ ИЗМЕНИЛОСЬ 2026-09-11 И ПОЧЕМУ (кросс-ревью, P3). Раньше на этом месте стояло
      // `expect(...'edge-frozen-figures'...).toBeFalsy()` с обоснованием «замораживать было
      // нечего». Обоснование неверно: заморожено было ОКНО, и это факт о нём — неверно лишь то,
      // что заморожен ОТВЕТ. Пока уведомление о заморозке существовало только для вида `frozen`
      // (то есть означало «заморожен ответ со строкой»), сказать первое, не сказав второго, было
      // нельзя, и тест закреплял единственный доступный тогда выход — молчать. Цена молчания
      // названа находкой: замороженное окно при неспрошенном источнике становилось наблюдаемо
      // НЕОТЛИЧИМО от живого, то есть ровно то, против чего это уведомление и заведено.
      //
      // ОХРАНЯЕМЫЙ ИНВАРИАНТ НЕ ОСЛАБЛЕН, А УСИЛЕН: раньше «повышения происхождения не было»
      // доказывалось ОТСУТСТВИЕМ элемента; теперь элемент обязан быть И обязан не утверждать,
      // что за числами стоит ответ бэкенда. Мутация «выдавать заморозке молчания текст про
      // замороженный ответ» прежним ассертом ловилась, этим — тоже.
      const frozenNotice = popup?.querySelector('[data-testid="edge-frozen-figures"]') as HTMLElement | null
      expect(
        frozenNotice,
        'замороженное окно на экране неотличимо от живого: заморозка не названа вовсе',
      ).toBeTruthy()
      expect(
        (frozenNotice?.textContent ?? '').toLowerCase(),
        'снимок МОЛЧАНИЯ предъявлен как снимок ОТВЕТА — происхождение чисел повышено самим ' +
          'фактом заморозки',
      ).not.toContain('backend answer')

      const after = popup?.querySelector('[data-testid="edge-source-unavailable"]') as HTMLElement | null
      expect(
        after,
        'заморозка сняла предупреждение, хотя ответа так и не было: снимок молчания выдан за ' +
          'снимок ответа — происхождение чисел повышено самим фактом заморозки',
      ).toBeTruthy()
      expect((after?.textContent ?? '').toLowerCase()).toContain('has not been requested')
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals(
        '__GEO_TEST_INTERACT_PHASE',
        '__GEO_TEST_INTERACT_CANCEL',
        '__GEO_TEST_SNAPSHOT',
        '__GEO_TEST_TRUSTLINES_REF',
        '__GEO_TEST_TRUSTLINES_FETCH_STATE_REF',
      )
      vi.unstubAllGlobals()
    }
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

  // 049 S2 (decision 6): the test of the success toast after a clearing is removed - the contract is removed (no toast).

  // -----------------------------------------------------------------------
  // P0-2: busy-gate tests (ESC and outside-click while interact.mode.busy)
  // -----------------------------------------------------------------------

  // 046 wave 3 (advisor's decision 2026-10-19): Escape while busy asked a native `confirm()` and cancelled on "yes"; spec
  // 046 forbids the native confirm and the owner's rule (no edge cases in the UI) means no dialog at all. Escape while busy
  // is IGNORED: consumed, no question, no cancel, no window closed. The two tests of the confirm (true / false) are replaced.
  it('P0-2: ESC while busy → ignored: no confirm, no cancel, no WM Escape, the window stays; after the operation Escape works again', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const handleEsc = getRequiredGeoTestGlobal('__GEO_TEST_WM_HANDLE_ESC')
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busyRef.value = true
      await nextTick()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(confirmSpy, 'no native confirm').not.toHaveBeenCalled()
      expect(cancel, 'the operation is not cancelled').not.toHaveBeenCalled()
      expect(handleEsc, 'Escape is consumed: no window closes').not.toHaveBeenCalled()
      expect(host.querySelector('[data-testid="manual-payment-panel"]'), 'the window stays').toBeTruthy()

      // Counter-check: the operation finished (busy false) - Escape is the window manager's again.
      busyRef.value = false
      await nextTick()
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      expect(handleEsc).toHaveBeenCalledTimes(1)
      expect(confirmSpy).not.toHaveBeenCalled()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_BUSY_REF', '__GEO_TEST_WM_HANDLE_ESC')
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

  // 046 wave 3: the two outside-click tests of the confirm are replaced by one. This file's `onCanvasClick` is the mock above
  // (it mirrors the Step0 policy); the product function is judged in `useSimulatorApp.windowManagementStep0.test.ts`
  // (ignored while busy; on the real interact mode the payment in flight completes). Held here: the canvas click reaches
  // the handler and nothing of the window changes.
  it('P0-2: outside-click while busy → no confirm, no cancel, the window stays', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')

    stubMissingResizeObserver()
    const confirmSpy = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmSpy)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_CANCEL')
      const busyRef = getRequiredGeoTestGlobal('__GEO_TEST_INTERACT_BUSY_REF')
      busyRef.value = true
      await nextTick()

      const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
      expect(canvas).toBeTruthy()
      canvas?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      await nextTick()
      await nextTick()

      expect(confirmSpy, 'no native confirm').not.toHaveBeenCalled()
      expect(cancel).not.toHaveBeenCalled()
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

// ---------------------------------------------------------------------------
// Analytics overlay surface (spec 007, T705)
// ---------------------------------------------------------------------------

function analyticsDock(host: HTMLElement): HTMLElement | null {
  return host.querySelector('[data-surface="real-metrics-panel"]')
}

/** 048 S2 (amendment 10): the toggle is in the top bar's service row with `?debug=1` (was in "Admin ▾" since S1). */
function analyticsToggle(host: HTMLElement): HTMLButtonElement {
  const btn = host.querySelector('[data-testid="analytics-toggle"]')
  expect(btn).toBeTruthy()
  return btn as HTMLButtonElement
}

function makeAnalyticsMetrics(): MetricsResponse {
  return {
    api_version: 'v1',
    run_id: 'run-1',
    equivalent: 'UAH',
    from_ms: 0,
    to_ms: 5_000,
    step_ms: 5_000,
    series: [{ key: 'success_rate', unit: '%', points: [{ t_ms: 0, v: '0.75' }] }],
  }
}

type AnalyticsTestTarget = { kind: 'edge'; from: string; to: string } | { kind: 'node'; id: string }

function makeAnalyticsBottlenecks(target: AnalyticsTestTarget): BottlenecksResponse {
  return {
    api_version: 'v1',
    run_id: 'run-1',
    equivalent: 'UAH',
    items: [
      {
        target,
        score: 0.85,
        reason_code: 'FREQUENT_ABORTS',
        label: 'Hot line',
        suggested_action: 'Raise trust limit',
      },
    ],
  }
}

describe('SimulatorAppRoot - analytics overlay surface (spec 007, T705)', () => {
  // 043 S1: the Analytics toggle and its dock live behind "Подробнее" (the off case: its own test below).
  // 048 S2 (amendment 10): and they are the developer's - every test here but the one without it runs with `?debug=1`
  // (the mock's `isDebugUi`), and with no token: the dock asks for none (was: the administrator's, S1).
  afterEach(() => {
    storedDetails(null)
    clearGeoTestGlobals('__GEO_TEST_ACCESS_TOKEN', '__GEO_TEST_DEBUG_UI')
  })

  beforeEach(() => {
    storedDetails(true)
    setGeoTestGlobal('__GEO_TEST_DEBUG_UI', true)
    const simStorage = getSimStorage()
    simStorage.readAnalyticsPanelOpen.mockReturnValue(null)
    simStorage.writeAnalyticsPanelOpen.mockClear()
  })

  afterEach(() => {
    clearGeoTestGlobals(
      '__GEO_TEST_ANALYTICS_METRICS_PHASE_REF',
      '__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF',
      '__GEO_TEST_ANALYTICS_METRICS_REF',
      '__GEO_TEST_ANALYTICS_BOTTLENECKS_REF',
      '__GEO_TEST_ANALYTICS_METRICS_ERROR_REF',
      '__GEO_TEST_ANALYTICS_BOTTLENECKS_ERROR_REF',
      '__GEO_TEST_ANALYTICS_METRICS_REASON_REF',
      '__GEO_TEST_ANALYTICS_BOTTLENECKS_REASON_REF',
      '__GEO_TEST_ANALYTICS_IS_VISIBLE',
      '__GEO_TEST_FOCUS_ON_EDGE',
      '__GEO_TEST_LAYOUT_LINKS',
      '__GEO_TEST_CAMERA',
      '__GEO_TEST_WORLD_TO_SCREEN',
      '__GEO_TEST_ACTIVE_EDGES',
      '__GEO_TEST_PRUNE_ACTIVE_EDGES',
    )
  })

  /**
   * 048 S2 (amendment 10), the root's half: without `?debug=1` there is no toggle, and a dock a browser remembers open
   * is neither mounted nor polled (the gate the root feeds `useSimulatorApp` is false) - for a visitor AND for the
   * administrator. Rewritten from S1's "a visitor (no admin token)…": red before S2 for the admin token (the toggle was in
   * "Admin ▾" and the remembered dock on screen). The counter-check is the test below: the same mount with `?debug=1`
   * has both, with no token.
   */
  it('without ?debug=1 (no token, a participant JWT, an admin token): no analytics toggle, and a remembered open dock is neither shown nor polled', async () => {
    setUrl('/?mode=real')
    setGeoTestGlobal('__GEO_TEST_DEBUG_UI', false)
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)
    for (const token of ['', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl', 'admin-token']) {
      setGeoTestGlobal('__GEO_TEST_ACCESS_TOKEN', token)
      const host = document.createElement('div')
      document.body.appendChild(host)
      const app = mountSimulatorAppRoot(host)
      try {
        await nextTick()
        expect(host.querySelector('[data-testid="top-details-row"]'), 'Details is on').not.toBeNull()
        expect(host.querySelector('[data-testid="analytics-toggle"]'), `token "${token.slice(0, 6)}": no toggle`).toBeNull()
        expect(analyticsDock(host), 'no dock').toBeNull()
        expect(getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_IS_VISIBLE')(), 'the poll gate is closed').toBe(false)
      } finally {
        app.unmount()
        host.remove()
      }
    }
  })

  it('the toggle (?debug=1, no token) shows and hides the surface, and persists the choice', async () => {
    setUrl('/?mode=real&debug=1')
    const simStorage = getSimStorage()

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      // Default is off: nothing is mounted and nothing was written yet.
      expect(analyticsDock(host)).toBeNull()
      expect(analyticsToggle(host).getAttribute('aria-pressed')).toBe('false')
      expect(simStorage.writeAnalyticsPanelOpen).not.toHaveBeenCalled()

      analyticsToggle(host).click()
      await nextTick()

      expect(analyticsDock(host)).toBeTruthy()
      expect(analyticsToggle(host).getAttribute('aria-pressed')).toBe('true')
      // Same mechanism as the «Подробнее» flag: written through useSimulatorStorage.
      expect(simStorage.writeAnalyticsPanelOpen).toHaveBeenLastCalledWith(true)

      analyticsToggle(host).click()
      await nextTick()

      expect(analyticsDock(host)).toBeNull()
      expect(analyticsToggle(host).getAttribute('aria-pressed')).toBe('false')
      expect(simStorage.writeAnalyticsPanelOpen).toHaveBeenLastCalledWith(false)
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('a stored "open" survives the reload: the surface is there on first paint', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      expect(analyticsDock(host)).toBeTruthy()
      expect(analyticsToggle(host).getAttribute('aria-pressed')).toBe('true')
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('never shows over fixtures, even when the stored flag says open', async () => {
    // Fixtures have no metric store behind them; a panel here could only ever say "no data".
    setUrl('/?mode=fixtures')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()
      expect(analyticsDock(host)).toBeNull()
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('takes its placement and z-order from the catalog descriptor, not from the component', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const dock = analyticsDock(host)
      expect(dock).toBeTruthy()

      const expected = resolveOverlayDockStyle('real-metrics-panel')
      for (const [prop, value] of Object.entries(expected)) {
        expect(dock!.style.getPropertyValue(prop)).toBe(value)
      }

      // Spot-check the two decisions visible on screen: it docks right, on its own z layer —
      // and specifically NOT on `--ds-z-panel`, which is the WindowManager's layer.
      expect(dock!.style.getPropertyValue('--ds-ov-dock-left')).toBe('auto')
      expect(dock!.style.getPropertyValue('--ds-ov-dock-z')).toBe('var(--ds-z-analytics-dock)')
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('carries both streams, separately, all the way through to the mounted surface', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const metricsPhase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_PHASE_REF')
      const bottlenecksPhase = getRequiredGeoTestGlobal(
        '__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF',
      )
      const metrics = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      const metricsError = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_ERROR_REF')
      const metricsReason = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_METRICS_REASON_REF')
      const bottlenecksReason = getRequiredGeoTestGlobal(
        '__GEO_TEST_ANALYTICS_BOTTLENECKS_REASON_REF',
      )

      const section = (name: 'metrics' | 'bottlenecks'): Element =>
        analyticsDock(host)!.querySelector(`[data-section="${name}"]`) as Element

      // ready — measurements are shown.
      metrics.value = makeAnalyticsMetrics()
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      metricsPhase.value = 'ready'
      bottlenecksPhase.value = 'ready'
      await nextTick()

      expect(section('metrics').querySelector('[data-state="ready"]')).toBeTruthy()
      expect(analyticsDock(host)!.querySelector('.bnl__item')).toBeTruthy()

      /**
       * The mixed pair, end to end through the real mount point.
       *
       * `/bottlenecks` raises `db_read_failed` in its own database session while `/metrics`
       * answers normally. Before the panel was split this pair rendered as one "No measurements
       * recorded", over a response that was decoded and in hand.
       */
      bottlenecksPhase.value = 'unavailable'
      bottlenecksReason.value = 'db_read_failed'
      await nextTick()

      expect(section('metrics').querySelector('[data-state="ready"]')).toBeTruthy()
      expect(section('metrics').querySelector('.mkc')).toBeTruthy()
      expect(section('metrics').textContent).not.toContain('No measurements recorded')
      const noBottlenecks = section('bottlenecks').querySelector('[data-state="unavailable"]')
      expect(noBottlenecks).toBeTruthy()
      expect(noBottlenecks!.textContent).toContain('db_read_failed')

      // unavailable — the backend has no measurements and said so. Not an error surface.
      metricsPhase.value = 'unavailable'
      metricsReason.value = 'storage_disabled'
      await nextTick()

      const unavailable = section('metrics').querySelector('[data-state="unavailable"]')
      expect(unavailable).toBeTruthy()
      expect(section('metrics').querySelector('[data-state="error"]')).toBeNull()
      // The correlation token the backend logged reaches the screen.
      expect(unavailable!.textContent).toContain('storage_disabled')

      // error — a failure, announced as one, with its message.
      metricsPhase.value = 'error'
      metricsReason.value = ''
      metricsError.value = 'HTTP 500 metrics'
      await nextTick()

      const errored = section('metrics').querySelector('[data-state="error"]')
      expect(errored).toBeTruthy()
      expect(section('metrics').querySelector('[data-state="unavailable"]')).toBeNull()
      expect(errored!.textContent).toContain('HTTP 500 metrics')
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('a bottleneck row asks the camera for that exact edge', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      phase.value = 'ready'
      await nextTick()

      const focusBtn = analyticsDock(host)!.querySelector(
        '.bnl__actions button',
      ) as HTMLButtonElement
      expect(focusBtn).toBeTruthy()

      const focusOnEdge = getRequiredGeoTestGlobal('__GEO_TEST_FOCUS_ON_EDGE')
      focusBtn.click()
      await nextTick()

      // Direction included: A to B and B to A are different edges everywhere in this app.
      expect(focusOnEdge).toHaveBeenCalledWith('alice', 'bob')
    } finally {
      app.unmount()
      host.remove()
    }
  })

  /**
   * The dock covers the right-hand side of the canvas, and "Focus" is pressed inside the dock.
   *
   * The camera centres on the middle of what it is told is visible, and it is told nothing unless
   * this component measures the dock: it has no notion of panels. Left uninformed it centres on
   * the canvas' geometric middle, which on a window narrower than roughly 1144px is inside the
   * dock — the framed edge ends up behind the panel the user clicked in, a short one entirely.
   *
   * The number is measured, not assumed: the dock's width is `min(token, 100% - inset)`, so on a
   * narrow window it is the percentage that wins and a hard-coded 560 would be wrong exactly
   * where the overlap happens.
   */
  it('tells the camera how much of the canvas the dock is covering', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    const nativeRect = Element.prototype.getBoundingClientRect
    let rectSpy: { mockRestore: () => void } | null = null
    const rect = (left: number, right: number): DOMRect =>
      ({
        x: left,
        y: 0,
        left,
        right,
        top: 0,
        bottom: 600,
        width: right - left,
        height: 600,
        toJSON: () => ({}),
      }) as DOMRect

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      phase.value = 'ready'
      await nextTick()

      // jsdom lays nothing out, so the two elements this reads are given a geometry: a 1000px
      // host with a 560px dock hugging its right edge, plus a 12px gap.
      rectSpy = vi
        .spyOn(Element.prototype, 'getBoundingClientRect')
        .mockImplementation(function (this: Element) {
          if (this.classList.contains('root')) return rect(0, 1000)
          if (this.getAttribute('data-surface') === 'real-metrics-panel') return rect(428, 988)
          return nativeRect.call(this)
        })

      const focusBtn = analyticsDock(host)!.querySelector(
        '.bnl__actions button',
      ) as HTMLButtonElement
      const focusOnEdge = getRequiredGeoTestGlobal('__GEO_TEST_FOCUS_ON_EDGE')
      focusBtn.click()
      await nextTick()

      // Host right edge to dock left edge: the panel plus the gap beside it, since the camera
      // must not aim into that sliver either.
      expect(focusOnEdge).toHaveBeenCalledWith('alice', 'bob', { viewportInsetRight: 572 })
    } finally {
      rectSpy?.mockRestore()
      app.unmount()
      host.remove()
    }
  })

  /**
   * The companion of the test above: with nothing to measure — the environment has no layout, or
   * the dock is not mounted — the call is the one it has always been, not the same call carrying
   * a zero. "No inset" and "an inset of zero" must stay the same request.
   */
  it('asks for plain framing when there is no dock geometry to report', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      phase.value = 'ready'
      await nextTick()

      const focusBtn = analyticsDock(host)!.querySelector(
        '.bnl__actions button',
      ) as HTMLButtonElement
      const focusOnEdge = getRequiredGeoTestGlobal('__GEO_TEST_FOCUS_ON_EDGE')
      focusBtn.click()
      await nextTick()

      expect(focusOnEdge).toHaveBeenCalledWith('alice', 'bob')
      expect(focusOnEdge.mock.calls[0]).toHaveLength(2)
    } finally {
      app.unmount()
      host.remove()
    }
  })

  /**
   * Spec 007 §4.2, acceptance item 4: after acting on a bottleneck row the edge is *highlighted*,
   * so the user can see which one was meant.
   *
   * Framing is not enough on its own. The camera fits a segment, and where several trustlines run
   * between neighbouring participants the fitted region contains all of them — the user is left
   * looking at a bundle and guessing. These tests read the highlight overlay itself, which is the
   * same state the renderer paints from.
   */
  it('a bottleneck row both frames the edge and marks it, so the user can tell which one', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      phase.value = 'ready'
      await nextTick()

      const camera = getRequiredGeoTestGlobal('__GEO_TEST_CAMERA')
      const activeEdges = getRequiredGeoTestGlobal('__GEO_TEST_ACTIVE_EDGES')

      // A known starting point, so "moved" cannot be confused with "was already there".
      camera.panX = 7
      camera.panY = 8
      camera.zoom = 1.25
      expect(activeEdges.size).toBe(0)

      const focusBtn = analyticsDock(host)!.querySelector(
        '.bnl__actions button',
      ) as HTMLButtonElement
      expect(focusBtn).toBeTruthy()
      focusBtn.click()
      await nextTick()

      // The camera framed the segment: tight axis is Y, so zoom = 440 / 200.
      expect(camera.zoom).toBeCloseTo(2.2)
      expect(camera.panX).toBeCloseTo(60)
      expect(camera.panY).toBeCloseTo(-140)

      // Both ends of the edge really are on screen afterwards.
      const worldToScreen = getRequiredGeoTestGlobal('__GEO_TEST_WORLD_TO_SCREEN')
      const aliceOnScreen = worldToScreen(100, 100)
      const bobOnScreen = worldToScreen(300, 300)
      expect(aliceOnScreen.x).toBeCloseTo(280)
      expect(aliceOnScreen.y).toBeCloseTo(80)
      expect(bobOnScreen.x).toBeCloseTo(720)
      expect(bobOnScreen.y).toBeCloseTo(520)

      // ...and exactly that edge is lit, at full strength, and nothing else is.
      expect([...activeEdges.keys()]).toEqual([keyEdge('alice', 'bob')])
      expect(activeEdges.get(keyEdge('alice', 'bob'))).toBe(1)
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('the highlight outlives the camera jump and then expires on its own', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      phase.value = 'ready'
      await nextTick()

      const activeEdges = getRequiredGeoTestGlobal('__GEO_TEST_ACTIVE_EDGES')
      const prune = getRequiredGeoTestGlobal('__GEO_TEST_PRUNE_ACTIVE_EDGES')

      // The overlay's clock is pinned at 0 in the mock, so these are elapsed milliseconds.
      ;(analyticsDock(host)!.querySelector('.bnl__actions button') as HTMLButtonElement).click()
      await nextTick()

      // Still at full strength well past the moment the eye reaches the canvas: the highlight is
      // a deliberate answer to a click, not a flicker that is gone before it is looked at.
      prune(1_500)
      expect(activeEdges.get(keyEdge('alice', 'bob'))).toBe(1)

      // And it is temporary — no stale marker survives to confuse the next row that is clicked.
      prune(5_000)
      expect(activeEdges.size).toBe(0)
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('an edge the snapshot no longer has is neither framed nor highlighted', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    // The panel still lists `alice → bob` — it was polled before the graph changed — but the
    // laid-out snapshot no longer has that edge. This is the case the highlight must not paper
    // over: the camera honestly refuses to move, and a marker drawn anyway would point at
    // whatever now sits under that key.
    setGeoTestGlobal('__GEO_TEST_LAYOUT_LINKS', [])

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'edge', from: 'alice', to: 'bob' })
      phase.value = 'ready'
      await nextTick()

      const camera = getRequiredGeoTestGlobal('__GEO_TEST_CAMERA')
      const activeEdges = getRequiredGeoTestGlobal('__GEO_TEST_ACTIVE_EDGES')

      camera.panX = 7
      camera.panY = 8
      camera.zoom = 1.25

      const focusBtn = analyticsDock(host)!.querySelector(
        '.bnl__actions button',
      ) as HTMLButtonElement
      focusBtn.click()
      await nextTick()

      expect(camera.panX).toBe(7)
      expect(camera.panY).toBe(8)
      expect(camera.zoom).toBe(1.25)
      expect(activeEdges.size).toBe(0)
    } finally {
      app.unmount()
      host.remove()
    }
  })

  it('a node bottleneck opens that node instead of pretending an edge was framed', async () => {
    setUrl('/?mode=real')
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)

    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)

    try {
      await nextTick()

      const phase = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_PHASE_REF')
      const bottlenecks = getRequiredGeoTestGlobal('__GEO_TEST_ANALYTICS_BOTTLENECKS_REF')
      bottlenecks.value = makeAnalyticsBottlenecks({ kind: 'node', id: 'carol' })
      phase.value = 'ready'
      await nextTick()

      const wmOpen = getRequiredGeoTestGlobal('__GEO_TEST_WM_OPEN')
      wmOpen.mockClear()
      const focusOnEdge = getRequiredGeoTestGlobal('__GEO_TEST_FOCUS_ON_EDGE')
      const activeEdges = getRequiredGeoTestGlobal('__GEO_TEST_ACTIVE_EDGES')

      const focusBtn = analyticsDock(host)!.querySelector(
        '.bnl__actions button',
      ) as HTMLButtonElement
      focusBtn.click()
      await nextTick()
      await nextTick()

      expect(focusOnEdge).not.toHaveBeenCalled()
      // A node has no edge to name, so the edge highlight stays out of it entirely.
      expect(activeEdges.size).toBe(0)

      const opened = wmOpen.mock.calls
        .map((c) => c[0] as { type?: string; data?: { nodeId?: unknown } } | undefined)
        .filter(Boolean)
      expect(
        opened.some((o) => o?.type === 'node-card' && String(o?.data?.nodeId) === 'carol'),
      ).toBe(true)
    } finally {
      app.unmount()
      host.remove()
    }
  })
})

/**
 * 039 slice B, PR 1 - the legend and the colours at the level of the mounted root (the app composable is mocked,
 * as everywhere in this file; what is real here is the root's template and its wiring).
 * NOT seen here: pixels, the real resolution of the tokens (`vizMapping.test.ts`, `e2e/qa/data-colours.qa.ts`).
 */
describe('SimulatorAppRoot - legend and data colours (039 slice B)', () => {
  // 043 S1: the equivalent selector and the theme switcher live behind "Подробнее".
  beforeEach(() => storedDetails(true))
  afterEach(() => storedDetails(null))

  const role = (over: Record<string, unknown>) => ({ ...makeSelectedNode(String(over.id), String(over.id)), viz_shape_key: 'circle', ...over })
  const snapshotOf = (equivalent: string, nodes: Array<Record<string, unknown>>): GraphSnapshot =>
    ({ equivalent, generated_at: '2026-10-12T00:00:00Z', nodes, links: [] }) as unknown as GraphSnapshot

  function mount(search = '/?mode=real&ui=interact') {
    setUrl(search)
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    return { host, done: () => { app.unmount(); host.remove(); clearGeoTestGlobals('__GEO_TEST_SNAPSHOT'); vi.unstubAllGlobals() } }
  }

  it('the legend is a row INSIDE the measured bottom bar, in its main row, which comes after the Details row (043 S1)', async () => {
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', snapshotOf('UAH', [role({ id: 'a', net_sign: 1 }), role({ id: 'b', net_sign: -1 })]))
    const { host, done } = mount()
    await nextTick()
    // The element the bottom-bar measurer takes (`queryBottomStackElement`): the first `.ds-ov-bottom` that is not the history overlay.
    const measured = [...host.querySelectorAll('.ds-ov-bottom')].find((el) => !el.classList.contains('sar-interact-history-overlay'))!
    const legend = host.querySelector('[data-testid="legend"]')!
    expect(legend, 'a legend is rendered').not.toBeNull()
    expect(measured.contains(legend), 'inside the measured bar: its height is published with it').toBe(true)
    // 043 S1 (mock-up, decision 1): the main row - legend, total debt, Reset view - is the last row, so turning Details
    // on or off never moves it; the Details row is laid above it. (Until 043 S1 the controls came after the legend.)
    const detailsRow = measured.querySelector('[data-testid="bottom-details-row"]')!
    expect(detailsRow, 'counter-check: Details is on, its row is rendered').not.toBeNull()
    expect(detailsRow.compareDocumentPosition(legend) & Node.DOCUMENT_POSITION_FOLLOWING, 'the legend comes after the Details row').toBeTruthy()
    expect(legend.closest('.bb-main-row'), 'the legend is in the main row').not.toBeNull()
    expect(host.querySelectorAll('[data-testid="legend"]')).toHaveLength(1)
    done()
  })

  it('the title names the equivalent of the SHOWN snapshot, not the selector (which may run ahead)', async () => {
    // The mocked app's selector says UAH; the snapshot on screen is of EUR (as after a switch that has not landed, reversed).
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', snapshotOf(' eur ', [role({ id: 'a', net_sign: 1 })]))
    const { host, done } = mount()
    await nextTick()
    expect(host.querySelector('[data-testid="legend-title"]')?.textContent).toBe(t('legend.titleWithEquivalent', { eq: 'EUR' }))
    // Counter-check: the selector (the mocked app's `eq` is UAH, with one option here - 048 S1: then shown as text) does not say EUR.
    const selector = host.querySelector<HTMLElement>('[data-testid="equivalent-select"], [data-testid="equivalent-text"]')
    expect(selector, 'the equivalent selector is on screen').not.toBeNull()
    expect(selector instanceof HTMLSelectElement ? selector.value : selector!.textContent).not.toBe('EUR')
    done()
  })

  it('entries follow the snapshot: only what it contains; no Hub without a participant of type hub', async () => {
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', snapshotOf('UAH', [role({ id: 'a', net_sign: 1, links_count: 99, name: 'Hub' }), role({ id: 'b', net_sign: null })]))
    const { host, done } = mount()
    await nextTick()
    const ids = () => [...host.querySelectorAll('[data-testid="legend"] [data-legend-role]')].map((el) => el.getAttribute('data-legend-role'))
    expect(ids()).toEqual(['creditor', 'unknown', 'person'])
    done()
  })

  it('no scene, or a scene without participants: no legend, and its row takes no element of the bar', async () => {
    for (const snap of [null, snapshotOf('UAH', [])]) {
      if (snap) setGeoTestGlobal('__GEO_TEST_SNAPSHOT', snap)
      const { host, done } = mount()
      await nextTick()
      expect(host.querySelector('[data-testid="legend"]'), snap ? 'empty scene' : 'no scene').toBeNull()
      expect(host.querySelector('.ds-ov-bottom__legend')?.children.length ?? 0).toBe(0)
      done()
    }
  })

  it('the colours are resolved on mount and again after the theme changed in the same mounted app', async () => {
    const resolve = getGeoTestGlobal('__GEO_TEST_RESOLVE_VIZ_COLORS')!
    resolve.mockClear()
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', snapshotOf('UAH', [role({ id: 'a', net_sign: 1 })]))
    const { host, done } = mount('/?mode=real&ui=interact&theme=saas')
    await nextTick()
    expect(resolve, 'on mount').toHaveBeenCalledTimes(1)
    const root = host.querySelector<HTMLElement>('.root')!
    expect(root.getAttribute('data-theme')).toBe('saas')

    let themeAtResolve: string | null = null
    resolve.mockImplementation(() => { themeAtResolve = root.getAttribute('data-theme') })
    const select = host.querySelector<HTMLSelectElement>('select[data-testid="theme-select"]')!
    select.value = 'hud'
    select.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    await nextTick()
    expect(resolve, 'after the switch').toHaveBeenCalledTimes(2)
    expect(themeAtResolve, 'the tokens are read when the new theme is already on the root element').toBe('hud')
    resolve.mockReset()
    done()
  })
})

/**
 * 043 S1, reproducer (a): the first view of the demo has at most seven controls in its two bars (spec 043, decision 1;
 * Verification plan 1 (a)). Counted in the DOM of the mounted root, never in the source: an element counts when it is
 * an interactive control (`button`, `select`, `input`, `[role=button]`, and `summary` - a menu of the bars opens from
 * one), and a person can reach it - enabled, not `hidden`/`inert`, not `display: none`/`visibility: hidden` on itself
 * or an ancestor, not `tabindex="-1"`, and not inside a closed `<details>` (its own summary excepted).
 *
 * LIMITS: happy-dom has no layout, so "visible" is read from attributes and computed style, not from a box; the mocked
 * app has no admin token; the first view has no control of the administrator (048 S2: "Admin ▾" is gone, the run row
 * is behind "Подробнее").
 */
describe('SimulatorAppRoot - 043 S1: the first view (Details off by default)', () => {
  const CONTROL = 'button, select, input, summary, [role="button"]'

  function reachable(el: HTMLElement): boolean {
    if ((el as HTMLButtonElement).disabled) return false
    if (el.getAttribute('tabindex') === '-1') return false
    if (el.closest('[hidden], [inert]')) return false
    for (let cur: HTMLElement | null = el; cur; cur = cur.parentElement) {
      const s = getComputedStyle(cur)
      if (s.display === 'none' || s.visibility === 'hidden') return false
      if (cur !== el && cur.tagName === 'DETAILS' && !(cur as HTMLDetailsElement).open) {
        // Only the summary of a closed <details> is reachable.
        if (!(el.tagName === 'SUMMARY' && el.parentElement === cur)) return false
      }
    }
    return true
  }

  function barControls(host: HTMLElement): string[] {
    const bars = [...host.querySelectorAll<HTMLElement>('.ds-ov-top, .ds-ov-bottom')].filter((b) => !b.classList.contains('sar-interact-history-overlay'))
    return bars.flatMap((bar) => [...bar.querySelectorAll<HTMLElement>(CONTROL)].filter(reachable))
      .map((el) => `${el.tagName.toLowerCase()}[${el.getAttribute('aria-label') ?? el.dataset.testid ?? (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 24)}]`)
  }

  function mount(search: string) {
    getSimStorage().readDetailsOpen.mockImplementation(() => null)
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', {
      equivalent: 'UAH',
      generated_at: '2026-10-16T00:00:00Z',
      nodes: [makeSelectedNode('alice', 'Alice'), makeSelectedNode('bob', 'Bob')],
      links: [{ source: 'alice', target: 'bob', trust_limit: '100', used: '25', available: '75' }],
    })
    setUrl(search)
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    return { host, done: () => { app.unmount(); host.remove(); clearGeoTestGlobals('__GEO_TEST_SNAPSHOT'); vi.unstubAllGlobals() } }
  }

  for (const search of ['/?mode=real&ui=interact&testMode=0', '/?mode=real&testMode=0', '/?testMode=0']) {
    it(`reproducer (a): ${search} - at most 7 reachable controls in the two bars, the canvas being everything else`, async () => {
      const { host, done } = mount(search)
      try {
        await nextTick()
        await nextTick()
        const controls = barControls(host)
        // Anti-vacuum: the counter does see the bars (the bottom bar always has the toggle or at least one control).
        expect(host.querySelector('.ds-ov-top'), 'the top bar is mounted').not.toBeNull()
        expect(controls.length, `the counter found controls at all: ${JSON.stringify(controls)}`).toBeGreaterThan(0)
        expect(controls.length, `reachable controls in the two bars: ${JSON.stringify(controls)}`).toBeLessThanOrEqual(7)
      } finally {
        done()
      }
    })
  }
})

/**
 * 043 S1: the "Подробнее" toggle and what it gates, observed in the mounted root (spec 043, decisions 1 and 4).
 * Hidden means NOT RENDERED: every check below looks for the element in the DOM, not at its style.
 */
describe('SimulatorAppRoot - 043 S1: the Details toggle and the moved surfaces', () => {
  // 048 S1 (decisions 4 and 5): a visitor's service controls. The source and the quality are `?debug=1`'s, the "Run
  // <state>" badge is gone - each has its own test below. 048 S2: the Labels select is gone (amendment 11), the analytics
  // toggle is `?debug=1`'s (amendment 10); the whole of "Подробнее" by addressee - the test "048 S2 (amendment 12)".
  const SERVICE = [
    ['theme switcher', '[data-testid="theme-select"]'],
    ['equivalent', '[data-testid="equivalent-select"], [data-testid="equivalent-text"]'],
    ['layout', '[data-testid="layout-select"]'],
    ['graph navigator', '[role="region"][data-testid="graph-navigator"]'],
    ['full metrics', '[data-testid="system-metrics"]'],
  ] as const
  const DEVELOPER = [
    ['source select', '[data-testid="source-select"]'],
    ['quality', '[data-testid="quality-select"]'],
    ['SSE badge', '[data-testid="sse-badge"]'],
    ['analytics toggle', '[data-testid="analytics-toggle"]'],
  ] as const

  const SNAPSHOT = {
    equivalent: 'UAH',
    generated_at: '2026-10-16T00:00:00Z',
    nodes: [makeSelectedNode('alice', 'Alice'), makeSelectedNode('bob', 'Bob')],
    links: [{ source: 'alice', target: 'bob', trust_limit: '100', used: '25', available: '75' }],
  }

  function mount(search = '/?mode=real&ui=interact') {
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', SNAPSHOT)
    setUrl(search)
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    return {
      host,
      app,
      done: () => {
        app.unmount()
        host.remove()
        storedDetails(null)
        clearGeoTestGlobals('__GEO_TEST_SNAPSHOT', '__GEO_TEST_INTERACT_HISTORY', '__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_DEBUG_UI', '__GEO_TEST_ACCESS_TOKEN')
        vi.unstubAllGlobals()
      },
    }
  }

  const present = (host: HTMLElement) => SERVICE.filter(([, sel]) => host.querySelector(sel)).map(([name]) => name)
  const toggle = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('[data-testid="details-toggle"]')!

  /** The bindings of the mounted root (`<script setup>`): the mocked actions it wires its children to. */
  function rootSetup(app: ReturnType<typeof mountSimulatorAppRoot>): Record<string, any> {
    const wrapper = (app as unknown as { _instance: { subTree: { component: { setupState: Record<string, any> } } } })._instance
    return wrapper.subTree.component.setupState
  }

  it('off by default: no service control is rendered; the first row has the name, the state, the actions and the toggle', async () => {
    storedDetails(null)
    const { host, done } = mount()
    try {
      await nextTick()
      expect(present(host), 'service controls rendered with Details off').toEqual([])
      expect(toggle(host).getAttribute('aria-expanded')).toBe('false')
      const top = host.querySelector('.ds-ov-top')!
      for (const id of ['network-name', 'network-state', 'actionbar-payment', 'actionbar-trustline', 'actionbar-clearing', 'details-toggle']) {
        expect(top.querySelector(`[data-testid="${id}"]`), `${id} is in the top bar`).not.toBeNull()
      }
      expect(host.querySelector('[data-testid="network-state"]')?.tagName, 'the state is text, not a control').toBe('SPAN')
      // The total debt is in the bottom row, compact.
      const bottom = [...host.querySelectorAll('.ds-ov-bottom')].find((el) => !el.classList.contains('sar-interact-history-overlay'))!
      // 046 wave 1, full (2026-10-19): by test id; the label is the dictionary's ("Total debt" / "Общий долг").
      expect(bottom.querySelector('[data-testid="system-balance"] [data-testid="sbb-total-debt"]')).not.toBeNull()
      expect(bottom.querySelector('[data-testid="system-balance"] [data-testid="sbb-utilization"]')).toBeNull()
    } finally {
      done()
    }
  })

  it('on: every service control appears without a reload, the choice is written, and off removes them again', async () => {
    storedDetails(null)
    const writes = getSimStorage().writeDetailsOpen
    writes.mockClear()
    const { host, done } = mount()
    try {
      await nextTick()
      toggle(host).click()
      await nextTick()
      expect(present(host), 'every service control is rendered').toEqual(SERVICE.map(([name]) => name))
      expect(toggle(host).getAttribute('aria-expanded')).toBe('true')
      expect(writes).toHaveBeenLastCalledWith(true)
      // The navigator is a button of the bottom row now.
      expect(host.querySelector('.graph-navigator')!.closest('.ds-ov-bottom')).not.toBeNull()

      toggle(host).click()
      await nextTick()
      expect(present(host), 'off again: nothing left').toEqual([])
      expect(writes).toHaveBeenLastCalledWith(false)
    } finally {
      done()
    }
  })

  it('a remembered "on" is applied on the first paint (a reload)', async () => {
    storedDetails(true)
    const { host, done } = mount()
    try {
      await nextTick()
      expect(present(host)).toEqual(SERVICE.map(([name]) => name))
    } finally {
      done()
    }
  })

  /**
   * 048 S1 (decision 5): the source, the render quality and the SSE badge are the developer's - rendered with `?debug=1`
   * only, never by "Подробнее" alone. Red before S1: all three were in every visitor's "Подробнее". Hidden means not
   * rendered (the counter-check: the same query finds all three with `debug`).
   */
  it('?debug=1 alone adds the source, the quality, the SSE badge and (048 S2) the analytics toggle to "Подробнее"', async () => {
    const devPresent = (host: HTMLElement) => DEVELOPER.filter(([, sel]) => host.querySelector(sel)).map(([name]) => name)
    for (const debug of [false, true]) {
      storedDetails(true)
      setGeoTestGlobal('__GEO_TEST_DEBUG_UI', debug)
      const { host, done } = mount('/?mode=real')
      try {
        await nextTick()
        expect(present(host), `debug ${debug}: the visitor's controls`).toEqual(SERVICE.map(([name]) => name))
        expect(devPresent(host), `debug ${debug}`).toEqual(debug ? DEVELOPER.map(([name]) => name) : [])
      } finally {
        done()
      }
    }
  })

  // 048 S1 (decision 4): no "Run <state>" badge anywhere (the state of the first row says it), with or without Details.
  it('no "Run <state>" badge: the state is said once, in the first row', async () => {
    storedDetails(true)
    const { host, done } = mount('/?mode=real')
    try {
      await nextTick()
      expect(host.querySelector('[data-testid="top-details-row"]'), 'Details is on').not.toBeNull()
      expect(host.querySelector('[aria-label="Run state"]')).toBeNull()
      expect(host.querySelectorAll('[role="status"][aria-live="polite"]').length, 'one live region for the run: the first row\'s state')
        .toBeGreaterThanOrEqual(1)
      expect(host.querySelector('[data-testid="network-state"]')?.getAttribute('role')).toBe('status')
    } finally {
      done()
    }
  })

  /**
   * 048 S2 (amendment 12; Verification plan 2 of the brief): the whole of "Подробнее", by addressee - read from the
   * mounted root as a list of what each part holds (a control by its test id, aria-label or text; a passive item by
   * its test id), by structure, not by words. A visitor: top - the theme, the language, the stop reason (when there is
   * one); the network figures; bottom - the equivalent, the layout, "Inspect graph". The administrator: the same plus the
   * row of the own run and the payments badge. Anti-vacuum: both lists are read by the same function on the same page,
   * and differ exactly by the administrator's row. Red before S2: the visitor's top had the scenario, Stop, the tempo,
   * Apply and the badge, and the bottom had Labels.
   */
  /**
   * 046 wave 1, §15 Codex round (advisor's condition, 2026-10-19; 048 decision 3, AGENTS.md section 12): the line of a run
   * ended by an error is the interface's sentence with the code, and the server's FULL text stays reachable - the
   * `title` of the line. Red before: the title repeated the sentence (`String(lastError)`).
   */
  it('046 wave 1: the error line of a run ended by an error says the sentence; its title is the server full text', async () => {
    const { host, app, done } = mount('/?mode=real')
    try {
      const real = rootSetup(app).real as Record<string, unknown>
      const line = runStatusErrorLine({ state: 'error', last_error: { code: 'REAL_MODE_TICK_FAILED', message: 'tick failed at step 12', at: 't' } }, () => true)!
      real.lastErrorLine = line
      real.lastError = renderRunErrorLine(line, 'en')
      await nextTick()
      const value = host.querySelector('[data-testid="top-error"] .ds-value')!
      expect(value.textContent, 'the sentence with the code').toBe(t('runLine.errorStopped', { code: 'REAL_MODE_TICK_FAILED' }))
      expect(value.getAttribute('title'), 'the server full text').toBe('REAL_MODE_TICK_FAILED: tick failed at step 12')
      // Counter-check: a line without a server text (the idle stop) keeps its own text as the title.
      real.lastErrorLine = { kind: 'dict', key: 'runLine.idleStopped' }
      real.lastError = t('runLine.idleStopped')
      await nextTick()
      expect(host.querySelector('[data-testid="top-error"] .ds-value')!.getAttribute('title')).toBe(t('runLine.idleStopped'))
    } finally {
      done()
    }
  })

  it('048 S2 (amendment 12): the whole of «Подробнее» - the visitor\'s, and the administrator\'s (the same plus the own run)', async () => {
    const contents = (host: HTMLElement) => {
      const list = (root: Element | null) => (root ? [...root.querySelectorAll<HTMLElement>(
        'select, button, input, summary, a[href], [role="button"], [tabindex]:not([tabindex="-1"]), [data-testid="stop-reason"], [data-testid="run-payments"], [data-testid="sse-badge"], [data-testid="equivalent-text"]',
      )] : []).filter((el) => {
        // A control inside a closed menu is not on screen (its summary is): the navigator's own fields.
        const closed = el.closest('details:not([open])')
        return !closed || (el.tagName === 'SUMMARY' && el.parentElement === closed)
      }).map((el) => el.dataset.testid ?? el.getAttribute('aria-label') ?? (el.textContent ?? '').replace(/\s+/g, ' ').trim())
      const figures = [...host.querySelectorAll<HTMLElement>('[data-testid="system-metrics"] [data-testid^="sbb-"]')].map((el) => el.dataset.testid)
      return {
        top: list(host.querySelector('[data-testid="top-details-row"]')),
        figures,
        bottom: list(host.querySelector('[data-testid="bottom-details-row"]')),
      }
    }
    const VISITOR = {
      top: ['theme-select', 'language-select'],
      figures: ['sbb-available', 'sbb-trustlines', 'sbb-participants', 'sbb-utilization'],
      // 046 wave 1, full (2026-10-19): the controls carry test ids (their words are the dictionary's).
      bottom: ['equivalent-text', 'layout-select', 'graph-navigator-summary'],
    }
    // 049 S2: a visitor's living run has the tempo by words (decision 10); everyone has «Показывать события» (decision 5).
    const TEMPO = ['tempo-step-slow', 'tempo-step-normal', 'tempo-step-fast']
    const FEED = 'events-feed-toggle'
    for (const [who, token, ownRun] of [
      ['a visitor (no token)', '', [...TEMPO, FEED] as string[]],
      ['a visitor (a participant JWT)', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl', [...TEMPO, FEED] as string[]],
      ['the administrator (an admin token)', 'admin-token', ['scenario-select', 'run-stop', 'intensity-input', 'intensity-apply', FEED, 'run-payments']],
    ] as const) {
      storedDetails(true)
      setGeoTestGlobal('__GEO_TEST_ACCESS_TOKEN', token)
      const { host, app, done } = mount('/?mode=real')
      try {
        const real = rootSetup(app).real as Record<string, unknown>
        real.runStatus = { run_id: 'run-mock', scenario_id: 'sc1', state: 'running' }
        await nextTick()
        expect(contents(host), `${who}, a running run`).toEqual({ ...VISITOR, top: [...VISITOR.top, ...ownRun] })
        // A stopped run with a reason: the reason is passive text for everyone (044); Start replaces Stop in the row.
        real.runStatus = { run_id: 'run-mock', scenario_id: 'sc1', state: 'stopped', stop_source: 'server', stop_reason: 'idle_no_subscribers' }
        await nextTick()
        const stoppedRow = token === 'admin-token' ? ['scenario-select', 'run-start', 'intensity-input', 'intensity-apply', FEED, 'stop-reason', 'run-payments'] : [FEED, 'stop-reason']
        expect(contents(host).top, `${who}, a stopped run`).toEqual([...VISITOR.top, ...stoppedRow])
      } finally {
        done()
      }
    }
  })

  // The polling half is the real composable's (`useSimulatorApp.analyticsGate.test.ts`: `analytics.isVisible`, which
  // gates the poll, follows the getter the root feeds); here the analytics are mocked and only the DOM is observed.
  it('a remembered open analytics dock is not shown while Details is off, and comes back with it', async () => {
    storedDetails(null)
    getSimStorage().readAnalyticsPanelOpen.mockReturnValue(true)
    setGeoTestGlobal('__GEO_TEST_DEBUG_UI', true) // 048 S2: the dock is the developer's (`?debug=1`)
    const { host, done } = mount()
    try {
      await nextTick()
      expect(host.querySelector('[data-surface="real-metrics-panel"]'), 'no dock without its toggle on screen').toBeNull()
      toggle(host).click()
      await nextTick()
      await nextTick()
      expect(host.querySelector('[data-surface="real-metrics-panel"]'), 'the remembered dock is back with Details').not.toBeNull()
    } finally {
      getSimStorage().readAnalyticsPanelOpen.mockReturnValue(null)
      done()
    }
  })

  it('the history is three lines in the bottom overlay shell (was eight)', async () => {
    // 046 wave 3 (2026-10-19): a history line is kept as its key and data (BACKLOG 046-3); the data tells the lines apart.
    setGeoTestGlobal('__GEO_TEST_INTERACT_HISTORY', [1, 2, 3, 4, 5].map((i) => ({ id: `h${i}`, icon: '•', line: { key: 'history.trustlineClosed', vars: { from: 'action', to: String(i) } }, timeText: `10:0${i}` })))
    const { host, done } = mount()
    try {
      await nextTick()
      const shell = host.querySelector('.ds-ov-bottom.sar-interact-history-overlay')
      expect(shell, 'the overlay shell hosts it').not.toBeNull()
      const items = [...shell!.querySelectorAll('.interact-history__item')].map((li) => (li.textContent ?? '').replace(/\s+/g, ' ').trim())
      expect(items).toHaveLength(3)
      expect(items[0]).toContain('action → 5')
    } finally {
      done()
    }
  })

  /**
   * 049 S2 (decision 5): «Показывать события» - off by default (nothing remembered: «Последние действия», three lines, no
   * «вы»); on: remembered like the theme, the history is «События сети», five lines, an own line marked «вы». The lines
   * the composable gives are mocked here (what it writes: `eventFeed.p049s2.test.ts`); the switch and the log are the root's.
   */
  it('049 S2: «Показывать события» - off by default; on: remembered, «Network events», five lines, own lines marked «you»', async () => {
    storedDetails(true)
    setGeoTestGlobal('__GEO_TEST_INTERACT_HISTORY', [1, 2, 3, 4, 5, 6].map((i) => ({
      id: `h${i}`, seq: i, icon: '•', source: i === 6 ? 'own' : 'network', timeText: `10:0${i}`,
      line: { key: 'history.trustlineClosed', vars: { from: 'action', to: String(i) } },
    })))
    const { host, done } = mount('/?mode=real')
    try {
      await nextTick()
      const box = () => host.querySelector<HTMLInputElement>('[data-testid="events-feed-toggle"]')
      const log = () => host.querySelector<HTMLElement>('.sar-interact-history-overlay [data-testid="recent-actions"]')!
      const items = () => [...log().querySelectorAll('.interact-history__text')].map((e) => e.textContent!.trim())
      expect(box(), 'the switch is behind «Подробнее»').not.toBeNull()
      expect(box()!.checked, 'off by default (nothing remembered)').toBe(false)
      expect(log().querySelector('.interact-history__header')!.textContent).toBe('Recent actions')
      expect(items(), 'off: three lines, no mark').toHaveLength(3)
      expect(items()[0]).toBe('Trustline closed: action → 6')

      box()!.click()
      await nextTick()
      expect(getSimStorage().writeEventsFeedOn, 'remembered like the theme').toHaveBeenLastCalledWith(true)
      expect(log().getAttribute('data-feed')).toBe('on')
      expect(log().querySelector('.interact-history__header')!.textContent).toBe('Network events')
      expect(items(), 'on: five lines').toHaveLength(5)
      expect(items()[0], 'an own line is marked').toBe('you · Trustline closed: action → 6')
      expect(items()[1], 'a network line is not').toBe('Trustline closed: action → 5')

      box()!.click()
      await nextTick()
      expect(getSimStorage().writeEventsFeedOn).toHaveBeenLastCalledWith(false)
      expect(items()).toHaveLength(3)
    } finally {
      getSimStorage().writeEventsFeedOn.mockClear()
      done()
    }
  })

  it("049 S2 (decision 10): a visitor's tempo step reaches the run - the same action as the administrator's Apply", async () => {
    storedDetails(true)
    const { host, app, done } = mount('/?mode=real')
    try {
      await nextTick()
      const setup = rootSetup(app)
      const real = setup.real as Record<string, unknown>
      const actions = setup.realActions as Record<string, Mock>
      real.runId = 'run-1'
      real.runStatus = { run_id: 'run-1', state: 'running', intensity_percent: 5 }
      await nextTick()
      for (const fn of Object.values(actions)) fn.mockClear?.()
      const step = (id: string) => host.querySelector<HTMLButtonElement>(`[data-testid="tempo-step-${id}"]`)!
      expect(step('normal').getAttribute('aria-pressed'), "the run's tempo is pressed").toBe('true')
      step('slow').click()
      expect(actions.setIntensityPercent).toHaveBeenCalledWith(2)
      expect(actions.applyIntensity).toHaveBeenCalledTimes(1)
      expect(actions.setIntensityPercent.mock.invocationCallOrder[0]!, 'the percent is set before it is applied')
        .toBeLessThan(actions.applyIntensity.mock.invocationCallOrder[0]!)
    } finally {
      done()
    }
  })

  it('one onboarding hint: shown on the first view, gone after the first action (its close button, or the toggle)', async () => {
    storedDetails(null)
    const first = mount()
    try {
      await nextTick()
      const hints = first.host.querySelectorAll('[data-testid="onboarding-hint"]')
      expect(hints).toHaveLength(1)
      // 048 item 13 (a), 046 wave 1: the three actions in the words of the protocol (no longer "press Send Payment").
      expect(hints[0]!.querySelector('[data-testid="onboarding-protocol"]')?.textContent).toBe(t('onboarding.protocol'))
      expect(hints[0]!.textContent).not.toContain('Send Payment')
      expect(hints[0]!.querySelector('.sar-onboarding-hint__text')!.textContent!.replace(/\s+/g, ' ').trim(), 'two sentences, a space between')
        .toBe(`${t('onboarding.protocol')} ${t('onboarding.explore')}`)
      expect(hints[0]!.closest('.ds-ov-top'), 'it hangs under the top bar').not.toBeNull()
      hints[0]!.querySelector<HTMLButtonElement>('[data-testid="onboarding-close"]')!.click()
      await nextTick()
      expect(first.host.querySelector('[data-testid="onboarding-hint"]')).toBeNull()
    } finally {
      first.done()
    }

    // 043 S2: remembered - the dismissal is written, and a load that reads it shows no hint.
    expect(getSimStorage().writeOnboardingDismissed, 'the dismissal is remembered').toHaveBeenLastCalledWith(true)
    getSimStorage().readOnboardingDismissed.mockImplementation(() => true)
    const back = mount('/?mode=real')
    try {
      await nextTick()
      expect(back.host.querySelector('[data-testid="onboarding-hint"]'), 'a dismissed hint does not come back on the next load').toBeNull()
    } finally {
      back.done()
      getSimStorage().readOnboardingDismissed.mockImplementation(() => null)
    }

    // The old Auto-Run address is the same screen: the hint names the three actions, which are on it (decision 2).
    const second = mount('/?mode=real')
    try {
      await nextTick()
      const hint = second.host.querySelector('[data-testid="onboarding-hint"]')
      expect(hint, 'shown on the old Auto-Run address too').not.toBeNull()
      expect(hint!.querySelector('[data-testid="onboarding-protocol"]'), 'the actions it names are on this screen').not.toBeNull()
      expect(second.host.querySelector('[data-testid="actionbar-payment"]')).not.toBeNull()
      toggle(second.host).click()
      await nextTick()
      expect(second.host.querySelector('[data-testid="onboarding-hint"]'), 'gone after "Подробнее"').toBeNull()
    } finally {
      second.done()
    }
  })

  it('an interact flow started from the first row dismisses the hint', async () => {
    storedDetails(null)
    const { host, done } = mount()
    try {
      await nextTick()
      host.querySelector<HTMLButtonElement>('[data-testid="actionbar-payment"]')!.click()
      await nextTick()
      await nextTick()
      expect(host.querySelector('[data-testid="onboarding-hint"]')).toBeNull()
    } finally {
      done()
    }
  })

  /**
   * F-041-9 (`TopBar.wiring.test.ts` read the parent's template as a string and looked for `@start-run="..."` etc.).
   * Observed instead: a click or a change on the control in the MOUNTED root reaches the mocked action it is wired to.
   */
  it('wiring (was a source-text guard): the controls of the top bar reach the actions of the app', async () => {
    storedDetails(true)
    setGeoTestGlobal('__GEO_TEST_DEBUG_UI', true) // 048 S1: "Источник" is `?debug=1`'s
    // 048 S2 (amendment 8): the scenario, Start/Stop and the tempo are the administrator's row - an admin token (the mock
    // reads it at the mount).
    setGeoTestGlobal('__GEO_TEST_ACCESS_TOKEN', 'admin-token')
    const { host, app, done } = mount('/?mode=real&debug=1')
    clearGeoTestGlobals('__GEO_TEST_ACCESS_TOKEN')
    try {
      await nextTick()
      const setup = rootSetup(app)
      const real = setup.real as Record<string, unknown>
      const actions = setup.realActions as Record<string, Mock>
      for (const fn of Object.values(actions)) fn.mockClear?.()

      // 046 wave 1, full (2026-10-19): by test id - the accessible name is the dictionary's, in the interface language.
      const scenario = host.querySelector<HTMLSelectElement>('select[data-testid="scenario-select"]')!
      scenario.dispatchEvent(new Event('change', { bubbles: true }))
      expect(actions.setSelectedScenarioId).toHaveBeenCalledWith('')

      const intensity = host.querySelector<HTMLInputElement>('input[data-testid="intensity-input"]')!
      intensity.value = '140'
      intensity.dispatchEvent(new Event('input', { bubbles: true }))
      expect(actions.setIntensityPercent, 'clamped to 0..100 on the way').toHaveBeenCalledWith(100)

      const byId = (id: string) => host.querySelector<HTMLButtonElement>(`.ds-ov-top button[data-testid="${id}"]`)
      real.runId = 'run-1'
      real.runStatus = { run_id: 'run-1', state: 'running' }
      await nextTick()
      // 043 S2: "Поток" in the first row - pause on a living run, resume on a paused one. 048 S1: the service row's own
      // Pause/Resume are gone; "Поток" is the one path.
      const flow = () => host.querySelector<HTMLButtonElement>('[data-testid="flow-toggle"]')
      expect(flow()?.getAttribute('aria-pressed'), 'the flow is on').toBe('true')
      flow()!.click()
      expect(actions.pause, 'the flow button on a living run reaches pause').toHaveBeenCalledTimes(1)
      byId('run-stop')!.click()
      expect(actions.stop).toHaveBeenCalledTimes(1)
      byId('intensity-apply')!.click()
      expect(actions.applyIntensity).toHaveBeenCalledTimes(1)
      real.runStatus = { run_id: 'run-1', state: 'paused' }
      await nextTick()
      expect(flow()?.getAttribute('aria-pressed'), 'the flow is off').toBe('false')
      flow()!.click()
      expect(actions.resume, 'the flow button on a paused run reaches resume').toHaveBeenCalledTimes(1)
      real.runId = null
      real.runStatus = null
      real.selectedScenarioId = 'sc1'
      await nextTick()
      expect(flow(), 'no run: no flow button (Start is under Details)').toBeNull()
      byId('run-start')!.click()
      expect(actions.startRun).toHaveBeenCalledTimes(1)

      // 048 S2 (amendment 9): "Admin ▾" and its four bindings (runs, stop all, attach, stop one) are gone - nothing to walk.

      const theme = host.querySelector<HTMLSelectElement>('select[data-testid="theme-select"]')!
      theme.value = 'hud'
      theme.dispatchEvent(new Event('change', { bubbles: true }))
      await nextTick()
      expect(host.querySelector('.root')!.getAttribute('data-theme'), 'the theme switch reaches the root').toBe('hud')

      // 043 S2: "Источник" (bottom row, Details) is the one switch that navigates - the pipeline is replaced. The same
      // source navigates nowhere; the other one reloads with `?mode=` and without `?ui=`.
      const nav = stubLocationHrefSetter()
      try {
        const source = host.querySelector<HTMLSelectElement>('[data-testid="source-select"]')!
        expect(source.value, 'the select shows the source on screen').toBe('real')
        const before = nav.hrefSet.mock.calls.length
        source.value = 'real'
        source.dispatchEvent(new Event('change', { bubbles: true }))
        expect(nav.hrefSet.mock.calls.length, 'the same source: no navigation').toBe(before)
        source.value = 'fixtures'
        source.dispatchEvent(new Event('change', { bubbles: true }))
        expect(nav.hrefSet.mock.calls.length, 'the other source: one navigation').toBe(before + 1)
        const to = new URL(String(nav.hrefSet.mock.calls.at(-1)?.[0] ?? ''), 'http://localhost')
        // 048 S1 (decision 5), reproducer (д), the root's half: `?debug=1` is carried across the switch (it was dropped
        // until S1: the switch then left the developer on a page without the switch).
        expect([to.searchParams.get('mode'), to.searchParams.get('ui'), to.searchParams.get('debug')], 'offline snapshot, no ?ui=, ?debug=1 kept')
          .toEqual(['fixtures', null, '1'])
      } finally {
        nav.restore()
      }
    } finally {
      done()
    }
  })
})

/**
 * 043 S1, fix-delta review (class 1): a kept-alive edge popup must never print the figures of one equivalent under
 * the name of another. Path: an edge of a UAH scene, its "Send Payment" (the popup stays as frozen context), then
 * the scene is replaced by the EUR one (the Equivalent selector of the bottom bar, from the keyboard).
 */
describe('SimulatorAppRoot - 043 S1 review: the kept-alive edge popup and a scene replacement', () => {
  const UAH = {
    equivalent: 'UAH', generated_at: '2026-10-16T00:00:00Z',
    nodes: [makeSelectedNode('alice', 'Alice'), makeSelectedNode('bob', 'Bob')],
    links: [{ source: 'alice', target: 'bob', trust_limit: '500.00', used: '120.00', available: '380.00', status: 'active' }],
  }

  it('the EUR scene does not keep the frozen UAH figures on screen under EUR', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setGeoTestGlobal('__GEO_TEST_SNAPSHOT', UAH)
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    const popupText = () => (host.querySelector('[data-testid="edge-detail-popup"]')?.textContent ?? '').replace(/\s+/g, ' ')
    try {
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = [{
        from_pid: 'alice', from_name: 'Alice', to_pid: 'bob', to_name: 'Bob', equivalent: 'UAH', limit: '500.00', used: '120.00', available: '380.00', status: 'active',
      } as TrustlineInfo]
      await nextTick()
      await nextTick()
      expect(popupText(), 'counter-check: the popup shows the UAH line').toContain('500.00')
      expect(popupText()).toContain('UAH')

      host.querySelector<HTMLButtonElement>('[data-testid="edge-send-payment"]')!.click()
      await nextTick()
      await nextTick()
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]'), 'counter-check: kept alive as frozen context').toHaveLength(1)
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]'), 'the payment panel is open').toHaveLength(1)

      // Capture: the unit of the frozen figures is frozen with them, and the popup prints THAT one (a value the live
      // scene never has proves it is not the live one that is printed).
      const setup0 = (app as unknown as { _instance: { subTree: { component: { setupState: Record<string, any> } } } })._instance.subTree.component.setupState
      expect(setup0.wmEdgeDetail.frozenEquivalent.value, 'the equivalent is captured at freeze time').toBe('UAH')
      setup0.wmEdgeDetail.frozenEquivalent.value = 'XQ1'
      await nextTick()
      // 045 S3: the equivalent is said once, in the title of the window (`edge-unit`), the limit in its own span.
      const shown = (id: string) => host.querySelector(`[data-testid="edge-detail-popup"] [data-testid="${id}"]`)?.textContent?.trim()
      expect([shown('edge-limit'), shown('edge-unit')], 'the kept-alive popup prints the captured equivalent, not the live one').toEqual(['500.00', 'XQ1'])
      setup0.wmEdgeDetail.frozenEquivalent.value = 'UAH'
      await nextTick()

      // The EUR scene replaces the UAH one.
      const wrapper = (app as unknown as { _instance: { subTree: { component: { setupState: Record<string, any> } } } })._instance
      wrapper.subTree.component.setupState.state.snapshot = { ...UAH, equivalent: 'EUR', links: [{ ...UAH.links[0], trust_limit: '7.00' }] }
      getRequiredGeoTestGlobal('__GEO_TEST_SCENE_EQ_REF').value = 'EUR'
      await nextTick()
      await nextTick()

      const text = popupText()
      expect(text.includes('500.00') && text.includes('EUR'), `frozen UAH figures labelled EUR: "${text}"`).toBe(false)
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]'), 'the frozen edge context of the old scene is released').toHaveLength(0)
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]'), 'the payment panel the person is in stays').toHaveLength(1)
    } finally {
      app.unmount()
      host.remove()
      getRequiredGeoTestGlobal('__GEO_TEST_SCENE_EQ_REF').value = 'UAH'
      getRequiredGeoTestGlobal('__GEO_TEST_TRUSTLINES_REF').value = []
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_CANCEL', '__GEO_TEST_INTERACT_STATE', '__GEO_TEST_SNAPSHOT')
      vi.unstubAllGlobals()
    }
  })
})

/**
 * 048 S3 (decision 3; repro (а) of the spec's verification plan): a window's failure has ONE place - the window's own block.
 * Until S3 an error toast showed the same `state.error` and, when it expired after 4-8 s, cleared it: the block in the
 * window went out with it. Red before: an `error-toast` beside the block, and after the expiry no block.
 */
describe('048 S3: a window error is the window block alone - no toast, and it does not expire', () => {
  it('the payment window\'s failure: no toast; after 10 s the block is still there, with its request id', async () => {
    vi.useFakeTimers()
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'confirm-payment')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      const st = getInteractState() as TestInteractState & { errorRef?: string | null }
      st.error = 'Not enough limit on the route.'
      st.errorRef = 'req-win-1'
      await nextTick()
      await nextTick()
      const block = () => host.querySelector('[data-testid="manual-payment-error"]')
      expect(block(), 'anti-vacuum: the window shows the failure').not.toBeNull()
      expect(block()!.getAttribute('data-request-id')).toBe('req-win-1')
      expect(host.querySelector('[data-testid="error-toast"]'), 'no second place: the toast is gone').toBeNull()

      await vi.advanceTimersByTimeAsync(10_000)
      await nextTick()
      expect(st.error, 'nothing cleared the failure after a few seconds').toBe('Not enough limit on the route.')
      expect(block(), 'the block is still in the window').not.toBeNull()
    } finally {
      app.unmount()
      host.remove()
      vi.useRealTimers()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_STATE')
      vi.unstubAllGlobals()
    }
  })
})

/**
 * 048 S3 (internal adversarial pass, F1): "Close line" from the edge popup fails - the phase stays `editing-trustline`
 * with the quick popup, and the popup does not draw `state.error` (046 W3 owns it). Until S3 only the error toast showed
 * it; after the toast's removal it was shown nowhere. Red before the fix: no `edge-detail-error`.
 */
describe('048 S3: a failure of the edge popup is shown in the popup\'s window', () => {
  it('editing-trustline with the quick popup and a failure: the window block under the popup, with its request id (data-request-id)', async () => {
    setGeoTestGlobal('__GEO_TEST_INTERACT_PHASE', 'editing-trustline')
    setUrl('/?mode=real&ui=interact')
    stubMissingResizeObserver()
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = mountSimulatorAppRoot(host)
    try {
      await nextTick()
      const st = getInteractState() as TestInteractState & { errorRef?: string | null }
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.selectedEdgeKey = 'alice->bob'
      await nextTick()
      await nextTick()
      const popup = host.querySelector('.ws-shell[data-win-type="edge-detail"]')
      expect(popup, 'anti-vacuum: the quick popup is open').not.toBeNull()
      expect(host.querySelector('[data-testid="edge-detail-error"]'), 'no failure, no block').toBeNull()

      st.error = 'The trust line was not found.'
      st.errorRef = 'req-edge-1'
      await nextTick()
      await nextTick()
      const block = host.querySelector('[data-testid="edge-detail-error"]')
      expect(block, 'the failure is shown').not.toBeNull()
      expect(popup!.contains(block), 'in the popup\'s own window').toBe(true)
      expect(block!.getAttribute('data-request-id')).toBe('req-edge-1')
      // 049 S2 (decision 7): the id is the attribute only - no drawn line.
      expect(block!.querySelector('[data-testid="error-request-id"]')).toBeNull()
    } finally {
      app.unmount()
      host.remove()
      clearGeoTestGlobals('__GEO_TEST_INTERACT_PHASE', '__GEO_TEST_INTERACT_STATE')
      vi.unstubAllGlobals()
    }
  })
})
