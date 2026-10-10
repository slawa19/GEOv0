/**
 * 037 C: the result of a clearing stays on the panel until the user closes it. Real `useInteractMode`, fake timers: what the
 * old code did on a timer (preview -> running -> idle after about one second, leaving only a toast) is exactly what is observed.
 */
import { computed, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useInteractMode } from './useInteractMode'
import type { GraphSnapshot } from '../types'

type Actions = Parameters<typeof useInteractMode>[0]['actions']
type Clearing = Awaited<ReturnType<Actions['runClearing']>>

const twoCycles: Clearing = {
  ok: true, equivalent: 'UAH', cleared_cycles: 2, total_cleared_amount: '14.00',
  cycles: [
    { cleared_amount: '7.00', edges: [{ from: 'alice', to: 'bob' }, { from: 'bob', to: 'carol' }, { from: 'carol', to: 'alice' }] },
    { cleared_amount: '7.00', edges: [{ from: 'alice', to: 'carol' }, { from: 'carol', to: 'alice' }] },
  ],
}
const none: Clearing = { ok: true, equivalent: 'UAH', cleared_cycles: 0, total_cleared_amount: '0.00', cycles: [] }

function mk(runClearing: Actions['runClearing'], onClearingDone?: (r: Clearing) => void) {
  const actions = {
    actionsDisabled: ref(false), sendPayment: vi.fn(), createTrustline: vi.fn(), updateTrustline: vi.fn(), closeTrustline: vi.fn(),
    runClearing, fetchParticipants: vi.fn(async () => []), fetchTrustlines: vi.fn(async () => []), fetchPaymentTargets: vi.fn(async () => []),
  } as unknown as Actions
  return useInteractMode({
    actions, runId: computed(() => 'run_test'), equivalent: computed(() => 'UAH'), snapshot: ref<GraphSnapshot | null>(null),
    onClearingDone,
  })
}

/** Run the clearing and let EVERY timer the code could be waiting on elapse: what is left is what the code leaves for good. */
async function runAndWait(im: ReturnType<typeof mk>) {
  const p = im.confirmClearing()
  await vi.advanceTimersByTimeAsync(60_000)
  await p
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.useRealTimers() })

