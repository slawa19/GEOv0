import { computed, createApp, h, nextTick, reactive, ref, type Ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

// IMPORTANT: This test verifies conditional rendering in SimulatorAppRoot when the URL contains `ui=interact`.
// We mock `useSimulatorApp()` to keep the test fast + deterministic while still deriving flags from the query string.

vi.mock('../composables/useSimulatorApp', () => {
       return {
    useSimulatorApp: () => {
      const qs = () => {
        try {
          return new URLSearchParams(window.location.search)
        } catch {
          return new URLSearchParams('')
        }
      }

      const apiMode = computed<'fixtures' | 'real'>(() => (qs().get('mode') || '').toLowerCase() === 'real' ? 'real' : 'fixtures')
      const isInteractUi = computed(() => (qs().get('ui') || '').toLowerCase() === 'interact')

      const phase = ref(String((globalThis as any).__GEO_TEST_INTERACT_PHASE ?? 'idle'))
      ;(globalThis as any).__GEO_TEST_PHASE_REF = phase

      const nodeCardOpen = ref(Boolean((globalThis as any).__GEO_TEST_NODE_CARD_OPEN ?? false))
      const setNodeCardOpen = vi.fn((open: boolean) => {
        nodeCardOpen.value = !!open
      })
      ;(globalThis as any).__GEO_TEST_SET_NODE_CARD_OPEN = setNodeCardOpen

       const cancel = vi.fn()
       ;(globalThis as any).__GEO_TEST_INTERACT_CANCEL = cancel

       const startPaymentFlow = vi.fn()
       ;(globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW = startPaymentFlow

       const startClearingFlow = vi.fn(() => {
         phase.value = 'confirm-clearing'
       })
       ;(globalThis as any).__GEO_TEST_INTERACT_START_CLEARING_FLOW = startClearingFlow

       const setPaymentFromPid = vi.fn()
       ;(globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID = setPaymentFromPid

       const setPaymentToPid = vi.fn()
       ;(globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID = setPaymentToPid

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
        isDemoUi: computed(() => false),
        isInteractUi,
        isTestMode: computed(() => true),
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
             setTrustlineFromPid: vi.fn(),
             setTrustlineToPid: vi.fn(),
             selectTrustline: vi.fn(),

             startPaymentFlow: startPaymentFlow,
             startTrustlineFlow: vi.fn(() => {
               phase.value = 'picking-trustline-from'
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
        isNodeCardOpen: computed(() => nodeCardOpen.value),
        setNodeCardOpen,
        hoveredEdge: reactive({ key: null, fromId: '', toId: '', amountText: '' }),
        clearHoveredEdge: vi.fn(),
        edgeTooltipStyle: () => ({}),
        selectedNode: computed(() => (globalThis as any).__GEO_TEST_SELECTED_NODE ?? null),
        nodeCardStyle: computed(() => (globalThis as any).__GEO_TEST_NODE_CARD_STYLE ?? ({ left: '100px', top: '100px' })),
        selectedNodeScreenCenter: computed(() => (globalThis as any).__GEO_TEST_NODE_SCREEN_CENTER ?? null),
        selectedNodeEdgeStats: computed(() => null),

        // pinning
        dragToPin: reactive({ dragState: { active: false } }),
        isSelectedPinned: computed(() => false),
        pinSelectedNode: vi.fn(),
        unpinSelectedNode: vi.fn(),

        // handlers
        onCanvasClick: vi.fn(),
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
        getNodeById: () => null,
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

  it('Escape calls interact.mode.cancel() when phase is not idle', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'confirm-payment'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    expect(cancel).toBeTruthy()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(cancel).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
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

  it('EdgeDetailPopup: Send Payment starts payment flow and pre-fills pids (to→from)', async () => {
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

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    const startPaymentFlow = (globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW as ReturnType<typeof vi.fn>
    const setFrom = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID as ReturnType<typeof vi.fn>
    const setTo = (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_TO_PID as ReturnType<typeof vi.fn>

    expect(cancel).toHaveBeenCalledTimes(1)
    expect(startPaymentFlow).toHaveBeenCalledTimes(1)
    // trustline alice→bob => payment bob→alice
    expect(setFrom).toHaveBeenCalledWith('bob')
    expect(setTo).toHaveBeenCalledWith('alice')

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_START_PAYMENT_FLOW
    delete (globalThis as any).__GEO_TEST_INTERACT_SET_PAYMENT_FROM_PID
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
    expect(cancel).toHaveBeenCalledTimes(0)

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
    delete (globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE
  })

  it('Escape closes NodeCardOverlay first (does not cancel idle interact)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'idle'
    ;(globalThis as any).__GEO_TEST_NODE_CARD_OPEN = true
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    const cancel = (globalThis as any).__GEO_TEST_INTERACT_CANCEL as ReturnType<typeof vi.fn>
    expect(cancel).toBeTruthy()

    const setOpen = (globalThis as any).__GEO_TEST_SET_NODE_CARD_OPEN as ReturnType<typeof vi.fn>
    expect(setOpen).toBeTruthy()

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(setOpen).toHaveBeenCalledWith(false)
    expect(cancel).toHaveBeenCalledTimes(0)

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
    delete (globalThis as any).__GEO_TEST_SET_NODE_CARD_OPEN
    delete (globalThis as any).__GEO_TEST_INTERACT_CANCEL
  })

  it('NodeCardOverlay: clicking "Run Clearing" calls interact.mode.startClearingFlow() (wiring)', async () => {
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

    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Run Clearing')) as
      | HTMLButtonElement
      | undefined
    expect(btn).toBeTruthy()

    btn?.click()
    await nextTick()

    const startClearing = (globalThis as any).__GEO_TEST_INTERACT_START_CLEARING_FLOW as ReturnType<typeof vi.fn>
    expect(startClearing).toBeTruthy()
    expect(startClearing).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
    delete (globalThis as any).__GEO_TEST_INTERACT_PHASE
    delete (globalThis as any).__GEO_TEST_NODE_CARD_OPEN
    delete (globalThis as any).__GEO_TEST_SELECTED_NODE
    delete (globalThis as any).__GEO_TEST_INTERACT_START_CLEARING_FLOW
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
