/**
 * 038 S3 - discrete performance budgets of Simulator UI without a GPU. A two-sided ratchet (AGENTS.md, section 6).
 *
 * Only COUNTS live here: draws, frame requests, live objects, a cap, a scale in thousandths - all taken on
 * `page.clock`, so they are the same on any machine. Time (frame ms, fps, heap) is NOT here and must not be put
 * here: it is a property of the machine, reported as diagnostics by `frame.qaperf.ts`.
 *
 * The test (`budgets.qaperf.ts`) demands exact equality: a worse number is a regression, a better one has to be
 * recorded - otherwise the constant drifts above reality and lets the debt back in. Every move of a value is a dated
 * line in `since` with the reason; the old value stays in the line.
 *
 * Tool versions the numbers depend on: Playwright and its Chromium are pinned by `package-lock.json`
 * (`@playwright/test`); the clock is Playwright's `page.clock` (one animation frame per 16 ms of fake time).
 * Scene: `public/simulator-fixtures/v1/UAH/snapshot.json` (100 nodes, 348 trust lines, `limits.max_particles` 300),
 * 1280x720, software rendering, quality `low` (set by the product's own no-GPU autoguard).
 */
export type Budget = {
  name: string
  value: number
  what: string
  /** Which direction is a regression; `either` for a recorded behaviour where both directions are a change. */
  worseIs: 'higher' | 'lower' | 'either'
  since: string
}

const b = (name: string, value: number, worseIs: Budget['worseIs'], what: string, since: string): Budget => ({ name, value, worseIs, what, since })

/**
 * sha256 (first 16 hex) of ten periods of the load `S3-MIXED-V1` (`perf-load.ts`).
 * 2026-10-11: cb0a3e1cfafb8a80 - first record (038 S3). A change of the generator or of the fixture moves it, and
 * every wall-clock number taken with the old load stops being comparable.
 */
export const LOAD_FINGERPRINT = 'cb0a3e1cfafb8a80'

