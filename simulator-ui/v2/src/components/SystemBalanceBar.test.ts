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

/** The metrics of the bar by their test ids (048 S1: by structure, not by their words - 046 owns the words). */
const METRICS = ['sbb-available', 'sbb-trustlines', 'sbb-participants', 'sbb-utilization'] as const
const shownIds = (host: HTMLElement) => [...host.querySelectorAll<HTMLElement>('[data-testid^="sbb-"]')].map((el) => el.dataset.testid)

describe('SystemBalanceBar', () => {
  it('compact (043 S1: the bottom row of the first view) shows the total debt alone', async () => {
    const { app, host } = mountSystemBalanceBar({ compact: true })
    await nextTick()

    expect(shownIds(host)).toEqual(['sbb-total-debt'])
    expect(host.querySelector('[data-testid="sbb-total-debt"] .ds-value')?.textContent, 'its amount, at the precision of UAH').toContain('1234.00')
    // 046 wave 1, full (2026-10-19): by test id; its accessible name is the dictionary's ("System balance" in English).
    expect(host.querySelector('[data-testid="system-balance"]')?.getAttribute('aria-label')).toBe('System balance')

    app.unmount()
    host.remove()
  })

  it('variant "metrics" (043 S1: the Details row) shows everything but the total debt, under its own name; "full" shows all', async () => {
    const metrics = mountSystemBalanceBar({ variant: 'metrics' })
    await nextTick()
    expect(shownIds(metrics.host)).toEqual([...METRICS])
    // Its own accessible name: the bottom row's total debt stays the one "System balance" on the page.
    expect(metrics.host.querySelector('[data-testid="system-metrics"]')?.getAttribute('aria-label')).toBe('System metrics')
    expect(metrics.host.querySelector('[data-testid="system-balance"]')).toBeNull()
    metrics.app.unmount()
    metrics.host.remove()

    const full = mountSystemBalanceBar()
    await nextTick()
    expect(shownIds(full.host)).toEqual(['sbb-total-debt', ...METRICS])
    full.app.unmount()
    full.host.remove()
  })

  // 048 S1 (decision 4): the "Clean" badge repeated "Общий долг 0.00" - with no debt the bar shows the total and the
  // metrics, and nothing else. Red before S1: a sixth panel (the badge) in every variant but `debt`.
  it('no debt: no "Clean" badge beside the metrics - the total debt says 0', async () => {
    const zero = { totalUsed: '0', totalAvailable: '10', equivalent: 'UAH', activeTrustlines: 1, activeParticipants: 2, utilization: 0 }
    for (const variant of ['metrics', 'full'] as const) {
      const { app, host } = mountSystemBalanceBar({ balance: ref({ ...zero, isClean: true }), variant })
      await nextTick()
      const panels = host.querySelectorAll('.ds-ov-metric')
      expect(panels.length, `${variant}: one panel per metric`).toBe(variant === 'full' ? 5 : 4)
      expect(host.querySelector('.ds-badge--ok'), `${variant}: no "Clean" badge`).toBeNull()
      app.unmount()
      host.remove()
    }
  })

  it('keeps the shared HudBar contract and token-driven progress geometry', () => {
    const sfc = readHere('./SystemBalanceBar.vue')

    expect(sfc).toContain('<HudBar variant="ghost" layout="start" fit class="system-balance-bar__inner">')
    expect(sfc).toContain('width: var(--ds-sbb-track-width);')
    expect(sfc).toContain('height: var(--ds-sbb-track-height);')
    expect(sfc).toContain("<div class=\"ds-progress__bar\" :style=\"{ width: utilPct + '%' }\" />")
  })
})