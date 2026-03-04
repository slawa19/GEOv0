import { createApp, h, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import HudBar from './HudBar.vue'

function mountHudBar(
  props: Record<string, unknown> = {},
  slot: Record<string, any> | null = null,
) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const app = createApp({
    render: () => h(HudBar as any, props, slot ?? { default: () => 'content' }),
  })

  app.mount(host)

  return { host, app }
}

describe('HudBar', () => {
  it('renders slot content and applies variant/layout/fit classes', async () => {
    const { app, host } = mountHudBar(
      { variant: 'ghost', layout: 'start', fit: true },
      {
        default: () => [
          h('div', { class: 'hud-bar__left', 'data-testid': 'left' }, 'L'),
          h('div', { class: 'hud-bar__center', 'data-testid': 'center' }, 'C'),
          h('div', { class: 'hud-bar__right', 'data-testid': 'right' }, 'R'),
        ],
      },
    )
    await nextTick()

    const bar = host.querySelector('.hud-bar') as HTMLElement | null
    expect(bar).toBeTruthy()
    expect(bar?.classList.contains('ds-panel')).toBe(true)
    expect(bar?.classList.contains('hud-bar--variant-ghost')).toBe(true)
    expect(bar?.classList.contains('hud-bar--layout-start')).toBe(true)
    expect(bar?.classList.contains('hud-bar--fit')).toBe(true)

    expect(host.querySelector('[data-testid="left"]')?.textContent).toBe('L')
    expect(host.querySelector('[data-testid="center"]')?.textContent).toBe('C')
    expect(host.querySelector('[data-testid="right"]')?.textContent).toBe('R')

    app.unmount()
    host.remove()
  })

  it('has predictable defaults (solid + between, no fit)', async () => {
    const { app, host } = mountHudBar()
    await nextTick()

    const bar = host.querySelector('.hud-bar') as HTMLElement | null
    expect(bar).toBeTruthy()
    expect(bar?.classList.contains('hud-bar--variant-solid')).toBe(true)
    expect(bar?.classList.contains('hud-bar--layout-between')).toBe(true)
    expect(bar?.classList.contains('hud-bar--fit')).toBe(false)

    app.unmount()
    host.remove()
  })
})

