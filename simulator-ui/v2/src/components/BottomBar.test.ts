import { createApp, h, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { SceneId } from '../scenes'

const { default: BottomBar } = await import('./BottomBar.vue')

async function mountBottomBar(opts: { propOverrides?: Record<string, unknown> } = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const eq = ref('UAH')
  const layoutMode = ref('admin-force')
  const quality = ref('med')
  const labelsLod = ref('off')
  const scene = ref<SceneId>('A')

  const defaultProps = {
    apiMode: 'real' as const,
    isDemoFixtures: false,
    equivalentOptions: ['UAH', 'EUR'],

    showResetView: false,
    resetView: vi.fn(),

    runId: 'r1',
    refreshSnapshot: vi.fn(),

    artifacts: [],
    artifactsLoading: false,
    refreshArtifacts: vi.fn(),
    downloadArtifact: vi.fn(),

    analyticsPanelOpen: false,
    toggleAnalyticsPanel: vi.fn(),

    // 043 S1: the controls these tests are about live behind "Подробнее".
    detailsOpen: true,
    ...opts.propOverrides,
  }

  const app = createApp({
    render: () =>
      h(BottomBar, {
        ...defaultProps,
        'onUpdate:eq': (v: string) => (eq.value = v),
        'onUpdate:layoutMode': (v: string) => (layoutMode.value = v),
        'onUpdate:quality': (v: string) => (quality.value = v),
        'onUpdate:labelsLod': (v: string) => (labelsLod.value = v),
        'onUpdate:scene': (v: SceneId) => (scene.value = v),
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

describe('BottomBar — Artifacts menu (<details>)', () => {
  it('moves keyboard-opened focus inside Artifacts and restores summary focus on Escape', async () => {
    const downloadArtifact = vi.fn()
    const { app, host } = await mountBottomBar({
      propOverrides: {
        artifacts: [{ name: 'run-report.csv' }],
        downloadArtifact,
      },
    })
    await nextTick()

    const details = host.querySelector('details[aria-label="Artifacts"]') as HTMLDetailsElement
    const summary = details.querySelector('summary') as HTMLElement
    const refreshButton = Array.from(details.querySelectorAll('button')).find(
      (candidate) => candidate.textContent?.trim() === 'Refresh',
    ) as HTMLButtonElement | undefined
    const downloadButton = Array.from(details.querySelectorAll('button')).find(
      (candidate) => candidate.textContent?.trim() === 'run-report.csv',
    ) as HTMLButtonElement | undefined

    expect(details).toBeTruthy()
    expect(summary).toBeTruthy()
    expect(refreshButton).toBeTruthy()
    expect(downloadButton).toBeTruthy()

    summary.focus()
    summary.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }))
    details.open = true
    details.dispatchEvent(new Event('toggle'))
    await nextTick()

    expect(document.activeElement).toBe(refreshButton)

    downloadButton?.focus()
    downloadButton?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true }))
    expect(document.activeElement).toBe(refreshButton)

    refreshButton?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await nextTick()

    expect(details.open).toBe(false)
    expect(document.activeElement).toBe(summary)
    expect(downloadArtifact).toHaveBeenCalledTimes(0)

    app.unmount()
    host.remove()
  })
})


describe('BottomBar — analytics surface toggle (spec 007, T705)', () => {
  it('reports the surface state to assistive tech and flips it on click', async () => {
    const toggleAnalyticsPanel = vi.fn()
    const { app, host } = await mountBottomBar({
      propOverrides: { analyticsPanelOpen: false, toggleAnalyticsPanel },
    })
    await nextTick()

    const btn = host.querySelector('[data-testid="bottombar-analytics-toggle"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    // A toggle, not a link: its state has to be readable, not merely inferable from the screen.
    expect(btn.getAttribute('aria-pressed')).toBe('false')

    btn.click()
    await nextTick()

    // The bar does not own the flag — it asks the owner to flip it.
    expect(toggleAnalyticsPanel).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
  })

  it('reflects an already-open surface', async () => {
    const { app, host } = await mountBottomBar({
      propOverrides: { analyticsPanelOpen: true, toggleAnalyticsPanel: vi.fn() },
    })
    await nextTick()

    const btn = host.querySelector('[data-testid="bottombar-analytics-toggle"]') as HTMLButtonElement
    expect(btn.getAttribute('aria-pressed')).toBe('true')

    app.unmount()
    host.remove()
  })

  it('is absent outside real mode, where there is no metric store to show', async () => {
    const { app, host } = await mountBottomBar({
      propOverrides: {
        apiMode: 'fixtures' as const,
        analyticsPanelOpen: false,
        toggleAnalyticsPanel: vi.fn(),
      },
    })
    await nextTick()

    expect(host.querySelector('[data-testid="bottombar-analytics-toggle"]')).toBeNull()

    app.unmount()
    host.remove()
  })
})
