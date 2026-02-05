import type { Ref } from 'vue'

import type { GraphSnapshot } from '../types'

type Quality = 'low' | 'med' | 'high'

export function __snapshotKeyForRenderLoop(snap: GraphSnapshot): string {
  // Be defensive: tests and some call sites may pass partial snapshots.
  const eq = String((snap as any)?.equivalent ?? '')
  const ts = String((snap as any)?.generated_at ?? '')
  return `${eq}|${ts}`
}

export function __shouldClearCachedPosOnSnapshotChange(opts: {
  cachedPos: Map<string, any>
  snapshotNodes: Array<{ id: string }>
}): boolean {
  const { cachedPos, snapshotNodes } = opts

  // Nothing cached: nothing to clear.
  if (cachedPos.size === 0) return false

  const nodeCount = snapshotNodes.length

  // Empty graph should not retain old positions.
  if (nodeCount === 0) return true

  // Detect a full graph change by overlap between current node IDs and cached IDs.
  let overlap = 0
  for (const n of snapshotNodes) {
    if (cachedPos.has(n.id)) overlap++
  }

  // No intersection => definitely a different graph.
  if (overlap === 0) return true

  // Low overlap => likely a different scene/snapshot (avoid keeping a growing union).
  const denom = Math.min(cachedPos.size, nodeCount)
  const overlapRatio = overlap / Math.max(1, denom)
  return overlapRatio < 0.2
}

export function __pruneCachedPosToSnapshotNodes(opts: {
  cachedPos: Map<string, any>
  snapshotNodes: Array<{ id: string }>
}) {
  const { cachedPos, snapshotNodes } = opts
  if (cachedPos.size === 0) return

  const currentIds = new Set<string>()
  for (const n of snapshotNodes) currentIds.add(n.id)

  for (const id of cachedPos.keys()) {
    if (!currentIds.has(id)) cachedPos.delete(id)
  }
}

/**
 * Indirection layer to make cachedPos hygiene observable in tests (spy-able) while
 * keeping the render-loop hot-path simple.
 */
export const __cachedPosHygiene = {
  shouldClearOnSnapshotChange: __shouldClearCachedPosOnSnapshotChange,
  pruneToSnapshotNodes: __pruneCachedPosToSnapshotNodes,
} as const

function __nodesSignatureForCachedPosHygiene(snapshotNodes: Array<{ id: string }>): string {
  // Cheap O(1) heuristic to detect node set changes even when snapshotKey is stable
  // (e.g. real-mode patches that keep `generated_at`).
  const len = snapshotNodes.length
  if (len <= 0) return '0'

  const first = String(snapshotNodes[0]?.id ?? '')
  const second = len > 1 ? String(snapshotNodes[1]?.id ?? '') : ''
  const mid = String(snapshotNodes[(len / 2) | 0]?.id ?? '')
  const penultimate = len > 1 ? String(snapshotNodes[len - 2]?.id ?? '') : ''
  const last = String(snapshotNodes[len - 1]?.id ?? '')

  // Intentionally do not include all IDs (would be O(n)).
  return `${len}|${first}|${second}|${mid}|${penultimate}|${last}`
}

type UseRenderLoopDeps = {
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>

  getSnapshot: () => GraphSnapshot | null
  getLayout: () => { w: number; h: number; nodes: any[]; links: any[] }
  getCamera: () => { panX: number; panY: number; zoom: number }

  isTestMode: () => boolean
  getQuality: () => Quality

  getFlash: () => number
  setFlash: (v: number) => void

  pruneFloatingLabels: (nowMs: number) => void

  // Optional: keep overlay sets bounded over long sessions.
  pruneActiveEdges?: (nowMs: number) => void

  drawBaseGraph: (ctx: CanvasRenderingContext2D, opts: any) => any
  renderFxFrame: (opts: any) => void
  mapping: any
  fxState: any

  getSelectedNodeId: () => string | null
  activeEdges: Set<string>

  // Optional: reduce link drawing cost (used during drag).
  getLinkLod?: () => 'full' | 'focus'

  // Optional: hide a node from canvas rendering (used for DOM drag preview).
  getHiddenNodeId?: () => string | null

  // Optional: run per-frame updates before drawing (e.g. live physics).
  beforeDraw?: (nowMs: number) => void

  // Optional: hint whether the scene is actively animating (physics, pan/zoom, demo playback).
  // When omitted, a conservative heuristic based on FX + flash is used.
  isAnimating?: () => boolean

  // Optional: hint that the browser is in software-only rendering mode.
  // Used to pick cheaper rendering paths that preserve aesthetics.
  isSoftwareMode?: () => boolean
}

