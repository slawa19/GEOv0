/**
 * 046 decision 4: an amount is a decimal string with a dot in both languages. A comma is REFUSED (advisor's decision,
 * round 1, 2026-10-18; it was read as the point and said, wave 2 `400c537f`; before the wave it was read silently): the
 * form says "Use a dot: 1.5" / «Введите сумму с точкой: 1.5» and sends nothing.
 *
 * Also held here: the two windows of wave 3 that read texts of this wave (the participant card, the line window) stay
 * English as a whole until wave 3 moves them - the shared helpers default to English, the action windows pass the
 * interface's language.
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import TrustlineManagementPanel from './TrustlineManagementPanel.vue'
import { trustlineFiguresNotice } from '../composables/interact/trustlinesSourceState'
import { setUiLocale } from '../i18n/locale'
import { t } from '../i18n/t'
import { renderAvailable } from '../utils/valueFormat'

afterEach(() => {
  setUiLocale('en')
  document.body.innerHTML = ''
})

async function settle() {
  for (let i = 0; i < 4; i += 1) await nextTick()
}

function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  createApp({ render: () => h(component, props) }).mount(host)
  return host
}

async function type(host: HTMLElement, selector: string, value: string) {
  const input = host.querySelector(selector) as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input'))
  await settle()
}

const state = (phase: string) => reactive({ phase, fromPid: 'alice', toPid: 'bob', selectedEdgeKey: null, edgeAnchor: null, error: null, lastClearing: null })
const people = [{ pid: 'alice', name: 'Alice' }, { pid: 'bob', name: 'Bob' }]

function payment(confirmPayment = vi.fn()) {
  return mount(ManualPaymentPanel, {
    phase: 'confirm-payment', state: state('confirm-payment'), unit: 'UAH', availableCapacity: '10.00',
    trustlinesLoading: false, paymentTargetsLoading: false, paymentTargetsLastError: null, paymentToTargetIds: new Set(['bob']),
    trustlines: [], participants: people, busy: false, canSendPayment: true, trustlinesState: 'answered',
    confirmPayment, cancel: vi.fn(), setFromPid: vi.fn(), setToPid: vi.fn(),
  })
}

function trustline(confirmTrustlineCreate = vi.fn()) {
  return mount(TrustlineManagementPanel, {
    phase: 'confirm-trustline-create', state: state('confirm-trustline-create'), unit: 'UAH', used: '0', currentLimit: null,
    available: null, figuresSource: { kind: 'no-row' }, participants: people, trustlines: [], busy: false,
    confirmTrustlineCreate, confirmTrustlineUpdate: vi.fn(), confirmTrustlineClose: vi.fn(), cancel: vi.fn(),
  })
}

const text = (host: HTMLElement, id: string) => (host.querySelector(`[data-testid="${id}"]`)?.textContent ?? '').replace(/\s+/g, ' ').trim()

describe('046 decision 4 (advisor, round 1, 2026-10-18) - a comma in an amount is refused, with the amount as it should be typed', () => {
  // Was (wave 2, 400c537f): "1,5" read as 1.5 and said. The advisor's decision: refuse - "1,000" meant as a thousand must
  // never become 1.000. One rule for both languages and every equivalent.
  it('payment: "1,5" sends nothing; Confirm is disabled and the reason is "Use a dot: 1.5" (en and ru)', async () => {
    const confirmPayment = vi.fn()
    const host = payment(confirmPayment)
    await settle()
    await type(host, '#mp-amount', '1,5')
    expect(text(host, 'mp-confirm-reason')).toBe('Use a dot: 1.5')
    const confirm = host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(confirm.getAttribute('title')).toBe('Use a dot: 1.5')
    setUiLocale('ru')
    await settle()
    expect(text(host, 'mp-confirm-reason')).toBe(t('common.commaRefused', { example: '1.5' }, 'ru'))
    expect(text(host, 'mp-confirm-reason')).toBe('Введите сумму с точкой: 1.5')
    confirm.click()
    await settle()
    expect(confirmPayment).not.toHaveBeenCalled()
    expect(host.querySelector('[data-testid="mp-confirm-warning"]'), 'no "read as" notice any more').toBeNull()
  })

  it('payment: "1,000" is refused as well - the form never decides whether it was a thousand or one', async () => {
    const confirmPayment = vi.fn()
    const host = payment(confirmPayment)
    await settle()
    await type(host, '#mp-amount', '1,000')
    expect(text(host, 'mp-confirm-reason')).toBe('Use a dot: 1.000')
    ;(host.querySelector('#mp-amount') as HTMLInputElement).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    await settle()
    expect(confirmPayment).not.toHaveBeenCalled()
  })

  it('counter-check: the same amount with a dot is sent; a text that is not a number keeps the general hint', async () => {
    const confirmPayment = vi.fn()
    const host = payment(confirmPayment)
    await settle()
    await type(host, '#mp-amount', '1.5')
    expect(host.querySelector('[data-testid="mp-confirm-reason"]')).toBeNull()
    ;(host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement).click()
    await settle()
    expect(confirmPayment).toHaveBeenCalledWith('1.5')
    await type(host, '#mp-amount', '1,5,5')
    expect(text(host, 'mp-confirm-reason')).toBe("Invalid amount format. Use digits and '.' for decimals.")
  })

  it('trust line: "1,5" creates nothing and says "Use a dot: 1.5" (en and ru); a dot creates', async () => {
    const confirmTrustlineCreate = vi.fn()
    const host = trustline(confirmTrustlineCreate)
    await settle()
    await type(host, '#tl-limit', '1,5')
    expect(text(host, 'tl-input-hint')).toBe('Use a dot: 1.5')
    setUiLocale('ru')
    await settle()
    expect(text(host, 'tl-input-hint')).toBe('Введите сумму с точкой: 1.5')
    const create = host.querySelector('[data-testid="trustline-create"]') as HTMLButtonElement
    expect(create.disabled).toBe(true)
    create.click()
    await settle()
    expect(confirmTrustlineCreate).not.toHaveBeenCalled()
    await type(host, '#tl-limit', '1.5')
    expect(host.querySelector('[data-testid="tl-input-hint"]')).toBeNull()
    create.click()
    await settle()
    expect(confirmTrustlineCreate).toHaveBeenCalledWith('1.5')
  })
})

// 046 wave 3 (2026-10-19): the rule of wave 2 - "the helpers of the wave 3 windows speak English unless a language is asked
// for" (the card and the line window were English as a whole then) - is replaced: wave 3 moved both windows, and a helper
// without a language speaks the language of the interface. `interactStrings.ts` is gone.
describe('046 wave 3 - the shared helpers speak the language of the interface', () => {
  it('without a language: the line notices and the excess over a limit are Russian under ru, English under en', () => {
    setUiLocale('ru')
    expect(trustlineFiguresNotice({ kind: 'loading' })).toBe(t('trustlineSource.loading', {}, 'ru'))
    expect(renderAvailable('-5.00')).toBe(t('common.overLimit', { amount: '5.00' }, 'ru'))
    setUiLocale('en')
    expect(trustlineFiguresNotice({ kind: 'loading' })).toBe('Trustline data is still loading from the server; the figures for this pair are not available yet.')
    expect(renderAvailable('-5.00')).toBe('over limit by 5.00')
    // Counter-check: an asked language still wins over the interface's.
    expect(trustlineFiguresNotice({ kind: 'loading' }, 'ru')).toBe(t('trustlineSource.loading', {}, 'ru'))
    expect(renderAvailable('-5.00', 'ru')).toBe(t('common.overLimit', { amount: '5.00' }, 'ru'))
  })
})
