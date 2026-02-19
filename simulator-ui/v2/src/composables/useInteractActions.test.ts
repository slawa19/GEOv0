import { describe, expect, it, vi } from 'vitest'
import { ref } from 'vue'

import { ApiError } from '../api/http'

const m = vi.hoisted(() => {
  return {
    actionPaymentReal: vi.fn(),
    actionTrustlineCreate: vi.fn(),
    actionTrustlineUpdate: vi.fn(),
    actionTrustlineClose: vi.fn(),
    actionClearingReal: vi.fn(),
    getParticipantsList: vi.fn(),
    getTrustlinesList: vi.fn(),
  }
})

vi.mock('../api/simulatorApi', () => {
  return {
    actionPaymentReal: m.actionPaymentReal,
    actionTrustlineCreate: m.actionTrustlineCreate,
    actionTrustlineUpdate: m.actionTrustlineUpdate,
    actionTrustlineClose: m.actionTrustlineClose,
    actionClearingReal: m.actionClearingReal,
    getParticipantsList: m.getParticipantsList,
    getTrustlinesList: m.getTrustlinesList,
  }
})

import { isInteractActionError, useInteractActions } from './useInteractActions'

describe('useInteractActions', () => {
  it('maps ApiError JSON body + flips actionsDisabled on 403 ACTIONS_DISABLED', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    m.actionPaymentReal.mockRejectedValueOnce(
      new ApiError('HTTP 403 Forbidden for /x', {
        status: 403,
        bodyText: JSON.stringify({ code: 'ACTIONS_DISABLED', message: 'disabled', details: { flag: 'SIMULATOR_ACTIONS_ENABLE' } }),
      }),
    )

    await expect(ia.sendPayment('a', 'b', '1.00', 'UAH')).rejects.toMatchObject({
      status: 403,
      code: 'ACTIONS_DISABLED',
      message: 'disabled',
      actionsDisabled: true,
    })
    expect(ia.actionsDisabled.value).toBe(true)

    m.actionPaymentReal.mockResolvedValueOnce({ ok: true } as any)
    await ia.sendPayment('a', 'b', '1.00', 'UAH')
    expect(ia.actionsDisabled.value).toBe(false)
  })

  it('does not clear actionsDisabled optimistically between repeated 403 ACTIONS_DISABLED', async () => {
    vi.useFakeTimers()
    try {
      const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
      const runId = ref('run_1')
      const ia = useInteractActions({ httpConfig, runId })

      const err = new ApiError('HTTP 403 Forbidden for /x', {
        status: 403,
        bodyText: JSON.stringify({ code: 'ACTIONS_DISABLED', message: 'disabled' }),
      })

      // First failure flips the flag ON.
      m.actionPaymentReal.mockRejectedValueOnce(err)
      await expect(ia.sendPayment('a', 'b', '1.00', 'UAH')).rejects.toMatchObject({ status: 403, code: 'ACTIONS_DISABLED' })
      expect(ia.actionsDisabled.value).toBe(true)

      // Second failure must not clear the flag before the await settles.
      m.actionPaymentReal.mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            setTimeout(() => reject(err), 10)
          }),
      )

      const p = ia.sendPayment('a', 'b', '1.00', 'UAH')
      // Attach a handler immediately to avoid unhandled rejection warnings when timers advance.
      void p.catch(() => undefined)
      expect(ia.actionsDisabled.value).toBe(true)

      await vi.advanceTimersByTimeAsync(9)
      expect(ia.actionsDisabled.value).toBe(true)

      await vi.advanceTimersByTimeAsync(1)
      await expect(p).rejects.toMatchObject({ status: 403, code: 'ACTIONS_DISABLED' })
      expect(ia.actionsDisabled.value).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('maps ApiError with non-JSON body to HTTP_<status> code', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    m.actionPaymentReal.mockRejectedValueOnce(new ApiError('HTTP 400 Bad Request for /x', { status: 400, bodyText: 'not-json' }))
    try {
      await ia.sendPayment('a', 'b', '1.00', 'UAH')
      throw new Error('expected rejection')
    } catch (e) {
      expect(isInteractActionError(e)).toBe(true)
      expect(e).toMatchObject({ status: 400, code: 'HTTP_400' })
    }
  })

  it('throws a mapped UNKNOWN error when runId is missing', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('')
    const ia = useInteractActions({ httpConfig, runId })

    try {
      await ia.sendPayment('a', 'b', '1.00', 'UAH')
      throw new Error('expected rejection')
    } catch (e) {
      expect(isInteractActionError(e)).toBe(true)
      expect(e).toMatchObject({ status: 0, code: 'UNKNOWN' })
      expect(String((e as any).message)).toContain('run_id')
    }
  })

  // BUG-11: successful API response tests

  it('sendPayment returns response on success and clears actionsDisabled', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    const mockResponse = { ok: true, payment_id: 'pay_1', from_pid: 'a', to_pid: 'b', amount: '1.00', equivalent: 'UAH', status: 'COMMITTED' }
    m.actionPaymentReal.mockResolvedValueOnce(mockResponse as any)

    const result = await ia.sendPayment('a', 'b', '1.00', 'UAH')
    expect(result).toMatchObject({ payment_id: 'pay_1', status: 'COMMITTED' })
    expect(ia.actionsDisabled.value).toBe(false)
  })

  it('createTrustline returns response on success', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    const mockResponse = { ok: true, trustline_id: 'tl_1', from_pid: 'a', to_pid: 'b', equivalent: 'UAH', limit: '1000' }
    m.actionTrustlineCreate.mockResolvedValueOnce(mockResponse as any)

    const result = await ia.createTrustline('a', 'b', '1000', 'UAH')
    expect(result).toMatchObject({ trustline_id: 'tl_1', limit: '1000' })
  })

  it('runClearing returns response with cycles on success', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    const cycle = { cleared_amount: '600', edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }] }
    const mockResponse = { ok: true, equivalent: 'UAH', cleared_cycles: 1, total_cleared_amount: '600.00', cycles: [cycle] }
    m.actionClearingReal.mockResolvedValueOnce(mockResponse as any)

    const result = await ia.runClearing('UAH')
    expect(result.cleared_cycles).toBe(1)
    expect(result.cycles).toHaveLength(1)
    expect(result.cycles[0]!.edges).toHaveLength(3)
  })

  it('fetchParticipants returns items array on success', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    const items = [{ pid: 'alice', name: 'Alice', type: 'person', status: 'active' }]
    m.getParticipantsList.mockResolvedValueOnce({ items } as any)

    const result = await ia.fetchParticipants()
    expect(result).toHaveLength(1)
    expect(result[0]!.pid).toBe('alice')
  })

  it('fetchTrustlines returns items array filtered by equivalent', async () => {
    const httpConfig = ref({ apiBase: 'http://example.test', accessToken: 'x' })
    const runId = ref('run_1')
    const ia = useInteractActions({ httpConfig, runId })

    const items = [{ from_pid: 'a', to_pid: 'b', equivalent: 'UAH', limit: '1000', used: '200', available: '800', status: 'active' }]
    m.getTrustlinesList.mockResolvedValueOnce({ items } as any)

    const result = await ia.fetchTrustlines('UAH')
    expect(result).toHaveLength(1)
    expect(result[0]!.used).toBe('200')
  })
})

