import { afterEach, describe, expect, it, vi } from 'vitest'

import { throttle } from './throttle'

describe('throttle', () => {
  afterEach(() => vi.useRealTimers())

  it('cancels trailing work', () => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000)
    const fn = vi.fn()
    const throttled = throttle(fn, 100)

    throttled('first')
    vi.advanceTimersByTime(10)
    throttled('trailing')
    throttled.cancel()
    vi.advanceTimersByTime(100)

    expect(fn).toHaveBeenCalledTimes(1)
    expect(fn).toHaveBeenCalledWith('first')
  })
})
