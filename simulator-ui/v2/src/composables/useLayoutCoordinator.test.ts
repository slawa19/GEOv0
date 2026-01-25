import { computed, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useLayoutCoordinator } from './useLayoutCoordinator'

describe('useLayoutCoordinator', () => {
  it('debounced relayout respects lastLayoutKey cache', async () => {
    vi.useFakeTimers()

    const snapshotRef = ref({
      generated_at: 't1',
      nodes: [{ id: 'A' }],
      links: [],
    })

    const computeLayout = vi.fn()
    const clampCameraPan = vi.fn()

    const coordinator = useLayoutCoordinator({
      canvasEl: ref(null),
      fxCanvasEl: ref(null),
      hostEl: ref(null),
      snapshot: computed(() => snapshotRef.value),
      layoutMode: ref<'admin-force'>('admin-force'),
      dprClamp: computed(() => 1),
      isTestMode: computed(() => false),
      getSourcePath: () => 'src',
      computeLayout,
      clampCameraPan,
    })

    coordinator.layout.w = 800
    coordinator.layout.h = 600

    coordinator.requestRelayoutDebounced(10)
    vi.advanceTimersByTime(10)

    expect(computeLayout).toHaveBeenCalledTimes(1)
    expect(clampCameraPan).toHaveBeenCalledTimes(1)

    coordinator.requestRelayoutDebounced(10)
    vi.advanceTimersByTime(10)

    expect(computeLayout).toHaveBeenCalledTimes(1)
    expect(clampCameraPan).toHaveBeenCalledTimes(2)

    vi.useRealTimers()
  })

  it('requestResizeAndLayout coalesces to one scheduled run', () => {
    vi.useFakeTimers()

    const computeLayout = vi.fn()
    const clampCameraPan = vi.fn()

    const coordinator = useLayoutCoordinator({
      canvasEl: ref(null),
      fxCanvasEl: ref(null),
      hostEl: ref(null),
      snapshot: computed(() => ({ generated_at: 't1', nodes: [], links: [] })),
      layoutMode: ref<'admin-force'>('admin-force'),
      dprClamp: computed(() => 1),
      isTestMode: computed(() => false),
      getSourcePath: () => 'src',
      computeLayout,
      clampCameraPan,
    })

    coordinator.requestResizeAndLayout()
    coordinator.requestResizeAndLayout()

    vi.runAllTimers()

    expect(clampCameraPan).toHaveBeenCalledTimes(1)

    vi.useRealTimers()
  })
})
