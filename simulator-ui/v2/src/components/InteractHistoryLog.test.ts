import { createApp, h, nextTick, type Component } from 'vue'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import InteractHistoryLog from './InteractHistoryLog.vue'

function readHere(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

function mountInteractHistoryLog(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const defaultProps: Record<string, unknown> = {
    entries: [
      { id: '1', icon: 'A', text: 'First action', timeText: '10:00' },
      { id: '2', icon: 'B', text: 'Second action', timeText: '10:01' },
      { id: '3', icon: 'C', text: 'Third action', timeText: '10:02' },
    ],
    maxVisible: 2,
  }

  const app = createApp({
    render: () => h(InteractHistoryLog as Component, { ...defaultProps, ...overrides }),
  })

  app.mount(host)

  return { host, app }
}

describe('InteractHistoryLog', () => {
  it('shows only the most recent entries in reverse chronological order', async () => {
    const { app, host } = mountInteractHistoryLog()
    await nextTick()

    const items = Array.from(host.querySelectorAll('.interact-history__item'))
    expect(items).toHaveLength(2)
    expect(items[0]?.textContent).toContain('Third action')
    expect(items[1]?.textContent).toContain('Second action')
    expect(items[0]?.classList.contains('interact-history__item--latest')).toBe(true)

    app.unmount()
    host.remove()
  })

  it('keeps token-driven bottom-overlay styling and is hosted via the root bottom overlay shell', () => {
    const sfc = readHere('./InteractHistoryLog.vue')
    const root = readHere('./SimulatorAppRoot.vue')

    expect(sfc).toContain('font-size: var(--ds-typo-section-label-font-size);')
    expect(sfc).toContain('gap: var(--ds-ihl-list-gap);')
    expect(sfc).toContain('transition: opacity var(--ds-dur-slow);')
    expect(sfc).toContain('font-size: var(--ds-ihl-time-font-size);')

    expect(root).toContain('class="ds-ov-bottom sar-interact-history-overlay"')
    expect(root).toContain('<InteractHistoryLog :entries="interact.mode.history" :max-visible="8" />')
  })
})