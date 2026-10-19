import { createApp, h, nextTick, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

const { default: BottomBar } = await import('./BottomBar.vue')

async function mountBottomBar(opts: { propOverrides?: Record<string, unknown> } = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const eq = ref('UAH')
  const layoutMode = ref('admin-force')
  const quality = ref('med')
  const labelsLod = ref('off')

  const defaultProps = {
    apiMode: 'real' as const,
    equivalentOptions: ['UAH', 'EUR'],

    showResetView: false,
    resetView: vi.fn(),

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
        eq: eq.value,
        layoutMode: layoutMode.value,
        quality: quality.value,
        labelsLod: labelsLod.value,
      }),
  })

  app.mount(host)

  return {
    host,
    app,
    eq,
  }
}

/**
 * 048 S1 (decisions 4 and 5). What the service row of "Подробнее" holds for a visitor, and what only `?debug=1` adds.
 * The Analytics toggle and the Artifacts menu left this bar for "Admin ▾" of the top bar (their tests moved with them:
 * `TopBar.admin.p048.test.ts`); "Refresh" of the snapshot is gone (its function stays the recovery pipeline's).
 */
describe('BottomBar — the visitor, the developer (048 S1)', () => {
  const controls = (host: HTMLElement) =>
    Array.from(host.querySelectorAll<HTMLElement>('[data-testid="bottom-details-row"] select, [data-testid="bottom-details-row"] button'))
      .map((el) => el.getAttribute('aria-label') ?? el.textContent?.trim() ?? '')

  it('reproducer (б): no Scene select - offline or on the server, with or without ?debug=1', async () => {
    for (const apiMode of ['fixtures', 'real'] as const) {
      for (const debug of [false, true]) {
        const { app, host } = await mountBottomBar({ propOverrides: { apiMode, debug, setSource: vi.fn() } })
        await nextTick()
        // Counter-check: the row is rendered and its selects are found by the same query.
        expect(host.querySelector('[data-testid="bottom-details-row"] select[aria-label="Layout"]'), `${apiMode}/${debug}: the row is there`).not.toBeNull()
        expect(host.querySelector('[aria-label="Scene"]'), `${apiMode}/${debug}: no Scene`).toBeNull()
        app.unmount()
        host.remove()
      }
    }
  })

  it('a visitor: EQ, Layout, Labels - no source, no quality, no Refresh, Analytics or Artifacts; ?debug=1 adds the source and the quality', async () => {
    const visitor = await mountBottomBar({ propOverrides: { setSource: vi.fn() } })
    await nextTick()
    expect(controls(visitor.host)).toEqual(['Equivalent', 'Layout', 'Labels'])
    expect(visitor.host.querySelector('details'), 'no menu in the bar').toBeNull()
    visitor.app.unmount()
    visitor.host.remove()

    const developer = await mountBottomBar({ propOverrides: { setSource: vi.fn(), debug: true } })
    await nextTick()
    expect(controls(developer.host)).toEqual(['Equivalent', 'Layout', 'Источник', 'Quality', 'Labels'])
    expect(developer.host.querySelector('[data-testid="source-select"]')).not.toBeNull()
    expect(developer.host.querySelector('[data-testid="quality-select"]')).not.toBeNull()
    developer.app.unmount()
    developer.host.remove()
  })

  it('EQ: one equivalent is its code as text, more are a select (no fixed "UAH" branch)', async () => {
    const one = await mountBottomBar({ propOverrides: { equivalentOptions: ['HOUR'] } })
    await nextTick()
    expect(one.host.querySelector('[data-testid="equivalent-select"]')).toBeNull()
    expect(one.host.querySelector('[data-testid="equivalent-text"]')?.textContent).toBe('HOUR')
    one.app.unmount()
    one.host.remove()

    const two = await mountBottomBar({ propOverrides: { equivalentOptions: ['UAH', 'EUR'] } })
    await nextTick()
    expect(two.host.querySelector('[data-testid="equivalent-text"]')).toBeNull()
    const sel = two.host.querySelector<HTMLSelectElement>('[data-testid="equivalent-select"]')!
    expect(Array.from(sel.options).map((o) => o.value)).toEqual(['UAH', 'EUR'])
    sel.value = 'EUR'
    sel.dispatchEvent(new Event('change'))
    await nextTick()
    expect(two.eq.value, 'the choice reaches the owner').toBe('EUR')
    two.app.unmount()
    two.host.remove()
  })
})

