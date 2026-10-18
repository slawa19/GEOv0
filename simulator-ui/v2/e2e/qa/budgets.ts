/**
 * 038 S3 - discrete performance budgets of Simulator UI without a GPU. A two-sided ratchet (AGENTS.md, section 6).
 *
 * Only COUNTS live here: draws, frame requests, live objects, a cap, a scale in thousandths - all taken on
 * `page.clock`, so they are the same on any machine. Time (frame ms, fps, heap) is NOT here and must not be put
 * here: it is a property of the machine, reported as diagnostics by `frame.qaperf.ts`.
 *
 * The test (`budgets.qa.ts`, a member of the counted project `qa-stand` since 2026-10-12) demands exact equality: a worse number is a regression, a better one has to be
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
  DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP: b('DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP', 81, 'either',
    'draws from one 1-edge payment (no label) arriving in deep sleep until the loop sleeps again: the flight (one leg on its plan, then a 360 ms fade), receiver burst 420 ms, hold 250 ms at full rate, then 4 fps until 3 s of rest. More = the loop stays awake longer than the effect; fewer = the effect or the idle cadence was cut',
    '2026-10-11: 128 - first record (038 S3). '
    + '2026-10-18: 128 -> 81 (039 slice C): the beam of a payment is timed by its route plan, not by the wire 1200 ms - the 1-edge payment of this budget (links[0] of the 100-node fixture) now moves at the floor of 450 ms and fades for 360, so the loop is awake for fewer full-rate frames. Nothing is skipped: every frame of the effect is still drawn (render-loop-sleep.qa.ts)'),
  DRAWS_IN_ONE_PERIOD_OF_LOAD: b('DRAWS_IN_ONE_PERIOD_OF_LOAD', 225, 'either',
    'PASSES of the render loop during one period (3.6 s = 225 frames) of the load S3-MIXED-V1: one pass per frame. More = a second pass in a frame (an extra renderOnce per event); fewer = frames are skipped under load. It counts passes (one clear of the base canvas each), NOT how much a pass draws - that is DRAW_COMMANDS_*',
    '2026-10-11: 225 - first record (038 S3); the wording corrected the same day (adversarial pass, B2): a pass that drew the base graph twice left this number unchanged'),
  DRAW_COMMANDS_IN_A_STATIC_FRAME: b('DRAW_COMMANDS_IN_A_STATIC_FRAME', 744, 'either',
    'calls of drawing methods (stroke, fill, fillRect, strokeRect, fillText, strokeText, drawImage, putImageData) on the on-screen canvases in one pass with nothing but the base graph: 100 nodes, 348 trust lines, quality low. Higher = the frame got heavier (the main-thread canvas raster is the bottleneck on a slow CPU); lower = something is no longer drawn - or the graph is drawn cheaper, then record it',
    '2026-10-11: 969 - first record (038 S3, B2); the same in 3 runs on the dev server and 3 on the production build. '
    + '2026-10-12: 969 -> 844 (-125). 039 slice B, PR 1: the sign glyph (one stroke) stands where the pictogram was - that of a person was one fill, that of an organisation one fill and six fillRect - and a frozen node has no rim sprite under its dashed outline. On the 100-node fixture: 20 organisations x 6 + 5 frozen x 1 = 125 calls fewer in every pass. Nothing is skipped: every node still gets its fill, outline and sign (src/render/nodePainter.test.ts). '
    + '2026-10-18: 844 -> 744 (-100). 039 slice C, glow only on active nodes: an idle node draws neither the bloom nor the rim sprite (two drawImage), a crisp rim stroke instead (one stroke) - 95 non-frozen nodes x (-2 + 1) and 5 frozen x (-1, the bloom) = -100. The same seen by the counted test of motion-c.qa.ts (no sprite on the base canvas in a static pass)'),
  BASE_STROKES_IN_A_STATIC_FRAME: b('BASE_STROKES_IN_A_STATIC_FRAME', 643, 'either',
    'stroke() calls on the BASE canvas in the one static pass of DRAW_COMMANDS_IN_A_STATIC_FRAME (100 nodes, 348 trust lines, quality low, no GPU): 348 lines + 100 node outlines + 95 crisp rims of idle non-frozen nodes + 100 sign glyphs. Criterion 8 of spec 039 asked for 20 or fewer; that part was withdrawn with edge batching (decision 1 of "Замер стоимости кадра"). Higher = more is stroked a frame; lower = something is no longer stroked - or drawn cheaper, then record it',
    '2026-10-18: 643 - first record (039 slice C; the amendment of criterion 8 of 2026-10-18 in spec 039). Before slice C the same pass stroked 548 (no rim strokes: the rims were glow sprites); the move 548 -> 643 is a record of behaviour - idle nodes are outlined by a stroke instead of two drawImage sprites - not a regression in time (the frame got faster: spec 039, Changelog 2026-10-18)'),
  BASE_DRAW_IMAGES_IN_A_STATIC_FRAME: b('BASE_DRAW_IMAGES_IN_A_STATIC_FRAME', 0, 'higher',
    'drawImage() calls (glow sprites) on the BASE canvas in the same one static pass: nothing is selected or highlighted, so no node glows (039 slice C, glow only on active nodes). Higher = idle nodes glow again (the most expensive part of the no-GPU frame); lower is impossible',
    '2026-10-18: 0 - first record (039 slice C). Before slice C the same pass drew 195 sprites (bloom of 100 nodes, rim of 95), all composited with screen'),
  DRAW_COMMANDS_FOR_ONE_PAYMENT_FROM_SLEEP: b('DRAW_COMMANDS_FOR_ONE_PAYMENT_FROM_SLEEP', 60485, 'either',
    'drawing calls on the on-screen canvases over the passes of DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP',
    '2026-10-11: 124238 - first record (038 S3, B2); the same in 3 runs on the dev server and 3 on the production build. '
    + '2026-10-12: 124238 -> 108238 (-16000 = 128 passes x 125): 039 slice B, PR 1, the same 125 calls per pass as DRAW_COMMANDS_IN_A_STATIC_FRAME. '
    + '2026-10-18: 108238 -> 60485 (039 slice C): 81 passes instead of 128 (DRAWS_FOR_ONE_PAYMENT_FROM_SLEEP) at 100 calls fewer each (DRAW_COMMANDS_IN_A_STATIC_FRAME); the beam of the low quality draws its trail stroke'),
  DRAW_COMMANDS_IN_ONE_PERIOD_OF_LOAD: b('DRAW_COMMANDS_IN_ONE_PERIOD_OF_LOAD', 187882, 'either',
    'drawing calls on the on-screen canvases over the 225 passes of one period of S3-MIXED-V1',
    '2026-10-11: 239802 - first record (038 S3, B2); the same in 3 runs on the dev server and 3 on the production build, and in the second (warm) period of each. '
    + '2026-10-12: 239802 -> 211677 (-28125 = 225 passes x 125): 039 slice B, PR 1, the same 125 calls per pass as DRAW_COMMANDS_IN_A_STATIC_FRAME. '
    + '2026-10-18: 211677 -> 187882 (-23795, 039 slice C): the idle nodes draw no glow sprites (-100 a pass, -22500 over 225 passes); the rest is the changed effects - the clearing of the period draws a spark round its cycles and no longer 40 bursts with shockwaves at once, and a beam of the low quality draws its trail stroke'),
  OFFSCREEN_SHADOW_BLUR_SETS_IN_A_WARM_PERIOD: b('OFFSCREEN_SHADOW_BLUR_SETS_IN_A_WARM_PERIOD', 0, 'higher',
    'assignments of a non-zero shadowBlur to an off-screen canvas (glow sprite baking, render/glowSprites.ts) during a period of the load that the page has already drawn once: the sprite cache holds. Higher = sprites are baked again while drawing',
    '2026-10-11: 0 - first record (038 S3, B3)'),
  PEAK_SPARKS_IN_ONE_PERIOD: b('PEAK_SPARKS_IN_ONE_PERIOD', 7, 'either',
    'most sparks alive at once during one period of S3-MIXED-V1, sampled every 150 ms. It is what the limiter of useRealTxFx.ts admits (3 beams/s, burst 2; a beam is one spark at a time) times how long a beam lives. Lower with the same load = beams are no longer shown; higher = the limiter was loosened or beams live longer',
    '2026-10-11: 5 - first record (038 S3). '
    + '2026-10-18: 5 -> 7 (039 slice C): the limiter is unchanged; a beam of a 3- or 6-edge route now lives as long as its legs (at least 450 ms each, then a fade) instead of 1200 ms, so more admitted beams are in flight at a sample, and the clearing of the period adds its spark'),
  PEAK_EDGE_PULSES_IN_ONE_PERIOD: b('PEAK_EDGE_PULSES_IN_ONE_PERIOD', 20, 'either',
    'most edge pulses alive at once during one period of S3-MIXED-V1: the 20 edges of its one clearing',
    '2026-10-11: 20 - first record (038 S3)'),
  PEAK_NODE_BURSTS_IN_ONE_PERIOD: b('PEAK_NODE_BURSTS_IN_ONE_PERIOD', 5, 'either',
    'most node bursts alive at once during one period of S3-MIXED-V1 (senders, intermediaries and receivers of admitted beams; the nodes of a clearing cycle as its spark passes them)',
    '2026-10-11: 35 - first record (038 S3). '
    + '2026-10-18: 35 -> 5 (039 slice C): a clearing no longer bursts every node of its lines at the first instant (up to 40, bloom and shockwave with screen composite) - a node of a cycle lights up as the spark passes it; the 30 lines of this load are a sample of the scene, not cycles, so few of them are'),
  PEAK_LABELS_IN_ONE_PERIOD: b('PEAK_LABELS_IN_ONE_PERIOD', 26, 'either',
    'most amount labels mounted at once during one period of S3-MIXED-V1, sampled every 150 ms',
    '2026-10-11: 24 - first record (038 S3). '
    + '2026-10-18: 24 -> 26 (039 slice C, R2): a label lives as long as its CSS fade (2600 ms) instead of 1900 ms, and a receiver label comes when its beam arrives'),
  LIVE_FX_AFTER_THE_STREAM: b('LIVE_FX_AFTER_THE_STREAM', 0, 'higher',
    'sparks + edge pulses + node bursts still in FX state 12 s after the last event of the stream (a leak)',
    '2026-10-11: 0 - first record (038 S3)'),
  LIVE_LABELS_AFTER_THE_STREAM: b('LIVE_LABELS_AFTER_THE_STREAM', 0, 'higher',
    'amount labels still mounted 8-12 s after the last event (the longest label lives 4200 ms - its CSS animation - after the 1.2 s loop of a clearing)',
    '2026-10-11: 0 - first record (038 S3)'),
  LIVE_LABELS_CAP: b('LIVE_LABELS_CAP', 120, 'either',
    'most amount labels mounted at once under a flood that asks for more: the product cap MAX_FLOATING_LABELS (useOverlayState.ts). Higher = the cap is gone or raised (more DOM under load); lower = the cap was lowered - record it',
    '2026-10-11: 120 - first record (038 S3)'),
  MAX_PARTICLES_AT_60_FPS: b('MAX_PARTICLES_AT_60_FPS', 300, 'either',
    'DERIVED, not an independent budget: the particle cap (__maxParticles) at 60 fps is the declared limits.max_particles of the snapshot read back unscaled. Kept as the reference point the low-FPS cap is compared with',
    '2026-10-11: 300 - first record (038 S3)'),
  FX_BUDGET_SCALE_X1000_AT_SUSTAINED_10_FPS: b('FX_BUDGET_SCALE_X1000_AT_SUSTAINED_10_FPS', 450, 'either',
    'fxBudgetScale x 1000 after 8 s at 10 fps: the lowest step of FX_BUDGET_SCALE_TARGETS (useRenderLoop.ts). Higher = the budget does not fall on a slow machine; lower = effects are cut harder than recorded',
    '2026-10-11: 450 - first record (038 S3); S0 saw 0.45 on wall clock at CPU x4'),
  MAX_PARTICLES_AT_SUSTAINED_10_FPS: b('MAX_PARTICLES_AT_SUSTAINED_10_FPS', 135, 'either',
    'DERIVED, not an independent budget: the particle cap after 8 s at 10 fps is floor(300 x 0.45), a consequence of FX_BUDGET_SCALE_X1000_AT_SUSTAINED_10_FPS and the declared limit. Kept because it is the number FRAMES_WITH_LIVE_FX_ABOVE_THE_CAP is judged against',
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
  NODE_LABELS_WITH_CSS_FILTER_NO_GPU: b('NODE_LABELS_WITH_CSS_FILTER_NO_GPU', 0, 'higher',
    'node labels with a computed CSS filter (drop-shadow) when Labels = Neighbors and the hub is selected (28 labels, the cap of useLabelNodes.ts), no GPU. With a GPU the filter stays - held by the counter-test in no-gpu-effects.qaperf.ts',
    '2026-10-11: 0 - first record (038 S3) together with the gate by data-gpu=0 in src/App.css; before the gate the same test read 28'),
  ELEMENTS_WITH_BACKDROP_FILTER_NO_GPU: b('ELEMENTS_WITH_BACKDROP_FILTER_NO_GPU', 0, 'higher',
    'elements with a computed backdrop-filter under load, no window open, quality low, no GPU (policy 5: CSS gating)',
    '2026-10-11: 0 - first record (038 S3)'),
} as const satisfies Record<string, Budget>

/**
 * Elements with a computed CSS `filter` or `backdrop-filter` without a GPU, by state of the page
 * (`no-gpu-effects.qa.ts`). The whole list is the budget: expected empty in every state.
 * An entry here is a recorded debt and needs a dated line with its measured cost.
 * 2026-10-11: first record (038 S3).
 */
export const CSS_EFFECTS_NO_GPU: Record<string, string[]> = {
  'the named load': [],
  'Labels = Neighbors, hub selected': [],
  'node card': [],
  'edge tooltip': [],
  'payment panel': [],
  'node being dragged': [],
}
