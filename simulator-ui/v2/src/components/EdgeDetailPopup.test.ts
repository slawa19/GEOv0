import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it } from 'vitest'

import EdgeDetailPopup from './EdgeDetailPopup.vue'

describe('EdgeDetailPopup', () => {
  it('uses anchor prop to position popup (style left/top)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'editing-trustline',
      fromPid: 'alice',
      toPid: 'bob',
      selectedEdgeKey: 'alice→bob',
      error: null,
      lastClearing: null,
    })

    const app = createApp({
      render: () =>
        h(EdgeDetailPopup as any, {
          phase: state.phase,
          state,
          anchor: { x: 100, y: 200 },
          unit: 'UAH',
          used: '0.00',
          limit: '10.00',
          available: '10.00',
          status: 'active',
          busy: false,
          close: () => undefined,
        }),
    })

    app.mount(host)
    await nextTick()

    const el = host.querySelector('[data-testid="edge-detail-popup"]') as HTMLDivElement | null
    expect(el).toBeTruthy()

    // +12 offset as per component logic.
    expect(el!.style.left).toBe('112px')
    expect(el!.style.top).toBe('212px')

    app.unmount()
    host.remove()
  })
})

