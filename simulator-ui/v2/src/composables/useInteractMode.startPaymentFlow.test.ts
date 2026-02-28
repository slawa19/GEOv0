/**
 * AC-MP-13: startPaymentFlow() calls refreshTrustlines({ force: true })
 *
 * Tests that the MP-6a requirement is met: when the user initiates a payment flow,
 * the trustlines cache is force-refreshed so that the To-dropdown tri-state is
 * reliable from the very first interaction.
 */
import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

// ── Mock useInteractDataCache ──────────────────────────────────────────────────
// We spy on refreshTrustlines to verify the MP-6a call.

const mockRefreshTrustlines = vi.fn().mockResolvedValue(undefined)
const mockRefreshParticipants = vi.fn().mockResolvedValue(undefined)
const mockRefreshPaymentTargets = vi.fn().mockResolvedValue(undefined)

vi.mock('./interact/useInteractDataCache', () => ({
  useInteractDataCache: () => ({
    participants: ref([]),
    trustlines: ref([]),
    trustlinesLoading: ref(false),
    trustlinesLastError: ref(null),
    paymentTargetsLastError: ref(null),
    paymentTargetsByKey: ref(new Map()),
    paymentTargetsLoadingByKey: ref(new Map()),
    paymentTargetsKey: (o: { runId: string; eq: string; fromPid: string; maxHops: number }) =>
      `${o.runId}:${o.eq}:${o.fromPid}:${o.maxHops}`,
    refreshParticipants: mockRefreshParticipants,
    refreshTrustlines: mockRefreshTrustlines,
    refreshPaymentTargets: mockRefreshPaymentTargets,
    invalidateTrustlinesCache: vi.fn(),
    patchTrustlineLimitLocal: vi.fn(),
    findActiveTrustline: vi.fn().mockReturnValue(null),
  }),
}))

// ── Import after mock registration ────────────────────────────────────────────
import { useInteractMode } from './useInteractMode'
import { useInteractActions } from './useInteractActions'

// Minimal actions stub (only the async action methods are needed; they are not
// invoked by startPaymentFlow, so plain vi.fn() stubs are sufficient).
function makeActions(): ReturnType<typeof useInteractActions> {
  return {
    sendPayment: vi.fn(),
    createTrustline: vi.fn(),
    updateTrustline: vi.fn(),
    closeTrustline: vi.fn(),
    fetchTrustlines: vi.fn(),
    fetchParticipants: vi.fn(),
    fetchPaymentTargets: vi.fn(),
  } as unknown as ReturnType<typeof useInteractActions>
}

// ─────────────────────────────────────────────────────────────────────────────

describe('useInteractMode › startPaymentFlow', () => {
  it('AC-MP-13: calls refreshTrustlines({ force: true }) when starting a payment flow (MP-6a)', () => {
    mockRefreshTrustlines.mockClear()

    const mode = useInteractMode({
      actions: makeActions(),
      runId: ref('run-1'),
      equivalent: ref('UAH'),
      snapshot: ref(null),
    })

    // Pre-condition: must be in idle phase.
    expect(mode.phase.value).toBe('idle')

    mode.startPaymentFlow()

    expect(mockRefreshTrustlines).toHaveBeenCalledWith({ force: true })
  })

  it('AC-MP-13b: does NOT call refreshTrustlines({ force: true }) when already busy', () => {
    mockRefreshTrustlines.mockClear()

    const mode = useInteractMode({
      actions: makeActions(),
      runId: ref('run-1'),
      equivalent: ref('UAH'),
      snapshot: ref(null),
    })

    // Simulate busy (direct ref mutation is not possible; simulate by calling
    // a method that would set busy — but since all actions are mocked to never
    // resolve instantly, we can test the guard via phase != idle).
    // Easiest: call startPaymentFlow once to move out of idle.
    mode.startPaymentFlow()
    mockRefreshTrustlines.mockClear()

    // Phase is now picking-payment-from (not idle) → second call is a no-op.
    mode.startPaymentFlow()

    expect(mockRefreshTrustlines).not.toHaveBeenCalled()
  })
})
