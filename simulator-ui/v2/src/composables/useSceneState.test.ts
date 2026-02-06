import { computed, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import type { GraphSnapshot } from '../types'
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

    await s.loadScene()
    // incremental update => no expensive resets
    expect(resetCamera).toHaveBeenCalledTimes(1)
    expect(resetOverlays).toHaveBeenCalledTimes(1)
    expect(resetLayoutKeyCache).toHaveBeenCalledTimes(1)
    expect(ensureRenderLoop).toHaveBeenCalledTimes(2)
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
      ;(globalThis as any).window = prevWindow
    }
  })
})