describe('the clearing result stays until it is closed', () => {
  it('survives every timer: still on the panel, busy released, Close (cancel) is what removes it', async () => {
    const im = mk(vi.fn(async () => twoCycles))
    im.startClearingFlow()
    await runAndWait(im)

    expect(im.phase.value, 'the panel is still open').not.toBe('idle')
    expect(im.state.lastClearing).toEqual(twoCycles)
    expect(im.busy.value, 'Close must not be blocked by a busy flag').toBe(false)

    im.cancel()
    expect(im.phase.value).toBe('idle')
    expect(im.state.lastClearing, 'lastClearing is kept in memory through idle').toEqual(twoCycles)
  })

  it('a second clearing replaces the result: it is cleared when the new one starts, not before', async () => {
    const second: Clearing = { ...twoCycles, cleared_cycles: 1, total_cleared_amount: '3.00', cycles: [twoCycles.cycles[0]!] }
    const run = vi.fn<Actions['runClearing']>().mockResolvedValueOnce(twoCycles)
    const im = mk(run)
    im.startClearingFlow()
    await runAndWait(im)
    im.cancel()
    expect(im.state.lastClearing).toEqual(twoCycles)

    let release!: (r: Clearing) => void
    run.mockImplementationOnce(() => new Promise<Clearing>((r) => { release = r }))
    im.startClearingFlow()
    const pending = im.confirmClearing()
    await vi.advanceTimersByTimeAsync(0)
    expect(im.state.lastClearing, 'cleared at the START of the new clearing').toBeNull()
    release(second)
    await vi.advanceTimersByTimeAsync(60_000)
    await pending
    expect(im.state.lastClearing).toEqual(second)
  })

  it('the clearing effect on the canvas is called once, right with the answer, with the answer as its argument (not moved by the result staying)', async () => {
    const done = vi.fn()
    const im = mk(vi.fn(async () => twoCycles), done)
    im.startClearingFlow()
    const p = im.confirmClearing()
    await vi.advanceTimersByTimeAsync(0)
    expect(done, 'called when the answer arrived, with no timer waited for').toHaveBeenCalledTimes(1)
    expect(done).toHaveBeenCalledWith(twoCycles)
    await vi.advanceTimersByTimeAsync(60_000)
    await p
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('the toast is a success for cleared cycles only; "no cycles" is not announced as success', async () => {
    const im = mk(vi.fn(async () => twoCycles))
    im.startClearingFlow()
    await runAndWait(im)
    expect(im.successMessage.value).toBe('Clearing done: 2/2 cycles')

    const empty = mk(vi.fn(async () => none))
    empty.startClearingFlow()
    await runAndWait(empty)
    expect(empty.successMessage.value, 'nothing was cleared: no success toast').toBeNull()
    expect(empty.state.lastClearing).toEqual(none)
    expect(empty.phase.value).not.toBe('idle')
  })

  it('a refusal keeps the panel open with the error (no result, no stuck "preview"), and is not a success', async () => {
    const im = mk(vi.fn(async () => { throw new Error('boom') }))
    im.startClearingFlow()
    await runAndWait(im)

    expect(im.state.error).toBeTruthy()
    expect(im.state.lastClearing).toBeNull()
    expect(im.successMessage.value).toBeNull()
    expect(im.busy.value).toBe(false)
    expect(im.phase.value).not.toBe('idle')
  })
})

describe('what ends a held result: the user, or a deliberate replacement - never an empty click on the canvas', () => {
  it('an empty click on the canvas does not remove a finished clearing result; Close does', async () => {
    const im = mk(vi.fn(async () => twoCycles))
    im.startClearingFlow()
    await runAndWait(im)

    im.cancelFromCanvas()
    expect(im.phase.value, 'the held result stays').not.toBe('idle')
    expect(im.state.lastClearing).toEqual(twoCycles)

    im.cancel()
    expect(im.phase.value).toBe('idle')
  })

  it('...nor a finished refusal', async () => {
    const im = mk(vi.fn(async () => { throw new Error('boom') }))
    im.startClearingFlow()
    await runAndWait(im)
    im.cancelFromCanvas()
    expect(im.phase.value).not.toBe('idle')
    expect(im.state.clearingFailure).toBeTruthy()
  })

  it('a click on the canvas still cancels what is NOT a held result (the confirm step, a payment being filled in)', () => {
    const im = mk(vi.fn(async () => twoCycles))
    im.startClearingFlow()
    expect(im.phase.value).toBe('confirm-clearing')
    im.cancelFromCanvas()
    expect(im.phase.value).toBe('idle')

    im.startPaymentFlow()
    im.cancelFromCanvas()
    expect(im.phase.value).toBe('idle')
  })

  it('a line clicked while a result is held does not replace it (the user closes it first)', async () => {
    const im = mk(vi.fn(async () => twoCycles))
    im.startClearingFlow()
    await runAndWait(im)
    expect(im.selectEdge('alice→bob')).toBe(false)
    expect(im.phase.value).toBe('clearing-preview')
  })
})

describe('a refusal is a state of the panel, not a passing message', () => {
  it('the refusal text is kept in the state after the transient error is cleared (the toast went away)', async () => {
    const im = mk(vi.fn(async () => { throw new Error('boom') }))
    im.startClearingFlow()
    await runAndWait(im)
    expect(im.state.clearingFailure).toBeTruthy()

    im.state.error = null // what the error toast does when it expires
    expect(im.state.clearingFailure, 'survives the toast').toBeTruthy()
    expect(im.busy.value).toBe(false)

    im.cancel()
    expect(im.state.clearingFailure, 'Close ends it').toBeNull()
  })

  it('the next clearing starts clean: no earlier refusal, no earlier success announcement', async () => {
    const run = vi.fn<Actions['runClearing']>().mockResolvedValueOnce(twoCycles).mockResolvedValueOnce(none)
    const im = mk(run)
    im.startClearingFlow()
    await runAndWait(im)
    expect(im.successMessage.value).toBe('Clearing done: 2/2 cycles')
    im.cancel()

    // The same composable, before the old toast would have expired: the next clearing finds nothing.
    im.startClearingFlow()
    expect(im.successMessage.value, 'starting a clearing withdraws the earlier announcement').toBeNull()
    await runAndWait(im)
    expect(im.successMessage.value).toBeNull()
    expect(im.state.lastClearing).toEqual(none)
  })
})
