import { computed, createApp, h, nextTick, reactive, ref, type Ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

// ---------------------------------------------------------------------------
// Shared simulatorStorage stub (used by SimulatorAppRoot for demo enter/exit).
// ---------------------------------------------------------------------------
const __GEO_TEST_SIM_STORAGE = {
  forceDesiredModeReal: vi.fn(),
  isFxDebugRun: vi.fn(() => false),
  clearFxDebugRunState: vi.fn(),

  readUiTheme: vi.fn(() => null),
  writeUiTheme: vi.fn(),

  readDevtoolsOpenReal: vi.fn(() => null),
  writeDevtoolsOpenReal: vi.fn(),
  readDevtoolsOpenDemo: vi.fn(() => null),
  writeDevtoolsOpenDemo: vi.fn(),
  readDevtoolsOpenRealSnapshot: vi.fn(() => null),
  writeDevtoolsOpenRealSnapshot: vi.fn(),
  clearDevtoolsOpenRealSnapshot: vi.fn(),
} as const

;(globalThis as any).__GEO_TEST_SIM_STORAGE = __GEO_TEST_SIM_STORAGE

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
      const open = vi.fn((o: any) => origOpen(o))
      ;(globalThis as any).__GEO_TEST_WM_OPEN = open

      const origHandleEsc = wm.handleEsc
      const handleEsc = vi.fn((ev: any, o: any) => origHandleEsc(ev, o))
      ;(globalThis as any).__GEO_TEST_WM_HANDLE_ESC = handleEsc

      const origGetTopmostInGroup = wm.getTopmostInGroup
      const getTopmostInGroup = vi.fn((g: any) => origGetTopmostInGroup(g))
      ;(globalThis as any).__GEO_TEST_WM_GET_TOPMOST_IN_GROUP = getTopmostInGroup

      return { ...wm, open, handleEsc, getTopmostInGroup }
    },
  }
})

// IMPORTANT: This test verifies conditional rendering in SimulatorAppRoot when the URL contains `ui=interact`.
// We mock `useSimulatorApp()` to keep the test fast + deterministic while still deriving flags from the query string.

