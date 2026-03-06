import { computed, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { GraphLink, GraphNode, GraphSnapshot } from '../types'
import { useSceneState } from './useSceneState'

type UseSceneStateDeps = Parameters<typeof useSceneState>[0]
type ClearScheduledTimeoutArg = Parameters<UseSceneStateDeps['clearScheduledTimeouts']>[0]

function makeNode(id: string, name?: string): GraphNode {
  return name ? { id, name } : { id }
}

function makeLink(source: string, target: string): GraphLink {
  return { source, target }
}

function setMockWindow(windowValue: (Window & typeof globalThis) | undefined): void {
  Object.defineProperty(globalThis, 'window', {
    value: windowValue,
    configurable: true,
    writable: true,
  })
}

function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-25T00:00:00Z',
    nodes: [makeNode('A'), makeNode('B')],
    links: [makeLink('A', 'B')],
  }
}

describe('useSceneState', () => {
  it('loadScene loads snapshot and updates state', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: 'A' as string | null,
    })

    const snapshot = makeSnapshot()
    const loadSnapshot = vi.fn(async () => ({ snapshot, sourcePath: 'snap.json' }))

    const clearScheduledTimeouts = vi.fn()
    const resetCamera = vi.fn()
    const resetLayoutKeyCache = vi.fn()
    const resetOverlays = vi.fn()
    const resizeAndLayout = vi.fn()
    const ensureRenderLoop = vi.fn()
    const setupResizeListener = vi.fn()
    const teardownResizeListener = vi.fn()
    const stopRenderLoop = vi.fn()

    const s = useSceneState({
      eq,
      scene,
      layoutMode,
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq,
      state,
      loadSnapshot,
      clearScheduledTimeouts,
      resetCamera,
      resetLayoutKeyCache,
      resetOverlays,
      resizeAndLayout,
      ensureRenderLoop,
      setupResizeListener,
      teardownResizeListener,
      stopRenderLoop,
    })

    await s.loadScene()

    expect(state.snapshot).toEqual(snapshot)
    expect(state.sourcePath).toBe('snap.json')
    expect(resizeAndLayout).toHaveBeenCalledTimes(1)
    expect(ensureRenderLoop).toHaveBeenCalledTimes(1)

    // Full reload: first clear keeps critical, then clears all.
    expect(clearScheduledTimeouts).toHaveBeenCalled()
  })

  it('treats scenario preview -> preview (different scenario id) as a full scene change even if node IDs match', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })

    const snapshot1: GraphSnapshot = {
      equivalent: 'UAH',
      generated_at: '2026-02-01T00:00:00Z',
      nodes: [makeNode('A', 'Alice'), makeNode('B', 'Bob')],
      links: [makeLink('A', 'B')],
    }

    const snapshot2: GraphSnapshot = {
      ...snapshot1,
      generated_at: '2026-02-01T00:00:01Z',
      // Same node IDs, but represent a different scenario context.
      links: [makeLink('B', 'A')],
    }

    const loadSnapshot = vi.fn()
    loadSnapshot.mockResolvedValueOnce({
      snapshot: snapshot1,
      sourcePath: 'GET http://127.0.0.1:18000/api/v1/simulator/scenarios/S1/graph/preview?equivalent=UAH&mode=real',
    })
    loadSnapshot.mockResolvedValueOnce({
      snapshot: snapshot2,
      sourcePath: 'GET http://127.0.0.1:18000/api/v1/simulator/scenarios/S2/graph/preview?equivalent=UAH&mode=real',
    })

    const clearScheduledTimeouts = vi.fn()
    const resetCamera = vi.fn()
    const resetLayoutKeyCache = vi.fn()
    const resetOverlays = vi.fn()
    const resizeAndLayout = vi.fn()
    const ensureRenderLoop = vi.fn()
    const onIncrementalSnapshotLoaded = vi.fn()

    const s = useSceneState({
      eq,
      scene,
      layoutMode,
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq,
      state,
      loadSnapshot,
      clearScheduledTimeouts,
      resetCamera,
      resetLayoutKeyCache,
      resetOverlays,
      resizeAndLayout,
      ensureRenderLoop,
      onIncrementalSnapshotLoaded,
      setupResizeListener: vi.fn(),
      teardownResizeListener: vi.fn(),
      stopRenderLoop: vi.fn(),
    })

    await s.loadScene()
    expect(resetCamera).toHaveBeenCalledTimes(1)
    expect(resetLayoutKeyCache).toHaveBeenCalledTimes(1)
    expect(resetOverlays).toHaveBeenCalledTimes(1)
    expect(onIncrementalSnapshotLoaded).toHaveBeenCalledTimes(0)

    await s.loadScene()
    // Different scenario preview must be treated as full reload.
    expect(resetCamera).toHaveBeenCalledTimes(2)
    expect(resetLayoutKeyCache).toHaveBeenCalledTimes(2)
    expect(resetOverlays).toHaveBeenCalledTimes(2)
    expect(onIncrementalSnapshotLoaded).toHaveBeenCalledTimes(0)
  })

  it('non-incremental loadScene clears ALL timers (including critical) because node context changed', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })

    const snapshot1 = makeSnapshot() // nodes [A, B]
    const snapshot2: GraphSnapshot = {
      ...makeSnapshot(),
      nodes: [{ id: 'X' }, { id: 'Y' }], // DIFFERENT node IDs → non-incremental
      links: [{ source: 'X', target: 'Y' }],
    }

    const loadSnapshot = vi.fn()
    loadSnapshot.mockResolvedValueOnce({ snapshot: snapshot1, sourcePath: 's1' })
    loadSnapshot.mockResolvedValueOnce({ snapshot: snapshot2, sourcePath: 's2' })

    const clearScheduledTimeouts = vi.fn()
    const resetOverlays = vi.fn()

    const s = useSceneState({
      eq,
      scene,
      layoutMode,
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq,
      state,
      loadSnapshot,
      clearScheduledTimeouts,
      resetCamera: vi.fn(),
      resetLayoutKeyCache: vi.fn(),
      resetOverlays,
      resizeAndLayout: vi.fn(),
      ensureRenderLoop: vi.fn(),
      setupResizeListener: vi.fn(),
      teardownResizeListener: vi.fn(),
      stopRenderLoop: vi.fn(),
    })

    // First load: full reload (hasLoadedOnce=false).
    await s.loadScene()
    expect(resetOverlays).toHaveBeenCalledTimes(1)

    clearScheduledTimeouts.mockClear()
    resetOverlays.mockClear()

    // Second load: node IDs changed → non-incremental → must clear ALL timers.
    await s.loadScene()

    // Non-incremental: keepCritical pass first, then unconditional clear.
    const clearTimeoutCalls = clearScheduledTimeouts.mock.calls as unknown as Array<
      [ClearScheduledTimeoutArg?]
    >

    const keepCriticalCalls = clearTimeoutCalls.filter(
      ([arg]: [ClearScheduledTimeoutArg?]) => arg?.keepCritical === true,
    )
    const fullClearCalls = clearTimeoutCalls.filter(
      ([arg]: [ClearScheduledTimeoutArg?]) => arg === undefined || arg?.keepCritical !== true,
    )
    expect(keepCriticalCalls.length).toBeGreaterThanOrEqual(1)
    expect(fullClearCalls.length).toBeGreaterThanOrEqual(1)

    // resetOverlays must be called on non-incremental path.
    expect(resetOverlays).toHaveBeenCalledTimes(1)
  })

  it('loadScene skips expensive resets for incremental update (same node IDs)', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })

    const snapshot1 = makeSnapshot()
    const snapshot2: GraphSnapshot = { ...snapshot1, generated_at: '2026-01-25T00:00:01Z' }
    const loadSnapshot = vi.fn()
    loadSnapshot.mockResolvedValueOnce({ snapshot: snapshot1, sourcePath: 'snap1.json' })
    loadSnapshot.mockResolvedValueOnce({ snapshot: snapshot2, sourcePath: 'snap2.json' })

    const clearScheduledTimeouts = vi.fn()
    const resetCamera = vi.fn()
    const resetLayoutKeyCache = vi.fn()
    const resetOverlays = vi.fn()
    const resizeAndLayout = vi.fn()
    const ensureRenderLoop = vi.fn()

    const s = useSceneState({
      eq,
      scene,
      layoutMode,
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq,
      state,
      loadSnapshot,
      clearScheduledTimeouts,
      resetCamera,
      resetLayoutKeyCache,
      resetOverlays,
      resizeAndLayout,
      ensureRenderLoop,
      setupResizeListener: vi.fn(),
      teardownResizeListener: vi.fn(),
      stopRenderLoop: vi.fn(),
    })

    await s.loadScene()
    expect(resetCamera).toHaveBeenCalledTimes(1)
    expect(resetOverlays).toHaveBeenCalledTimes(1)
    expect(resetLayoutKeyCache).toHaveBeenCalledTimes(1)

    // First load is a full reload: keepCritical pass + full clear.
    expect(clearScheduledTimeouts).toHaveBeenCalledTimes(2)
    expect(clearScheduledTimeouts.mock.calls[0]?.[0]).toEqual({ keepCritical: true })
    expect(clearScheduledTimeouts.mock.calls[1]?.[0]).toBeUndefined()

    await s.loadScene()
    // incremental update => no expensive resets
    expect(resetCamera).toHaveBeenCalledTimes(1)
    expect(resetOverlays).toHaveBeenCalledTimes(1)
    expect(resetLayoutKeyCache).toHaveBeenCalledTimes(1)
    expect(ensureRenderLoop).toHaveBeenCalledTimes(2)

    // Incremental update: must not clear critical timers.
    expect(clearScheduledTimeouts).toHaveBeenCalledTimes(3)
    expect(clearScheduledTimeouts.mock.calls[2]?.[0]).toEqual({ keepCritical: true })
  })

  it('incremental update does not relayout when only links change', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })

    const snapshot1 = makeSnapshot() // nodes [A,B], links [A->B]
    const snapshot2: GraphSnapshot = {
      ...snapshot1,
      generated_at: '2026-01-25T00:00:01Z',
      // Same node IDs, but link composition differs (preview → run can do this).
      links: [
        makeLink('A', 'B'),
        makeLink('B', 'A'),
      ],
    }

    const loadSnapshot = vi.fn()
    loadSnapshot.mockResolvedValueOnce({ snapshot: snapshot1, sourcePath: 'snap1.json' })
    loadSnapshot.mockResolvedValueOnce({ snapshot: snapshot2, sourcePath: 'snap2.json' })

    const resizeAndLayout = vi.fn()
    const onIncrementalSnapshotLoaded = vi.fn()

    const s = useSceneState({
      eq,
      scene,
      layoutMode,
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq,
      state,
      loadSnapshot,
      clearScheduledTimeouts: vi.fn(),
      resetCamera: vi.fn(),
      resetLayoutKeyCache: vi.fn(),
      resetOverlays: vi.fn(),
      resizeAndLayout,
      ensureRenderLoop: vi.fn(),
      onIncrementalSnapshotLoaded,
      setupResizeListener: vi.fn(),
      teardownResizeListener: vi.fn(),
      stopRenderLoop: vi.fn(),
    })

    await s.loadScene()
    expect(resizeAndLayout).toHaveBeenCalledTimes(1)

    await s.loadScene()
    expect(onIncrementalSnapshotLoaded).toHaveBeenCalledTimes(1)
    // Critical: no second relayout when only links changed.
    expect(resizeAndLayout).toHaveBeenCalledTimes(1)
  })

  it('setup applies allow-listed eq and focuses existing node from URL', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null,
    })

    // Simulate deep-link (Vitest runs in node env here)
    const prevWindow = globalThis.window
    setMockWindow({
      ...globalThis,
      location: { ...globalThis.location, search: '?scene=B&layout=type-split&eq=eur&focus=A' },
    } as unknown as Window & typeof globalThis)

    const snapshot = makeSnapshot()
    const loadSnapshot = vi.fn(async () => ({ snapshot, sourcePath: 'snap.json' }))

    try {
      const s = useSceneState({
        eq,
        scene,
        layoutMode,
        allowEqDeepLink: () => true,
        isEqAllowed: (v) => v === 'EUR' || v === 'UAH',
        effectiveEq,
        state,
        loadSnapshot,
        clearScheduledTimeouts: vi.fn(),
        resetCamera: vi.fn(),
        resetLayoutKeyCache: vi.fn(),
        resetOverlays: vi.fn(),
        resizeAndLayout: vi.fn(),
        ensureRenderLoop: vi.fn(),
        setupResizeListener: vi.fn(),
        teardownResizeListener: vi.fn(),
        stopRenderLoop: vi.fn(),
      })

      s.setup()
      await s.loadScene()

      expect(scene.value).toBe('B')
      expect(layoutMode.value).toBe('type-split')
      expect(eq.value).toBe('EUR')
      expect(loadSnapshot).toHaveBeenCalled()
      expect(state.selectedNodeId).toBe('A')
    } finally {
      setMockWindow(prevWindow)
    }
  })
})
