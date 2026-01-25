import { computed, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { DemoEvent, GraphSnapshot } from '../types'
import { useSceneState } from './useSceneState'

function makeSnapshot(): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: '2026-01-25T00:00:00Z',
    nodes: [{ id: 'A' }, { id: 'B' }],
    links: [{ source: 'A', target: 'B' }],
  }
}

describe('useSceneState', () => {
  it('loadScene loads snapshot and demo tx events for Scene D', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C' | 'D' | 'E'>('D')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      eventsPath: '',
      snapshot: null as GraphSnapshot | null,
      demoTxEvents: [] as any[],
      demoClearingPlan: null,
      demoClearingDone: null,
      selectedNodeId: 'A' as string | null,
    })

    const snapshot = makeSnapshot()
    const loadSnapshot = vi.fn(async () => ({ snapshot, sourcePath: 'snap.json' }))

    const events: DemoEvent[] = [
      { event_id: '1', ts: 't', type: 'tx.updated', equivalent: 'UAH', ttl_ms: 1, edges: [{ from: 'A', to: 'B' }] },
      { event_id: '2', ts: 't', type: 'clearing.plan', equivalent: 'UAH', plan_id: 'p', steps: [] },
    ]
    const loadEvents = vi.fn(async () => ({ events, sourcePath: 'events.json' }))

    const assertPlaylistEdgesExistInSnapshot = vi.fn()

    const clearScheduledTimeouts = vi.fn()
    const resetPlaylistPointers = vi.fn()
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
      loadEvents,
      assertPlaylistEdgesExistInSnapshot,
      clearScheduledTimeouts,
      resetPlaylistPointers,
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
    expect(state.eventsPath).toBe('events.json')
    expect(state.demoTxEvents).toHaveLength(1)
    expect(assertPlaylistEdgesExistInSnapshot).toHaveBeenCalledTimes(1)
    expect(resizeAndLayout).toHaveBeenCalledTimes(1)
    expect(ensureRenderLoop).toHaveBeenCalledTimes(1)
  })

  it('loadScene loads clearing plan/done for Scene E', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C' | 'D' | 'E'>('E')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      eventsPath: '',
      snapshot: null as GraphSnapshot | null,
      demoTxEvents: [] as any[],
      demoClearingPlan: null as any,
      demoClearingDone: null as any,
      selectedNodeId: 'A' as string | null,
    })

    const snapshot = makeSnapshot()
    const loadSnapshot = vi.fn(async () => ({ snapshot, sourcePath: 'snap.json' }))

    const events: DemoEvent[] = [
      { event_id: 'p', ts: 't', type: 'clearing.plan', equivalent: 'UAH', plan_id: 'p', steps: [] },
      { event_id: 'd', ts: 't', type: 'clearing.done', equivalent: 'UAH', plan_id: 'p' },
    ]
    const loadEvents = vi.fn(async () => ({ events, sourcePath: 'events.json' }))

    const s = useSceneState({
      eq,
      scene,
      layoutMode,
      allowEqDeepLink: () => true,
      isEqAllowed: () => true,
      effectiveEq,
      state,
      loadSnapshot,
      loadEvents,
      assertPlaylistEdgesExistInSnapshot: vi.fn(),
      clearScheduledTimeouts: vi.fn(),
      resetPlaylistPointers: vi.fn(),
      resetCamera: vi.fn(),
      resetLayoutKeyCache: vi.fn(),
      resetOverlays: vi.fn(),
      resizeAndLayout: vi.fn(),
      ensureRenderLoop: vi.fn(),
      setupResizeListener: vi.fn(),
      teardownResizeListener: vi.fn(),
      stopRenderLoop: vi.fn(),
    })

    await s.loadScene()

    expect(state.demoClearingPlan?.type).toBe('clearing.plan')
    expect(state.demoClearingDone?.type).toBe('clearing.done')
    expect(loadEvents).toHaveBeenCalledWith('UAH', 'demo-clearing')
  })

  it('setup applies allow-listed eq and focuses existing node from URL', async () => {
    const eq = ref('UAH')
    const scene = ref<'A' | 'B' | 'C' | 'D' | 'E'>('A')
    const layoutMode = ref<'admin-force' | 'community-clusters' | 'balance-split' | 'type-split' | 'status-split'>('admin-force')
    const effectiveEq = computed(() => eq.value)

    const state = reactive({
      loading: false,
      error: '',
      sourcePath: '',
      eventsPath: '',
      snapshot: null as GraphSnapshot | null,
      demoTxEvents: [] as any[],
      demoClearingPlan: null,
      demoClearingDone: null,
      selectedNodeId: null as string | null,
    })

    // Simulate deep-link (Vitest runs in node env here)
    const prevWindow = (globalThis as any).window
    ;(globalThis as any).window = { location: { search: '?scene=B&layout=type-split&eq=eur&focus=A' } }

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
        loadEvents: vi.fn(async () => ({ events: [], sourcePath: 'events.json' })),
        assertPlaylistEdgesExistInSnapshot: vi.fn(),
        clearScheduledTimeouts: vi.fn(),
        resetPlaylistPointers: vi.fn(),
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
      ;(globalThis as any).window = prevWindow
    }
  })
})
