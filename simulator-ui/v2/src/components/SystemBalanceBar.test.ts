import { createApp, h, nextTick, ref, type Component } from 'vue'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import SystemBalanceBar from './SystemBalanceBar.vue'

function readHere(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

function mountSystemBalanceBar(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const defaultProps: Record<string, unknown> = {
    balance: ref({
      totalUsed: 1234,
      totalAvailable: 5678,
      activeTrustlines: 12,
      activeParticipants: 8,
      utilization: 0.42,
      isClean: false,
    }),
    equivalent: 'UAH',
    compact: false,
  }

  const app = createApp({
    render: () => h(SystemBalanceBar as Component, { ...defaultProps, ...overrides }),
  })

  app.mount(host)

  return { host, app }
}

describe('SystemBalanceBar', () => {
  it('compact (043 S1: the bottom row of the first view) shows the total debt alone', async () => {
    const { app, host } = mountSystemBalanceBar({ compact: true })
    await nextTick()

    expect(host.textContent).toContain('Total Debt')
    expect(host.textContent).not.toContain('Utilization')
    expect(host.textContent).not.toContain('Available Capacity')
    expect(host.textContent).not.toContain('Trustlines')
    expect(host.textContent).not.toContain('Participants')
    expect(host.querySelector('[aria-label="System balance"]')).not.toBeNull()

    app.unmount()
    host.remove()
  })

  it('variant "metrics" (043 S1: the Details row) shows everything but the total debt, under its own name; "full" shows all', async () => {
    const metrics = mountSystemBalanceBar({ variant: 'metrics' })
    await nextTick()
    const text = metrics.host.textContent ?? ''
    for (const label of ['Available Capacity', 'Trustlines', 'Participants', 'Utilization']) expect(text).toContain(label)
    expect(text).not.toContain('Total Debt')
    // Its own accessible name: the bottom row's total debt stays the one "System balance" on the page.
    expect(metrics.host.querySelector('[aria-label="System metrics"]')).not.toBeNull()
    expect(metrics.host.querySelector('[aria-label="System balance"]')).toBeNull()
    metrics.app.unmount()
    metrics.host.remove()

    const full = mountSystemBalanceBar()
    await nextTick()
    for (const label of ['Total Debt', 'Available Capacity', 'Trustlines', 'Participants', 'Utilization']) expect(full.host.textContent).toContain(label)
    full.app.unmount()
    full.host.remove()
  })

  it('keeps the shared HudBar contract and token-driven progress geometry', () => {
    const sfc = readHere('./SystemBalanceBar.vue')

    expect(sfc).toContain('<HudBar variant="ghost" layout="start" fit class="system-balance-bar__inner">')
    expect(sfc).toContain('width: var(--ds-sbb-track-width);')
    expect(sfc).toContain('height: var(--ds-sbb-track-height);')
    expect(sfc).toContain("<div class=\"ds-progress__bar\" :style=\"{ width: utilPct + '%' }\" />")
  })
})