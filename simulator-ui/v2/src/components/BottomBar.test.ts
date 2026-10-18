import { createApp, h, nextTick, reactive, ref } from 'vue'
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

describe('BottomBar — the offline effects workbench (042 T4205)', () => {
  function workbench(over: Record<string, unknown> = {}) {
    return reactive({
      offered: [
        { id: 'demo-tx', label: 'Платежи', count: 20 },
        { id: 'demo-clearing', label: 'Клиринг', count: 1 },
      ],
      playingId: null as string | null,
      cursorId: null as string | null,
      position: 0,
      total: 0,
      play: vi.fn(),
      step: vi.fn(),
      stop: vi.fn(),
      ...over,
    })
  }
  const buttons = (host: HTMLElement) =>
    Array.from(host.querySelectorAll('[data-testid="playlist-workbench"] button')).map((b) => b.textContent?.trim())

  it('offline: offers the playable playlists; "Проиграть" and "Шаг" act on the chosen one', async () => {
    const wb = workbench()
    const { app, host } = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, playlists: wb } })
    await nextTick()

    const select = host.querySelector('select[aria-label="Плейлист"]') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Платежи (20)', 'Клиринг (1)'])
    expect(buttons(host)).toEqual(['Проиграть', 'Шаг'])

    select.value = 'demo-clearing'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    ;(Array.from(host.querySelectorAll('[data-testid="playlist-workbench"] button')).find((b) => b.textContent?.trim() === 'Проиграть') as HTMLButtonElement).click()
    expect(wb.play).toHaveBeenCalledWith('demo-clearing')
    ;(Array.from(host.querySelectorAll('[data-testid="playlist-workbench"] button')).find((b) => b.textContent?.trim() === 'Шаг') as HTMLButtonElement).click()
    expect(wb.step).toHaveBeenCalledWith('demo-clearing')

    app.unmount()
    host.remove()
  })

  it('while a playlist plays: "Стоп" only, and the position of the played playlist', async () => {
    const wb = workbench({ playingId: 'demo-tx', cursorId: 'demo-tx', position: 3, total: 20 })
    const { app, host } = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, playlists: wb } })
    await nextTick()

    expect(buttons(host)).toEqual(['Стоп'])
    expect(host.querySelector('[aria-label="Позиция плейлиста"]')?.textContent).toBe('3 / 20')
    ;(host.querySelector('[data-testid="playlist-workbench"] button') as HTMLButtonElement).click()
    expect(wb.stop).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
  })

  it('is absent on the server source, and offline when no playlist can be played (no control that does nothing)', async () => {
    const real = await mountBottomBar({ propOverrides: { apiMode: 'real' as const, playlists: workbench() } })
    await nextTick()
    expect(real.host.querySelector('[data-testid="playlist-workbench"]')).toBeNull()
    real.app.unmount()
    real.host.remove()

    const none = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, playlists: workbench({ offered: [] }) } })
    await nextTick()
    expect(none.host.querySelector('[data-testid="playlist-workbench"]')).toBeNull()
    // Counter-check: the same mount with an offered playlist shows it (the absence above is not a broken selector).
    none.app.unmount()
    none.host.remove()
    const some = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, playlists: workbench() } })
    await nextTick()
    expect(some.host.querySelector('[data-testid="playlist-workbench"]')).not.toBeNull()
    some.app.unmount()
    some.host.remove()
  })
})
