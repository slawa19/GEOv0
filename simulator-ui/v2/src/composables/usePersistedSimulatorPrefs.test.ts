import { describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'
import type { LayoutMode } from '../layout/forceLayout'
import type { LabelsLod, Quality } from '../types/uiPrefs'
import { usePersistedSimulatorPrefs } from './usePersistedSimulatorPrefs'

type MemStorage = {
  getItem: (k: string) => string | null
  setItem: (k: string, v: string) => void
  _data: Map<string, string>
}

function createMemStorage(seed?: Record<string, string>): MemStorage {
  const _data = new Map<string, string>(seed ? Object.entries(seed) : [])
  return {
    _data,
    getItem: (k) => (_data.has(k) ? _data.get(k)! : null),
    setItem: (k, v) => {
      _data.set(k, String(v))
    },
  }
}

describe('usePersistedSimulatorPrefs', () => {
  it('loads valid values from storage and ignores invalid', () => {
    const storage = createMemStorage({
      'geo.sim.layoutMode': 'type-split',
      'geo.sim.quality': 'med',
      'geo.sim.labelsLod': 'neighbors',
    })

    const layoutMode = ref<LayoutMode>('admin-force')
    const quality = ref<Quality>('high')
    const labelsLod = ref<LabelsLod>('selection')

    const prefs = usePersistedSimulatorPrefs({
      layoutMode,
      quality,
      labelsLod,
      requestResizeAndLayout: () => undefined,
      storage,
      timers: { setTimeout: () => 0, clearTimeout: () => undefined },
    })

    prefs.loadFromStorage()

    expect(layoutMode.value).toBe('type-split')
    expect(quality.value).toBe('med')
    expect(labelsLod.value).toBe('neighbors')

    storage.setItem('geo.sim.quality', 'INVALID')
    prefs.loadFromStorage()
    expect(quality.value).toBe('med')
  })

  it('persists changes with debounce and calls requestResizeAndLayout on quality change', async () => {
    vi.useFakeTimers()

    const storage = createMemStorage()
    const requestResizeAndLayout = vi.fn()

    const layoutMode = ref<LayoutMode>('admin-force')
    const quality = ref<Quality>('high')
    const labelsLod = ref<LabelsLod>('selection')

    const prefs = usePersistedSimulatorPrefs({
      layoutMode,
      quality,
      labelsLod,
      requestResizeAndLayout,
      storage,
      timers: {
        setTimeout: (fn, ms) => setTimeout(fn, ms) as unknown as number,
        clearTimeout: (id) => clearTimeout(id as unknown as any),
      },
      debounceMs: 250,
    })

    layoutMode.value = 'status-split'
    await nextTick()

    vi.advanceTimersByTime(249)
    expect(storage._data.get('geo.sim.layoutMode')).toBeUndefined()
    vi.advanceTimersByTime(1)
    expect(storage._data.get('geo.sim.layoutMode')).toBe('status-split')

    quality.value = 'low'
    await nextTick()
    expect(requestResizeAndLayout).toHaveBeenCalledTimes(1)

    vi.advanceTimersByTime(250)
    expect(storage._data.get('geo.sim.quality')).toBe('low')

    labelsLod.value = 'off'
    await nextTick()
    vi.advanceTimersByTime(250)
    expect(storage._data.get('geo.sim.labelsLod')).toBe('off')

    prefs.dispose()
    vi.useRealTimers()
  })

  it('dispose cancels pending writes', async () => {
    vi.useFakeTimers()

    const storage = createMemStorage()

    const layoutMode = ref<LayoutMode>('admin-force')
    const quality = ref<Quality>('high')
    const labelsLod = ref<LabelsLod>('selection')

    const prefs = usePersistedSimulatorPrefs({
      layoutMode,
      quality,
      labelsLod,
      requestResizeAndLayout: () => undefined,
      storage,
      timers: {
        setTimeout: (fn, ms) => setTimeout(fn, ms) as unknown as number,
        clearTimeout: (id) => clearTimeout(id as unknown as any),
      },
      debounceMs: 250,
    })

    layoutMode.value = 'community-clusters'
    await nextTick()

    prefs.dispose()

    vi.advanceTimersByTime(250)
    expect(storage._data.get('geo.sim.layoutMode')).toBeUndefined()

    vi.useRealTimers()
  })
})
