import { reactive } from 'vue'

import type { LayoutNodeLike } from '../types/layout'
import { clamp } from '../utils/math'

export type { LayoutNodeLike }

type UseCameraDeps<N extends LayoutNodeLike> = {
  canvasEl: { value: HTMLCanvasElement | null }
  hostEl: { value: HTMLElement | null }

  getLayoutNodes: () => N[]
  getLayoutW: () => number
  getLayoutH: () => number

  isTestMode: () => boolean

  /**
   * Optional: notify external wiring that camera state actually changed.
   * Intended to be called after applying a wheel/pan batch.
   */
  onCameraChanged?: () => void
}

type RectLike = { left: number; top: number }

/**
 * Interactive zoom range. `onWheel` and `focusOnEdge` share it so that framing an edge
 * can never leave the camera at a zoom the user could not have reached by scrolling.
 */
const ZOOM_MIN = 0.4
const ZOOM_MAX = 3.0

/**
 * Viewport padding kept free around content. Shared by pan clamping and by edge framing,
 * so a framed edge lands inside exactly the region the clamp is willing to keep.
 */
const CAMERA_PAD_PX = 80

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

    // Per-gesture locks: if everything is already visible, dragging the background
    // should not move the graph.
    lockPanX: false,
    lockPanY: false,
    lockedPanX: 0,
    lockedPanY: 0,
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

  function getCameraClampInfo() {
    const bounds = getWorldBounds()
    if (!bounds) return null

    // NOTE: `padPx` is used only for clamping when content is larger than the viewport.
    // When content fits the viewport, we lock panning and keep it centered.
    const padPx = CAMERA_PAD_PX
    const z = clamp(camera.zoom, 0.2, 10)

    const worldW = Math.max(1, bounds.maxX - bounds.minX)
    const worldH = Math.max(1, bounds.maxY - bounds.minY)
    const contentW = worldW * z
    const contentH = worldH * z

    const layoutW = deps.getLayoutW()
    const layoutH = deps.getLayoutH()

    const screenMinX = bounds.minX * z + camera.panX
    const screenMaxX = bounds.maxX * z + camera.panX
    const screenMinY = bounds.minY * z + camera.panY
    const screenMaxY = bounds.maxY * z + camera.panY

    // True when the entire world bounds are currently visible in the viewport.
    // This is different from `fitX/fitY` (which is size-only) and is used to decide
    // whether background dragging should be allowed at all.
    const epsPx = 0.5
    const fullyVisibleX = screenMinX >= -epsPx && screenMaxX <= layoutW + epsPx
    const fullyVisibleY = screenMinY >= -epsPx && screenMaxY <= layoutH + epsPx

    // If the content fully fits the viewport, lock panning on that axis.
    // This prevents the “jump” effect when users try to drag a fully visible graph.
    const fitX = contentW <= layoutW
    const fitY = contentH <= layoutH

    const centeredPanX = (layoutW - contentW) / 2 - bounds.minX * z
    const centeredPanY = (layoutH - contentH) / 2 - bounds.minY * z

    const minPanX = (layoutW - padPx) - bounds.maxX * z
    const maxPanX = padPx - bounds.minX * z
    const minPanY = (layoutH - padPx) - bounds.maxY * z
    const maxPanY = padPx - bounds.minY * z

    return {
      bounds,
      padPx,
      z,
      worldW,
      worldH,
      contentW,
      contentH,
      layoutW,
      layoutH,

      screenMinX,
      screenMaxX,
      screenMinY,
      screenMaxY,
      fullyVisibleX,
      fullyVisibleY,

      fitX,
      fitY,
      centeredPanX,
      centeredPanY,
      minPanX,
      maxPanX,
      minPanY,
      maxPanY,
    }
  }

  function clampCameraPan() {
    if (deps.isTestMode()) return
    const info = getCameraClampInfo()
    if (!info) return

    if (info.fitX) camera.panX = info.centeredPanX
    else camera.panX = clamp(camera.panX, info.minPanX, info.maxPanX)

    if (info.fitY) camera.panY = info.centeredPanY
    else camera.panY = clamp(camera.panY, info.minPanY, info.maxPanY)
  }

  /**
   * Point the camera at one edge, given both of its endpoints in layout space.
   *
   * An edge is a segment, not a point, so this fits the segment's bounds instead of
   * centering on one end: the camera ends up centered on the segment's midpoint at the
   * largest interactive zoom that still leaves both endpoints inside the padded viewport.
   * The result depends only on the two endpoints and the viewport size — no animation,
   * no easing, no dependence on the camera's previous position.
   *
   * Returns `true` when the camera was moved, `false` when it was not. `false` happens when
   * an endpoint is missing (or has non-finite coordinates): the caller asked for an edge the
   * current snapshot cannot place. That is a normal outcome — a snapshot can change between
   * the panel's poll and the click — so it is neither an exception nor a silent no-op: the
   * camera stays exactly where it was and the caller can see that focusing did not happen.
   *
   * Identity lives outside the camera: this composable only ever knows `__x`/`__y`, so
   * resolving "edge from → to" into two endpoints is the wiring's job (`useAppViewWiring`),
   * which is also the layer that knows whether the edge is in the snapshot at all.
   */
  function focusOnEdge(a: LayoutNodeLike | null | undefined, b: LayoutNodeLike | null | undefined): boolean {
    if (!a || !b) return false
    if (!Number.isFinite(a.__x) || !Number.isFinite(a.__y)) return false
    if (!Number.isFinite(b.__x) || !Number.isFinite(b.__y)) return false

    const minX = Math.min(a.__x, b.__x)
    const maxX = Math.max(a.__x, b.__x)
    const minY = Math.min(a.__y, b.__y)
    const maxY = Math.max(a.__y, b.__y)

    const layoutW = deps.getLayoutW()
    const layoutH = deps.getLayoutH()

    const availW = Math.max(1, layoutW - 2 * CAMERA_PAD_PX)
    const availH = Math.max(1, layoutH - 2 * CAMERA_PAD_PX)

    const spanX = maxX - minX
    const spanY = maxY - minY

    // A degenerate segment (both ends at the same point) has no span to fit on that axis,
    // so that axis asks for the maximum zoom instead of producing Infinity.
    const fitZoom = Math.min(spanX > 0 ? availW / spanX : ZOOM_MAX, spanY > 0 ? availH / spanY : ZOOM_MAX)

    camera.zoom = clamp(fitZoom, ZOOM_MIN, ZOOM_MAX)
    camera.panX = layoutW / 2 - ((minX + maxX) / 2) * camera.zoom
    camera.panY = layoutH / 2 - ((minY + maxY) / 2) * camera.zoom

    // Same post-step the wheel path uses: keep the camera inside its legal pan range,
    // then let the render loop wake up from deep-idle.
    clampCameraPan()
    deps.onCameraChanged?.()

    return true
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

    // Defensive reset: if we ever miss a pointerup (e.g. due to browser quirks),
    // we must not keep a stale active pan session alive.
    panState.active = false
    panState.pointerId = -1
    panState.moved = false
    panState.lockPanX = false
    panState.lockPanY = false

    // If the graph fits the viewport, do not start a pan gesture at all.
    // This ensures background dragging does not move the scene when nothing is off-screen.
    const info = getCameraClampInfo()
    if (info?.fullyVisibleX && info?.fullyVisibleY) return

    panState.active = true
    panState.pointerId = ev.pointerId
    panState.startClientX = ev.clientX
    panState.startClientY = ev.clientY
    panState.startPanX = camera.panX
    panState.startPanY = camera.panY
    panState.moved = false

    // Decide whether background dragging should be able to pan.
    // Lock axis if content fits that axis.
    panState.lockPanX = !!info?.fullyVisibleX
    panState.lockPanY = !!info?.fullyVisibleY
    panState.lockedPanX = camera.panX
    panState.lockedPanY = camera.panY

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
    if (!panState.moved && dx * dx + dy * dy >= 9) {
      // Transition into "pan" mode.
      // Important: don't apply the full delta from pointerdown on the same frame,
      // otherwise the first pan update can "jump" (and trigger clamp/centering).
      // Start panning from the current pointer position instead.
      panState.moved = true
      panState.startClientX = ev.clientX
      panState.startClientY = ev.clientY
      panState.startPanX = camera.panX
      panState.startPanY = camera.panY
      return
    }

    // Don't update camera pan until movement exceeds the drag threshold (3 px).
    // This prevents micro-jitter on click/dblclick from triggering clampCameraPan()
    // which can snap/center the graph when content fits the viewport (fitX/fitY).
    if (!panState.moved) return

    const info = getCameraClampInfo()

    // Lock background panning when the graph is already fully visible.
    // Keep the last "locked" pan values stable for the whole gesture.
    const desiredPanX = panState.lockPanX ? panState.lockedPanX : panState.startPanX + dx
    const desiredPanY = panState.lockPanY ? panState.lockedPanY : panState.startPanY + dy

    if (!info) {
      camera.panX = desiredPanX
      camera.panY = desiredPanY
      return
    }

    // IMPORTANT: when content fits, min/max pan bounds can be inverted; never clamp in that case.
    // Keep pan locked (stable) instead.
    camera.panX = info.fitX || panState.lockPanX ? panState.lockedPanX : clamp(desiredPanX, info.minPanX, info.maxPanX)
    camera.panY = info.fitY || panState.lockPanY ? panState.lockedPanY : clamp(desiredPanY, info.minPanY, info.maxPanY)
  }

  function onPointerUp(ev: PointerEvent) {
    if (!panState.active) return false
    if (ev.pointerId !== panState.pointerId) return false

    const wasClick = !panState.moved

    panState.active = false
    panState.pointerId = -1

    const canvas = deps.canvasEl.value
    try {
      canvas?.releasePointerCapture(ev.pointerId)
    } catch {
      // ignore
    }

    // Returns true if it was a click (no pan).
    return wasClick
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
      const nextZoom = clamp(camera.zoom * k, ZOOM_MIN, ZOOM_MAX)
      if (nextZoom === camera.zoom) {
        // Still notify: user interaction happened, and wiring may need to wake up
        // from deep-idle even if zoom is clamped.
        deps.onCameraChanged?.()
        return
      }

      camera.zoom = nextZoom
      camera.panX = sx - before.x * camera.zoom
      camera.panY = sy - before.y * camera.zoom
      clampCameraPan()

      deps.onCameraChanged?.()
    })
  }

  return {
    camera,
    panState,
    wheelState,
    resetCamera,
    getWorldBounds,
    clampCameraPan,
    focusOnEdge,
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
