/**
 * 039 slice D (T3940) - the bottom row's total debt states "was -> now" as two fields of its own, in both languages.
 *
 * The bar renders what `useSystemBalance` gives it (`debtBefore`, `totalUsed`, `equivalent`); WHEN a "was" exists is
 * judged in `composables/debtChange.p039d.test.ts`. Here: the two fields, their order, the one equivalent, the
 * precision (no digit dropped, none invented), the dictionary's words in ru and en, and no pair without a "was".
 */
import { createApp, h, nextTick, type Component } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import { setUiLocale, type UiLocale } from '../i18n/locale'
import type { SystemBalance } from '../composables/useSystemBalance'
import SystemBalanceBar from './SystemBalanceBar.vue'

const BALANCE: SystemBalance = {
  totalUsed: '25.00', totalAvailable: '0', equivalent: 'UAH', activeTrustlines: 1, activeParticipants: 2, utilization: 0, debtBefore: '55.00',
}

function mount(balance: SystemBalance, variant: 'debt' | 'full' = 'debt') {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(SystemBalanceBar as Component, { balance, variant }) })
  app.mount(host)
  const text = (sel: string) => host.querySelector(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? null
  return { host, text, done: () => { app.unmount(); host.remove() } }
}

afterEach(() => setUiLocale('en'))

describe('039 D - the total debt "was -> now"', () => {
  const WORDS: Record<UiLocale, { label: string; was: string; now: string }> = {
    ru: { label: 'Общий долг', was: 'было', now: 'стало' },
    en: { label: 'Total debt', was: 'was', now: 'now' },
  }

  for (const lang of ['ru', 'en'] as const) {
    it(`${lang}: "was" and "now" are two fields, in that order, with the one equivalent and the words of the dictionary`, async () => {
      setUiLocale(lang)
      const m = mount(BALANCE)
      await nextTick()
      const before = m.host.querySelector('[data-testid="total-debt-before"]')
      const after = m.host.querySelector('[data-testid="total-debt-after"]')
      expect(m.text('[data-testid="total-debt-before"]')).toBe('55.00')
      expect(m.text('[data-testid="total-debt-after"]')).toBe('25.00 UAH')
      expect(!!(before!.compareDocumentPosition(after!) & Node.DOCUMENT_POSITION_FOLLOWING), '"was" comes first').toBe(true)
      // The figure the rest of the app reads (`.ds-value`, e2e `readMetricValue`) is still "now", once.
      expect(m.host.querySelectorAll('[data-testid="sbb-total-debt"] .ds-value')).toHaveLength(1)
      expect(m.text('[data-testid="sbb-total-debt"] .ds-value')).toBe('25.00 UAH')
      const w = WORDS[lang]
      expect(m.text('[data-testid="sbb-total-debt"] .ds-label')).toBe(w.label)
      expect(m.text('[data-testid="total-debt-change"]')).toBe(`${w.was} 55.00 → ${w.now} 25.00 UAH`)
      m.done()
    })
  }

  it('a switch of language re-renders the pair without a remount', async () => {
    setUiLocale('en')
    const m = mount(BALANCE)
    await nextTick()
    expect(m.text('[data-testid="total-debt-change"]')).toBe('was 55.00 → now 25.00 UAH')
    setUiLocale('ru')
    await nextTick()
    expect(m.text('[data-testid="total-debt-change"]')).toBe('было 55.00 → стало 25.00 UAH')
    m.done()
  })

  it('no "was": the total alone, as before slice D', async () => {
    const m = mount({ ...BALANCE, debtBefore: null })
    await nextTick()
    expect(m.host.querySelector('[data-testid="total-debt-before"]')).toBeNull()
    expect(m.host.querySelector('[data-testid="total-debt-after"]')).toBeNull()
    expect(m.host.querySelector('[data-testid="total-debt-change"]')).toBeNull()
    expect(m.text('[data-testid="sbb-total-debt"] .ds-value')).toBe('25.00 UAH')
    m.done()
  })

  it('amounts at the precision of the equivalent, never fewer digits than the value has', async () => {
    const m = mount({ ...BALANCE, totalUsed: '9007199254740993.315', debtBefore: '9007199254740993.3', equivalent: 'UAH' })
    await nextTick()
    expect(m.text('[data-testid="total-debt-before"]')).toBe('9007199254740993.30')
    expect(m.text('[data-testid="total-debt-after"]')).toBe('9007199254740993.315 UAH')
    m.done()
  })

  it('the metrics row (Details) has no total debt and so no pair', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({ render: () => h(SystemBalanceBar as Component, { balance: BALANCE, variant: 'metrics' }) })
    app.mount(host)
    await nextTick()
    expect(host.querySelector('[data-testid="total-debt-before"]')).toBeNull()
    app.unmount()
    host.remove()
  })
})
