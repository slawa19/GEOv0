/**
 * 046 wave 2 (decision 4): an amount is a decimal string with a dot in both languages, and a comma typed for the
 * decimal point is not taken silently. The form keeps reading it as the point (MP-4 / TL-1: '1,5' is sent as '1.5') and
 * SAYS so with the amount as it will be sent - so "1,000" meant as a thousand is seen as 1.000 before Confirm.
 * Red before the wave: '1,5' was sent as 1.5 with nothing on screen.
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
import { interactText } from '../i18n/interactStrings'
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

describe('046 decision 4 - a comma is read as the decimal point, and said', () => {
  it('payment: "1,5" is sent as 1.5 and the window says how it read it (en and ru)', async () => {
    const confirmPayment = vi.fn()
    const host = payment(confirmPayment)
    await settle()
    await type(host, '#mp-amount', '1,5')
    expect(text(host, 'mp-confirm-warning')).toBe('The comma was read as a decimal point: 1.5 UAH.')
    setUiLocale('ru')
    await settle()
    expect(text(host, 'mp-confirm-warning')).toBe(t('common.commaRead', { amount: '1.5', unit: 'UAH' }, 'ru'))
    ;(host.querySelector('[data-testid="manual-payment-confirm"]') as HTMLButtonElement).click()
    await settle()
    expect(confirmPayment).toHaveBeenCalledWith('1.5')
  })

  it('payment: "1,000" is shown as 1.000 - the reading a person meaning a thousand has to see', async () => {
    const host = payment()
    await settle()
    await type(host, '#mp-amount', '1,000')
    expect(text(host, 'mp-confirm-warning')).toContain('1.000 UAH')
  })

  it('counter-check: an amount with a dot gets no comma note', async () => {
    const host = payment()
    await settle()
    await type(host, '#mp-amount', '1.5')
    expect(host.querySelector('[data-testid="mp-confirm-warning"]')).toBeNull()
  })

  it('trust line: "1,5" is created as 1.5 and the window says how it read it; a dot gets no note', async () => {
    const confirmTrustlineCreate = vi.fn()
    const host = trustline(confirmTrustlineCreate)
    await settle()
    await type(host, '#tl-limit', '1,5')
    expect(text(host, 'tl-step-hint')).toBe('The comma was read as a decimal point: 1.5 UAH.')
    ;(host.querySelector('[data-testid="trustline-create"]') as HTMLButtonElement).click()
    await settle()
    expect(confirmTrustlineCreate).toHaveBeenCalledWith('1.5')
    await type(host, '#tl-limit', '1.5')
    expect(host.querySelector('[data-testid="tl-step-hint"]')).toBeNull()
  })
})

describe('046 wave 2 - the windows of wave 3 stay English as a whole', () => {
  it('the old keys, the line notices and the excess over a limit are English under ru unless a language is asked for', () => {
    setUiLocale('ru')
    expect(interactText('cardBusyReason')).toBe('Operation in progress… please wait.')
    expect(interactText('lineDirectionTitle')).toBe('Why is the direction reversed?')
    expect(trustlineFiguresNotice({ kind: 'loading' })).toBe('Trustline data is still loading from the backend; the figures for this pair are not available yet.')
    expect(renderAvailable('-5.00')).toBe('over limit by 5.00')
    // Counter-check: asked for, the same helpers speak Russian (the trust line window passes the interface's language).
    expect(trustlineFiguresNotice({ kind: 'loading' }, 'ru')).toBe(t('trustlineSource.loading', {}, 'ru'))
    expect(renderAvailable('-5.00', 'ru')).toBe(t('common.overLimit', { amount: '5.00' }, 'ru'))
  })
})
