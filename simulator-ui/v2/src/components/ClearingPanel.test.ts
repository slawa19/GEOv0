import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { SimulatorActionClearingRealResponse } from '../api/simulatorTypes'
import type { InteractPhase } from '../composables/interact/useInteractFSM'

import ClearingPanel from './ClearingPanel.vue'

describe('ClearingPanel', () => {
  it('CL-1: in confirm step shows running feedback + disables buttons when busy (not stuck)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-clearing' as InteractPhase,
      fromPid: null as string | null,
      toPid: null as string | null,
      initiatedWithPrefilledFrom: false,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as SimulatorActionClearingRealResponse | null,
    })

    const confirmClearing = vi.fn()

    const app = createApp({
      render: () =>
        h(ClearingPanel, {
          phase: 'confirm-clearing',
          state,
          busy: true,
          equivalent: 'EQ',
          confirmClearing,
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
    expect(help?.querySelector('.cp-spinner')).toBeTruthy()
    expect(host.querySelector('[data-testid="clearing-panel"]')?.getAttribute('aria-busy')).toBe('true')
    expect(help?.getAttribute('role')).toBe('status')
    expect(help?.getAttribute('aria-live')).toBe('polite')

    const btnConfirm = host.querySelector('button.ds-btn--primary') as HTMLButtonElement | null
    expect(btnConfirm).toBeTruthy()
    expect((btnConfirm!.textContent ?? '').trim()).toBe('Running…')
    expect(btnConfirm!.disabled).toBe(true)

    // guard against accidental submit while busy
    btnConfirm!.click()
    expect(confirmClearing).toHaveBeenCalledTimes(0)

    const btnCancel = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Cancel') as
      | HTMLButtonElement
      | undefined
    expect(btnCancel).toBeTruthy()
    expect(btnCancel!.disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('CL-1: in confirm step keeps normal help text when not busy', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'confirm-clearing' as InteractPhase,
      fromPid: null as string | null,
      toPid: null as string | null,
      initiatedWithPrefilledFrom: false,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as SimulatorActionClearingRealResponse | null,
    })

    const app = createApp({
      render: () =>
        h(ClearingPanel, {
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

    const btnConfirm = host.querySelector('button.ds-btn--primary') as HTMLButtonElement | null
    expect(btnConfirm).toBeTruthy()
    expect((btnConfirm!.textContent ?? '').trim()).toBe('Confirm')
    expect(btnConfirm!.disabled).toBe(false)

    const btnCancel = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Cancel') as
      | HTMLButtonElement
      | undefined
    expect(btnCancel).toBeTruthy()
    expect(btnCancel!.disabled).toBe(false)

    app.unmount()
    host.remove()
  })

  it('CL-2: in preview step shows loading state when lastClearing is not ready yet', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      phase: 'clearing-preview' as InteractPhase,
      fromPid: null as string | null,
      toPid: null as string | null,
      initiatedWithPrefilledFrom: false,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: null as SimulatorActionClearingRealResponse | null,
    })

    const app = createApp({
      render: () =>
        h(ClearingPanel, {
          phase: 'clearing-preview',
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

    const loading = host.querySelector('[data-testid="clearing-preview-loading"]') as HTMLElement | null
    expect(loading).toBeTruthy()
    expect(loading?.textContent ?? '').toContain('Preparing preview')
    expect(loading?.querySelector('.cp-spinner')).toBeTruthy()
    expect(loading?.getAttribute('role')).toBe('status')
    expect(loading?.getAttribute('aria-live')).toBe('polite')

    app.unmount()
    host.remove()
  })

  it('announces the completed preview result without adding an alert', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const state = reactive({
      phase: 'clearing-preview' as InteractPhase,
      fromPid: null as string | null,
      toPid: null as string | null,
      initiatedWithPrefilledFrom: false,
      selectedEdgeKey: null as string | null,
      edgeAnchor: null as { x: number; y: number } | null,
      error: null as string | null,
      lastClearing: {
        ok: true,
        equivalent: 'EQ',
        cleared_cycles: 1,
        total_cleared_amount: '12',
        cycles: [{ cleared_amount: '12', edges: [] }],
      } as unknown as SimulatorActionClearingRealResponse,
    })
    const app = createApp({
      render: () =>
        h(ClearingPanel, {
          phase: 'clearing-preview',
          state,
          busy: false,
          equivalent: 'EQ',
          confirmClearing: vi.fn(),
          cancel: vi.fn(),
        }),
    })
    app.mount(host)
    await nextTick()

    const result = host.querySelector('.cp-preview-stack')
    expect(result?.getAttribute('role')).toBe('status')
    expect(result?.getAttribute('aria-live')).toBe('polite')
    expect(result?.textContent).toContain('Total cleared')
    expect(result?.querySelector('[role="alert"]')).toBeNull()

    app.unmount()
    host.remove()
  })
})

