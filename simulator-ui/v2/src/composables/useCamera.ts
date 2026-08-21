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
 * Framing options. Everything here is optional, and omitting all of it is the historical
 * behaviour: an edge framed in the middle of the whole canvas.
 */
export type FocusOnEdgeOptions = {
  /**
   * How many CSS px of the viewport's right-hand side are covered and must not be framed into.
   *
   * The camera does not know that a panel exists, let alone which one or how wide it is: it is
   * given a number by the layer that owns the covering surface and can measure it. Without this,
   * "focus" centres on the geometric middle of the canvas, which on a narrow window is underneath
   * the very panel the user pressed "Focus" in — a short edge can vanish behind it completely.
   *
   * Values that are absent, non-finite or non-positive mean "nothing is covered".
   */
  viewportInsetRight?: number
}

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

  /**
   * How much of the viewport's right-hand side is covered by something opaque, in CSS px.
   *
   * The camera knows nothing about what covers it — a panel, a dock, anything. It is told a
   * number, and the layer that owns the covering surface is the layer that measures it. `0`,
   * which is also what a missing/garbage value normalises to, means "nothing is covered" and
   * reproduces the pre-inset behaviour exactly.
   */
  function normaliseInsetRight(value: number | null | undefined, layoutW: number): number {
    const raw = Number(value ?? 0)
    if (!Number.isFinite(raw) || raw <= 0) return 0
    return Math.min(raw, Math.max(0, layoutW - 1))
  }

  function getCameraClampInfo(insetRight = 0) {
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

    const fullLayoutW = deps.getLayoutW()
    const layoutH = deps.getLayoutH()

    // Everything below reasons about the region the user can actually see. With no inset that
    // region IS the viewport, so every number is unchanged for every existing caller.
    const layoutW = Math.max(1, fullLayoutW - normaliseInsetRight(insetRight, fullLayoutW))

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

    /**
     * The legal pan range on an axis whose content FITS the visible region.
     *
     * `minPan*`/`maxPan*` above say "no gutter wider than `padPx`": content larger than the
     * viewport must cover it. Content that fits cannot satisfy that — a graph narrower than
     * `layoutW - 2 * padPx` cannot reach within `padPx` of both edges at once, so the range
     * inverts, which is the whole reason a fitting axis is treated separately at all.
     *
     * The same rule, in the only orientation that survives: at least `padPx` of content stays
     * inside the visible region. It is the identical pair of expressions with the two world
     * bounds exchanged, it is never inverted, and — unlike "the whole graph stays visible" — it
     * does not forbid the rest of the graph from leaving the screen while one edge is framed,
     * which is what framing an edge inside a small graph necessarily does.
     */
    const fitMinPanX = padPx - bounds.maxX * z
    const fitMaxPanX = (layoutW - padPx) - bounds.minX * z
    const fitMinPanY = padPx - bounds.maxY * z
    const fitMaxPanY = (layoutH - padPx) - bounds.minY * z

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
      fitMinPanX,
      fitMaxPanX,
      fitMinPanY,
      fitMaxPanY,
    }
  }

  /**
   * What the clamp is allowed to do on an axis whose content fits the visible region.
   *
   * - `recenter-when-fits` (the default, and what every interactive path uses): re-center the
   *   whole graph. The user did not aim at anything, so "everything, centered" is the answer.
   * - `hold-bounds`: keep the pan the caller just chose, and only pull it back if it would push
   *   content out of view. Used by paths that aimed the camera deliberately — re-centering there
   *   would silently discard the aim and show the user something they did not ask for.
   *
   * Both modes share one clamp and one set of bounds on purpose: the axis where content does NOT
   * fit is clamped identically either way, so there is exactly one camera mechanism, not two.
   */
  type PanClampMode = 'recenter-when-fits' | 'hold-bounds'

  type PanClampOptions = {
    mode?: PanClampMode
    /** See `normaliseInsetRight`. Omitted means "nothing covers the viewport". */
    insetRight?: number
  }

  function clampCameraPan(options?: PanClampOptions) {
    if (deps.isTestMode()) return
    const info = getCameraClampInfo(options?.insetRight ?? 0)
    if (!info) return

    const holdBounds = options?.mode === 'hold-bounds'

    // `Math.min`/`Math.max` only guard the pathological viewport (narrower than `2 * padPx`),
    // where even this range would invert and `clamp` would silently return its lower bound.
    const holdX = (lo: number, hi: number) => clamp(camera.panX, Math.min(lo, hi), Math.max(lo, hi))
    const holdY = (lo: number, hi: number) => clamp(camera.panY, Math.min(lo, hi), Math.max(lo, hi))

    if (info.fitX) {
      camera.panX = holdBounds ? holdX(info.fitMinPanX, info.fitMaxPanX) : info.centeredPanX
    } else camera.panX = clamp(camera.panX, info.minPanX, info.maxPanX)

    if (info.fitY) {
      camera.panY = holdBounds ? holdY(info.fitMinPanY, info.fitMaxPanY) : info.centeredPanY
    } else camera.panY = clamp(camera.panY, info.minPanY, info.maxPanY)
  }

  /**
   * Point the camera at one edge, given both of its endpoints in layout space.
   *
   * An edge is a segment, not a point, so this fits the segment's bounds instead of
   * centering on one end: the camera ends up centered on the segment's midpoint at the
   * largest interactive zoom that still leaves both endpoints inside the padded viewport.
   * The result depends only on the two endpoints, the viewport size and the optional inset —
   * no animation, no easing, no dependence on the camera's previous position.
   *
   * "Centered" means centered in the region the caller says is visible (see `FocusOnEdgeOptions`),
   * and the pan that achieves it survives the closing clamp: on this path the clamp holds bounds
   * and never re-centers, because re-centering the graph is precisely the opposite of framing
   * one edge of it.
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
  function focusOnEdge(
    a: LayoutNodeLike | null | undefined,
    b: LayoutNodeLike | null | undefined,
    options?: FocusOnEdgeOptions,
  ): boolean {
    if (!a || !b) return false
    if (!Number.isFinite(a.__x) || !Number.isFinite(a.__y)) return false
    if (!Number.isFinite(b.__x) || !Number.isFinite(b.__y)) return false

    const minX = Math.min(a.__x, b.__x)
    const maxX = Math.max(a.__x, b.__x)
    const minY = Math.min(a.__y, b.__y)
    const maxY = Math.max(a.__y, b.__y)

    const fullLayoutW = deps.getLayoutW()
    const layoutH = deps.getLayoutH()

    // The edge is framed in the region the user can see, not in the region the canvas occupies:
    // the two differ by whatever an opaque surface covers on the right. With no inset the two are
    // the same region and every number below is what it was before.
    const insetRight = normaliseInsetRight(options?.viewportInsetRight, fullLayoutW)
    const layoutW = Math.max(1, fullLayoutW - insetRight)

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

    // Same clamp the wheel path uses, in the mode that only holds bounds: framing is an aim, and
    // the default mode would answer it by re-centering the whole graph — i.e. by discarding it.
    // The inset goes with it so the bounds are the bounds of the same region the framing used.
    clampCameraPan({ mode: 'hold-bounds', insetRight })
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
