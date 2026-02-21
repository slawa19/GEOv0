import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'

function mountPopup(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const state = reactive({
    phase: 'editing-trustline',
    fromPid: 'alice',
    toPid: 'bob',
    selectedEdgeKey: 'alice→bob',
    edgeAnchor: { x: 100, y: 200 },
    error: null,
    lastClearing: null,
  })

  const defaultProps: Record<string, unknown> = {
    phase: state.phase,
    state,
    unit: 'UAH',
    used: '0.00',
    limit: '10.00',
    available: '10.00',
    status: 'active',
    busy: false,
    forceHidden: false,
    close: () => undefined,
  }

  const app = createApp({
    render: () =>
      h(EdgeDetailPopup as any, { ...defaultProps, ...overrides }),
  })

  app.mount(host)
  return { app, host }
}

describe('EdgeDetailPopup', () => {
  it('renders when phase=editing-trustline, edgeAnchor set, forceHidden=false (edge click)', async () => {
    const { app, host } = mountPopup({ forceHidden: false })
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]')
    expect(el).toBeTruthy()

    app.unmount()
    host.remove()
  })

  it('does NOT render when forceHidden=true (TrustlineManagementPanel is shown instead)', async () => {
    const { app, host } = mountPopup({ forceHidden: true })
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]')
    expect(el).toBeFalsy()

    app.unmount()
    host.remove()
  })

  it('does NOT render when edgeAnchor is null (no edge click)', async () => {
    const state = reactive({
      phase: 'editing-trustline',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: 'alice→bob',
      edgeAnchor: null,
      error: null,
      lastClearing: null,
    })
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(EdgeDetailPopup as any, {
          phase: state.phase,
          state,
          unit: 'UAH',
          forceHidden: false,
          close: () => undefined,
        }),
    })
    app.mount(host)
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]')
    expect(el).toBeFalsy()

    app.unmount()
    host.remove()
  })
})
