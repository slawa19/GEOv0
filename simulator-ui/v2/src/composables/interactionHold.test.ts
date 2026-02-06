import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'

import { createInteractionHold } from './interactionHold'

describe('createInteractionHold()', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2020-01-01T00:00:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('activates on markInteraction() and holds for N ms', () => {
    const h = createInteractionHold({ holdMs: 250 })

    expect(h.isInteracting.value).toBe(false)

    h.markInteraction()
    expect(h.isInteracting.value).toBe(true)

    vi.advanceTimersByTime(249)
    expect(h.isInteracting.value).toBe(true)

    vi.advanceTimersByTime(1)
    expect(h.isInteracting.value).toBe(false)
  })

  it('extends the hold window when events keep coming', () => {
    const h = createInteractionHold({ holdMs: 250 })

    h.markInteraction()
    expect(h.isInteracting.value).toBe(true)

    vi.advanceTimersByTime(200)
    h.markInteraction()

    // original deadline was +250, but was extended to +450
    vi.advanceTimersByTime(249) // now at +449
    expect(h.isInteracting.value).toBe(true)

    vi.advanceTimersByTime(1) // now at +450
    expect(h.isInteracting.value).toBe(false)
  })

  describe('intensity easing', () => {
    it('starts at 0 before any interaction', () => {
      const h = createInteractionHold({ holdMs: 250 })
      expect(h.intensity.value).toBe(0)
      expect(h.getIntensity()).toBe(0)
    })

    it('ramps up to 1.0 over easeInMs after markInteraction()', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({ holdMs: 250, easeInMs: 100 })

      h.markInteraction()

      // At t=0: intensity starts ramping from 0
      const v0 = h.getIntensity(baseMs)
      expect(v0).toBe(0)

      // At t=50ms: should be ~0.5
      const v50 = h.getIntensity(baseMs + 50)
      expect(v50).toBeCloseTo(0.5, 1)

      // At t=100ms: should be 1.0
      const v100 = h.getIntensity(baseMs + 100)
      expect(v100).toBe(1)

      // At t=150ms: still 1.0 (holding)
      const v150 = h.getIntensity(baseMs + 150)
      expect(v150).toBe(1)
    })

    it('stays at 1.0 while hold is active, then fades after delay', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({
        holdMs: 250,
        easeInMs: 100,
        easeOutDelayMs: 200,
        easeOutMs: 150,
      })

      h.markInteraction()

      // Ramp up complete
      h.getIntensity(baseMs + 100)
      expect(h.intensity.value).toBe(1)

      // Hold window expires at +250ms
      vi.advanceTimersByTime(250)
      expect(h.isInteracting.value).toBe(false)

      // During easeOutDelay (200ms after hold expired): intensity stays at 1.0
      const vDelay = h.getIntensity(baseMs + 350) // 100ms into delay
      expect(vDelay).toBe(1)

      // After easeOutDelay: start ramping down from 1.0 over 150ms
      // delay ends at baseMs + 250 + 200 = baseMs + 450
      const vFadeStart = h.getIntensity(baseMs + 450)
      expect(vFadeStart).toBe(1)

      // 75ms into fade: ~0.5
      const vFadeMid = h.getIntensity(baseMs + 525)
      expect(vFadeMid).toBeCloseTo(0.5, 1)

      // 150ms into fade: 0.0
      const vFadeEnd = h.getIntensity(baseMs + 600)
      expect(vFadeEnd).toBe(0)
    })

    it('re-activates during ease-out and resumes from current intensity', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({
        holdMs: 250,
        easeInMs: 100,
        easeOutDelayMs: 200,
        easeOutMs: 150,
      })

      h.markInteraction()

      // Ramp up and hold
      h.getIntensity(baseMs + 100)
      expect(h.intensity.value).toBe(1)

      // Let hold expire
      vi.advanceTimersByTime(250)

      // Wait past delay, start fading
      // delay ends at baseMs + 450
      const vFading = h.getIntensity(baseMs + 525) // 75ms into fade → ~0.5
      expect(vFading).toBeCloseTo(0.5, 1)

      // Re-activate: mark interaction while fading
      vi.setSystemTime(new Date(baseMs + 525))
      h.markInteraction(baseMs + 525)

      // Should ramp up from ~0.5 over 100ms
      const vResume = h.getIntensity(baseMs + 575) // 50ms after re-activation
      // From 0.5, gain = (1-0.5) * 50/100 = 0.25 → 0.75
      expect(vResume).toBeCloseTo(0.75, 1)

      // Full intensity again
      const vFull = h.getIntensity(baseMs + 625)
      expect(vFull).toBe(1)
    })

    it('updates intensity ref when getIntensity() is called', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({ holdMs: 250, easeInMs: 100 })

      expect(h.intensity.value).toBe(0)

      h.markInteraction()
      h.getIntensity(baseMs + 50)
      expect(h.intensity.value).toBeCloseTo(0.5, 1)

      h.getIntensity(baseMs + 100)
      expect(h.intensity.value).toBe(1)
    })

    it('dispose resets intensity to 0', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({ holdMs: 250, easeInMs: 100 })

      h.markInteraction()
      h.getIntensity(baseMs + 100)
      expect(h.intensity.value).toBe(1)

      h.dispose()
      expect(h.intensity.value).toBe(0)
      expect(h.isInteracting.value).toBe(false)
    })

    it('instant mark sets intensity=1.0 immediately (skips ramp-up)', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({ holdMs: 250, easeInMs: 100 })

      h.markInteraction({ instant: true })

      // Intensity should be 1.0 immediately — no ramp-up phase.
      const v0 = h.getIntensity(baseMs)
      expect(v0).toBe(1)
      expect(h.isInteracting.value).toBe(true)

      // Still 1.0 during the hold window.
      const v100 = h.getIntensity(baseMs + 100)
      expect(v100).toBe(1)
    })

    it('instant mark followed by hold expiry triggers normal ease-out', () => {
      const baseMs = Date.now()
      const h = createInteractionHold({
        holdMs: 250,
        easeInMs: 100,
        easeOutDelayMs: 200,
        easeOutMs: 150,
      })

      h.markInteraction({ instant: true })
      expect(h.getIntensity(baseMs)).toBe(1)

      // Hold expires at +250ms
      vi.advanceTimersByTime(250)
      expect(h.isInteracting.value).toBe(false)

      // During delay: intensity stays at 1.0
      expect(h.getIntensity(baseMs + 350)).toBe(1)

      // After delay (at +450ms): ramping down
      // 75ms into fade: ~0.5
      expect(h.getIntensity(baseMs + 525)).toBeCloseTo(0.5, 1)

      // Fully faded at +600ms
      expect(h.getIntensity(baseMs + 600)).toBe(0)
    })

    it('instant mark with explicit nowMs works', () => {
      const h = createInteractionHold({ holdMs: 250, easeInMs: 100 })

      h.markInteraction({ instant: true, nowMs: 5000 })

      const v = h.getIntensity(5000)
      expect(v).toBe(1)
      expect(h.isInteracting.value).toBe(true)
    })
  })
})