vi.mock('../composables/useSimulatorApp', () => {
        return {
    useSimulatorApp: (opts?: any) => {
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

      const phase = ref(String((globalThis as any).__GEO_TEST_INTERACT_PHASE ?? 'idle'))
      ;(globalThis as any).__GEO_TEST_PHASE_REF = phase

      const selectedNode = ref<any>((globalThis as any).__GEO_TEST_SELECTED_NODE ?? null)
      const selectedNodeScreenCenter = ref<any>((globalThis as any).__GEO_TEST_NODE_SCREEN_CENTER ?? null)

      // Test helper: allow tests to request an initial NodeCard window by setting globals.
      // WM-only runtime: NodeCard is opened via uiOpenOrUpdateNodeCard(), not via boolean flags.
      const initialNodeCardOpen = Boolean((globalThis as any).__GEO_TEST_NODE_CARD_OPEN ?? false)
      if (initialNodeCardOpen && selectedNode.value && typeof opts?.uiOpenOrUpdateNodeCard === 'function') {
        queueMicrotask(() => {
          opts.uiOpenOrUpdateNodeCard({
            nodeId: String(selectedNode.value.id),
            anchor: selectedNodeScreenCenter.value ?? null,
          })
        })
      }

       const cancel = vi.fn()
       ;(globalThis as any).__GEO_TEST_INTERACT_CANCEL = cancel
       // Simulate real FSM cleanup: cancel clears interact state.
       // IMPORTANT: edgeAnchor reset is important for Step 4 anchor propagation tests:
       // trustline(edge popup) → payment must keep anchor.
       cancel.mockImplementation(() => {
         interactState.edgeAnchor = null as any
         interactState.fromPid = null as any
         interactState.toPid = null as any
         interactState.initiatedWithPrefilledFrom = false as any
         interactState.selectedEdgeKey = null as any
         phase.value = 'idle'
       })

       // Interact-mode FSM stub: make root integration tests able to assert that
       // panels become active and confirm-step UI renders without relying on timers.
       const startPaymentFlow = vi.fn(() => {
         const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
         if (st) st.initiatedWithPrefilledFrom = false
         phase.value = 'picking-payment-from'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW = startPaymentFlow

       const startPaymentFlowWithFrom = vi.fn((fromPid: string) => {
         const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
         if (st) {
           st.fromPid = fromPid
           st.initiatedWithPrefilledFrom = true
         }
         phase.value = 'picking-payment-to'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM = startPaymentFlowWithFrom

       const startClearingFlow = vi.fn(() => {
         phase.value = 'confirm-clearing'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_START_CLEARING_FLOW = startClearingFlow

       const setPaymentFromPid = vi.fn((pid: string | null) => {
         const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
         if (st) st.fromPid = pid
         // Real FSM: after picking From, user goes to picking To.
         // When From is cleared (e.g. cancel / invalidation), go back to picking From.
         phase.value = pid ? 'picking-payment-to' : 'picking-payment-from'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID = setPaymentFromPid

       const setPaymentToPid = vi.fn((pid: string | null) => {
         const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
         if (st) st.toPid = pid
         // Real FSM: selecting a To advances to confirm step; clearing To keeps user in picking To.
         phase.value = pid ? 'confirm-payment' : 'picking-payment-to'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID = setPaymentToPid

       const setTrustlineFromPid = vi.fn((pid: string | null) => {
         const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
         if (st) st.fromPid = pid
         phase.value = pid ? 'picking-trustline-to' : 'picking-trustline-from'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID = setTrustlineFromPid

       const setTrustlineToPid = vi.fn((pid: string | null) => {
         const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
         if (st) st.toPid = pid
         // Minimal FSM-like behavior for back-step tests.
         if (!pid) {
           phase.value = 'picking-trustline-to'
           return
         }
         phase.value = 'editing-trustline'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID = setTrustlineToPid

       const confirmTrustlineClose = vi.fn(async () => undefined)
       ;(globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE = confirmTrustlineClose

       const successMessage = ref<string | null>(null)
       ;(globalThis as any).__GEO_TEST_INTERACT_SUCCESS_MESSAGE = successMessage

      const interactBusy = ref(false)
      ;(globalThis as any).__GEO_TEST_INTERACT_BUSY_REF = interactBusy

      const trustlinesLoading = ref(false)
      ;(globalThis as any).__GEO_TEST_TRUSTLINES_LOADING_REF = trustlinesLoading

      const paymentTargetsLoading = ref(false)
      ;(globalThis as any).__GEO_TEST_PAYMENT_TARGETS_LOADING_REF = paymentTargetsLoading

      const paymentTargetsLastError = ref<string | null>(null)
      ;(globalThis as any).__GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF = paymentTargetsLastError

      const paymentToTargetIds = ref<Set<string> | undefined>(undefined)
      ;(globalThis as any).__GEO_TEST_PAYMENT_TO_TARGET_IDS_REF = paymentToTargetIds

      const interactState = reactive({
        fromPid: 'alice',
        toPid: 'bob',
         initiatedWithPrefilledFrom: false,
        selectedEdgeKey: null as string | null,
        edgeAnchor: { x: 10, y: 10 },
        error: '',
        lastClearing: null as any,
      })
      ;(globalThis as any).__GEO_TEST_INTERACT_STATE = interactState

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
          runStatus: null as any,
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
          runs: ref([] as any[]),
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
          snapshot: null as any,
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
              participants: ref([] as any[]),
              trustlines: ref([] as any[]),
              canSendPayment: ref(true),

            setPaymentFromPid,
            setPaymentToPid,
              setTrustlineFromPid,
              setTrustlineToPid,
             selectTrustline: vi.fn(),

             startPaymentFlow: startPaymentFlow,
             startPaymentFlowWithFrom,
              startTrustlineFlow: vi.fn(() => {
                 const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
                 if (st) st.initiatedWithPrefilledFrom = false
                phase.value = 'picking-trustline-from'
              }),
              startTrustlineFlowWithFrom: vi.fn((fromPid: string) => {
                const st = (globalThis as any).__GEO_TEST_INTERACT_STATE
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
              history: reactive([] as any[]),
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
        perf: reactive({} as any),
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
        selectedNodeEdgeStats: computed(() => null),

        // pinning
        dragToPin: reactive({ dragState: { active: false } }),
        isSelectedPinned: computed(() => false),
        pinSelectedNode: vi.fn(),
        unpinSelectedNode: vi.fn(),

        // handlers
        onCanvasClick: vi.fn(() => {
          // Minimal integration wiring for root interaction tests:
          // emulate empty-canvas click behavior (outside click) by delegating
          // to WM-aware callback (WM-only runtime).
          try {
            const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL
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
        onCanvasPointerDown: vi.fn(),
        onCanvasPointerMove: vi.fn(),
        onCanvasPointerUp: vi.fn(),
        onCanvasWheel: vi.fn(),

        // labels
        labelNodes: computed(() => []),
        floatingLabelsViewFx: computed(() => []),
        worldToCssTranslateNoScale: () => 'translate(0px, 0px)',

        // helpers for template
        getNodeById: (id: string | null) => {
          const n = (globalThis as any).__GEO_TEST_SELECTED_NODE ?? null
          if (!id || !n) return null
          return String(n.id) === String(id) ? n : null
        },
        resetView: vi.fn(),
      }
    },
  }
})

import SimulatorAppRoot from './SimulatorAppRoot.vue'

function setUrl(search: string) {
  // Use a same-origin relative URL to satisfy happy-dom History security checks.
  window.history.replaceState({}, '', search)
}

function stubLocationHrefSetter() {
  const hrefSet = vi.fn()
  const prevLocation = window.location
  const currentHref = String(prevLocation.href)
  const currentSearch = String(prevLocation.search ?? '')
  const currentOrigin = String((prevLocation as any).origin ?? 'http://localhost')

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
  it('ui=interact renders interact HUD + ActionBar + ManualPaymentPanel and does not render intensity slider', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
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
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
  })

  it('renders ManualPaymentPanel through WindowLayer (WindowShell) and does not duplicate legacy panel', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    setUrl('/?mode=real&ui=interact')

    // Step 3: WM wiring must not crash in environments without native ResizeObserver.
    // (happy-dom may not provide it; we stub it out explicitly to ensure coverage)
    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('renders TrustlineManagementPanel through WindowLayer and does not duplicate legacy panel', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-trustline-create'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('renders ClearingPanel through WindowLayer and does not duplicate legacy panel', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-clearing'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('renders EdgeDetailPopup through WindowLayer (WindowShell)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const popups = host.querySelectorAll('[data-testid="edge-detail-popup"]')
      expect(popups.length).toBe(1)
      expect((popups[0] as HTMLElement).closest('.ws-shell')).toBeTruthy()

      // legacy render must be disabled (no duplicate absolute popup)
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('renders NodeCardOverlay through WindowLayer (WindowShell)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(1)

      const cards = host.querySelectorAll('.ds-ov-node-card')
      expect(cards.length).toBe(1)
      expect((cards[0] as HTMLElement).closest('.ws-shell')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
      delete (globalThis as any).__GEO_TEST_SELECTED_NODE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('coexistence — interact-panel and node-card both render (2 windows)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const shells = host.querySelectorAll('.ws-shell')
      expect(shells.length).toBe(2)

      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(1)
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
      delete (globalThis as any).__GEO_TEST_SELECTED_NODE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('NodeCard action → opens interact-panel and keeps NodeCard open (H-1 coexistence)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
      delete (globalThis as any).__GEO_TEST_SELECTED_NODE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_WM_HANDLE_ESC
      vi.unstubAllGlobals()
    }
  })

  it('cross-group replace — Change Limit closes edge-detail window and opens trustline panel', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      vi.unstubAllGlobals()
    }
  })

  it('edge-detail UI-close closes the window and does NOT cancel interact flow (H-3)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()

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
      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.edgeAnchor = { x: 20, y: 20 }
      await nextTick()
      await nextTick()
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('Send Payment from edge-detail keeps edge-detail window open (keepAlive)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
      expect(host.querySelectorAll('.ws-shell').length).toBe(1)

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()

      // Click "Send Payment" button inside edge-detail (uses data-testid).
      const sendBtn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
      expect(sendBtn).toBeTruthy()

      sendBtn?.click()
      await nextTick()
      await nextTick()

      // Edge-detail must STILL be visible (keepAlive).
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      // Payment interact-panel must also appear (coexistence).
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(1)

      // Two windows: edge-detail + interact-panel.
      expect(host.querySelectorAll('.ws-shell').length).toBe(2)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('outside-click closes topmost/active inspector via WM (edge-detail) and cancels interact flow', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()

      const getTopmost = (globalThis as any).__GEO_TEST_WM_GET_TOPMOST_IN_GROUP as ReturnType<typeof vi.fn>
      expect(getTopmost).toBeTruthy()

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
      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st?.edgeAnchor).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      delete (globalThis as any).__GEO_TEST_WM_GET_TOPMOST_IN_GROUP
      vi.unstubAllGlobals()
    }
  })

  it('outside-click closes inspector (node-card) and cancels interact-panel (hard dismiss)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      // Coexistence: interact + inspector.
      expect(host.querySelectorAll('[data-testid="manual-payment-panel"]').length).toBe(1)
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

      const getTopmost = (globalThis as any).__GEO_TEST_WM_GET_TOPMOST_IN_GROUP as ReturnType<typeof vi.fn>
      expect(getTopmost).toBeTruthy()

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
      delete (globalThis as any).__GEO_TEST_SELECTED_NODE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_WM_GET_TOPMOST_IN_GROUP
      vi.unstubAllGlobals()
    }
  })

  it('EdgeDetailPopup Close closes edge-detail window (UI-close) and does NOT cancel interact flow', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      // Initially: edge-detail window is visible.
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()

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
      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.edgeAnchor = { x: 20, y: 20 }
      await nextTick()
      await nextTick()
      expect(host.querySelectorAll('[data-testid="edge-detail-popup"]').length).toBe(1)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('EdgeDetailPopup send-payment → payment keeps edge anchor (wm.open gets non-null anchor)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()

      const btn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      const open = (globalThis as any).__GEO_TEST_WM_OPEN as ReturnType<typeof vi.fn>
      expect(open).toBeTruthy()

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_WM_OPEN
      vi.unstubAllGlobals()
    }
  })

  it('MP-0: routesLoading in root yields tri-state unknown in ManualPaymentPanel (shows updating help)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-payment-to'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()

      const trustlinesLoading = (globalThis as any).__GEO_TEST_TRUSTLINES_LOADING_REF as Ref<boolean>
      const paymentTargetsLoading = (globalThis as any).__GEO_TEST_PAYMENT_TARGETS_LOADING_REF as Ref<boolean>
      const paymentTargetsLastError = (globalThis as any).__GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF as Ref<string | null>
      const paymentToTargetIds = (globalThis as any).__GEO_TEST_PAYMENT_TO_TARGET_IDS_REF as Ref<Set<string> | undefined>

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      // Clean up refs created during mock setup to avoid cross-test leakage.
      delete (globalThis as any).__GEO_TEST_INTERACT_BUSY_REF
      delete (globalThis as any).__GEO_TEST_TRUSTLINES_LOADING_REF
      delete (globalThis as any).__GEO_TEST_PAYMENT_TARGETS_LOADING_REF
      delete (globalThis as any).__GEO_TEST_PAYMENT_TARGETS_LAST_ERROR_REF
      delete (globalThis as any).__GEO_TEST_PAYMENT_TO_TARGET_IDS_REF
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
    }
  })

  it('ui=interact renders trustline/clearing panels depending on phase', async () => {
    setUrl('/?mode=real&ui=interact')

    // Trustline create phase
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-trustline-create'
    {
      const host = document.createElement('div')
      document.body.appendChild(host)
      const app = createApp({ render: () => h(SimulatorAppRoot as any) })
      app.mount(host)
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()
      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeFalsy()

      app.unmount()
      host.remove()
    }

    // Clearing phase
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-clearing'
    {
      const host = document.createElement('div')
      document.body.appendChild(host)
      const app = createApp({ render: () => h(SimulatorAppRoot as any) })
      app.mount(host)
      await nextTick()

      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeTruthy()

      app.unmount()
      host.remove()
    }

    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
  })

  it('clicking ActionBar "Run Clearing" changes phase and shows ClearingPanel (behavioral)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    expect(host.querySelector('[data-testid="clearing-panel"]')).toBeFalsy()

    const btn = host.querySelector('[data-testid="actionbar-clearing"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click()
    await nextTick()

    expect(host.querySelector('[data-testid="clearing-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
  })

  it('Escape delegates to WM and does not cancel confirm-payment (step-back instead)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    expect(cancel).toBeTruthy()

    const setTo = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID as ReturnType<typeof vi.fn>
    expect(setTo).toBeTruthy()

    const handleEsc = (globalThis as any).__GEO_TEST_WM_HANDLE_ESC as ReturnType<typeof vi.fn>
    expect(handleEsc).toBeTruthy()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(handleEsc).toHaveBeenCalledTimes(1)
    expect(cancel).toHaveBeenCalledTimes(0)
    expect(setTo).toHaveBeenCalledWith(null)

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID
    delete (globalThis as any).__GEO_TEST_WM_HANDLE_ESC
  })

  it('Escape delegates to wm.handleEsc() and does NOT cancel interact flow', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()

      const setTo = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID as ReturnType<typeof vi.fn>
      expect(setTo).toBeTruthy()

      const handleEsc = (globalThis as any).__GEO_TEST_WM_HANDLE_ESC as ReturnType<typeof vi.fn>
      expect(handleEsc).toBeTruthy()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))

      expect(handleEsc).toHaveBeenCalledTimes(1)
      expect(cancel).toHaveBeenCalledTimes(0)
      // In WM mode, ESC should attempt Interact step-back first.
      expect(setTo).toHaveBeenCalledWith(null)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID
      delete (globalThis as any).__GEO_TEST_WM_HANDLE_ESC
      vi.unstubAllGlobals()
    }
  })

  it('ESC on picking-payment-to closes when initiatedWithPrefilledFrom=true (NodeCard)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-payment-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = true
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()
      expect(setFrom).toBeTruthy()

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('ESC on picking-payment-to steps back when initiatedWithPrefilledFrom=false (ActionBar)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-payment-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF as Ref<string>
      expect(phaseRef).toBeTruthy()
      phaseRef.value = 'picking-payment-to'

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()
      expect(setFrom).toBeTruthy()

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      delete (globalThis as any).__GEO_TEST_PHASE_REF
      vi.unstubAllGlobals()
    }
  })

  it('ActionBar payment — 2nd ESC after step-back closes window (cancel ×1)', async () => {
    // AC regression: after step-back from picking-to → picking-from (ActionBar), 2nd ESC must close.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-payment-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF as Ref<string>
      phaseRef.value = 'picking-payment-to'

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID as ReturnType<typeof vi.fn>

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      delete (globalThis as any).__GEO_TEST_PHASE_REF
      vi.unstubAllGlobals()
    }
  })

  it('latched initiatedWithPrefilledFrom stays true after manual FROM change via dropdown', async () => {
    // Regression: initiatedWithPrefilledFrom must be latched (not derived from fromPid truthy).
    // After user changes FROM in dropdown, ESC must still close (not step-back to picking-from).
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-payment-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
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

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID as ReturnType<typeof vi.fn>

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('EdgeDetail payment confirm ESC clears TO, then closes on 2nd ESC (prefilled)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.initiatedWithPrefilledFrom = true

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setTo = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()
      expect(setTo).toBeTruthy()
      expect(setFrom).toBeTruthy()

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('Trustline picking-to closes when initiatedWithPrefilledFrom=true (NodeCard)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-trustline-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = true
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()
      expect(setFrom).toBeTruthy()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(1)
      expect(setFrom).not.toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('Trustline picking-to steps back when initiatedWithPrefilledFrom=false (ActionBar)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-trustline-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()
      expect(setFrom).toBeTruthy()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      expect(cancel).toHaveBeenCalledTimes(0)
      expect(setFrom).toHaveBeenCalledWith(null)
      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('ActionBar trustline — 2nd ESC after step-back closes window (cancel ×1)', async () => {
    // AC regression: after step-back from picking-trustline-to → picking-trustline-from, 2nd ESC must close.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-trustline-to'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.initiatedWithPrefilledFrom = false
      await nextTick()
      await nextTick()

      const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF as Ref<string>
      phaseRef.value = 'picking-trustline-to'

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID as ReturnType<typeof vi.fn>

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_FROM_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      delete (globalThis as any).__GEO_TEST_PHASE_REF
      vi.unstubAllGlobals()
    }
  })

  it('clearing confirm — ESC closes window without step-back, cancel called exactly once', async () => {
    // AC-7 regression: Run Clearing has no step-back; ESC must close and cancel exactly once.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-clearing'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeTruthy()

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()
      await nextTick()

      // cancel must be called exactly once (no double-cancel from step-back + close)
      expect(cancel).toHaveBeenCalledTimes(1)
      expect(host.querySelector('[data-testid="clearing-panel"]')).toBeFalsy()
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('Trustline edit ESC clears TO and stays open, then closes on 2nd ESC (prefilled)', async () => {
    // IMPORTANT: `editing-trustline` may map to EdgeDetailPopup when `useFullTrustlineEditor=false`.
    // This test asserts ESC step-back for the *full* trustline editor (TrustlineManagementPanel),
    // so we must enter the trustline flow via ActionBar to set `useFullTrustlineEditor=true`.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      // Enter via ActionBar so the root sets useFullTrustlineEditor=true.
      const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
      expect(tlBtn).toBeTruthy()
      tlBtn!.click()
      await nextTick()
      await nextTick()

      const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as any
      expect(st).toBeTruthy()
      st.fromPid = 'alice'
      st.toPid = 'bob'
      st.initiatedWithPrefilledFrom = true

      // Force phase to editor state (full editor window exists in WM layer).
      const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF as Ref<string>
      expect(phaseRef).toBeTruthy()
      phaseRef.value = 'editing-trustline'
      await nextTick()
      await nextTick()

      expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

      const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
      const setTo = (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID as ReturnType<typeof vi.fn>
      expect(cancel).toBeTruthy()
      expect(setTo).toBeTruthy()

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
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_INTERACT_SET_TRUSTLINE_TO_PID
      delete (globalThis as any).__GEO_TEST_INTERACT_STATE
      vi.unstubAllGlobals()
    }
  })

  it('canvas shows crosshair cursor in interact picking phases', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'picking-payment-from'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    const canvas = host.querySelector('canvas.canvas') as HTMLCanvasElement | null
    expect(canvas).toBeTruthy()
    expect(canvas?.style.cursor).toBe('crosshair')

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
  })

  it('TrustlineManagementPanel: Close TL requires 2 clicks (armed confirmation)', async () => {
    // Enter via ActionBar to set useFullTrustlineEditor=true, then advance to editing-trustline.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    // Click ActionBar trustline button → useFullTrustlineEditor=true, phase=picking-trustline-from
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // Advance to editing-trustline (simulates FSM progression)
    const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF
    phaseRef.value = 'editing-trustline'
    await nextTick()

    const confirmClose = (globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE as ReturnType<typeof vi.fn>
    expect(confirmClose).toBeTruthy()

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
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE
  })

  it('EdgeDetailPopup: hidden when TrustlineManagementPanel is visible (no duplicate windows)', async () => {
    // Enter via ActionBar so useFullTrustlineEditor=true → TrustlineManagementPanel shown,
    // EdgeDetailPopup hidden via forceHidden prop.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    // Click ActionBar trustline button → useFullTrustlineEditor=true
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // Advance to editing-trustline
    const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF
    phaseRef.value = 'editing-trustline'
    await nextTick()

    // EdgeDetailPopup should NOT render (forceHidden=true because useFullTrustlineEditor=true).
    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
    expect(btn).toBeFalsy()

    // TrustlineManagementPanel should render instead.
    expect(host.querySelector('[data-testid="trustline-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE
  })

  it('AC-ED-3: EdgeDetailPopup "Send Payment" activates Manual Payment and pre-fills pids (trustline to→from)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    const btn = host.querySelector('[data-testid="edge-send-payment"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click()
    await nextTick()
    await nextTick()

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    const setTo = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID as ReturnType<typeof vi.fn>
    const startPaymentFlowWithFrom = (globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM as ReturnType<typeof vi.fn>

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
    const st = (globalThis as any).__GEO_TEST_INTERACT_STATE as { fromPid: string | null; toPid: string | null }
    expect(st.fromPid).toBe('bob')
    expect(st.toPid).toBe('alice')

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM
    delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID
  })

  it('Escape disarms Close TL confirmation (does not cancel flow)', async () => {
    // Enter via ActionBar to set useFullTrustlineEditor=true, then advance to editing-trustline.
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    // Click ActionBar trustline button → useFullTrustlineEditor=true
    const tlBtn = host.querySelector('[data-testid="actionbar-trustline"]') as HTMLButtonElement | null
    expect(tlBtn).toBeTruthy()
    tlBtn?.click()
    await nextTick()

    // Advance to editing-trustline
    const phaseRef = (globalThis as any).__GEO_TEST_PHASE_REF
    phaseRef.value = 'editing-trustline'
    await nextTick()

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    expect(cancel).toBeTruthy()

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
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE
  })

  it('Escape closes NodeCard window first (does not cancel idle interact)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()
    await nextTick()

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    expect(cancel).toBeTruthy()

    const handleEsc = (globalThis as any).__GEO_TEST_WM_HANDLE_ESC as ReturnType<typeof vi.fn>
    expect(handleEsc).toBeTruthy()

    expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(handleEsc).toHaveBeenCalledTimes(1)
    expect(cancel).toHaveBeenCalledTimes(0)

    await nextTick()
    await nextTick()
    expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(0)

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
    delete (globalThis as any).__GEO_TEST_SELECTED_NODE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_WM_HANDLE_ESC
  })

  it('Escape is routed to WM and closes NodeCard via back-stack (H-2 ESC policy)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(1)

      const handleEsc = (globalThis as any).__GEO_TEST_WM_HANDLE_ESC as ReturnType<typeof vi.fn>
      expect(handleEsc).toBeTruthy()

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
      await nextTick()

      // Must go through WM.
      expect(handleEsc).toHaveBeenCalledTimes(1)

      // Not visible.
      expect(host.querySelectorAll('.ds-ov-node-card').length).toBe(0)
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
      delete (globalThis as any).__GEO_TEST_SELECTED_NODE
      delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
      delete (globalThis as any).__GEO_TEST_WM_HANDLE_ESC
      vi.unstubAllGlobals()
    }
  })

  it('NodeCardOverlay: clicking "Send Payment" starts payment flow and shows ManualPaymentPanel (wiring)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()
    await nextTick()

    const card = host.querySelector('.ds-ov-node-card') as HTMLElement | null
    expect(card).toBeTruthy()

    const btn = card!.querySelector('[data-testid="node-card-send-payment"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()

    btn?.click()
    await nextTick()

    const startPaymentFlowWithFrom = (globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM as ReturnType<typeof vi.fn>
    expect(startPaymentFlowWithFrom).toBeTruthy()
    expect(startPaymentFlowWithFrom).toHaveBeenCalledTimes(1)
    expect(startPaymentFlowWithFrom).toHaveBeenCalledWith('bob')

    expect(host.querySelector('[data-testid="manual-payment-panel"]')).toBeTruthy()

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
    delete (globalThis as any).__GEO_TEST_SELECTED_NODE
    delete (globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW_WITH_FROM
  })

  it('NodeCardOverlay "Send Payment" opens interact-panel once with node anchor (no intermediate window)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    ;(globalThis as any).__GEO_TEST_SELECTED_NODE = {
      id: 'bob',
      name: 'Bob',
      type: 'person',
      status: 'active',
      viz_color_key: 'unknown',
      net_balance: '0',
    }
    ;(globalThis as any).__GEO_TEST_NODE_SCREEN_CENTER = { x: 111, y: 222 }
    setUrl('/?mode=real&ui=interact')

    vi.stubGlobal('ResizeObserver', undefined as any)

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()
      await nextTick()

      const card = host.querySelector('.ds-ov-node-card') as HTMLElement | null
      expect(card).toBeTruthy()

      const btn = card!.querySelector('[data-testid="node-card-send-payment"]') as HTMLButtonElement | null
      expect(btn).toBeTruthy()

      const wmOpen = (globalThis as any).__GEO_TEST_WM_OPEN as ReturnType<typeof vi.fn>
      expect(wmOpen).toBeTruthy()

      btn?.click()
      await nextTick()
      await nextTick()

      const calls = wmOpen.mock.calls.map((c: any[]) => c?.[0]).filter(Boolean)
      const interactPanelOpens = calls.filter((o: any) => o.type === 'interact-panel')
      expect(interactPanelOpens.length).toBe(1)
      expect(interactPanelOpens[0].anchor).toEqual({ x: 111, y: 222, space: 'host', source: 'panel' })
    } finally {
      app.unmount()
      host.remove()
      delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
      delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
      delete (globalThis as any).__GEO_TEST_SELECTED_NODE
      delete (globalThis as any).__GEO_TEST_NODE_SCREEN_CENTER
      delete (globalThis as any).__GEO_TEST_WM_OPEN
      vi.unstubAllGlobals()
    }
  })

  it('success toast: successful clearing confirm renders SuccessToast and allows dismiss', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
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
    const successMessage = (globalThis as any).__GEO_TEST_INTERACT_SUCCESS_MESSAGE as ReturnType<typeof ref<string | null>>
    expect(successMessage).toBeTruthy()
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
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_SUCCESS_MESSAGE
  })
})

describe('SimulatorAppRoot - Demo UI DevTools snapshot/restore wiring', () => {
  it('enter demo: snapshots real devtools open state; URL reload does not keep devtools param', async () => {
    const simStorage = (globalThis as any).__GEO_TEST_SIM_STORAGE as any
    expect(simStorage).toBeTruthy()

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

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()

      const devDetails = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement | null
      expect(devDetails).toBeTruthy()
      devDetails!.open = true

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent || '').includes('Enter Demo UI')) as
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
    const simStorage = (globalThis as any).__GEO_TEST_SIM_STORAGE as any
    expect(simStorage).toBeTruthy()

    simStorage.writeDevtoolsOpenRealSnapshot.mockClear()
    simStorage.writeDevtoolsOpenReal.mockClear()
    simStorage.clearDevtoolsOpenRealSnapshot.mockClear()

    // Setup: in demo UI, with snapshot present.
    simStorage.readDevtoolsOpenRealSnapshot.mockReturnValue(false)

    setUrl('/?mode=real&ui=demo&debug=1&devtools=1&testMode=0')

    const nav = stubLocationHrefSetter()

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    try {
      app.mount(host)
      await nextTick()

      const devDetails = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement | null
      expect(devDetails).toBeTruthy()
      devDetails!.open = true

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent || '').includes('Exit Demo UI')) as
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
