import { createApp, h, nextTick } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import NodeCardOverlay from './NodeCardOverlay.vue'
import { fmtAmt } from '../utils/numberFormat'

function mountNodeCard(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const defaultProps: Record<string, unknown> = {
    node: { id: 'alice', name: 'Alice', status: 'active', type: 'person' },
    style: { left: '0px', top: '0px' },
    edgeStats: { outLimitText: '0', inLimitText: '0', degree: 0 },
    equivalentText: 'UAH',

    showPinActions: false,
    isPinned: false,
    pin: () => undefined,
    unpin: () => undefined,

    interactMode: true,
    interactTrustlines: [],
    trustlinesLoading: false,
    interactBusy: false,
  }

  const app = createApp({
    render: () => h(NodeCardOverlay as any, { ...defaultProps, ...overrides }),
  })

  app.mount(host)

  return { app, host }
}

describe('NodeCardOverlay (Interact Mode flags)', () => {
  it('disables quick actions when interactBusy=true (independent of trustlinesLoading)', async () => {
    const { app, host } = mountNodeCard({ interactBusy: true, trustlinesLoading: true })
    await nextTick()

    const btns = host.querySelectorAll('.nco-interact-actions button')
    expect(btns.length).toBe(3)
    expect((btns[0] as HTMLButtonElement).disabled).toBe(true)
    expect((btns[1] as HTMLButtonElement).disabled).toBe(true)
    expect((btns[2] as HTMLButtonElement).disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('does NOT disable quick actions when trustlinesLoading=true but interactBusy=false', async () => {
    const { app, host } = mountNodeCard({ interactBusy: false, trustlinesLoading: true })
    await nextTick()

    const btns = host.querySelectorAll('.nco-interact-actions button')
    expect(btns.length).toBe(3)
    expect((btns[0] as HTMLButtonElement).disabled).toBe(false)
    expect((btns[1] as HTMLButtonElement).disabled).toBe(false)
    expect((btns[2] as HTMLButtonElement).disabled).toBe(false)

    app.unmount()
    host.remove()
  })

  it('renders loading placeholder when no trustlines and trustlinesLoading=true', async () => {
    const { app, host } = mountNodeCard({ interactTrustlines: [], trustlinesLoading: true })
    await nextTick()

    const empty = host.querySelector('.nco-trustlines__empty')
    expect(empty?.textContent ?? '').toContain('Loading trustlines')

    app.unmount()
    host.remove()
  })

  it('disables trustline edit buttons when interactBusy=true', async () => {
    const onInteractEditTrustline = vi.fn()
    const trustlines = [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent: 'UAH',
        limit: '10.00',
        used: '1.00',
        available: '9.00',
        status: 'active',
      },
    ]

    const { app, host } = mountNodeCard({
      interactTrustlines: trustlines,
      interactBusy: true,
      onInteractEditTrustline,
    })
    await nextTick()

    const editBtn = host.querySelector('.nco-trustline-row__edit') as HTMLButtonElement | null
    expect(editBtn).toBeTruthy()
    expect(editBtn?.disabled).toBe(true)

    editBtn?.click()
    expect(onInteractEditTrustline).toHaveBeenCalledTimes(0)

    app.unmount()
    host.remove()
  })

  it('NC-1: IN trustline renders edit button and clicking it calls onInteractEditTrustline(from,to)', async () => {
    const onInteractEditTrustline = vi.fn()
    const trustlines = [
      {
        from_pid: 'bob',
        from_name: 'Bob',
        to_pid: 'alice',
        to_name: 'Alice',
        equivalent: 'UAH',
        limit: '10.00',
        used: '1.00',
        available: '9.00',
        status: 'active',
      },
    ]

    const { app, host } = mountNodeCard({
      node: { id: 'alice', name: 'Alice', status: 'active', type: 'person' },
      interactTrustlines: trustlines,
      onInteractEditTrustline,
    })
    await nextTick()

    const editBtn = host.querySelector('button[aria-label="Edit trustline"]') as HTMLButtonElement | null
    expect(editBtn).toBeTruthy()

    editBtn?.click()
    await nextTick()

    expect(onInteractEditTrustline).toHaveBeenCalledTimes(1)
    expect(onInteractEditTrustline).toHaveBeenCalledWith('bob', 'alice')

    app.unmount()
    host.remove()
  })

  it('NC-2: trustline row renders available column (fmtAmt(tl.available))', async () => {
    const trustlines = [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent: 'UAH',
        limit: '10.00',
        used: '1.00',
        available: '9.00',
        status: 'active',
      },
    ]

    const { app, host } = mountNodeCard({
      node: { id: 'alice', name: 'Alice', status: 'active', type: 'person' },
      interactTrustlines: trustlines,
    })
    await nextTick()

    const avail = host.querySelector('.nco-trustline-row__avail') as HTMLElement | null
    expect(avail).toBeTruthy()
    expect(avail?.classList.contains('ds-mono')).toBe(true)
    expect((avail?.textContent ?? '').trim()).toBe(fmtAmt('9.00'))

    app.unmount()
    host.remove()
  })

  it('NC-3: trustline with available="0" gets saturated class', async () => {
    const trustlines = [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent: 'UAH',
        limit: '10.00',
        used: '1.00',
        available: '0',
        status: 'active',
      },
    ]

    const { app, host } = mountNodeCard({
      node: { id: 'alice', name: 'Alice', status: 'active', type: 'person' },
      interactTrustlines: trustlines,
    })
    await nextTick()

    const row = host.querySelector('.nco-trustline-row') as HTMLElement | null
    expect(row).toBeTruthy()
    expect(row?.classList.contains('nco-trustline-row--saturated')).toBe(true)

    app.unmount()
    host.remove()
  })

  it('NC-4: clicking quick action "Run Clearing" calls onInteractRunClearing()', async () => {
    const onInteractRunClearing = vi.fn()

    const { app, host } = mountNodeCard({ onInteractRunClearing })
    await nextTick()

    const btn = host.querySelector('button[title="Run clearing (global)"]') as HTMLButtonElement | null
    expect(btn).toBeTruthy()
    btn?.click()
    await nextTick()

    expect(onInteractRunClearing).toHaveBeenCalledTimes(1)

    app.unmount()
    host.remove()
  })
})

