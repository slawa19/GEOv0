import type { ComputedRef, Ref } from 'vue'
import { reactive } from 'vue'

export type LayoutState<N, L> = {
  nodes: N[]
  links: L[]
  w: number
  h: number
}

type UseLayoutCoordinatorDeps<N, L, M extends string, S extends { generated_at: string; nodes: unknown[]; links: unknown[] }> = {
  canvasEl: Ref<HTMLCanvasElement | null>
  fxCanvasEl: Ref<HTMLCanvasElement | null>
  hostEl: Ref<HTMLDivElement | null>

  snapshot: ComputedRef<S | null>
  layoutMode: Ref<M>

  dprClamp: ComputedRef<number>
  isTestMode: ComputedRef<boolean>

  getSourcePath: () => string
  computeLayout: (snapshot: S, w: number, h: number, mode: M) => void
  clampCameraPan?: () => void

  // Optional: notify render loop that visible state changed (e.g. after resize).
  wakeUp?: () => void
}

type UseLayoutCoordinatorReturn<N, L> = {
  layout: LayoutState<N, L>

  resizeAndLayout: () => void
  requestResizeAndLayout: () => void
  requestRelayoutDebounced: (delayMs?: number) => void

  setupResizeListener: () => void
  teardownResizeListener: () => void

  resetLayoutKeyCache: () => void

  setClampCameraPan: (fn: () => void) => void
}

function getWin(): any {
  return typeof window !== 'undefined' ? window : globalThis
}

function getDoc(): any {
  return typeof document !== 'undefined' ? document : (globalThis as any).document
}

function addMqlListener(mql: MediaQueryList, cb: (ev?: any) => void) {
  if (typeof mql.addEventListener === 'function') mql.addEventListener('change', cb)
  // Legacy Safari.
  else if (typeof (mql as any).addListener === 'function') (mql as any).addListener(cb)
}

function removeMqlListener(mql: MediaQueryList, cb: (ev?: any) => void) {
  if (typeof mql.removeEventListener === 'function') mql.removeEventListener('change', cb)
  else if (typeof (mql as any).removeListener === 'function') (mql as any).removeListener(cb)
}

export function useLayoutCoordinator<
  N,
  L,
  M extends string,
  S extends { generated_at: string; nodes: unknown[]; links: unknown[] },
