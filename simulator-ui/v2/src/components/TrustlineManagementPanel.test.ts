import { createApp, h, nextTick, reactive } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import TrustlineManagementPanel from './TrustlineManagementPanel.vue'

function baseState(partial?: Partial<any>) {
  // Minimal InteractState shape used by the panel.
  return reactive({
    phase: 'idle',
    fromPid: null as string | null,
    toPid: null as string | null,
    selectedEdgeKey: null as string | null,
    edgeAnchor: null as { x: number; y: number } | null,
    error: null as string | null,
    lastClearing: null as any,
    ...(partial ?? {}),
  })
}

describe('TrustlineManagementPanel', () => {
  it('TL-1a: createValid accepts 0 (Create enabled for valid from/to + limit=0)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

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

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

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

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

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

    const warn = host.querySelector('[data-testid="tl-close-blocked"]') as HTMLElement | null
    expect(warn).toBeTruthy()
    expect((warn!.textContent ?? '').trim()).toContain('used: 1 EQ')

    app.unmount()
    host.remove()
  })

  it('AC-TL-10: reverse_used > 0, used = 0 => Close TL disabled + inline warning', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          // used must be 0, but reverse debt exists
          used: '0',
          currentLimit: '10',
          available: '10',
          participants: [],
          trustlines: [
            {
              from_pid: 'alice',
              from_name: 'Alice',
              to_pid: 'bob',
              to_name: 'Bob',
              equivalent: 'EQ',
              limit: '10.00',
              used: '0.00',
              reverse_used: '0.01',
              available: '10.00',
              status: 'active',
            },
          ],
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

    const warn = host.querySelector('[data-testid="tl-close-blocked"]') as HTMLElement | null
    expect(warn).toBeTruthy()
    expect((warn!.textContent ?? '').toLowerCase()).toContain('reverse')
    expect(warn!.textContent ?? '').toContain('0.01')

    app.unmount()
    host.remove()
  })

  it("TL-1/TL-1a: normalizes amount before sending ('1,5' -> '1.5')", async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

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

  it('TL-3: marks existing trustlines as (exists) in create-flow To dropdown', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: null })

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
            { pid: 'carol', name: 'Carol' },
          ],
          trustlines: [
            {
              from_pid: 'alice',
              from_name: 'Alice',
              to_pid: 'bob',
              to_name: 'Bob',
              equivalent: 'EQ',
              limit: '10.00',
              used: '0.00',
              available: '10.00',
              status: 'active',
            },
          ],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate: vi.fn(),
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
          setFromPid: vi.fn(),
          setToPid: vi.fn(),
          selectTrustline: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const toSelect = host.querySelector('#tl-to') as HTMLSelectElement | null
    expect(toSelect).toBeTruthy()
    expect(toSelect!.disabled).toBe(false)

    const optionsText = Array.from(toSelect!.querySelectorAll('option')).map((o) => (o.textContent ?? '').trim())
    expect(optionsText.some((t) => t.includes('(exists)'))).toBe(true)

    app.unmount()
    host.remove()
  })

  it('AC-TL-7: newLimit="0" with used="0" enables Update and sends "0"', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })
    const confirmTrustlineUpdate = vi.fn()

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          used: '0',
          currentLimit: '10',
          available: '10',
          participants: [],
          trustlines: [],
          busy: false,
          confirmTrustlineCreate: vi.fn(),
          confirmTrustlineUpdate,
          confirmTrustlineClose: vi.fn(),
          cancel: vi.fn(),
        }),
    })

    app.mount(host)
    await nextTick()

    const input = host.querySelector('#tl-new-limit') as HTMLInputElement | null
    expect(input).toBeTruthy()

    input!.value = '0'
    input!.dispatchEvent(new Event('input'))
    await nextTick()

    // Update button should be enabled (0 >= 0 used).
    const btn = Array.from(host.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim() === 'Update') as HTMLButtonElement | undefined
    expect(btn).toBeTruthy()
    expect(btn!.disabled).toBe(false)

    // No limit-too-low warning.
    const warn = host.querySelector('[data-testid="tl-limit-too-low"]') as HTMLElement | null
    expect(warn).toBeNull()

    // Click sends normalized "0".
    btn!.click()
    expect(confirmTrustlineUpdate).toHaveBeenCalledTimes(1)
    expect(confirmTrustlineUpdate).toHaveBeenCalledWith('0')

    app.unmount()
    host.remove()
  })

  it('TL-4: pre-fills newLimit from effectiveLimit (trustlines list) rather than props.currentLimit', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const state = baseState({ fromPid: 'alice', toPid: 'bob' })

    const app = createApp({
      render: () =>
        h(TrustlineManagementPanel as any, {
          phase: 'editing-trustline',
          state,
          unit: 'EQ',
          // Intentionally conflicting snapshot-like prop.
          currentLimit: '111.00',
          used: '0.00',
          available: '111.00',
          participants: [
            { pid: 'alice', name: 'Alice' },
            { pid: 'bob', name: 'Bob' },
          ],
          trustlines: [
            {
              from_pid: 'alice',
              from_name: 'Alice',
              to_pid: 'bob',
              to_name: 'Bob',
              equivalent: 'EQ',
              limit: '10.00',
              used: '0.00',
              available: '10.00',
              status: 'active',
            },
          ],
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
    expect(input!.value).toBe('10.00')

    app.unmount()
    host.remove()
  })
})

