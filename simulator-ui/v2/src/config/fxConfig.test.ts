import { describe, expect, it } from 'vitest'
import { FX_CONFIG, getFxConfig, intensityScale } from './fxConfig'

describe('FX_CONFIG structure', () => {
  it('shared contains only mode-agnostic params', () => {
    const shared = FX_CONFIG.shared
    expect(shared).toHaveProperty('microGapMs')
    expect(shared).toHaveProperty('labelLifeMs')
    expect(shared).toHaveProperty('sourceBurstMs')
    expect(shared).toHaveProperty('targetBurstMs')
    expect(shared).toHaveProperty('cleanupPadMs')
    expect(shared).toHaveProperty('labelThrottleMs')

    expect(shared).not.toHaveProperty('highlightThickness')
    expect(shared).not.toHaveProperty('microThickness')
    expect(shared).not.toHaveProperty('nodeBurstMs')
  })

  it('demo and real have different highlightPulseMs', () => {
    expect(FX_CONFIG.demo.highlightPulseMs).toBeLessThan(FX_CONFIG.real.highlightPulseMs)
  })

  it('demo and real have different microTtlMs', () => {
    expect(FX_CONFIG.demo.microTtlMs).toBeLessThan(FX_CONFIG.real.microTtlMs)
  })

  it('real has exclusive params not in demo', () => {
    expect(FX_CONFIG.real).toHaveProperty('highlightThickness')
    expect(FX_CONFIG.real).toHaveProperty('microThickness')
    expect(FX_CONFIG.real).toHaveProperty('nodeBurstMs')
    expect(FX_CONFIG.demo).not.toHaveProperty('highlightThickness')
  })
})

describe('getFxConfig', () => {
  it('returns merged config for demo', () => {
    const cfg = getFxConfig('demo')
    expect(cfg.microGapMs).toBe(FX_CONFIG.shared.microGapMs)
    expect(cfg.highlightPulseMs).toBe(FX_CONFIG.demo.highlightPulseMs)
  })

  it('returns merged config for real', () => {
    const cfg = getFxConfig('real')
    expect(cfg.microGapMs).toBe(FX_CONFIG.shared.microGapMs)
    expect(cfg.highlightPulseMs).toBe(FX_CONFIG.real.highlightPulseMs)
    expect(cfg.highlightThickness).toBe(FX_CONFIG.real.highlightThickness)
  })

  it('shared params are identical across modes', () => {
    const demo = getFxConfig('demo')
    const real = getFxConfig('real')
    expect(demo.microGapMs).toBe(real.microGapMs)
    expect(demo.labelLifeMs).toBe(real.labelLifeMs)
    expect(demo.sourceBurstMs).toBe(real.sourceBurstMs)
    expect(demo.targetBurstMs).toBe(real.targetBurstMs)
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
