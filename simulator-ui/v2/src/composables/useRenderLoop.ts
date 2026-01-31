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

  function clamp01(v: number) {
    return Math.max(0, Math.min(1, v))
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
    const dpr = canvas.width / Math.max(1, layout.w)
    const renderQuality: Quality = deps.isTestMode() ? 'high' : deps.getQuality()

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

    // Optional hard cap for long-running sessions (reserved in spec).
    const maxParticles = snap.limits?.max_particles
    if (typeof maxParticles === 'number' && Number.isFinite(maxParticles)) {
      pruneFxToMaxParticles(maxParticles)
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
