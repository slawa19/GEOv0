import { createApp, h, nextTick, type Component } from 'vue'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import EdgeTooltip from './EdgeTooltip.vue'
import { setUiLocale } from '../i18n/locale'

const edgeTooltipComponent: Component = EdgeTooltip

// 046 wave 1, full (2026-10-19): the sentences below are the Russian dictionary's (`edgeTooltip.*`); unit tests run in
// English (happy-dom's navigator), so the language is chosen through the locale module, never `<html lang>` by hand.
// The English counter-check is the last describe of the file.
beforeEach(() => setUiLocale('ru'))
afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
})

describe('EdgeTooltip', () => {
  // 048 S1 (decision 4): the raw `Status: active` row under the sentence is gone - an English word under a Russian
  // sentence. Red before S1: with a status and the old `interactMode` the row (and its divider) was drawn. By structure,
  // not by the word: no detail row, no divider; the sentence stays.
  it('the line in words, and no raw status row under it (even when the edge carries a status)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    const app = createApp({
      render: () =>
        h(edgeTooltipComponent, {
          edge: {
            key: 'a->b',
            fromId: 'A',
            toId: 'B',
            amountText: '10 UAH',
            trustLimit: '20',
            used: '10',
            available: '10',
            edgeStatus: 'active',
          },
          style: { left: '10px', top: '20px' },
          // What the root passed until S1 (the prop is gone; an unknown attribute changes nothing).
          interactMode: true,
          getNodeName: (id: string) => id,
        }),
    })

    try {
      app.mount(host)
      await nextTick()

      const tooltip = host.querySelector('.ds-ov-tooltip') as HTMLElement | null
      expect(tooltip).toBeTruthy()
      const text = (tooltip?.textContent ?? '').replace(/\s+/g, ' ')
      // 043 S1: the line in words - creditor `from` trusts debtor `to` up to the limit; `to` owes `from` the used amount.
      expect(text).toContain('A доверяет B до 20')
      expect(text).toContain('долг B перед A — 10 · доступно 10')
      expect(tooltip!.querySelector('.ds-ov-tooltip__row'), 'no detail row').toBeNull()
      expect(tooltip!.querySelector('.ds-ov-tooltip__divider'), 'no divider').toBeNull()
      expect(tooltip!.querySelectorAll('.ds-ov-tooltip__amount'), 'the two lines of the sentence').toHaveLength(2)
    } finally {
      app.unmount()
      host.remove()
    }
  })
})

describe('EdgeTooltip - 043 S1: the line in words and numbers, while watching too', () => {
  function mountTip(props: Record<string, unknown>) {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const names: Record<string, string> = { m: 'Марта', o: 'Олег' }
    const app = createApp({ render: () => h(edgeTooltipComponent, { style: {}, getNodeName: (id: string) => names[id] ?? null, ...props }) })
    app.mount(host)
    return { host, done: () => { app.unmount(); host.remove() } }
  }
  const textOf = (host: HTMLElement) => (host.textContent ?? '').replace(/\s+/g, ' ').trim()

  it('outside Interact the figures are a sentence with the unit, and the direction is creditor -> debtor', async () => {
    const { host, done } = mountTip({
      edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: 'from: 120.00 / to: 500.00 UAH', trustLimit: '500.00', used: '120.00', available: '380.00', equivalent: 'UAH' },
    })
    await nextTick()
    const text = textOf(host)
    expect(text).toContain('Марта доверяет Олег до 500.00 UAH')
    expect(text).toContain('долг Олег перед Марта — 120.00 UAH · доступно 380.00 UAH')
    // The bare "from: / to:" line is replaced, not shown beside the sentence.
    expect(text).not.toContain('from: 120.00')
    done()
  })

  it('a negative "available" reads as the excess over the limit; without figures the amount line stays', async () => {
    const over = mountTip({ edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: '', trustLimit: '100', used: '130', available: '-30', equivalent: 'UAH' } })
    await nextTick()
    expect(textOf(over.host)).toContain('сверх лимита на 30 UAH')
    over.done()

    const bare = mountTip({ edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: 'from: — / to: — UAH' } })
    await nextTick()
    expect(textOf(bare.host)).toContain('from: — / to: — UAH')
    expect(textOf(bare.host)).not.toContain('доверяет')
    bare.done()
  })
})

describe('EdgeTooltip - 043 S1 review finding 1: the figures carry their own equivalent', () => {
  it('prints the equivalent the figures were read with, never a live one handed in beside them', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    // `unit` is what the root passed until the fix: the LIVE scene equivalent. After the fix it is not a prop at all.
    const app = createApp({ render: () => h(edgeTooltipComponent, {
      style: {}, getNodeName: () => null, unit: 'EUR',
      edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: '', trustLimit: '500.00', used: '120.00', available: '380.00', equivalent: 'UAH' },
    }) })
    app.mount(host)
    await nextTick()
    const text = (host.textContent ?? '').replace(/\s+/g, ' ')
    expect(text).toContain('до 500.00 UAH')
    expect(text).not.toContain('EUR')
    app.unmount()
    host.remove()
  })
})

describe('EdgeTooltip - 046 wave 1: the same sentences in English, the names in bold, no Cyrillic of the interface', () => {
  it('English: one template per line; the names and amounts are data (as they came)', async () => {
    setUiLocale('en')
    const host = document.createElement('div')
    document.body.appendChild(host)
    const names: Record<string, string> = { m: 'Marta', o: 'Oleh' }
    const app = createApp({ render: () => h(edgeTooltipComponent, {
      style: {}, getNodeName: (id: string) => names[id] ?? null,
      edge: { key: 'm->o', fromId: 'm', toId: 'o', amountText: '', trustLimit: '500.00', used: '120.00', available: '-30', equivalent: 'UAH' },
    }) })
    app.mount(host)
    await nextTick()
    const trust = host.querySelector('[data-testid="edge-tooltip-trust"]')!
    const debt = host.querySelector('[data-testid="edge-tooltip-debt"]')!
    expect((trust.textContent ?? '').replace(/\s+/g, ' ').trim()).toBe('Marta trusts Oleh up to 500.00 UAH')
    expect([...trust.querySelectorAll('b')].map((b) => b.textContent)).toEqual(['Marta', 'Oleh'])
    expect((debt.textContent ?? '').replace(/\s+/g, ' ').trim()).toBe('Oleh owes Marta 120.00 UAH · over limit by 30 UAH')
    expect(host.querySelector('[data-testid="edge-tooltip"]')!.getAttribute('aria-label')).toBe('Edge tooltip')
    expect(/[Ѐ-ӿ]/.test(host.textContent ?? '')).toBe(false)
    app.unmount()
    host.remove()
  })
})
