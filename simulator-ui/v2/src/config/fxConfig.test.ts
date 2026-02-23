import { describe, expect, it } from 'vitest'
import { FX_CONFIG, intensityScale } from './fxConfig'

describe('FX_CONFIG structure', () => {
  it('clearing is canonical (mode-agnostic) and contains expected keys', () => {
    const clearing = FX_CONFIG.clearing

    expect(clearing).toHaveProperty('microGapMs')
    expect(clearing).toHaveProperty('labelLifeMs')
    expect(clearing).toHaveProperty('sourceBurstMs')
    expect(clearing).toHaveProperty('targetBurstMs')
    expect(clearing).toHaveProperty('cleanupPadMs')
    expect(clearing).toHaveProperty('labelThrottleMs')
    expect(clearing).toHaveProperty('highlightPulseMs')
    expect(clearing).toHaveProperty('microTtlMs')
    expect(clearing).toHaveProperty('highlightThickness')
    expect(clearing).toHaveProperty('microThickness')
    expect(clearing).toHaveProperty('nodeBurstMs')
  })
})

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