>(deps: UseLayoutCoordinatorDeps<N, L, M, S>): UseLayoutCoordinatorReturn<N, L> {
  const layout = reactive({
    nodes: [] as N[],
    links: [] as L[],
    w: 0,
    h: 0,
  }) as unknown as LayoutState<N, L>

  let resizeRafId: number | null = null
  let relayoutDebounceId: number | null = null
  let lastLayoutKey: string | null = null

  let listenersActive = false

  let dprMql: MediaQueryList | null = null
  let resizeObserver: ResizeObserver | null = null

  let clampCameraPanFn: () => void = deps.clampCameraPan ?? (() => {})

  function setClampCameraPan(fn: () => void) {
    clampCameraPanFn = fn
  }

  function resetLayoutKeyCache() {
    lastLayoutKey = null
  }

  function resizeCanvases() {
    const canvas = deps.canvasEl.value
    const fxCanvas = deps.fxCanvasEl.value
    const host = deps.hostEl.value
    const snap = deps.snapshot.value
    if (!canvas || !fxCanvas || !host || !snap) return

    const rect = host.getBoundingClientRect()
    const win = getWin()
    const dpr = Math.min(deps.dprClamp.value, win.devicePixelRatio || 1)

    const cssW = Math.max(1, Math.floor(rect.width))
    const cssH = Math.max(1, Math.floor(rect.height))
    const pxW = Math.max(1, Math.floor(cssW * dpr))
    const pxH = Math.max(1, Math.floor(cssH * dpr))

    const cssWStr = `${cssW}px`
    const cssHStr = `${cssH}px`

    if (canvas.width !== pxW) canvas.width = pxW
    if (canvas.height !== pxH) canvas.height = pxH
    if (canvas.style.width !== cssWStr) canvas.style.width = cssWStr
    if (canvas.style.height !== cssHStr) canvas.style.height = cssHStr

    if (fxCanvas.width !== canvas.width) fxCanvas.width = canvas.width
    if (fxCanvas.height !== canvas.height) fxCanvas.height = canvas.height
    if (fxCanvas.style.width !== canvas.style.width) fxCanvas.style.width = canvas.style.width
    if (fxCanvas.style.height !== canvas.style.height) fxCanvas.style.height = canvas.style.height

    layout.w = cssW
    layout.h = cssH
  }

  function recomputeLayout() {
    const snap = deps.snapshot.value
    if (!snap) return

    const snapKey = `${deps.getSourcePath()}|${snap.generated_at}|${snap.nodes.length}|${snap.links.length}`
    const key = `${snapKey}|${deps.layoutMode.value}|${layout.w}x${layout.h}`
    if (key === lastLayoutKey) return
    lastLayoutKey = key

    deps.computeLayout(snap, layout.w, layout.h, deps.layoutMode.value)
  }

  function resizeAndLayout() {
    resizeCanvases()
    recomputeLayout()
    clampCameraPanFn()
    deps.wakeUp?.()
  }

  function requestResizeAndLayout() {
    if (resizeRafId !== null) return

    const win = getWin()
    const raf: (cb: (t: number) => void) => number =
      typeof win.requestAnimationFrame === 'function'
        ? win.requestAnimationFrame.bind(win)
        : (cb) => win.setTimeout(() => cb(win.performance?.now?.() ?? Date.now()), 0)

    resizeRafId = raf(() => {
      resizeRafId = null
      // Cheap per-frame resize: update canvas sizes; avoid heavy force relayout.
      resizeCanvases()
      clampCameraPanFn()
      deps.wakeUp?.()
    })
  }

  function requestRelayoutDebounced(delayMs = 180) {
    const win = getWin()
    if (relayoutDebounceId !== null) win.clearTimeout(relayoutDebounceId)
    relayoutDebounceId = win.setTimeout(() => {
      relayoutDebounceId = null
      recomputeLayout()
      clampCameraPanFn()
      deps.wakeUp?.()
    }, delayMs)
  }

  function onWindowResize() {
    requestResizeAndLayout()
    requestRelayoutDebounced()
  }

  function onVisibilityChange() {
    const doc = getDoc()
    if (doc?.visibilityState !== 'visible') return

    // Returning from background can leave the UI in deep idle + with stale canvas sizing.
    // Force a cheap resize and queue a relayout as a safety net.
    requestResizeAndLayout()
    requestRelayoutDebounced(0)
  }

  function teardownDprListener() {
    if (!dprMql) return
    try {
      removeMqlListener(dprMql, onDprChange)
    } catch {
      // ignore
    }
    dprMql = null
  }

  function setupDprListener() {
    const win = getWin()
    if (typeof win.matchMedia !== 'function') return

    const dpr = win.devicePixelRatio || 1
    dprMql = win.matchMedia(`(resolution: ${dpr}dppx)`)
    if (!dprMql) return

    addMqlListener(dprMql, onDprChange)
  }

  function onDprChange() {
    // DPR may keep changing (zoom, moving window between screens).
    // Recreate the media-query watcher and update canvas sizes.
    teardownDprListener()
    setupDprListener()
    requestResizeAndLayout()
    requestRelayoutDebounced(0)
  }

  function onHostResize() {
    requestResizeAndLayout()
    requestRelayoutDebounced()
  }

  function setupHostResizeObserver() {
    const host = deps.hostEl.value
    if (!host) return
    if (typeof ResizeObserver === 'undefined') return

    resizeObserver = new ResizeObserver(() => {
      onHostResize()
    })
    resizeObserver.observe(host)
  }

  function setupResizeListener() {
    if (listenersActive) return
    listenersActive = true

    const win = getWin()
    win.addEventListener?.('resize', onWindowResize)

    const doc = getDoc()
    doc?.addEventListener?.('visibilitychange', onVisibilityChange)

    setupDprListener()
    setupHostResizeObserver()
  }

  function teardownResizeListener() {
    if (!listenersActive) return
    listenersActive = false

    const win = getWin()
    win.removeEventListener?.('resize', onWindowResize)

    const doc = getDoc()
    doc?.removeEventListener?.('visibilitychange', onVisibilityChange)

    teardownDprListener()

    if (resizeObserver) {
      try {
        resizeObserver.disconnect()
      } catch {
        // ignore
      }
      resizeObserver = null
    }

    if (resizeRafId !== null) {
      if (typeof win.cancelAnimationFrame === 'function') win.cancelAnimationFrame(resizeRafId)
      else win.clearTimeout(resizeRafId)
      resizeRafId = null
    }

    if (relayoutDebounceId !== null) {
      win.clearTimeout(relayoutDebounceId)
      relayoutDebounceId = null
    }
  }

  // Preserve old behavior: layout coordinator itself doesn't block in test-mode.
  // Test determinism is handled at the interaction layer (pan/zoom disabled), and
  // by using stable DPR clamps; keeping resize/layout active avoids surprises.
  void deps.isTestMode

  return {
    layout,
    resizeAndLayout,
    requestResizeAndLayout,
    requestRelayoutDebounced,
    setupResizeListener,
    teardownResizeListener,
    resetLayoutKeyCache,
    setClampCameraPan,
  }
}
