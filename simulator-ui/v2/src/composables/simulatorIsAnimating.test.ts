import { describe, expect, it } from 'vitest'
import { createSimulatorIsAnimating } from './simulatorIsAnimating'

describe('createSimulatorIsAnimating', () => {
  it('does not depend on playlist.playing (demo playback alone must not keep animating)', () => {
    const isAnimating = createSimulatorIsAnimating({
      isPhysicsRunning: () => false,
      getPlaylistPlaying: () => true,
    })

    expect(isAnimating()).toBe(false)
  })

  it('returns true when physics is running', () => {
    const isAnimating = createSimulatorIsAnimating({
      isPhysicsRunning: () => true,
    })
    expect(isAnimating()).toBe(true)
  })
})
