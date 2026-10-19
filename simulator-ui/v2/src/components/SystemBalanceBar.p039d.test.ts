/**
 * 039 slice D (T3940) - the bottom row states the last clearing's "was -> now" as two fields of its own, in both
 * languages, above the bar.
 *
 * The bar renders what `useSystemBalance` gives it (`clearingChange`, `totalUsed`, `equivalent`); WHEN a pair exists
 * is judged in `composables/debtChange.p039d.test.ts`. Here: the two fields, their order, the equivalent, the
 * precision (no digit dropped, none invented), the dictionary's words in ru and en, the bar's own figure (the current
 * total, which may differ from the clearing's "now" once a payment followed it), and no pair without one. That the
 * bar keeps its size with the pair is the stand's (`e2e/qa/debt-before-after.qa.ts`).
 */
import { createApp, h, nextTick, type Component } from 'vue'
import { afterEach, describe, expect, it } from 'vitest'

import { setUiLocale, type UiLocale } from '../i18n/locale'
import type { SystemBalance } from '../composables/useSystemBalance'
import SystemBalanceBar from './SystemBalanceBar.vue'

// A payment of 2.00 followed the clearing 55.00 -> 25.00: the bar says 27.00, the pair stays the clearing's.
const BALANCE: SystemBalance = {
  totalUsed: '27.00', totalAvailable: '0', equivalent: 'UAH', activeTrustlines: 1, activeParticipants: 2, utilization: 0,
  clearingChange: { before: '55.00', after: '25.00' },
}

function mount(balance: SystemBalance, variant: 'debt' | 'full' | 'metrics' = 'debt') {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(SystemBalanceBar as Component, { balance, variant }) })
  app.mount(host)
  const text = (sel: string) => host.querySelector(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? null
  return { host, text, done: () => { app.unmount(); host.remove() } }
}

afterEach(() => setUiLocale('en'))

describe('039 D - the last clearing\'s "was -> now" above the total debt', () => {
  const WORDS: Record<UiLocale, { label: string; sentence: string }> = {
    ru: { label: 'Общий долг', sentence: 'клиринг: было 55.00 → стало 25.00 UAH' },
    en: { label: 'Total debt', sentence: 'clearing: was 55.00 → became 25.00 UAH' },
  }

  for (const lang of ['ru', 'en'] as const) {
    it(`${lang}: "was" and "now" are two fields, in that order, with the equivalent and the words of the dictionary`, async () => {
      setUiLocale(lang)
      const m = mount(BALANCE)
      await nextTick()
      const before = m.host.querySelector('[data-testid="total-debt-before"]')
      const after = m.host.querySelector('[data-testid="total-debt-after"]')
      expect(m.text('[data-testid="total-debt-before"]')).toBe('55.00')
      expect(m.text('[data-testid="total-debt-after"]'), 'the clearing\'s own after-total').toBe('25.00')
      expect(!!(before!.compareDocumentPosition(after!) & Node.DOCUMENT_POSITION_FOLLOWING), '"was" comes first').toBe(true)
      const w = WORDS[lang]
      expect(m.text('[data-testid="total-debt-change"]')).toBe(w.sentence)
      // The figure the rest of the app reads (`.ds-value`, e2e `readMetricValue`) is the CURRENT total, once.
      expect(m.host.querySelectorAll('[data-testid="sbb-total-debt"] .ds-value')).toHaveLength(1)
      expect(m.text('[data-testid="sbb-total-debt"] .ds-value')).toBe('27.00 UAH')
      expect(m.text('[data-testid="sbb-total-debt"] .ds-label')).toBe(w.label)
      m.done()
    })
  }

  it('a switch of language re-renders the pair without a remount', async () => {
    setUiLocale('en')
    const m = mount(BALANCE)
    await nextTick()
    expect(m.text('[data-testid="total-debt-change"]')).toBe(WORDS.en.sentence)
    setUiLocale('ru')
    await nextTick()
    expect(m.text('[data-testid="total-debt-change"]')).toBe(WORDS.ru.sentence)
    m.done()
  })

  it('no clearing pair: the total alone, as before slice D', async () => {
    const m = mount({ ...BALANCE, clearingChange: null })
    await nextTick()
    expect(m.host.querySelector('[data-testid="total-debt-change"]')).toBeNull()
    expect(m.host.querySelector('[data-testid="total-debt-before"]')).toBeNull()
    expect(m.host.querySelector('[data-testid="total-debt-after"]')).toBeNull()
    expect(m.text('[data-testid="sbb-total-debt"] .ds-value')).toBe('27.00 UAH')
    m.done()
  })

  it('amounts at the precision of the equivalent, never fewer digits than the value has', async () => {
    const m = mount({ ...BALANCE, clearingChange: { before: '9007199254740993.3', after: '9007199254740993.215' } })
    await nextTick()
    expect(m.text('[data-testid="total-debt-before"]')).toBe('9007199254740993.30')
    expect(m.text('[data-testid="total-debt-after"]')).toBe('9007199254740993.215')
    m.done()
  })

  it('the metrics row (Details) has no total debt and so no pair', async () => {
    const m = mount(BALANCE, 'metrics')
    await nextTick()
    expect(m.host.querySelector('[data-testid="total-debt-change"]')).toBeNull()
    m.done()
  })
})
