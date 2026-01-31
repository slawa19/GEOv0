import type { Ref } from 'vue'

import type { GraphSnapshot } from '../types'

type Quality = 'low' | 'med' | 'high'

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
}

export function useRenderLoop(deps: UseRenderLoopDeps): UseRenderLoopReturn {
  let rafId: number | null = null
  let timeoutId: number | null = null

  let running = false

  let lastActiveAtMs = 0

  let lastCanvas: HTMLCanvasElement | null = null
  let lastFxCanvas: HTMLCanvasElement | null = null
  let cachedCtx: CanvasRenderingContext2D | null = null
  let cachedFx: CanvasRenderingContext2D | null = null

  // Hot-path cache: avoid per-frame allocations for node lookup in render.
  const cachedPos = new Map<string, any>()

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

  // Adaptive DPR clamp: when fill-rate dominates (common in Chrome with blur/compositing),
  // reducing canvas resolution can be a much stronger lever than just lowering FX budgets.
  let adaptiveDprClamp: number | null = null
  let dprUpgradeStreak = 0

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

    const renderQuality: Quality =
      deps.isTestMode() ? 'high' : activeForPerf && adaptiveRenderQuality ? adaptiveRenderQuality : userQuality

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
    if (!isActive) {
      fpsSampleStartedAtMs = 0
      fpsSampleFrames = 0
      qualityUpgradeStreak = 0
      adaptiveRenderQuality = null

      dprUpgradeStreak = 0
      adaptiveDprClamp = null

      fxBudgetScale = 1
      return
    }

    if (fpsSampleStartedAtMs === 0) {
      fpsSampleStartedAtMs = nowMs
      fpsSampleFrames = 0
    }

    fpsSampleFrames++

    const windowMs = 900
    const elapsed = nowMs - fpsSampleStartedAtMs
    if (elapsed < windowMs) return

    const fps = (fpsSampleFrames * 1000) / Math.max(1, elapsed)
    lastFps = fps

    // 1) Budget scale (particle cap)
    let targetScale = 1
    if (fps < 22) targetScale = 0.45
    else if (fps < 28) targetScale = 0.6
    else if (fps < 34) targetScale = 0.72
    else if (fps < 44) targetScale = 0.86
    fxBudgetScale = fxBudgetScale * 0.8 + targetScale * 0.2

    // 2) Render-quality override (hysteresis)
    // Downgrade quickly when FPS is bad; upgrade slowly when it stabilizes.
    const pickDowngrade = (): Quality | null => {
      if (baseQuality === 'low') return null

      if (fps < 18) return 'low'

      if (baseQuality === 'high') {
        if (fps < 26) return 'low'
        if (fps < 34) return 'med'
        return null
      }

      // baseQuality === 'med'
      if (fps < 24) return 'low'
      return null
    }

    const wantedDowngrade = pickDowngrade()
    if (wantedDowngrade) {
      adaptiveRenderQuality = wantedDowngrade
      qualityUpgradeStreak = 0
    } else {
      // Candidate for upgrade back to base quality.
      const upgradeFps = baseQuality === 'high' ? 48 : 42
      if (fps >= upgradeFps) qualityUpgradeStreak++
      else qualityUpgradeStreak = 0

      // Require a few consecutive good windows to avoid oscillation.
      if (qualityUpgradeStreak >= 3) {
        adaptiveRenderQuality = null
        qualityUpgradeStreak = 0
      }
    }

    // 3) DPR clamp override (hysteresis)
    // Heuristic: if FPS collapses while active, drop DPR quickly (fill-rate win),
    // then restore slowly once stable.
    const pickDprClamp = (): number | null => {
      if (baseQuality === 'low') return null
      if (fps < 20) return 1
      if (fps < 28) return 1.25
      return null
    }

    const wantedDpr = pickDprClamp()
    if (wantedDpr !== null) {
      adaptiveDprClamp = wantedDpr
      dprUpgradeStreak = 0
    } else {
      const upgradeFps = baseQuality === 'high' ? 50 : 44
      if (fps >= upgradeFps) dprUpgradeStreak++
      else dprUpgradeStreak = 0

      if (dprUpgradeStreak >= 3) {
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

    // Keep full-speed rendering briefly after activity ends to avoid flicker.
    const holdActiveMs = 250
    const active = isAnimatingNow()
    if (active) lastActiveAtMs = nowMs

    const inHold = nowMs - lastActiveAtMs < holdActiveMs
    if (active || inHold) {
      rafId = win.requestAnimationFrame(loop)
      return
    }

    const idleFps = 12
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
    lastActiveAtMs = win.performance?.now?.() ?? Date.now()
    rafId = win.requestAnimationFrame(loop)
  }

  function stopRenderLoop() {
    const win = typeof window !== 'undefined' ? window : (globalThis as any)
    if (rafId !== null) win.cancelAnimationFrame(rafId)
    if (timeoutId !== null) win.clearTimeout(timeoutId)
    rafId = null
    timeoutId = null
    running = false
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
  }

  return { ensureRenderLoop, stopRenderLoop, renderOnce }
}
