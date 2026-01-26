import type { Ref } from 'vue'

type Quality = 'low' | 'med' | 'high'

type UseRenderLoopDeps = {
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>

  getSnapshot: () => { palette?: any } | null
  getLayout: () => { w: number; h: number; nodes: any[]; links: any[] }
  getCamera: () => { panX: number; panY: number; zoom: number }

  isTestMode: () => boolean
  getQuality: () => Quality

  getFlash: () => number
  setFlash: (v: number) => void

  pruneFloatingLabels: (nowMs: number) => void

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
}

type UseRenderLoopReturn = {
  ensureRenderLoop: () => void
  stopRenderLoop: () => void
  renderOnce: (nowMs?: number) => void
}

export function useRenderLoop(deps: UseRenderLoopDeps): UseRenderLoopReturn {
  let rafId: number | null = null

  let lastCanvas: HTMLCanvasElement | null = null
  let lastFxCanvas: HTMLCanvasElement | null = null
  let cachedCtx: CanvasRenderingContext2D | null = null
  let cachedFx: CanvasRenderingContext2D | null = null

  function clamp01(v: number) {
    return Math.max(0, Math.min(1, v))
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

    deps.pruneFloatingLabels(nowMs)

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

  function ensureRenderLoop() {
    if (rafId !== null) return
    const loop = (t: number) => {
      renderFrame(t)
      rafId = window.requestAnimationFrame(loop)
    }
    rafId = window.requestAnimationFrame(loop)
  }

  function stopRenderLoop() {
    if (rafId !== null) {
      window.cancelAnimationFrame(rafId)
      rafId = null
    }
  }

  function renderOnce(nowMs?: number) {
    const t =
      typeof nowMs === 'number'
        ? nowMs
        : typeof performance !== 'undefined'
          ? performance.now()
          : Date.now()
    renderFrame(t)
  }

  return { ensureRenderLoop, stopRenderLoop, renderOnce }
}
