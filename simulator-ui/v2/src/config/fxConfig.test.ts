import { describe, expect, it } from 'vitest'
import { FX_CONFIG, intensityScale } from './fxConfig'

// 034 S8 (F-034-D15): the case `FX_CONFIG structure` was removed together with its subject - it asserted that the
// `clearing` group had eleven keys, and nothing in the runtime read that group. What must hold now is the
// opposite statement, kept by `fxConfig.deadSettings.p034.test.ts`: the config has no group that nobody reads.

describe('intensityScale', () => {
  it('returns 1 for empty/undefined input', () => {
    expect(intensityScale()).toBe(1)
    expect(intensityScale('')).toBe(1)
    expect(intensityScale(undefined)).toBe(1)
  })

  it('maps known intensity keys', () => {
    expect(intensityScale('muted')).toBe(FX_CONFIG.intensity.muted)
    expect(intensityScale('low')).toBe(FX_CONFIG.intensity.muted)
    expect(intensityScale('active')).toBe(FX_CONFIG.intensity.active)
    expect(intensityScale('mid')).toBe(FX_CONFIG.intensity.active)
    expect(intensityScale('hi')).toBe(FX_CONFIG.intensity.hi)
    expect(intensityScale('high')).toBe(FX_CONFIG.intensity.hi)
  })

  it('is case-insensitive', () => {
    expect(intensityScale('MUTED')).toBe(FX_CONFIG.intensity.muted)
    expect(intensityScale('Hi')).toBe(FX_CONFIG.intensity.hi)
  })

  it('returns 1 for unknown keys', () => {
    expect(intensityScale('unknown')).toBe(1)
    expect(intensityScale('extreme')).toBe(1)
  })
})
