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
  it('renders utilization and hides secondary metrics in compact mode', async () => {
    const { app, host } = mountSystemBalanceBar({ compact: true })
    await nextTick()

    expect(host.textContent).toContain('Total Debt')
    expect(host.textContent).toContain('Utilization')
    expect(host.textContent).not.toContain('Available Capacity')
    expect(host.textContent).not.toContain('Trustlines')
    expect(host.textContent).not.toContain('Participants')

    app.unmount()
    host.remove()
  })

  it('keeps the shared HudBar contract and token-driven progress geometry', () => {
    const sfc = readHere('./SystemBalanceBar.vue')

    expect(sfc).toContain('<HudBar variant="ghost" layout="start" fit class="system-balance-bar__inner">')
    expect(sfc).toContain('width: var(--ds-sbb-track-width);')
    expect(sfc).toContain('height: var(--ds-sbb-track-height);')
    expect(sfc).toContain("<div class=\"ds-progress__bar\" :style=\"{ width: utilPct + '%' }\" />")
  })
})