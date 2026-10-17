export function createSimulatorIsAnimating(deps: {
  isPhysicsRunning: () => boolean
  /**
   * Legacy signal (demo playlist playing) — intentionally ignored.
   * Kept optional to enable unit tests that assert we do NOT depend on it.
   */
  getPlaylistPlaying?: () => boolean
}): () => boolean {
  // 043 S3: the Demo UI hold window (`demoActivityHold.ts`, kept the loop awake after a Demo UI event) is gone with the
  // mode; a live event wakes the loop through the event pipeline's `wake` intent.
  return () => deps.isPhysicsRunning()
}
