import { createApp, h, nextTick, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import InteractHistoryLog from './InteractHistoryLog.vue'

function mountInteractHistoryLog(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const defaultProps: Record<string, unknown> = {
    // 046 wave 3 (2026-10-19): a line is kept as its dictionary key and data (BACKLOG 046-3); the data tells them apart.
    entries: [
      { id: '1', icon: 'A', line: { key: 'history.trustlineClosed', vars: { from: 'First', to: 'action' } }, timeText: '10:00' },
      { id: '2', icon: 'B', line: { key: 'history.trustlineClosed', vars: { from: 'Second', to: 'action' } }, timeText: '10:01' },
      { id: '3', icon: 'C', line: { key: 'history.trustlineClosed', vars: { from: 'Third', to: 'action' } }, timeText: '10:02' },
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
    expect(items[0]?.textContent).toContain('Trustline closed: Third → action')
    expect(items[1]?.textContent).toContain('Trustline closed: Second → action')
    expect(items[0]?.classList.contains('interact-history__item--latest')).toBe(true)

    app.unmount()
    host.remove()
  })

  // How the root hosts it (the overlay shell, three lines) is observed in the root's DOM since 043 S1:
  // SimulatorAppRoot.interact.test.ts, "043 S1: the history is three lines in the bottom overlay shell".
})
