import type { ComputedRef, Ref } from 'vue'
import { markRaw, ref, shallowReactive, toRaw, watch } from 'vue'
import { fnv1a } from '../utils/hash'

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

  /**
   * Manual invalidation gate for consumers that used to rely on deep-reactive node coords.
   * Bump after physics sync so computed-based consumers (picking/labels/node-card) can re-evaluate.
   */
  layoutVersion: Ref<number>
  bumpLayoutVersion: () => void

  /**
   * Replace layout arrays with non-reactive (raw) arrays.
   * Critical for physics tick performance: avoids Vue proxy setter storms on __x/__y.
   */
  setLayout: (nodes: N[], links: L[]) => void

  resizeAndLayout: () => void
  requestResizeAndLayout: () => void
  requestRelayoutDebounced: (delayMs?: number) => void

  setupResizeListener: () => void
  teardownResizeListener: () => void

  resetLayoutKeyCache: () => void

  setClampCameraPan: (fn: () => void) => void
}

function getWin(): any {
  return ((globalThis as any).window ?? globalThis) as any
}

function getDoc(): any {
  const w = (globalThis as any).window as any
  return ((globalThis as any).document ?? w?.document) as any
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

export function computeSnapshotStructuralKey(snapshot: { nodes: unknown[]; links: unknown[] } | null): string {
  if (!snapshot) return '0|0|0'

  const nodesLen = snapshot.nodes?.length ?? 0
  const linksLen = snapshot.links?.length ?? 0
  const idsJoined = (snapshot.nodes as Array<{ id: unknown }>).map((n) => String(n.id)).join('|')
  const hash = fnv1a(idsJoined)

  return `${nodesLen}|${linksLen}|${hash}`
}

export function useLayoutCoordinator<
  N,
  L,
  M extends string,
  S extends { generated_at: string; nodes: unknown[]; links: unknown[] },
>(deps: UseLayoutCoordinatorDeps<N, L, M, S>): UseLayoutCoordinatorReturn<N, L> {
  // IMPORTANT: keep nodes/links RAW.
  // We still want `layout.w/h` to be reactive, but layout.nodes/links must NOT be Vue proxies.
  // Physics writes to node.__x/__y on every tick; proxied nodes would trigger massive overhead.
  const layout = shallowReactive({
    nodes: markRaw([] as N[]),
    links: markRaw([] as L[]),
    w: 0,
    h: 0,
  }) as unknown as LayoutState<N, L>

  const layoutVersion = ref(0)
  function bumpLayoutVersion() {
    layoutVersion.value++
  }

  function setLayout(nodes: N[], links: L[]) {
    // Always store RAW arrays + RAW elements.
    // Callers might pass Vue proxies (e.g. from accidental deep-reactive wrapping elsewhere).
    const nodesArr = toRaw(nodes) as unknown as N[]
    const linksArr = toRaw(links) as unknown as L[]

    // If elements are proxies, unwrap them too (relayout is infrequent, so O(n) here is fine).
    const rawNodes = nodesArr.map((n) => toRaw(n)) as unknown as N[]
    const rawLinks = linksArr.map((l) => toRaw(l)) as unknown as L[]

    layout.nodes = markRaw(rawNodes)
    layout.links = markRaw(rawLinks)
  }

	let resizeRafId: number | null = null
	let resizeRelayoutRafId: number | null = null
	let relayoutDebounceId: number | null = null
	let relayoutRafId: number | null = null
	let relayoutAfterResizePending = false

  // ITEM-18 (LOW): coordinate RAF scheduling between resize-relayout and plain relayout.
  // Without coordination, `requestResizeRelayoutAndWakeUpCoalesced()` and `requestRelayoutDebounced(0)`
  // can schedule two compute passes in the same animation frame.
  let relayoutRafResizePending = false
  let relayoutForcedInResizeRelayout = false
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

	function resizeCanvases(): boolean {
		const canvas = deps.canvasEl.value
		const fxCanvas = deps.fxCanvasEl.value
		const host = deps.hostEl.value
		const snap = deps.snapshot.value
		if (!canvas || !fxCanvas || !host || !snap) return false

		const rect = host.getBoundingClientRect()
		const win = getWin()
		const dpr = Math.min(deps.dprClamp.value, win.devicePixelRatio || 1)

    const cssW = Math.max(1, Math.floor(rect.width))
    const cssH = Math.max(1, Math.floor(rect.height))
    const pxW = Math.max(1, Math.floor(cssW * dpr))
    const pxH = Math.max(1, Math.floor(cssH * dpr))

		const cssWStr = `${cssW}px`
		const cssHStr = `${cssH}px`

		let changed = false

		if (canvas.width !== pxW) {
			canvas.width = pxW
			changed = true
		}
		if (canvas.height !== pxH) {
			canvas.height = pxH
			changed = true
		}
		if (canvas.style.width !== cssWStr) {
			canvas.style.width = cssWStr
			changed = true
		}
		if (canvas.style.height !== cssHStr) {
			canvas.style.height = cssHStr
			changed = true
		}

		if (fxCanvas.width !== canvas.width) {
			fxCanvas.width = canvas.width
			changed = true
		}
		if (fxCanvas.height !== canvas.height) {
			fxCanvas.height = canvas.height
			changed = true
		}
		if (fxCanvas.style.width !== canvas.style.width) {
			fxCanvas.style.width = canvas.style.width
			changed = true
		}
		if (fxCanvas.style.height !== canvas.style.height) {
			fxCanvas.style.height = canvas.style.height
			changed = true
		}

		if (layout.w !== cssW) {
			layout.w = cssW
			changed = true
		}
		if (layout.h !== cssH) {
			layout.h = cssH
			changed = true
		}

		return changed
	}

  function recomputeLayout() {
    const snap = deps.snapshot.value
    if (!snap) return

    // IMPORTANT: do not key layout on `generated_at`.
    // Real-mode streams and patch-only events often bump `generated_at` even when topology
    // (node/link composition) is unchanged. Recomputing layout in that case recreates physics
    // and causes a visible "graph jump".
    //
    // However, keying only on (sourcePath, nodeCount, linkCount) is too coarse: two different
    // snapshots can share the same source path and counts but have different node IDs (e.g.
    // after a scenario reset with a different participant set). Include a lightweight fingerprint
    // of the first N node IDs so those cases trigger a re-layout without hurting the common
    // "patch-only update" path (where IDs are stable).
    const NODE_ID_SAMPLE = 8
    const nodeIdFingerprint = (snap.nodes as Array<{ id: unknown }>)
      .slice(0, NODE_ID_SAMPLE)
      .map((n) => String(n.id))
      .join('~')
    const snapKey = `${deps.getSourcePath()}|${snap.nodes.length}|${snap.links.length}|${nodeIdFingerprint}`
    const key = `${snapKey}|${deps.layoutMode.value}|${layout.w}x${layout.h}`
    if (key === lastLayoutKey) return
    lastLayoutKey = key

    deps.computeLayout(snap, layout.w, layout.h, deps.layoutMode.value)
  }

	function resizeAndLayout() {
		resizeCanvases()

		// Always attempt recomputeLayout: it has its own dedup via lastLayoutKey,
		// so it won't redo work when neither snapshot nor viewport changed.
		// Skipping recomputeLayout when only canvas size was unchanged caused a bug:
		// switching scenarios (same viewport, new snapshot) never ran layout → nodes stayed at 0,0.
		recomputeLayout()
		clampCameraPanFn()
		deps.wakeUp?.()
	}

  function getRafScheduler(): (cb: (t: number) => void) => number {
    const win = getWin()
    return typeof win.requestAnimationFrame === 'function'
      ? win.requestAnimationFrame.bind(win)
      : (cb) => win.setTimeout(() => cb(win.performance?.now?.() ?? Date.now()), 0)
  }

  function cancelRaf(id: number) {
    const win = getWin()
    if (typeof win.cancelAnimationFrame === 'function') win.cancelAnimationFrame(id)
    else win.clearTimeout(id)
  }

  function performRelayoutNow() {
    recomputeLayout()
    clampCameraPanFn()
    deps.wakeUp?.()
  }

	function requestResizeAndLayout() {
		if (resizeRafId !== null) return

    const raf = getRafScheduler()

		resizeRafId = raf(() => {
			resizeRafId = null
			// Cheap per-frame resize: update canvas sizes; avoid heavy force relayout.
			const changed = resizeCanvases()
			if (!changed && !relayoutAfterResizePending) return

      // IMPORTANT: order guarantee.
      // If a relayout was requested (e.g. from visibilitychange / DPR watcher),
      // it MUST run after we update layout.w/h (resizeCanvases).
      if (relayoutAfterResizePending) {
        relayoutAfterResizePending = false
        performRelayoutNow()
        return
      }

			clampCameraPanFn()
			deps.wakeUp?.()
		})
	}

	function requestResizeRelayoutAndWakeUpCoalesced() {
		// If a relayout RAF is already pending, don't schedule a separate resize+relayout RAF.
		// Instead, request the pending relayout to resize first, so compute runs only once.
		if (relayoutRafId !== null) {
			relayoutRafResizePending = true
			return
		}

		if (resizeRelayoutRafId !== null) return
		const raf = getRafScheduler()
		resizeRelayoutRafId = raf(() => {
			resizeRelayoutRafId = null

			const changed = resizeCanvases()
			if (!changed && !relayoutForcedInResizeRelayout) return
			relayoutForcedInResizeRelayout = false

			performRelayoutNow()
		})
	}

  function requestRelayoutDebounced(delayMs = 180) {
    const win = getWin()

    // For "immediate" relayouts (visibility / DPR): ensure ordering vs resize RAF.
    // If resize is scheduled via RAF, a setTimeout(0) relayout can fire earlier.
    if (delayMs <= 0) {
      if (relayoutDebounceId !== null) {
        win.clearTimeout(relayoutDebounceId)
        relayoutDebounceId = null
      }

      if (resizeRafId !== null) {
        relayoutAfterResizePending = true
        return
      }

      // If a resize+relayout RAF is already pending, force it to run the relayout even
      // when resizeCanvases() detects no size changes.
      if (resizeRelayoutRafId !== null) {
        relayoutForcedInResizeRelayout = true
        return
      }

      if (relayoutRafId !== null) return
      const raf = getRafScheduler()
      relayoutRafId = raf(() => {
        relayoutRafId = null

        // If resize signals arrived while this relayout RAF was pending, apply size updates
        // first so computeLayout is keyed on fresh layout.w/h.
        if (relayoutRafResizePending) {
          relayoutRafResizePending = false
          resizeCanvases()
        }

        performRelayoutNow()
      })
      return
    }

    if (relayoutDebounceId !== null) win.clearTimeout(relayoutDebounceId)
    relayoutDebounceId = win.setTimeout(() => {
      relayoutDebounceId = null

      // If a resize is still pending (RAF not flushed yet), postpone relayout until
      // after size update to keep computeLayout keyed by fresh layout.w/h.
      if (resizeRafId !== null) {
        relayoutAfterResizePending = true
        return
      }

      if (resizeRelayoutRafId !== null) {
        relayoutForcedInResizeRelayout = true
        return
      }

      performRelayoutNow()
    }, delayMs)
  }

	function onWindowResize() {
		requestResizeRelayoutAndWakeUpCoalesced()
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

    // In some sandbox/iframe environments, `matchMedia()` (or even listener registration)
    // may throw (e.g. SecurityError). DPR listener is a best-effort enhancement and must
    // never crash the UI.
    let mql: MediaQueryList | null = null
    try {
      const dpr = win.devicePixelRatio || 1
      mql = win.matchMedia(`(resolution: ${dpr}dppx)`)
      if (!mql) return

      dprMql = mql
      addMqlListener(mql, onDprChange)
    } catch (err) {
      // If setup failed mid-way, ensure the state is consistent and cleanup is safe.
      if (mql) {
        try {
          removeMqlListener(mql, onDprChange)
        } catch {
          // ignore
        }
      }
      dprMql = null

      if (import.meta.env.DEV) console.warn('[layout] dpr listener setup failed:', err)
    }
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
		requestResizeRelayoutAndWakeUpCoalesced()
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
			cancelRaf(resizeRafId)
			resizeRafId = null
		}

		if (resizeRelayoutRafId !== null) {
			cancelRaf(resizeRelayoutRafId)
			resizeRelayoutRafId = null
		}

    if (relayoutRafId !== null) {
      cancelRaf(relayoutRafId)
      relayoutRafId = null
    }

    relayoutAfterResizePending = false
    relayoutRafResizePending = false
    relayoutForcedInResizeRelayout = false

    if (relayoutDebounceId !== null) {
      win.clearTimeout(relayoutDebounceId)
      relayoutDebounceId = null
    }
  }

  // Preserve old behavior: layout coordinator itself doesn't block in test-mode.
  // Test determinism is handled at the interaction layer (pan/zoom disabled), and
  // by using stable DPR clamps; keeping resize/layout active avoids surprises.
  void deps.isTestMode

  // Critical: layout mode changes must trigger a relayout even if the viewport size
  // doesn't change. Otherwise, deep-idle render loops may not redraw until focus/visibility events.
  watch(
    deps.layoutMode,
    () => {
      requestRelayoutDebounced(0)
    },
    { flush: 'sync' },
  )

  // Snapshot structural changes (nodes/links) must trigger a relayout.
  // This also supports incremental topology patches that mutate snapshot in-place.
  watch(
    () => {
      const s = deps.snapshot.value
      // Relayout should be driven by topology (node/link composition), not timestamps.
      return computeSnapshotStructuralKey(s)
    },
    () => {
      if (!deps.snapshot.value) return
      requestRelayoutDebounced(0)
    },
    { flush: 'post' },
  )

  return {
    layout,
    layoutVersion,
    bumpLayoutVersion,
    setLayout,
    resizeAndLayout,
    requestResizeAndLayout,
    requestRelayoutDebounced,
    setupResizeListener,
    teardownResizeListener,
    resetLayoutKeyCache,
    setClampCameraPan,
  }
}
