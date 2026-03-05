import { describe, expect, it, vi } from 'vitest'

import { __retryUntilTruthyOrDeadline } from './retryUntilTruthy'

describe('__retryUntilTruthyOrDeadline()', () => {
  it('times out: does not call onSuccess when value never becomes available', () => {
    vi.useFakeTimers()
    try {
      const nowMs = vi.fn(() => 0)
      const scheduleTimeout = (fn: () => void, ms: number) => {
        setTimeout(fn, ms)
      }

      const onSuccess = vi.fn()
      const onTimeout = vi.fn()

      __retryUntilTruthyOrDeadline({
        startedAtMs: 0,
        maxWaitMs: 800,
        retryDelayMs: 80,
        nowMs,
        scheduleTimeout,
        get: () => null,
        onSuccess,
        onTimeout,
      })

      nowMs.mockImplementation(() => 801)
      vi.advanceTimersByTime(801)

      expect(onSuccess).toHaveBeenCalledTimes(0)
      expect(onTimeout).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('succeeds before deadline: calls onSuccess exactly once', () => {
    vi.useFakeTimers()
    try {
      const nowMs = vi.fn(() => 0)
      const scheduleTimeout = (fn: () => void, ms: number) => {
        setTimeout(fn, ms)
      }

      const onSuccess = vi.fn()
      const onTimeout = vi.fn()

      let calls = 0
      const get = () => {
        calls++
        return calls >= 3 ? ({ ok: true } as const) : null
      }

      __retryUntilTruthyOrDeadline({
        startedAtMs: 0,
        maxWaitMs: 800,
        retryDelayMs: 80,
        nowMs,
        scheduleTimeout,
        get,
        onSuccess,
        onTimeout,
      })

      nowMs.mockImplementation(() => 80)
      vi.advanceTimersByTime(80)

      nowMs.mockImplementation(() => 160)
      vi.advanceTimersByTime(80)

      expect(onSuccess).toHaveBeenCalledTimes(1)
      expect(onTimeout).toHaveBeenCalledTimes(0)

      nowMs.mockImplementation(() => 10_000)
      vi.runOnlyPendingTimers()
      expect(onSuccess).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
