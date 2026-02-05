export type DemoActivityHold = {
  /** Mark that a demo event mutated visual state (patch applied / FX spawned). */
  markDemoEvent: (nowMs?: number) => void
  /** Last marked timestamp (ms). */
  getLastDemoEventAtMs: () => number
  /** True if we are within the post-event hold window. */
  isWithinHoldWindow: (nowMs?: number) => boolean
  holdMs: number
}

function defaultNowMs(): number {
  const win = typeof window !== 'undefined' ? window : (globalThis as any)
  const perfNow = win?.performance?.now
  return typeof perfNow === 'function' ? perfNow.call(win.performance) : Date.now()
}

/**
 * Tracks a short-lived "activity window" after demo events.
 *
 * Goal: do NOT keep the render loop running at 60fps just because demo playback is "playing".
 * Instead, animate briefly after each applied demo event so the loop can go idle/deep-idle
 * between events when nothing visual is changing.
 */
export function createDemoActivityHold(opts?: { holdMs?: number; nowMs?: () => number }): DemoActivityHold {
  const holdMs = Math.max(0, Number(opts?.holdMs ?? 350))
  const now = typeof opts?.nowMs === 'function' ? opts.nowMs : defaultNowMs

  let lastDemoEventAtMs = Number.NEGATIVE_INFINITY

  function markDemoEvent(nowMs?: number) {
    const t = typeof nowMs === 'number' ? nowMs : now()
    lastDemoEventAtMs = t
  }

  function getLastDemoEventAtMs() {
    return lastDemoEventAtMs
  }

  function isWithinHoldWindow(nowMs?: number) {
    const t = typeof nowMs === 'number' ? nowMs : now()
    return t - lastDemoEventAtMs < holdMs
  }

  return { markDemoEvent, getLastDemoEventAtMs, isWithinHoldWindow, holdMs }
}

