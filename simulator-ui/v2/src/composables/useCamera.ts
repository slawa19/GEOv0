import { reactive } from 'vue'

export type LayoutNodeLike = { __x: number; __y: number }

type UseCameraDeps<N extends LayoutNodeLike> = {
  canvasEl: { value: HTMLCanvasElement | null }
  hostEl: { value: HTMLElement | null }

  getLayoutNodes: () => N[]
  getLayoutW: () => number
  getLayoutH: () => number

  isTestMode: () => boolean
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

type RectLike = { left: number; top: number }

function getHostRect(host: HTMLElement): RectLike {
  const r = host.getBoundingClientRect()
  return { left: r.left, top: r.top }
}

export function useCamera<N extends LayoutNodeLike>(deps: UseCameraDeps<N>) {
  const camera = reactive({
    panX: 0,
    panY: 0,
    zoom: 1,
  })

  const panState = reactive({
    active: false,
    pointerId: -1,
    startClientX: 0,
    startClientY: 0,
    startPanX: 0,
    startPanY: 0,
    moved: false,
  })

  const wheelState = reactive({
    pendingDeltaY: 0,
    lastSx: 0,
    lastSy: 0,
    rafId: null as ReturnType<typeof setTimeout> | number | null,
  })

  function resetCamera() {
    camera.panX = 0
    camera.panY = 0
    camera.zoom = 1
  }

  function getWorldBounds() {
    const nodes = deps.getLayoutNodes()
    if (nodes.length === 0) return null

    let minX = Infinity
    let minY = Infinity
    let maxX = -Infinity
    let maxY = -Infinity

    for (const n of nodes) {
      if (n.__x < minX) minX = n.__x
      if (n.__x > maxX) maxX = n.__x
      if (n.__y < minY) minY = n.__y
      if (n.__y > maxY) maxY = n.__y
    }

    if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) return null
    return { minX, minY, maxX, maxY }
  }

  function clampCameraPan() {
    if (deps.isTestMode()) return
    const bounds = getWorldBounds()
    if (!bounds) return

    const padPx = 80
    const z = clamp(camera.zoom, 0.2, 10)

    const worldW = Math.max(1, bounds.maxX - bounds.minX)
    const worldH = Math.max(1, bounds.maxY - bounds.minY)
    const contentW = worldW * z
    const contentH = worldH * z

    const layoutW = deps.getLayoutW()
    const layoutH = deps.getLayoutH()

    // If the content is smaller than the viewport, center it (still respecting pad).
    if (contentW <= layoutW - padPx * 2) {
      camera.panX = (layoutW - contentW) / 2 - bounds.minX * z
    } else {
      const minPanX = (layoutW - padPx) - bounds.maxX * z
      const maxPanX = padPx - bounds.minX * z
      camera.panX = clamp(camera.panX, minPanX, maxPanX)
    }

    if (contentH <= layoutH - padPx * 2) {
      camera.panY = (layoutH - contentH) / 2 - bounds.minY * z
    } else {
      const minPanY = (layoutH - padPx) - bounds.maxY * z
      const maxPanY = padPx - bounds.minY * z
      camera.panY = clamp(camera.panY, minPanY, maxPanY)
    }
  }

  function worldToScreen(x: number, y: number) {
    return {
      x: x * camera.zoom + camera.panX,
      y: y * camera.zoom + camera.panY,
    }
  }

  function screenToWorld(x: number, y: number) {
    return {
      x: (x - camera.panX) / camera.zoom,
      y: (y - camera.panY) / camera.zoom,
    }
  }

  function worldToCssTranslate(x: number, y: number) {
    const p = worldToScreen(x, y)
    const scale = clamp(1 / Math.max(0.01, camera.zoom), 0.75, 1.25)
    if (deps.isTestMode() || Math.abs(scale - 1) < 1e-3) return `translate3d(${p.x}px, ${p.y}px, 0)`
    return `translate3d(${p.x}px, ${p.y}px, 0) scale(${scale})`
  }

  function clientToScreen(clientX: number, clientY: number) {
    const host = deps.hostEl.value
    if (!host) return { x: 0, y: 0 }
    const rect = getHostRect(host)
    return { x: clientX - rect.left, y: clientY - rect.top }
  }

  function onPointerDown(ev: PointerEvent) {
    if (deps.isTestMode()) return

    const canvas = deps.canvasEl.value
    if (!canvas) return

    panState.active = true
    panState.pointerId = ev.pointerId
    panState.startClientX = ev.clientX
    panState.startClientY = ev.clientY
    panState.startPanX = camera.panX
    panState.startPanY = camera.panY
    panState.moved = false

    try {
      canvas.setPointerCapture(ev.pointerId)
    } catch {
      // ignore
    }
  }

  function onPointerMove(ev: PointerEvent) {
    if (!panState.active) return
    if (ev.pointerId !== panState.pointerId) return

    const dx = ev.clientX - panState.startClientX
    const dy = ev.clientY - panState.startClientY
    if (!panState.moved && dx * dx + dy * dy >= 9) panState.moved = true

    camera.panX = panState.startPanX + dx
    camera.panY = panState.startPanY + dy
    clampCameraPan()
  }

  function onPointerUp(ev: PointerEvent) {
    if (!panState.active) return false
    if (ev.pointerId !== panState.pointerId) return false

    panState.active = false
    panState.pointerId = -1

    // Returns true if it was a click (no pan).
    return !panState.moved
  }

  function onWheel(ev: WheelEvent) {
    if (deps.isTestMode()) return

    const s = clientToScreen(ev.clientX, ev.clientY)
    wheelState.lastSx = s.x
    wheelState.lastSy = s.y
    wheelState.pendingDeltaY += ev.deltaY

    if (wheelState.rafId !== null) return

    const raf: (cb: (t: number) => void) => ReturnType<typeof setTimeout> | number =
      typeof requestAnimationFrame !== 'undefined'
        ? requestAnimationFrame
        : (cb) => setTimeout(() => cb(performance.now()), 0)

    wheelState.rafId = raf(() => {
      wheelState.rafId = null

      const dy = wheelState.pendingDeltaY
      wheelState.pendingDeltaY = 0

      const sx = wheelState.lastSx
      const sy = wheelState.lastSy
      const before = screenToWorld(sx, sy)

      const k = Math.exp(-dy * 0.001)
      const nextZoom = clamp(camera.zoom * k, 0.4, 3.0)
      if (nextZoom === camera.zoom) return

      camera.zoom = nextZoom
      camera.panX = sx - before.x * camera.zoom
      camera.panY = sy - before.y * camera.zoom
      clampCameraPan()
    })
  }

  return {
    camera,
    panState,
    wheelState,
    resetCamera,
    getWorldBounds,
    clampCameraPan,
    worldToScreen,
    screenToWorld,
    worldToCssTranslate,
    clientToScreen,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onWheel,
  }
}
