import { describe, expect, it, vi } from 'vitest'

describe('vitest animation frame harness', () => {
  it('runs live frames and suppresses cancelled frames deterministically', async () => {
    const cancelled = vi.fn()
    const live = vi.fn()

    const cancelledId = requestAnimationFrame(cancelled)
    cancelAnimationFrame(cancelledId)
    requestAnimationFrame(live)

    await Promise.resolve()

    expect(cancelled).not.toHaveBeenCalled()
    expect(live).toHaveBeenCalledTimes(1)
  })
})
