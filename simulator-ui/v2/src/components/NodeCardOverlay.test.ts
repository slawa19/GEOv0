import { createApp, h, nextTick, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import NodeCardOverlay from './NodeCardOverlay.vue'
import { DATA_PALETTE_DEFAULT } from '../vizMapping'
import { resetEquivalentPrecisions, setEquivalentPrecisions } from '../config/equivalentPrecision'

function mountNodeCard(overrides: Record<string, unknown> = {}) {
  const host = document.createElement('div')
  document.body.appendChild(host)

  const defaultProps: Record<string, unknown> = {
    node: { id: 'alice', name: 'Alice', status: 'active', type: 'person' },
    edgeStats: { outLimitText: '0', inLimitText: '0' },
    equivalentText: 'UAH',
    palette: DATA_PALETTE_DEFAULT,

    showPinActions: false,
    isPinned: false,
    pin: () => undefined,
    unpin: () => undefined,

    interactMode: true,
    interactTrustlines: [],
    trustlinesLoading: false,
    interactBusy: false,
  }

  const component: Component = NodeCardOverlay
  const app = createApp({
    render: () => h(component, { ...defaultProps, ...overrides }),
  })

  app.mount(host)

  return { app, host }
}

describe('NodeCardOverlay (Interact Mode flags)', () => {
  it('disables quick actions when interactBusy=true (independent of trustlinesLoading)', async () => {
    const { app, host } = mountNodeCard({ interactBusy: true, trustlinesLoading: true })
    await nextTick()

    const btns = host.querySelectorAll('.nco-interact-actions button')
    expect(btns.length).toBe(2)
    expect((btns[0] as HTMLButtonElement).disabled).toBe(true)
    expect((btns[1] as HTMLButtonElement).disabled).toBe(true)

    app.unmount()
    host.remove()
  })

  it('does NOT disable quick actions when trustlinesLoading=true but interactBusy=false', async () => {
    const { app, host } = mountNodeCard({ interactBusy: false, trustlinesLoading: true })
    await nextTick()

    const btns = host.querySelectorAll('.nco-interact-actions button')
    expect(btns.length).toBe(2)
    expect((btns[0] as HTMLButtonElement).disabled).toBe(false)
    expect((btns[1] as HTMLButtonElement).disabled).toBe(false)

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

    const editBtn = host.querySelector('button[aria-label="Edit trustline (set by Bob)"]') as HTMLButtonElement | null
    expect(editBtn).toBeTruthy()

    editBtn?.click()
    await nextTick()

    expect(onInteractEditTrustline).toHaveBeenCalledTimes(1)
    expect(onInteractEditTrustline).toHaveBeenCalledWith('bob', 'alice')

    app.unmount()
    host.remove()
  })

  /**
   * 012 / `T1208`, `C-B4-1-001`. This case used to compute its own expectation by calling
   * the very formatter under test — `toBe(`avail: ${fmtAmt('9.00')}`)`, i.e. f(x) === f(x),
   * an assertion structurally unable to fail whatever the formatter did with the money. It
   * now states the amount it expects, and a second case substitutes the equivalent to show
   * the expectation is not a constant.
   */
  function availableTextFor(equivalent: string, available = '9.00'): Promise<string> {
    const trustlines = [
      {
        from_pid: 'alice',
        from_name: 'Alice',
        to_pid: 'bob',
        to_name: 'Bob',
        equivalent,
        limit: '10.00',
        used: '1.00',
        available,
        status: 'active',
      },
    ]

    const { app, host } = mountNodeCard({
      node: { id: 'alice', name: 'Alice', status: 'active', type: 'person' },
      interactTrustlines: trustlines,
      equivalentText: equivalent,
    })

    return nextTick().then(() => {
      // 045 S3 (2026-10-18): the third column is gone; what is available is the row's `title`. 046 wave 3 (2026-10-19): the
      // title is the row's one sentence of what is available (`card.lineAvailable`, with the unit) - was "avail: <amount>".
      const row = host.querySelector('.nco-trustline-row') as HTMLElement | null
      expect(row).toBeTruthy()
      expect(host.querySelector('.nco-trustline-row__avail'), 'no third column').toBeNull()
      const text = (row?.getAttribute('title') ?? '').trim()
      app.unmount()
      host.remove()
      return text
    })
  }

  it('NC-2: trustline row renders available at the precision its equivalent declares', async () => {
    // UAH declares precision 2, so 9.00 available must read as 9.00 — not as `9`, which is
    // what the previous `parseFloat`-based formatter printed.
    expect(await availableTextFor('UAH')).toBe('available 9.00 UAH')
  })

  // 026 `T2602`: a limit lowered below the debt makes `available` negative - the excess over trust, not an amount.
  it('NC-2: a negative available reads as the excess over trust', async () => {
    expect(await availableTextFor('UAH', '-2.00')).toBe('over limit by 2.00 UAH')
  })

  it('NC-2: the same available amount must not render identically under UAH and HOUR', async () => {
    // 028 В-4: HOUR ships at 2 like UAH, so the catalogue answers 1 here to keep two precisions apart.
    setEquivalentPrecisions([{ code: 'HOUR', precision: 1 }])
    const asUah = await availableTextFor('UAH')
    const asHour = await availableTextFor('HOUR')
    resetEquivalentPrecisions()

    expect(
      asHour,
      `9.00 available renders as "${asUah}" under UAH (precision 2) and as "${asHour}" under `
        + 'HOUR (precision 1). Identical output means the row is blind to its own equivalent.',
    ).not.toBe(asUah)
    expect(asHour).toBe('available 9.0 HOUR')
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

  it('NC-3: trustline is NOT saturated when available is unknown/NaN (non-finite)', async () => {
    const cases = [null, undefined, '', '   ', 'abc', 'NaN']

    for (const available of cases) {
      const trustlines = [
        {
          from_pid: 'alice',
          from_name: 'Alice',
          to_pid: 'bob',
          to_name: 'Bob',
          equivalent: 'UAH',
          limit: '10.00',
          used: '1.00',
          available,
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
      expect(row?.classList.contains('nco-trustline-row--saturated')).toBe(false)

      app.unmount()
      host.remove()
    }
  })

  it('leaves geometry and the dialog role to the WM window: no inline geometry on the card, no nested dialog', async () => {
    // F-041-9 (041 T4107): was a read of the .vue text; now the rendered card is observed.
    // 045 S4: the card no longer overrides its own position inline (it is a static block by the stylesheet; the computed
    // value is read in a browser by e2e/p045-s3-inspectors.spec.ts, (и)) - here: nothing positions it from inside.
    const { app, host } = mountNodeCard()
    await nextTick()

    const card = host.querySelector('.ds-ov-node-card') as HTMLElement | null
    expect(card).toBeTruthy()
    expect(card!.getAttribute('style'), 'no inline style: geometry belongs to the WM shell').toBeNull()
    expect(host.querySelector('[role="dialog"]')).toBeNull()
    expect(host.querySelector('.ds-inspector-row')).toBeNull()

    app.unmount()
    host.remove()
  })

  // 045 S3, BACKLOG 043-4: Pay and New line start a flow only from `idle` (`useInteractMode` returns early otherwise); while
  // another action is open the card says so instead of offering buttons that silently do nothing.
  it('043-4: with another action open, Pay and New line are disabled and the reason is on a line and in their title', async () => {
    const onPay = vi.fn()
    const { app, host } = mountNodeCard({ interactPhase: 'picking-payment-from', onInteractSendPayment: onPay })
    await nextTick()
    const pay = host.querySelector('[data-testid="node-card-send-payment"]') as HTMLButtonElement
    const line = host.querySelector('[data-testid="node-card-new-trustline"]') as HTMLButtonElement
    const reason = host.querySelector('[data-testid="node-card-actions-reason"]')
    expect([pay.disabled, line.disabled]).toEqual([true, true])
    expect(reason?.textContent?.trim()).toBe('Cancel current action first (press ESC).')
    expect(pay.title).toBe(reason?.textContent?.trim())
    expect(pay.getAttribute('aria-describedby')).toBe('node-card-actions-reason')
    pay.click()
    expect(onPay).not.toHaveBeenCalled()
    app.unmount()
    host.remove()
  })

  it('043-4 counter-check: at idle the buttons are enabled, carry no reason and start the flow', async () => {
    const onPay = vi.fn()
    const { app, host } = mountNodeCard({ interactPhase: 'idle', onInteractSendPayment: onPay })
    await nextTick()
    const pay = host.querySelector('[data-testid="node-card-send-payment"]') as HTMLButtonElement
    expect(pay.disabled).toBe(false)
    expect(pay.hasAttribute('title')).toBe(false)
    expect(host.querySelector('[data-testid="node-card-actions-reason"]')).toBeNull()
    pay.click()
    expect(onPay).toHaveBeenCalledWith('alice')
    app.unmount()
    host.remove()
  })

  it('043-4: while busy the reason says the operation is in progress', async () => {
    const { app, host } = mountNodeCard({ interactPhase: 'idle', interactBusy: true })
    await nextTick()
    expect(host.querySelector('[data-testid="node-card-actions-reason"]')?.textContent?.trim()).toBe('Operation in progress… please wait.')
    app.unmount()
    host.remove()
  })

  // Owner's principle: equivalents are never mixed - a line of another equivalent is not listed under this card's one.
  it('lists only the lines of the equivalent the card is labelled with', async () => {
    const line = (to: string, equivalent: string) => ({ from_pid: 'alice', from_name: 'Alice', to_pid: to, to_name: to, equivalent, limit: '10.00', used: '1.00', available: '9.00', status: 'active' })
    const { app, host } = mountNodeCard({ equivalentText: 'UAH', interactTrustlines: [line('bob', 'UAH'), line('carol', 'HOUR'), line('dave', 'uah')] })
    await nextTick()
    const peers = Array.from(host.querySelectorAll('.nco-trustline-row__peer')).map((e) => (e.textContent ?? '').trim())
    expect(peers.sort(), 'the HOUR line is not listed under UAH').toEqual(['bob', 'dave'])
    app.unmount()
    host.remove()
  })

  // 039 closing review (finding 3): the card compares equivalents with THE shared `sameEquivalent` (039 target item 9),
  // and an empty value means the same here as there - empty after trimming (missing, '', blanks) is "no equivalent".
  // Red before (a local copy, `trim().toUpperCase()` of both sides, guards on the raw value): a row of blanks was
  // dropped under UAH although a row of '' was listed, and a card labelled with blanks dropped every line that had one.
  it('filters its lines with the shared equivalent semantics, empty values included', async () => {
    const line = (to: string, equivalent: unknown) => ({ from_pid: 'alice', from_name: 'Alice', to_pid: to, to_name: to, equivalent, limit: '10.00', used: '1.00', available: '9.00', status: 'active' })
    const lines = [line('same', ' uah '), line('other', 'HOUR'), line('near', 'UAH1'), line('missing', undefined), line('emptyStr', ''), line('blanks', '   ')]
    const peersUnder = async (equivalentText: unknown) => {
      const { app, host } = mountNodeCard({ equivalentText, interactTrustlines: lines })
      await nextTick()
      const peers = Array.from(host.querySelectorAll('.nco-trustline-row__peer')).map((e) => (e.textContent ?? '').trim()).sort()
      app.unmount()
      host.remove()
      return peers
    }
    // Another equivalent is never listed; a row without its own equivalent (missing, '' or blanks alike) is the card's.
    expect(await peersUnder('UAH')).toEqual(['blanks', 'emptyStr', 'missing', 'same'])
    // A card without an equivalent restricts nothing - blanks mean the same as ''.
    const all = ['blanks', 'emptyStr', 'missing', 'near', 'other', 'same']
    expect(await peersUnder('')).toEqual(all)
    expect(await peersUnder('   ')).toEqual(all)
  })

  // 045 S3 round (Codex §15 P2, 2026-10-18): what the card says must reach a keyboard and assistive technology, not only a
  // pointer's `title`.
  it('P2: the type and status of the participant are visible text (the meta line)', async () => {
    const { app, host } = mountNodeCard()
    await nextTick()
    expect(host.querySelector('[data-testid="node-card-meta"]')?.textContent?.trim()).toBe('person · active')
    app.unmount()
    host.remove()
  })

  it('P2: the Edit button of a line is described by what is available on it, with the unit', async () => {
    const tl = { from_pid: 'alice', from_name: 'Alice', to_pid: 'bob', to_name: 'Bob', equivalent: 'UAH', limit: '10.00', used: '1.00', available: '9.00', status: 'active' }
    const { app, host } = mountNodeCard({ interactTrustlines: [tl] })
    await nextTick()
    const edit = host.querySelector('.nco-trustline-row__edit') as HTMLButtonElement
    const ids = (edit.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
    const described = ids.map((id) => host.querySelector(`[id="${id}"]`)?.textContent?.trim() ?? '').join(' ')
    expect(described).toBe('available 9.00 UAH')
    app.unmount()
    host.remove()
  })

  // 039 closing review (finding 3): a row whose equivalent is blanks is listed as the card's - and is told in the card's
  // unit and precision, never as an amount without its equivalent.
  it('a row without its own equivalent (blanks) is told in the card\'s unit and precision', async () => {
    setEquivalentPrecisions([{ code: 'HOUR', precision: 1 }])
    try {
      const tl = { from_pid: 'alice', from_name: 'Alice', to_pid: 'bob', to_name: 'Bob', equivalent: '   ', limit: '10.0', used: '1.0', available: '9.0', status: 'active' }
      const { app, host } = mountNodeCard({ equivalentText: 'HOUR', interactTrustlines: [tl] })
      await nextTick()
      const edit = host.querySelector('.nco-trustline-row__edit') as HTMLButtonElement
      const ids = (edit.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
      const described = ids.map((id) => host.querySelector(`[id="${id}"]`)?.textContent?.trim() ?? '').join(' ')
      expect(described).toBe('available 9.0 HOUR')
      expect(host.querySelector('.nco-trustline-row__amounts')?.textContent?.replace(/\s+/g, ' ').trim()).toBe('1.0 / 10.0')
      app.unmount()
      host.remove()
    } finally {
      resetEquivalentPrecisions()
    }
  })

})