describe('BottomBar — the offline effects workbench (042 T4205)', () => {
  function workbench(over: Record<string, unknown> = {}) {
    const wb = reactive({
      offered: [
        { id: 'demo-tx', label: 'Платежи', count: 20 },
        { id: 'demo-clearing', label: 'Клиринг', count: 1 },
      ],
      selectedId: 'demo-tx',
      select: vi.fn((id: string) => {
        wb.selectedId = id
      }),
      playingId: null as string | null,
      cursorId: null as string | null,
      position: 0,
      total: 0,
      play: vi.fn(),
      step: vi.fn(),
      stop: vi.fn(),
      ...over,
    })
    return wb
  }
  const buttons = (host: HTMLElement) =>
    Array.from(host.querySelectorAll('[data-testid="playlist-workbench"] button')).map((b) => b.textContent?.trim())

  it('offline: offers the playable playlists; "Проиграть" and "Шаг" act on the chosen one', async () => {
    const wb = workbench()
    const { app, host } = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, debug: true, playlists: wb } })
    await nextTick()

    const select = host.querySelector('select[aria-label="Плейлист"]') as HTMLSelectElement
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Платежи (20)', 'Клиринг (1)'])
    expect(buttons(host)).toEqual(['Проиграть', 'Шаг'])

    select.value = 'demo-clearing'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    // The choice is the workbench's: it is asked to select (and so to stop a playback), not kept in the bar.
    expect(wb.select).toHaveBeenCalledWith('demo-clearing')
    ;(Array.from(host.querySelectorAll('[data-testid="playlist-workbench"] button')).find((b) => b.textContent?.trim() === 'Проиграть') as HTMLButtonElement).click()
    expect(wb.play).toHaveBeenCalledWith('demo-clearing')
    ;(Array.from(host.querySelectorAll('[data-testid="playlist-workbench"] button')).find((b) => b.textContent?.trim() === 'Шаг') as HTMLButtonElement).click()
    expect(wb.step).toHaveBeenCalledWith('demo-clearing')

    app.unmount()
    host.remove()
  })

  it('while a playlist plays: "Стоп" only, and the position of the played playlist', async () => {
    const wb = workbench({ playingId: 'demo-tx', cursorId: 'demo-tx', position: 3, total: 20 })
    const { app, host } = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, debug: true, playlists: wb } })
    await nextTick()

    expect(buttons(host)).toEqual(['Стоп'])
    expect(host.querySelector('[aria-label="Позиция плейлиста"]')?.textContent).toBe('3 / 20')
    ;(host.querySelector('[data-testid="playlist-workbench"] button') as HTMLButtonElement).click()
    expect(wb.stop).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
  })

  it('is absent on the server source, and offline when no playlist can be played (no control that does nothing)', async () => {
    const real = await mountBottomBar({ propOverrides: { apiMode: 'real' as const, debug: true, playlists: workbench() } })
    await nextTick()
    expect(real.host.querySelector('[data-testid="playlist-workbench"]')).toBeNull()
    real.app.unmount()
    real.host.remove()

    const none = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, debug: true, playlists: workbench({ offered: [] }) } })
    await nextTick()
    expect(none.host.querySelector('[data-testid="playlist-workbench"]')).toBeNull()
    // Counter-check: the same mount with an offered playlist shows it (the absence above is not a broken selector).
    none.app.unmount()
    none.host.remove()
    const some = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, debug: true, playlists: workbench() } })
    await nextTick()
    expect(some.host.querySelector('[data-testid="playlist-workbench"]')).not.toBeNull()
    some.app.unmount()
    some.host.remove()
  })

  // 048 S1 (decision 5): the workbench is the developer's - offline AND `?debug=1`. Red before S1: it was shown on the
  // offline source alone. The counter-check is the test above: the same mount with `debug` shows it.
  it('is absent offline without ?debug=1, a playable playlist notwithstanding', async () => {
    const { app, host } = await mountBottomBar({ propOverrides: { apiMode: 'fixtures' as const, playlists: workbench() } })
    await nextTick()
    expect(host.querySelector('[data-testid="bottom-details-row"]'), 'the row is there').not.toBeNull()
    expect(host.querySelector('[data-testid="playlist-workbench"]')).toBeNull()
    app.unmount()
    host.remove()
  })
})