type UseRenderLoopReturn = {
  ensureRenderLoop: () => void
  stopRenderLoop: () => void
  renderOnce: (nowMs?: number) => void
  /** Wake up from deep idle mode. Call on user interaction or animation events. */
  wakeUp: () => void
}

// Deep idle: stop render loop completely after this delay (ms) with no activity.
const DEEP_IDLE_DELAY_MS = 3000

// Adaptive performance tuning constants.
const ADAPTIVE_PERF = {
  // FPS sampling window in milliseconds.
  sampleWindowMs: 900,

  // Grace period before resetting quality after activity ends (avoids instant flip-back).
  // Keep long enough that physics stop → restart cycles don't cause visible quality flicker.
  qualityResetDelayMs: 2000,

  // Warmup period after activity starts before allowing quality downgrade.
  // This prevents short FX bursts from triggering quality changes.
  // Keep long enough to cover physics stabilization (~1.5-2s).
  warmupMs: 2000,

  // How long to keep full-speed rendering after activity ends.
  holdActiveMs: 250,

  // Consecutive bad samples required before downgrading quality/DPR.
  downgradeStreak: 2,

  // Consecutive good samples required before upgrading quality/DPR.
  upgradeStreak: 3,

  // Idle rendering rate when no activity.
  idleFps: 4,

  // FPS thresholds for quality downgrade decisions.
  fps: {
    criticalLow: 18,   // Always drop to 'low'
    lowFromHigh: 26,   // Drop high → low
    medFromHigh: 34,   // Drop high → med
    lowFromMed: 24,    // Drop med → low
    dprCritical: 20,   // Drop DPR to 1
    dprModerate: 28,   // Drop DPR to 1.25
    upgradeHigh: 48,   // Upgrade to high quality
    upgradeMed: 42,    // Upgrade to med quality
    upgradeDprHigh: 50,
    upgradeDprMed: 44,
  },

  // Budget scale targets based on FPS.
  budgetScaleTargets: [
    { fps: 22, scale: 0.45 },
    { fps: 28, scale: 0.6 },
    { fps: 34, scale: 0.72 },
    { fps: 44, scale: 0.86 },
  ],

  // Exponential smoothing factor for budget scale.
  budgetSmoothingFactor: 0.8,
} as const

