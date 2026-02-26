import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import TrustlineManagementPanel from './TrustlineManagementPanel.vue'

describe('TrustlineManagementPanel', () => {
  it('TL-1a: createValid accepts 0 (Create enabled for valid from/to + limit=0)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      error: null as string | null,
    })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: 'confirm-trustline-create',
          state,
          unit: 'EQ',
          used: '0',
          currentLimit: null,
          available: null,
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    expect(host.textContent ?? '').toContain('ESC to close')

    const input = host.querySelector('#tl-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()

    input!.value = '0'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Create') as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(false)

    app.unmount()
    host.remove()
  })

  it('TL-1: when newLimit < used, shows inline warn and disables Update', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      error: null as string | null,
    })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '10',
          currentLimit: '20',
          available: '10',
          participants: [],
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#tl-new-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()

    input!.value = '5'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    const warn = host.querySelector('[data-testid="tl-limit-too-low"]') as HTMLElement | null
    expect(warn).toBeTruthy()
    expect((warn!.textContent ?? '').trim()).toContain('New limit must be ≥ used')

    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Update') as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('TL-2 (Phase 1): when effectiveUsed > 0, disables Close and shows warning', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      error: null as string | null,
    })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '1',
          currentLimit: '10',
          available: '9',
          participants: [],
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const btn = host.querySelector('[data-testid="trustline-close-btn"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(true)

    const warn = Array.from(host.querySelectorAll('.ds-alert--warn')).find((el) => (el.textContent ?? '').includes('Cannot close: trustline has outstanding debt')) as
      | HTMLElement
      | undefined
    expect(warn).toBeTruthy()
    expect((warn!.textContent ?? '').trim()).toContain('(1 EQ)')

    app.unmount()
    host.remove()
  })

  it("TL-1/TL-1a: normalizes amount before sending ('1,5' -> '1.5')", async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = reactive({
      fromPid: 'alice' as string | null,
      toPid: 'bob' as string | null,
      error: null as string | null,
    })

    const confirmTrustlineCreate = vi.fn()
    const confirmTrustlineUpdate = vi.fn()

    const ui = reactive({
      phase: 'confirm-trustline-create' as 'confirm-trustline-create' | 'editing-trustline',
      used: '0',
    })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: ui.phase,
          state,
          unit: 'EQ',
          used: ui.used,
          currentLimit: '10',
          available: '10',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          trustlines: [],
          busy: false,
          confirmTrustlineCreate,
          confirmTrustlineUpdate,
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    // Create
    {
      const input = host.querySelector('#tl-limit') as HTMLInputElement
      input.value = '1,5'
      input.dispatchEvent(new Event('input'))
      await nextTick()

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Create') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      btn.click()
      expect(confirmTrustlineCreate).toHaveBeenCalledTimes(1)
      expect(confirmTrustlineCreate).toHaveBeenCalledWith('1.5')
    }

    // Switch to edit
    ui.phase = 'editing-trustline'
    ui.used = '0'
    await nextTick()
    await nextTick()

    {
      const input = host.querySelector('#tl-new-limit') as HTMLInputElement
      input.value = '1,5'
      input.dispatchEvent(new Event('input'))
      await nextTick()

      const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Update') as HTMLButtonElement
      expect(btn.disabled).toBe(false)
      btn.click()
      expect(confirmTrustlineUpdate).toHaveBeenCalledTimes(1)
      expect(confirmTrustlineUpdate).toHaveBeenCalledWith('1.5')
    }

    app.unmount()
    host.remove()
  })
})

