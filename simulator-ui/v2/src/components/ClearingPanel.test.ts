import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import ClearingPanel from './ClearingPanel.vue'

describe('ClearingPanel', () => {
  it('CL-1: in confirm step shows Running clearing… help when busy', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-clearing',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ClearingPanel as any, {
          phase: 'confirm-clearing',
          state,
          busy: true,
          equivalent: 'EQ',
          confirmClearing: vi.fn(),
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent ?? '').toContain('ESC to close')

    const help = host.querySelector('[data-testid="clearing-confirm-help"]') as HTMLElement | null
    expect(help).toBeTruthy()
    expect(help?.textContent ?? '').toContain('Running clearing…')

    app.unmount()
    host.remove()
  })

  it('CL-1: in confirm step keeps normal help text when not busy', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-clearing',
      fromPid: null as string | null,
      toPid: null as string | null,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as any,
    })

    const app = createApp({
      render: () =>
        h(ClearingPanel as any, {
          phase: 'confirm-clearing',
          state,
          busy: false,
          equivalent: 'EQ',
          confirmClearing: vi.fn(),
          cancel: vi.fn(),
          anchor: null,
          hostEl: null,
        }),
    })

    app.mount(host)
    await nextTick()

    const help = host.querySelector('[data-testid="clearing-confirm-help"]') as HTMLElement | null
    expect(help).toBeTruthy()
    expect(help?.textContent ?? '').toContain('This will run a clearing cycle in backend.')

    app.unmount()
    host.remove()
  })
})

