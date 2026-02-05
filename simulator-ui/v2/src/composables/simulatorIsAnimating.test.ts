import { describe, expect, it, vi } from 'vitest'
import { createDemoActivityHold } from './demoActivityHold'
import { createSimulatorIsAnimating } from './simulatorIsAnimating'

describe('createSimulatorIsAnimating', () => {
  it('does not depend on playlist.playing (demo playback alone must not keep animating)', () => {
    const hold = createDemoActivityHold({ holdMs: 300, nowMs: () => 1000 })

    const isAnimating = createSimulatorIsAnimating({
      isPhysicsRunning: () => false,
      isDemoHoldActive: () => hold.isWithinHoldWindow(),
      getPlaylistPlaying: () => true,
    })

    expect(isAnimating()).toBe(false)
  })

  it('returns true during the short hold window after a demo event, then false', () => {
    let now = 1000
    const nowMs = () => now

    const hold = createDemoActivityHold({ holdMs: 300, nowMs })

    const isAnimating = createSimulatorIsAnimating({
      isPhysicsRunning: () => false,
      isDemoHoldActive: () => hold.isWithinHoldWindow(),
    })

    expect(isAnimating()).toBe(false)

    hold.markDemoEvent()
    expect(isAnimating()).toBe(true)

    now += 299
    expect(isAnimating()).toBe(true)

    now += 1
    expect(isAnimating()).toBe(false)
  })

  it('returns true when physics is running regardless of demo hold', () => {
    const hold = createDemoActivityHold({ holdMs: 300, nowMs: () => 0 })
    const isAnimating = createSimulatorIsAnimating({
      isPhysicsRunning: () => true,
      isDemoHoldActive: () => hold.isWithinHoldWindow(),
    })
    expect(isAnimating()).toBe(true)
  })

  it('demo hold can be triggered by the demo player callback (onDemoEvent)', async () => {
    // This is a light wiring test for the intended pattern: demo event -> markDemoEvent + wakeUp.
    let now = 1000
    const nowMs = () => now
    const hold = createDemoActivityHold({ holdMs: 300, nowMs })
    const wakeUp = vi.fn()

    const onDemoEvent = () => {
      hold.markDemoEvent()
      wakeUp()
    }

    const isAnimating = createSimulatorIsAnimating({
      isPhysicsRunning: () => false,
      isDemoHoldActive: () => hold.isWithinHoldWindow(),
    })

    expect(isAnimating()).toBe(false)
    onDemoEvent()
    expect(wakeUp).toHaveBeenCalledTimes(1)
    expect(isAnimating()).toBe(true)

    now = 1400
    expect(isAnimating()).toBe(false)
  })
})

