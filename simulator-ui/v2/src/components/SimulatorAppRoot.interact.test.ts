import { computed, createApp, h, nextTick, reactive, ref } from 'vue'
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

      const cancel = vi.fn()
      ;(globalThis as any).__GEO_TEST_INTERACT_CANCEL = cancel

      const confirmTrustlineClose = vi.fn(async () => undefined)
      ;(globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE = confirmTrustlineClose

      const isInteractPickingPhase = computed(() => {
        if (!isInteractUi.value) return false
        const p = String(phase.value ?? '')
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
            busy: ref(false),
            state: reactive({
              fromPid: 'alice',
              toPid: 'bob',
              selectedEdgeKey: null,
              error: '',
              lastClearing: null,
            }),

            availableCapacity: ref('0'),
            participants: ref([] as any[]),
            trustlines: ref([] as any[]),
            canSendPayment: ref(true),

            setPaymentFromPid: vi.fn(),
            setPaymentToPid: vi.fn(),
            setTrustlineFromPid: vi.fn(),
            setTrustlineToPid: vi.fn(),
            selectTrustline: vi.fn(),

            startPaymentFlow: vi.fn(),
            startTrustlineFlow: vi.fn(),
            startClearingFlow: vi.fn(() => {
              phase.value = 'confirm-clearing'
            }),
             confirmPayment: vi.fn(async () => undefined),
             confirmTrustlineCreate: vi.fn(async () => undefined),
             confirmTrustlineUpdate: vi.fn(async () => undefined),
             confirmTrustlineClose,
             confirmClearing: vi.fn(async () => undefined),
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
        isNodeCardOpen: computed(() => false),
        hoveredEdge: reactive({ key: null, fromId: '', toId: '', amountText: '' }),
        edgeDetailAnchor: ref(null as any),
        clearHoveredEdge: vi.fn(),
        edgeTooltipStyle: () => ({}),
        selectedNode: computed(() => null),
        nodeCardStyle: () => ({}),
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
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
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

  it('EdgeDetailPopup: Close line requires 2 clicks (armed confirmation)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
    await nextTick()

    const confirmClose = (globalThis as any).__GEO_TEST_INTERACT_CONFIRM_TRUSTLINE_CLOSE as ReturnType<typeof vi.fn>
    expect(confirmClose).toBeTruthy()

    const btn = host.querySelector('[data-testid="edge-close-line-btn"]') as HTMLButtonElement | null
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

  it('Escape disarms Close TL confirmation (does not cancel flow)', async () => {
    ;(globalThis as any).__GEO_TEST_INTERACT_PHASE = 'editing-trustline'
    setUrl('/?mode=real&ui=interact')

    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({ render: () => h(SimulatorAppRoot as any) })
    app.mount(host)
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
})
