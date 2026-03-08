import { createApp, h, nextTick, type Component, type Slots } from 'vue'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import HudBar from './HudBar.vue'

function readHere(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

function mountHudBar(
  props: Record<string, unknown> = {},
  slot: Slots | null = null,
) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const component: Component = HudBar
  const app = createApp({
    render: () => h(component, props, slot ?? { default: () => 'content' }),
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

  it('owns the shared narrow-viewport HUD section and control shrink contract', () => {
    const sfc = readHere('./HudBar.vue')

    expect(sfc).toContain('@media (max-width: 500px)')
    expect(sfc).toContain(':deep(.hud-bar__left),')
    expect(sfc).toContain(':deep(.hud-bar__center),')
    expect(sfc).toContain(':deep(.hud-bar__right) {')
    expect(sfc).toContain(':deep(.ds-select) {')
    expect(sfc).toContain(':deep(.ds-btn) {')
    expect(sfc).toContain(':deep(.ds-label) {')
  })
})

