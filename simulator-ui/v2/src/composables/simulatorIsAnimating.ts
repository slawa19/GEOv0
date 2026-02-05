export function createSimulatorIsAnimating(deps: {
  isPhysicsRunning: () => boolean
  isDemoHoldActive: () => boolean
  /**
   * Legacy signal (demo playlist playing) — intentionally ignored.
   * Kept optional to enable unit tests that assert we do NOT depend on it.
   */
  getPlaylistPlaying?: () => boolean
}): () => boolean {
  return () => {
    if (deps.isPhysicsRunning()) return true
    if (deps.isDemoHoldActive()) return true
    return false
  }
}

