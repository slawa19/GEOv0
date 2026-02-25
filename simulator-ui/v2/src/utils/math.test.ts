import { describe, expect, it } from 'vitest'
import { safeClampToViewport } from './math'

describe('safeClampToViewport', () => {
  it('size=1, margin=22 → result in [0, 1]', () => {
    // m = min(22, max(0, 1/2 - 1)) = min(22, max(0, -0.5)) = min(22, 0) = 0
    // range = [0, 1]
    const below = safeClampToViewport(-100, 22, 1)
    const above = safeClampToViewport(100, 22, 1)
    const mid = safeClampToViewport(0.5, 22, 1)

    expect(below).toBeGreaterThanOrEqual(0)
    expect(below).toBeLessThanOrEqual(1)
    expect(above).toBeGreaterThanOrEqual(0)
    expect(above).toBeLessThanOrEqual(1)
    expect(mid).toBeGreaterThanOrEqual(0)
    expect(mid).toBeLessThanOrEqual(1)
  })

  it('size=50, margin=30 → effective margin=24, range=[24, 26]', () => {
    // m = min(30, max(0, 50/2 - 1)) = min(30, 24) = 24
    // range = [24, 26]
    const below = safeClampToViewport(0, 30, 50)
    const above = safeClampToViewport(100, 30, 50)
    const mid = safeClampToViewport(25, 30, 50)

    expect(below).toBe(24)
    expect(above).toBe(26)
    expect(mid).toBe(25)
  })

  it('size=200, margin=22 → normal range [22, 178]', () => {
    // m = min(22, max(0, 200/2 - 1)) = min(22, 99) = 22
    // range = [22, 178]
    const below = safeClampToViewport(0, 22, 200)
    const above = safeClampToViewport(200, 22, 200)
    const mid = safeClampToViewport(100, 22, 200)

    expect(below).toBe(22)
    expect(above).toBe(178)
    expect(mid).toBe(100)
  })
})