export const BUDGETS = {
  DRAWS_IN_2S_DEEP_SLEEP: b('DRAWS_IN_2S_DEEP_SLEEP', 0, 'higher',
    'completed draws of the render loop in 2 s of deep sleep (nothing moves, 3 s after the last activity)',
    '2026-10-11: 0 - first record (038 S3); measured by S0 as R4'),
  RAF_CALLS_IN_2S_DEEP_SLEEP: b('RAF_CALLS_IN_2S_DEEP_SLEEP', 0, 'higher',
    'requestAnimationFrame registrations by anyone on the page in 2 s of deep sleep',
    '2026-10-11: 0 - first record (038 S3)'),
  DRAWS_IN_48MS_AFTER_EVENT: b('DRAWS_IN_48MS_AFTER_EVENT', 3, 'either',
    'draws in the three frames (48 ms) after an event reaches a sleeping loop: 3 = it wakes on the next frame and draws every frame; fewer = a late wake-up, more = more than one draw per frame',
    '2026-10-11: 3 - first record (038 S3)'),
  DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP: b('DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP', 128, 'either',
    'draws from one 1-edge payment (no label) arriving in deep sleep until the loop sleeps again: flight 1200 ms, receiver burst 420 ms, hold 250 ms at full rate, then 4 fps until 3 s of rest. More = the loop stays awake longer than the effect; fewer = the effect or the idle cadence was cut',
    '2026-10-11: 128 - first record (038 S3)'),
  DRAWS_IN_ONE_PERIOD_OF_LOAD: b('DRAWS_IN_ONE_PERIOD_OF_LOAD', 225, 'either',
    'draws during one period (3.6 s = 225 frames) of the load S3-MIXED-V1: one draw per frame. More = something draws twice a frame (an extra renderOnce per event); fewer = frames are skipped under load',
    '2026-10-11: 225 - first record (038 S3)'),
  PEAK_SPARKS_IN_ONE_PERIOD: b('PEAK_SPARKS_IN_ONE_PERIOD', 5, 'either',
    'most sparks alive at once during one period of S3-MIXED-V1, sampled every 150 ms. It is what the limiter of useRealTxFx.ts admits (3 beams/s, burst 2; a beam is one spark at a time). Lower with the same load = beams are no longer shown; higher = the limiter was loosened',
    '2026-10-11: 5 - first record (038 S3)'),
  PEAK_EDGE_PULSES_IN_ONE_PERIOD: b('PEAK_EDGE_PULSES_IN_ONE_PERIOD', 20, 'either',
    'most edge pulses alive at once during one period of S3-MIXED-V1: the 20 edges of its one clearing',
    '2026-10-11: 20 - first record (038 S3)'),
  PEAK_NODE_BURSTS_IN_ONE_PERIOD: b('PEAK_NODE_BURSTS_IN_ONE_PERIOD', 35, 'either',
    'most node bursts alive at once during one period of S3-MIXED-V1 (the nodes of the clearing plus senders and receivers of admitted beams)',
    '2026-10-11: 35 - first record (038 S3)'),
  PEAK_LABELS_IN_ONE_PERIOD: b('PEAK_LABELS_IN_ONE_PERIOD', 24, 'either',
    'most amount labels mounted at once during one period of S3-MIXED-V1, sampled every 150 ms',
    '2026-10-11: 24 - first record (038 S3)'),
  LIVE_FX_AFTER_THE_STREAM: b('LIVE_FX_AFTER_THE_STREAM', 0, 'higher',
    'sparks + edge pulses + node bursts still in FX state 12 s after the last event of the stream (a leak)',
    '2026-10-11: 0 - first record (038 S3)'),
  LIVE_LABELS_AFTER_THE_STREAM: b('LIVE_LABELS_AFTER_THE_STREAM', 0, 'higher',
    'amount labels still mounted 8-12 s after the last event (the longest label lives 3800 ms)',
    '2026-10-11: 0 - first record (038 S3)'),
  LIVE_LABELS_CAP: b('LIVE_LABELS_CAP', 120, 'either',
    'most amount labels mounted at once under a flood that asks for more: the product cap MAX_FLOATING_LABELS (useOverlayState.ts). Higher = the cap is gone or raised (more DOM under load); lower = the cap was lowered - record it',
    '2026-10-11: 120 - first record (038 S3)'),
  MAX_PARTICLES_AT_60_FPS: b('MAX_PARTICLES_AT_60_FPS', 300, 'either',
    'the particle cap (__maxParticles) at 60 fps: the declared limits.max_particles of the snapshot, unscaled',
    '2026-10-11: 300 - first record (038 S3)'),
  FX_BUDGET_SCALE_X1000_AT_SUSTAINED_10_FPS: b('FX_BUDGET_SCALE_X1000_AT_SUSTAINED_10_FPS', 450, 'either',
    'fxBudgetScale x 1000 after 8 s at 10 fps: the lowest step of FX_BUDGET_SCALE_TARGETS (useRenderLoop.ts). Higher = the budget does not fall on a slow machine; lower = effects are cut harder than recorded',
    '2026-10-11: 450 - first record (038 S3); S0 saw 0.45 on wall clock at CPU x4'),
  MAX_PARTICLES_AT_SUSTAINED_10_FPS: b('MAX_PARTICLES_AT_SUSTAINED_10_FPS', 135, 'either',
    'the particle cap after 8 s at 10 fps: floor(300 x 0.45)',
    '2026-10-11: 135 - first record (038 S3)'),
  FRAMES_WITH_LIVE_FX_ABOVE_THE_CAP: b('FRAMES_WITH_LIVE_FX_ABOVE_THE_CAP', 0, 'higher',
    'frames (of 80 at 10 fps) in which more FX objects were alive than the cap of that frame allowed, with a demand above the cap',
    '2026-10-11: 0 - first record (038 S3)'),
  ONSCREEN_SHADOW_BLUR_SETS_NO_GPU: b('ONSCREEN_SHADOW_BLUR_SETS_NO_GPU', 0, 'higher',
    'assignments of a non-zero shadowBlur to a context of an on-screen canvas during one period of the load, quality low, no GPU (policy 4.1: no per-frame blur; glow comes from pre-baked sprites)',
    '2026-10-11: 0 - first record (038 S3)'),
  PEAK_LABELS_WITH_CSS_FILTER_NO_GPU: b('PEAK_LABELS_WITH_CSS_FILTER_NO_GPU', 0, 'higher',
    'most amount labels (payments and clearing) mounted at once with a computed CSS filter (drop-shadow) during one period of the load, no GPU (policy 5: on software compositing every such label is filtered again on every frame). With a GPU the filter stays - held by the counter-test in budgets.qaperf.ts',
    '2026-10-11: 24 - first record (038 S3), a recorded debt: every label carried the filter, and it took the scene from 60 to about 16 fps. '
    + '2026-10-11: 24 -> 0 - the filter is gated by data-gpu=0 in src/App.css (orchestrator decision of the same day: the same class of effect the policy already turns off without a GPU)'),
  ELEMENTS_WITH_BACKDROP_FILTER_NO_GPU: b('ELEMENTS_WITH_BACKDROP_FILTER_NO_GPU', 0, 'higher',
    'elements with a computed backdrop-filter under load, no window open, quality low, no GPU (policy 5: CSS gating)',
    '2026-10-11: 0 - first record (038 S3)'),
} as const satisfies Record<string, Budget>
