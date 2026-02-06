import { ref, type Ref } from 'vue'

export type MarkInteractionOpts = { instant?: boolean; nowMs?: number }

export type InteractionHold = {
  /** Reactive flag: true while user interaction is happening (+ post-event hold window). */
  isInteracting: Ref<boolean>
  /** Smooth intensity 0.0–1.0 with easing transitions. */
  intensity: Ref<number>
  /**
   * Mark any user interaction event (wheel/drag). Cheap + idempotent.
   * Pass `{ instant: true }` to skip ease-in ramp and set intensity=1.0 immediately
   * (useful for click/wheel after idle to avoid a slow first frame).
   */
  markInteraction: (opts?: MarkInteractionOpts | number) => void
  /**
   * Compute current intensity for the given timestamp (lazy easing).
   * Call once per render frame. Updates the reactive `intensity` ref.
   */
  getIntensity: (nowMs?: number) => number
  /** Configured hold window (ms). */
  holdMs: number
  /** Clear timers + reset state (optional cleanup). */
  dispose: () => void
}

export type InteractionHoldOpts = {
  holdMs?: number
  easeInMs?: number
  easeOutDelayMs?: number
  easeOutMs?: number
  nowMs?: () => number
}

function defaultNowMs() {
  // Use Date.now() to stay compatible with vitest fake timers (vi.setSystemTime / advanceTimers).
  return Date.now()
}

/**
 * Interaction hold detector with smooth intensity easing.
 *
 * Goals:
 * - Avoid per-event clearTimeout/setTimeout churn on pointermove.
 * - Keep a single timer and extend a deadline on every event.
 * - Provide smooth 0.0–1.0 intensity for gradual quality transitions.
 *
 * Intensity easing phases:
 * - idle: intensity = 0
 * - ramping-up: intensity rises from current → 1.0 over easeInMs
 * - holding: intensity = 1.0
 * - delaying: intensity stays at last value for easeOutDelayMs after hold ends
 * - ramping-down: intensity falls from current → 0.0 over easeOutMs
 */
export function createInteractionHold(opts?: InteractionHoldOpts): InteractionHold {
  const holdMs = Math.max(0, Math.floor(Number(opts?.holdMs ?? 250)))
  const easeInMs = Math.max(1, Number(opts?.easeInMs ?? 100))
  const easeOutDelayMs = Math.max(0, Number(opts?.easeOutDelayMs ?? 200))
  const easeOutMs = Math.max(1, Number(opts?.easeOutMs ?? 150))
  const now = typeof opts?.nowMs === 'function' ? opts.nowMs : defaultNowMs

  const isInteracting = ref(false)
  const intensity = ref(0)

  let deadlineAtMs = Number.NEGATIVE_INFINITY
  let timer: ReturnType<typeof setTimeout> | null = null

  // Intensity easing state machine.
  type Phase = 'idle' | 'ramping-up' | 'holding' | 'delaying' | 'ramping-down'
  let phase: Phase = 'idle'
  let phaseStartMs = 0
  let phaseStartIntensity = 0

  function computeIntensity(nowMs: number): number {
    switch (phase) {
      case 'idle':
        return 0
      case 'ramping-up': {
        const elapsed = nowMs - phaseStartMs
        const progress = Math.min(1, elapsed / easeInMs)
        const val = phaseStartIntensity + (1 - phaseStartIntensity) * progress
        if (val >= 1) {
          phase = 'holding'
          return 1
        }
        return Math.min(1, val)
      }
      case 'holding':
        return 1
      case 'delaying': {
        const elapsed = nowMs - phaseStartMs
        if (elapsed >= easeOutDelayMs) {
          phase = 'ramping-down'
          phaseStartMs = phaseStartMs + easeOutDelayMs
          phaseStartIntensity = phaseStartIntensity // maintain from delay start
          // Recurse to get ramping-down value for the remaining time.
          return computeIntensity(nowMs)
        }
        return phaseStartIntensity // Keep intensity during delay
      }
      case 'ramping-down': {
        const elapsed = nowMs - phaseStartMs
        const progress = Math.min(1, elapsed / easeOutMs)
        const val = phaseStartIntensity * (1 - progress)
        if (val <= 0) {
          phase = 'idle'
          return 0
        }
        return Math.max(0, val)
      }
    }
  }

  function getIntensity(nowMs?: number): number {
    const t = typeof nowMs === 'number' ? nowMs : now()
    const val = computeIntensity(t)
    intensity.value = val
    return val
  }

  function tick() {
    timer = null

    const t = now()
    const remaining = deadlineAtMs - t
    if (remaining > 0) {
      // Still within hold window; reschedule exactly to the deadline.
      timer = setTimeout(tick, remaining)
      return
    }

    isInteracting.value = false

    // Transition intensity phase: start ease-out delay.
    if (phase === 'holding' || phase === 'ramping-up') {
      const currentIntensity = computeIntensity(t)
      phase = 'delaying'
      phaseStartMs = t
      phaseStartIntensity = currentIntensity
    }
  }

  function markInteraction(optsOrNow?: MarkInteractionOpts | number) {
    const isObj = typeof optsOrNow === 'object' && optsOrNow !== null
    const t = isObj
      ? (typeof optsOrNow.nowMs === 'number' ? optsOrNow.nowMs : now())
      : (typeof optsOrNow === 'number' ? optsOrNow : now())
    const instant = isObj ? !!optsOrNow.instant : false

    deadlineAtMs = t + holdMs
    isInteracting.value = true

    if (instant) {
      // Skip ramp-up: go straight to holding with intensity=1.0.
      phase = 'holding'
      phaseStartMs = t
      phaseStartIntensity = 1
    } else if (phase === 'idle' || phase === 'delaying' || phase === 'ramping-down') {
      // Start ease-in if not already ramping up or holding.
      const currentIntensity = computeIntensity(t)
      phase = 'ramping-up'
      phaseStartMs = t
      phaseStartIntensity = currentIntensity
    }

    // Keep exactly one timer alive.
    if (timer === null) {
      timer = setTimeout(tick, holdMs)
    }
  }

  function dispose() {
    if (timer !== null) clearTimeout(timer)
    timer = null
    deadlineAtMs = Number.NEGATIVE_INFINITY
    isInteracting.value = false
    intensity.value = 0
    phase = 'idle'
  }

  return { isInteracting, intensity, markInteraction, getIntensity, holdMs, dispose }
}