export function useRenderLoop(deps: UseRenderLoopDeps): UseRenderLoopReturn {
  let rafId: number | null = null
  let timeoutId: number | null = null

  let running = false

  let lastActiveAtMs = 0

  // Deep idle state: when true, the render loop is completely stopped.
  let deepIdle = false
  let lastActivityTime = 0

  let lastCanvas: HTMLCanvasElement | null = null
  let lastFxCanvas: HTMLCanvasElement | null = null
  let cachedCtx: CanvasRenderingContext2D | null = null
  let cachedFx: CanvasRenderingContext2D | null = null

  // Hot-path cache: avoid per-frame allocations for node lookup in render.
  const cachedPos = new Map<string, any>()

  // Snapshot identity tracking for cache hygiene.
  // `cachedPos` can otherwise grow unbounded across full scene changes.
  let lastSnapshotKey: string | null = null

  // Additional cheap composition detectors for patch scenarios where `generated_at` doesn't change.
  // Keep as O(1) checks on most frames; run O(n) prune/clear only when a change is detected.
  let lastSnapshotNodesSig: string | null = null
  let lastSnapshotLinksCount: number | null = null

  // Adaptive FX budgeting (scenario playback on med/high can overwhelm Chrome).
  // We measure FPS only while the scene is animating and scale the particle cap.
  let fpsSampleStartedAtMs = 0
  let fpsSampleFrames = 0
  let fxBudgetScale = 1
  let lastFps = 60

  // Adaptive render quality: when Chrome collapses on Med/High, we temporarily render
  // with cheaper quality settings (disables blur/gradients) without changing user prefs.
  let adaptiveRenderQuality: Quality | null = null
  let qualityUpgradeStreak = 0
  let qualityDowngradeStreak = 0 // Require multiple bad samples before downgrading

  // Adaptive DPR clamp: when fill-rate dominates (common in Chrome with blur/compositing),
  // reducing canvas resolution can be a much stronger lever than just lowering FX budgets.
  let adaptiveDprClamp: number | null = null
  let dprUpgradeStreak = 0
  let dprDowngradeStreak = 0 // Require multiple bad samples before downgrading

  // Track when activity ended to delay quality reset (avoid instant flip-back).
  let activityEndedAtMs = 0

  // Track when activity started (for warmup period — don't downgrade quality immediately).
  let activityStartedAtMs = 0

  function clamp01(v: number) {
    return Math.max(0, Math.min(1, v))
  }

  function baseDprClampForQuality(q: Quality): number {
    if (q === 'low') return 1
    if (q === 'med') return 1.5
    return 2
  }

  function ensureCanvasDpr(layoutW: number, layoutH: number, desiredDpr: number) {
    const canvas = deps.canvasEl.value
    const fxCanvas = deps.fxCanvasEl.value
    if (!canvas || !fxCanvas) return

    const dpr = Math.max(0.5, Math.min(4, desiredDpr))
    const pxW = Math.max(1, Math.floor(layoutW * dpr))
    const pxH = Math.max(1, Math.floor(layoutH * dpr))

    if (canvas.width !== pxW) canvas.width = pxW
    if (canvas.height !== pxH) canvas.height = pxH

    if (fxCanvas.width !== canvas.width) fxCanvas.width = canvas.width
    if (fxCanvas.height !== canvas.height) fxCanvas.height = canvas.height
  }

  function pruneFxToMaxParticles(maxParticles: number) {
    const fxState = deps.fxState as any
    if (!fxState) return

    const sparks = Array.isArray(fxState.sparks) ? fxState.sparks : null
    const edgePulses = Array.isArray(fxState.edgePulses) ? fxState.edgePulses : null
    const nodeBursts = Array.isArray(fxState.nodeBursts) ? fxState.nodeBursts : null
    if (!sparks && !edgePulses && !nodeBursts) return

    const max = Math.max(0, Math.floor(maxParticles))
    const total = (sparks?.length ?? 0) + (edgePulses?.length ?? 0) + (nodeBursts?.length ?? 0)
    if (total <= max) return

    let overflow = total - max

    const dropFront = (arr: any[] | null) => {
      if (!arr || overflow <= 0) return
      const d = Math.min(arr.length, overflow)
      if (d > 0) {
        // Cheaper than splice(0,d) for large arrays: no element-by-element shifting via splice.
        arr.copyWithin(0, d)
        arr.length = arr.length - d
      }
      overflow -= d
    }

    // Prefer dropping oldest sparks first — they are the most numerous.
    dropFront(sparks)
    dropFront(edgePulses)
    dropFront(nodeBursts)
  }

  function renderFrame(nowMs: number) {
    const canvas = deps.canvasEl.value
    const fxCanvas = deps.fxCanvasEl.value
    const layout = deps.getLayout()
    const snap = deps.getSnapshot()

    if (!canvas || !fxCanvas || !snap) return

    // `cachedPos` can retain node IDs across full scene changes if the render loop persists.
    // Primary change detector is snapshot identity (O(1) on most frames).
    // Additionally, in real-mode patch flows `generated_at` can stay stable while node composition
    // changes — detect that cheaply and prune once per change.
    const key = __snapshotKeyForRenderLoop(snap)
    const snapshotNodes = Array.isArray((snap as any).nodes) ? ((snap as any).nodes as Array<{ id: string }>) : []
    const linksCount = Array.isArray((snap as any).links) ? ((snap as any).links as unknown[]).length : 0
    const nodesSig = __nodesSignatureForCachedPosHygiene(snapshotNodes)

    const keyChanged = lastSnapshotKey !== key
    const compositionChanged =
      !keyChanged && (lastSnapshotNodesSig !== nodesSig || lastSnapshotLinksCount !== linksCount)

    if (keyChanged || compositionChanged) {
      if (
        __cachedPosHygiene.shouldClearOnSnapshotChange({
          cachedPos,
          snapshotNodes,
        })
      ) {
        cachedPos.clear()
      } else {
        // Keep animations stable for unchanged nodes, but drop stale IDs so the cache stays bounded.
        __cachedPosHygiene.pruneToSnapshotNodes({ cachedPos, snapshotNodes })
      }

      lastSnapshotKey = key
      lastSnapshotNodesSig = nodesSig
      lastSnapshotLinksCount = linksCount
    }

    if (canvas !== lastCanvas) {
      lastCanvas = canvas
      cachedCtx = canvas.getContext('2d')
    }
    if (fxCanvas !== lastFxCanvas) {
      lastFxCanvas = fxCanvas
      cachedFx = fxCanvas.getContext('2d')
    }
    const ctx = cachedCtx
    const fx = cachedFx
    if (!ctx || !fx) return

    const camera = deps.getCamera()
    const userQuality: Quality = deps.isTestMode() ? 'high' : deps.getQuality()
    const softwareMode = !deps.isTestMode() && typeof deps.isSoftwareMode === 'function' ? !!deps.isSoftwareMode() : false

    const activeForPerf = isAnimatingNow()
    updateAdaptivePerf(nowMs, activeForPerf, userQuality)

    // Adaptive DPR downscaling: adjust canvas pixel resolution before computing dpr.
    // During grace period after activity ends, keep DPR stable to avoid canvas resize flicker.
    if (!deps.isTestMode()) {
      const win = typeof window !== 'undefined' ? window : (globalThis as any)
      const deviceDpr = Math.max(1, Number(win.devicePixelRatio ?? 1))
      const baseClamp = baseDprClampForQuality(userQuality)
      const clamp = adaptiveDprClamp !== null ? Math.min(baseClamp, adaptiveDprClamp) : baseClamp
      const desiredDpr = Math.min(deviceDpr, clamp)
      ensureCanvasDpr(layout.w, layout.h, desiredDpr)
      ;(deps.fxState as any).__dprClamp = clamp
    }

    const dpr = canvas.width / Math.max(1, layout.w)

    // Apply adaptive quality override if set — even during the grace period after activity ends.
    // This prevents the jarring "pop" when FX finish but quality instantly reverts.
    const renderQuality: Quality =
      deps.isTestMode() ? 'high' : adaptiveRenderQuality ?? userQuality

    // Clear in screen-space (pan/zoom must not affect clearing).
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, layout.w, layout.h)

    // Screen-space background fill (must happen before camera transform).
    ctx.fillStyle = '#020617'
    ctx.fillRect(0, 0, layout.w, layout.h)

    fx.setTransform(dpr, 0, 0, dpr, 0, 0)
    fx.clearRect(0, 0, layout.w, layout.h)

    // Screen-space flash overlay (clearing) — must not move with camera.
    const flash = deps.getFlash()
    if (flash > 0) {
      const t = clamp01(flash)
      fx.save()
      fx.globalAlpha = t
      const grad = fx.createRadialGradient(
        layout.w / 2,
        layout.h / 2,
        0,
        layout.w / 2,
        layout.h / 2,
        Math.max(layout.w, layout.h) * 0.7,
      )
      grad.addColorStop(0, deps.mapping.fx.flash.clearing.from)
      grad.addColorStop(1, deps.mapping.fx.flash.clearing.to)
      fx.fillStyle = grad
      fx.fillRect(0, 0, layout.w, layout.h)
      fx.restore()
      deps.setFlash(Math.max(0, flash - 0.03))
    }

    // Apply camera transform for drawing.
    ctx.translate(camera.panX, camera.panY)
    ctx.scale(camera.zoom, camera.zoom)
    fx.translate(camera.panX, camera.panY)
    fx.scale(camera.zoom, camera.zoom)

    if (deps.beforeDraw) deps.beforeDraw(nowMs)

    if (deps.pruneActiveEdges) deps.pruneActiveEdges(nowMs)
    deps.pruneFloatingLabels(nowMs)

    // FX hard cap:
    // - Prefer declared snapshot limits.
    // - Otherwise apply a quality-based default to keep demo playback bounded.
    //   (Without this, long/fast playlists can accumulate huge spark/pulse queues.)
    if (!deps.isTestMode()) {
      const declared = snap.limits?.max_particles
      const defaultMaxParticles = renderQuality === 'low' ? 120 : renderQuality === 'med' ? 180 : 220

      const baseMaxParticles =
        typeof declared === 'number' && Number.isFinite(declared) ? Math.max(0, Math.floor(declared)) : defaultMaxParticles

      // Only adapt while active; during idle (12fps throttle) we avoid reacting to low FPS.
      const scale = activeForPerf ? fxBudgetScale : 1
      const effectiveMaxParticles = Math.max(40, Math.floor(baseMaxParticles * scale))

      ;(deps.fxState as any).__maxParticles = effectiveMaxParticles
      ;(deps.fxState as any).__fxBudgetScale = scale
      ;(deps.fxState as any).__lastFps = lastFps
      ;(deps.fxState as any).__renderQuality = renderQuality
      pruneFxToMaxParticles(effectiveMaxParticles)
    }

    const linkLod = deps.getLinkLod ? deps.getLinkLod() : 'full'
    const pos = deps.drawBaseGraph(ctx, {
      w: layout.w,
      h: layout.h,
      nodes: layout.nodes,
      links: layout.links,
      mapping: deps.mapping,
      palette: snap.palette,
      selectedNodeId: deps.getSelectedNodeId(),
      activeEdges: deps.activeEdges,
      cameraZoom: camera.zoom,
      quality: renderQuality,
      softwareMode,
      linkLod,
      dragMode: linkLod === 'focus',
      hiddenNodeId: deps.getHiddenNodeId ? deps.getHiddenNodeId() : null,
      pos: cachedPos,
    })

    deps.renderFxFrame({
      nowMs,
      ctx: fx,
      pos,
      w: layout.w,
      h: layout.h,
      mapping: deps.mapping,
      fxState: deps.fxState,
      isTestMode: deps.isTestMode(),
      cameraZoom: camera.zoom,
      quality: renderQuality,
    })
  }

  function hasActiveFxOrOverlays() {
    if (deps.getFlash() > 0) return true
    if (deps.activeEdges && deps.activeEdges.size > 0) return true

    const fxState = deps.fxState as any
    const sparks = Array.isArray(fxState?.sparks) ? fxState.sparks.length : 0
    const edgePulses = Array.isArray(fxState?.edgePulses) ? fxState.edgePulses.length : 0
    const nodeBursts = Array.isArray(fxState?.nodeBursts) ? fxState.nodeBursts.length : 0
    return sparks + edgePulses + nodeBursts > 0
  }

  function isAnimatingNow() {
    if (deps.isTestMode()) return true
    try {
      if (typeof deps.isAnimating === 'function' && deps.isAnimating()) return true
    } catch {
      // ignore
    }
    return hasActiveFxOrOverlays()
  }

  function updateAdaptivePerf(nowMs: number, isActive: boolean, baseQuality: Quality) {
    // When activity ends, don't reset immediately — delay to avoid visual flicker.
    // The quality/DPR will stay at current levels for a grace period.
    const { qualityResetDelayMs, warmupMs, sampleWindowMs, downgradeStreak, upgradeStreak, fps: fpsThresholds, budgetScaleTargets, budgetSmoothingFactor } = ADAPTIVE_PERF

    if (!isActive) {
      // Track when activity ended.
      if (activityEndedAtMs === 0) {
        activityEndedAtMs = nowMs
      }

      // Reset sampling state but keep quality overrides until grace period passes.
      fpsSampleStartedAtMs = 0
      fpsSampleFrames = 0
      activityStartedAtMs = 0 // Reset warmup tracking

      const sinceStopped = nowMs - activityEndedAtMs
      if (sinceStopped >= qualityResetDelayMs) {
        // Grace period passed — now safe to reset quality overrides.
        qualityUpgradeStreak = 0
        qualityDowngradeStreak = 0
        adaptiveRenderQuality = null

        dprUpgradeStreak = 0
        dprDowngradeStreak = 0
        adaptiveDprClamp = null

        fxBudgetScale = 1
        activityEndedAtMs = 0
      }
      return
    }

    // Activity resumed — clear the ended timestamp and track start time.
    activityEndedAtMs = 0
    if (activityStartedAtMs === 0) {
      activityStartedAtMs = nowMs
    }

    // During warmup period, don't allow quality/DPR downgrades (short FX bursts shouldn't trigger changes).
    const inWarmup = nowMs - activityStartedAtMs < warmupMs

    if (fpsSampleStartedAtMs === 0) {
      fpsSampleStartedAtMs = nowMs
      fpsSampleFrames = 0
    }

    fpsSampleFrames++

    const elapsed = nowMs - fpsSampleStartedAtMs
    if (elapsed < sampleWindowMs) return

    const fps = (fpsSampleFrames * 1000) / Math.max(1, elapsed)
    lastFps = fps

    // 1) Budget scale (particle cap) — find the appropriate scale target.
    let targetScale = 1
    for (const target of budgetScaleTargets) {
      if (fps < target.fps) {
        targetScale = target.scale
        break
      }
    }
    fxBudgetScale = fxBudgetScale * budgetSmoothingFactor + targetScale * (1 - budgetSmoothingFactor)

    // 2) Render-quality override (hysteresis for BOTH downgrade and upgrade)
    // Require multiple consecutive bad samples before downgrading to avoid
    // single-frame FPS dips triggering visible quality changes.
    const pickDowngrade = (): Quality | null => {
      if (baseQuality === 'low') return null

      if (fps < fpsThresholds.criticalLow) return 'low'

      if (baseQuality === 'high') {
        if (fps < fpsThresholds.lowFromHigh) return 'low'
        if (fps < fpsThresholds.medFromHigh) return 'med'
        return null
      }

      // baseQuality === 'med'
      if (fps < fpsThresholds.lowFromMed) return 'low'
      return null
    }

    const wantedDowngrade = pickDowngrade()
    if (wantedDowngrade && !inWarmup) {
      // Only count downgrade streaks after warmup period.
      qualityDowngradeStreak++
      if (qualityDowngradeStreak >= downgradeStreak) {
        adaptiveRenderQuality = wantedDowngrade
        qualityUpgradeStreak = 0
      }
    } else if (!wantedDowngrade) {
      qualityDowngradeStreak = 0

      // Candidate for upgrade back to base quality.
      const upgradeFps = baseQuality === 'high' ? fpsThresholds.upgradeHigh : fpsThresholds.upgradeMed
      if (fps >= upgradeFps) qualityUpgradeStreak++
      else qualityUpgradeStreak = 0

      // Require a few consecutive good windows to avoid oscillation.
      if (qualityUpgradeStreak >= upgradeStreak) {
        adaptiveRenderQuality = null
        qualityUpgradeStreak = 0
      }
    }

    // 3) DPR clamp override (hysteresis for BOTH downgrade and upgrade)
    // Require multiple consecutive bad samples before downgrading DPR.
    const pickDprClamp = (): number | null => {
      if (baseQuality === 'low') return null
      if (fps < fpsThresholds.dprCritical) return 1
      if (fps < fpsThresholds.dprModerate) return 1.25
      return null
    }

    const wantedDpr = pickDprClamp()
    if (wantedDpr !== null && !inWarmup) {
      // Only count downgrade streaks after warmup period.
      dprDowngradeStreak++
      if (dprDowngradeStreak >= downgradeStreak) {
        adaptiveDprClamp = wantedDpr
        dprUpgradeStreak = 0
      }
    } else if (wantedDpr === null) {
      dprDowngradeStreak = 0

      const upgradeFps = baseQuality === 'high' ? fpsThresholds.upgradeDprHigh : fpsThresholds.upgradeDprMed
      if (fps >= upgradeFps) dprUpgradeStreak++
      else dprUpgradeStreak = 0

      if (dprUpgradeStreak >= upgradeStreak) {
        adaptiveDprClamp = null
        dprUpgradeStreak = 0
      }
    }

    fpsSampleStartedAtMs = nowMs
    fpsSampleFrames = 0
  }

  function scheduleNext(nowMs: number) {
    if (!running) return

    const win = typeof window !== 'undefined' ? window : (globalThis as any)
    const { holdActiveMs, idleFps } = ADAPTIVE_PERF

    // Keep full-speed rendering briefly after activity ends to avoid flicker.
    const active = isAnimatingNow()
    if (active) {
      lastActiveAtMs = nowMs
      lastActivityTime = nowMs
    }

    const inHold = nowMs - lastActiveAtMs < holdActiveMs
    if (active || inHold) {
      deepIdle = false
      rafId = win.requestAnimationFrame(loop)
      return
    }

    // Check for deep idle: if no activity for DEEP_IDLE_DELAY_MS, stop the loop completely.
    const timeSinceActivity = nowMs - lastActivityTime
    if (timeSinceActivity >= DEEP_IDLE_DELAY_MS) {
      deepIdle = true
      // Do NOT schedule next frame — loop stops completely.
      // Keep `running=true` to represent "loop is enabled" (started via ensureRenderLoop),
      // but with no scheduling pending. Any external event must call wakeUp()/ensureRenderLoop
      // to schedule the next frame.
      return
    }

    const idleDelayMs = Math.max(16, Math.floor(1000 / idleFps))

    timeoutId = win.setTimeout(() => {
      timeoutId = null
      rafId = win.requestAnimationFrame(loop)
    }, idleDelayMs)
  }

  const loop = (t: number) => {
    rafId = null
    renderFrame(t)
    scheduleNext(t)
  }

  function ensureRenderLoop() {
    if (rafId !== null || timeoutId !== null) return
    const win = typeof window !== 'undefined' ? window : (globalThis as any)
    running = true
    const nowMs = win.performance?.now?.() ?? Date.now()
    lastActiveAtMs = nowMs
    lastActivityTime = nowMs
    deepIdle = false
    rafId = win.requestAnimationFrame(loop)
  }

  function stopRenderLoop() {
    const win = typeof window !== 'undefined' ? window : (globalThis as any)
    if (rafId !== null) win.cancelAnimationFrame(rafId)
    if (timeoutId !== null) win.clearTimeout(timeoutId)
    rafId = null
    timeoutId = null
    running = false
    deepIdle = false
  }

  /**
   * Wake up from deep idle mode.
   * Call this when:
   * - Simulation starts
   * - Any animation event occurs
   * - User interacts with the canvas (mouse/touch)
   */
  function wakeUp() {
    const win = typeof window !== 'undefined' ? window : (globalThis as any)
    const nowMs = win.performance?.now?.() ?? Date.now()

    lastActivityTime = nowMs
    lastActiveAtMs = nowMs

    // If the loop isn't enabled (explicitly stopped via stopRenderLoop), don't restart it.
    if (!running) return

    // If a RAF is already queued, nothing to do.
    if (rafId !== null) return

    // If we're in idle-throttle mode (timeout scheduled), user activity should make
    // the UI responsive immediately: cancel the idle timeout and schedule a RAF.
    if (timeoutId !== null) {
      win.clearTimeout(timeoutId)
      timeoutId = null
    }

    // We may be in deep idle (no scheduling pending). Guarantee that a next frame is queued.
    // (Also works as a recovery if `deepIdle` got out-of-sync.)
    deepIdle = false
    rafId = win.requestAnimationFrame(loop)
  }

  function renderOnce(nowMs?: number) {
    const t =
      typeof nowMs === 'number'
        ? nowMs
        : typeof performance !== 'undefined'
          ? performance.now()
          : Date.now()
    renderFrame(t)

    // If the loop is running in idle-throttle mode, nudge it into the
    // short-lived active window so interactions feel responsive.
    lastActiveAtMs = t
    lastActivityTime = t

    // Wake up from deep idle if needed.
    wakeUp()
  }

  return { ensureRenderLoop, stopRenderLoop, renderOnce, wakeUp }
}
