import { createApp, h, nextTick, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

type SimulatorStorageMock = {
  readDevtoolsOpenReal: ReturnType<typeof vi.fn>
  writeDevtoolsOpenReal: ReturnType<typeof vi.fn>
  readDevtoolsOpenDemo: ReturnType<typeof vi.fn>
  writeDevtoolsOpenDemo: ReturnType<typeof vi.fn>
}

function makeSimulatorStorageMock(overrides: Partial<SimulatorStorageMock> = {}): SimulatorStorageMock {
  return {
    readDevtoolsOpenReal: vi.fn(() => null),
    writeDevtoolsOpenReal: vi.fn(),
    readDevtoolsOpenDemo: vi.fn(() => null),
    writeDevtoolsOpenDemo: vi.fn(),
    ...overrides,
  }
}

let storageMock: SimulatorStorageMock

// BottomBar imports useSimulatorStorage from usePersistedSimulatorPrefs.
// We mock the composable once, and swap the underlying mock object per-test.
vi.mock('../composables/usePersistedSimulatorPrefs', async () => {
  const actual = await vi.importActual<typeof import('../composables/usePersistedSimulatorPrefs')>(
    '../composables/usePersistedSimulatorPrefs',
  )
  return {
    ...actual,
    useSimulatorStorage: () => storageMock,
  }
})

async function mountBottomBar(opts: { isDemoUi: boolean }) {
  const { default: BottomBar } = await import('./BottomBar.vue')

  const host = document.createElement('div')
  document.body.appendChild(host)

  const eq = ref('UAH')
  const layoutMode = ref('admin-force')
  const quality = ref('med')
  const labelsLod = ref('off')
  const scene = ref('A' as any)

  const defaultProps = {
    apiMode: 'real' as const,
    activeSegment: 'auto' as const,
    isDemoFixtures: false,

    showResetView: false,
    resetView: vi.fn(),

    runId: 'r1',
    refreshSnapshot: vi.fn(),

    artifacts: [],
    artifactsLoading: false,
    refreshArtifacts: vi.fn(),
    downloadArtifact: vi.fn(),

    isWebDriver: false,
    isTestMode: false,
    isE2eScreenshots: false,

    isDemoUi: opts.isDemoUi,
    isExiting: false,
    toggleDemoUi: vi.fn(),

    fxDebugEnabled: false,
    fxBusy: false,
    runTxOnce: vi.fn(),
    runClearingOnce: vi.fn(),
  }

  const app = createApp({
    render: () =>
      h(BottomBar as any, {
        ...defaultProps,
        'onUpdate:eq': (v: string) => (eq.value = v),
        'onUpdate:layoutMode': (v: string) => (layoutMode.value = v),
        'onUpdate:quality': (v: string) => (quality.value = v),
        'onUpdate:labelsLod': (v: string) => (labelsLod.value = v),
        'onUpdate:scene': (v: any) => (scene.value = v),
        eq: eq.value,
        layoutMode: layoutMode.value,
        quality: quality.value,
        labelsLod: labelsLod.value,
        scene: scene.value,
      }),
  })

  app.mount(host)

  return {
    host,
    app,
    eq,
  }
}

describe('BottomBar — DevTools panel (<details>)', () => {
  beforeEach(() => {
    storageMock = makeSimulatorStorageMock()
  })

  it('is controlled: toggle updates the model, and :open reflects the model', async () => {
    const { app, host, eq } = await mountBottomBar({ isDemoUi: false })
    await nextTick()

    const details = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement
    expect(details).toBeTruthy()
    expect(details.open).toBe(false) // default when real pref is absent

    // Toggle event must update the model.
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    await nextTick()
    expect(details.open).toBe(true)

    // And the model must drive the DOM on subsequent renders.
    details.open = false
    details.dispatchEvent(new Event('toggle'))
    eq.value = 'EUR' // trigger an extra render (should keep it closed)
    await nextTick()
    expect(details.open).toBe(false)

    app.unmount()
    host.remove()
  })

  it('persists to the correct storage key: real vs demo', async () => {
    // --- real mode ---
    {
      const { app, host } = await mountBottomBar({ isDemoUi: false })
      await nextTick()

      const details = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement
      details.open = true
      details.dispatchEvent(new Event('toggle'))
      await nextTick()

      expect(storageMock.writeDevtoolsOpenReal).toHaveBeenCalledWith(true)
      expect(storageMock.writeDevtoolsOpenDemo).toHaveBeenCalledTimes(0)

      app.unmount()
      host.remove()
    }

    storageMock.writeDevtoolsOpenReal.mockClear()
    storageMock.writeDevtoolsOpenDemo.mockClear()

    // --- demo mode ---
    {
      const { app, host } = await mountBottomBar({ isDemoUi: true })
      await nextTick()

      // First enter demo: should persist open=true immediately (see readDevToolsOpenForCurrentMode).
      expect(storageMock.writeDevtoolsOpenDemo).toHaveBeenCalledWith(true)

      const details = host.querySelector('details[aria-label="Dev tools"]') as HTMLDetailsElement
      expect(details.open).toBe(true)

      details.open = false
      details.dispatchEvent(new Event('toggle'))
      await nextTick()

      expect(storageMock.writeDevtoolsOpenDemo).toHaveBeenLastCalledWith(false)
      expect(storageMock.writeDevtoolsOpenReal).toHaveBeenCalledTimes(0)

      app.unmount()
      host.remove()
    }
  })
})

