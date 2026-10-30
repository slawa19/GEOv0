/**
 * 054-B-3 (R2 B4-1): after a COMMITTED manual payment that used the whole capacity to the recipient, the patch of the
 * payment bumps the snapshot revision, `useInteractMode` refetches payment-targets, the fresh answer no longer has the
 * recipient, and the panel's recipient watcher (`ManualPaymentPanel.vue`, "recipient gone") reset To - which erased the
 * result the panel promises to keep until the user dismisses it (`useInteractMode.ts`, "The panel stays on the result").
 * Actual useInteractMode + actual ManualPaymentPanel, wired like `SimulatorAppRoot.vue`; only the HTTP actions are
 * stand-ins (as in useInteractMode.manualPayment.test.ts). The revision bump is the patch applier's (demo/patches.ts).
 * The last case is the counter-check: BEFORE a payment, a recipient the refreshed answer drops is still reset.
 */
import { computed, createApp, h, nextTick, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ManualPaymentPanel from './ManualPaymentPanel.vue'
import type { GraphSnapshot } from '../types'
import { useInteractMode } from '../composables/useInteractMode'

afterEach(() => { document.body.innerHTML = ''; window.sessionStorage.clear() })

const COMMITTED = {
  ok: true as const, payment_id: 'man:0123', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '100.00',
  status: 'COMMITTED', routes: [{ hops: [{ from: 'alice', to: 'bob', amount: '100.00' }] }],
}
async function settle() { for (let i = 0; i < 10; i += 1) { await Promise.resolve(); await nextTick() } }

type Targets = Array<{ to_pid: string; hops: number; max_available: string | null }>
const BEFORE_PAYMENT: Targets = [{ to_pid: 'bob', hops: 1, max_available: '100.00' }, { to_pid: 'carol', hops: 2, max_available: null }]

function stand(targetsAfterPayment: Targets) {
  window.history.replaceState({}, '', '/?mode=real&ui=interact')
  const snapshot = ref<GraphSnapshot>({
    equivalent: 'UAH', generated_at: '2026-01-01T00:00:00Z', data_revision: 0,
    nodes: ['alice', 'bob', 'carol'].map((id) => ({ id, name: id, type: 'person', status: 'active' })),
    links: [{ source: 'bob', target: 'alice', used: '0.00', available: '100.00', status: 'active' }],
  } as GraphSnapshot)
  let paid = false
  let beforePayment: Targets = BEFORE_PAYMENT
  const actions = {
    actionsDisabled: ref(false),
    sendPayment: vi.fn(async () => { paid = true; return COMMITTED }),
    createTrustline: vi.fn(), updateTrustline: vi.fn(), closeTrustline: vi.fn(), runClearing: vi.fn(),
    fetchParticipants: vi.fn(async () => ['alice', 'bob', 'carol'].map((pid) => ({ pid, name: pid, type: 'person', status: 'active' }))),
    fetchTrustlines: vi.fn(async () => []),
    fetchPaymentTargets: vi.fn(async () => (paid ? targetsAfterPayment : beforePayment)),
  } as unknown as Parameters<typeof useInteractMode>[0]['actions']
  const im = useInteractMode({ actions, runId: computed(() => 'run_1'), equivalent: computed(() => 'UAH'), snapshot })
  const host = document.createElement('div'); document.body.appendChild(host)
  createApp({
    render: () => h(ManualPaymentPanel as never, {
      phase: im.state.phase, state: im.state, unit: 'UAH', availableCapacity: im.availableCapacity.value,
      trustlinesLoading: im.trustlinesLoading.value, paymentTargetsLoading: im.paymentTargetsLoading.value,
      paymentTargetsMaxHops: im.paymentTargetsMaxHops, paymentToTargetIds: im.paymentTargetsLoading.value ? undefined : im.paymentToTargetIds.value,
      paymentToLastKnownTargetIds: im.paymentToLastKnownTargetIds?.value, trustlines: im.trustlines.value,
      trustlinesLastError: im.trustlinesLastError?.value ?? null, paymentTargetsLastError: im.paymentTargetsLastError?.value ?? null,
      participants: im.participants.value, busy: im.busy.value, canSendPayment: im.canSendPayment.value,
      confirmPayment: im.confirmPayment, setFromPid: im.setPaymentFromPid, setToPid: im.setPaymentToPid, cancel: im.cancel,
      trustlinesState: im.trustlinesFetchState.value.kind, paymentOutcome: im.paymentOutcome.value,
      retryPayment: im.retryPayment, dismissPaymentResult: im.dismissPaymentResult,
      discardUnresolvedPayment: im.discardUnresolvedPayment, targetEstimate: im.paymentTargetEstimate.value,
    }),
  }).mount(host)
  const bumpRevision = () => { snapshot.value = { ...snapshot.value, data_revision: (snapshot.value.data_revision ?? 0) + 1 } }
  const setTargetsBeforePayment = (next: Targets) => { beforePayment = next }
  return { im, host, bumpRevision, setTargetsBeforePayment }
}

describe('054-B-3: the receipt of a committed payment', () => {
  it('stays on screen when the refreshed targets no longer contain the recipient (the whole capacity was used)', async () => {
    const { im, host, bumpRevision } = stand([{ to_pid: 'carol', hops: 2, max_available: null }])
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob'); await settle()
    expect(im.state.phase, 'stand: at confirm').toBe('confirm-payment')
    await im.confirmPayment('100.00'); await settle()
    expect(host.querySelector('[data-testid="mp-result"]'), 'stand: the result is shown').not.toBeNull()

    bumpRevision(); await settle()   // the payment's tx.updated patch; targets are refetched
    expect(im.paymentOutcome.value?.kind, 'the receipt is dismissed by the user only').toBe('success')
    expect(host.querySelector('[data-testid="mp-result"]'), 'the result is still on screen').not.toBeNull()
  })

  it('counter-check: when the recipient stays reachable the receipt stays (the stand can see a kept receipt)', async () => {
    const { im, host, bumpRevision } = stand([{ to_pid: 'bob', hops: 1, max_available: '0.01' }])
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob'); await settle()
    await im.confirmPayment('100.00'); await settle()
    bumpRevision(); await settle()
    expect(im.paymentOutcome.value?.kind).toBe('success')
    expect(host.querySelector('[data-testid="mp-result"]')).not.toBeNull()
  })

  it('counter-check: BEFORE a payment, a recipient the refreshed targets no longer contain is still reset', async () => {
    const { im, host, bumpRevision, setTargetsBeforePayment } = stand([])
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob'); await settle()
    expect(im.state.phase, 'stand: at confirm').toBe('confirm-payment')
    expect(im.state.toPid, 'stand: the recipient is chosen').toBe('bob')

    setTargetsBeforePayment([{ to_pid: 'carol', hops: 2, max_available: null }])
    bumpRevision(); await settle()   // another payment's patch took the capacity to bob; targets are refetched
    expect(im.state.toPid, 'a recipient gone before the payment is reset').toBeNull()
    expect(host.querySelector('[data-testid="mp-result"]'), 'no result was ever shown').toBeNull()
    expect(im.paymentOutcome.value).toBeNull()
  })
})
