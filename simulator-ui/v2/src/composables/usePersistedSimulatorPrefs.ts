import type { Ref, WatchStopHandle } from 'vue'
import { watch } from 'vue'
import type { LayoutMode } from '../layout/forceLayout'
import type { LabelsLod, Quality } from '../types/uiPrefs'

type StorageLike = {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
}

type TimersLike = {
  setTimeout: (fn: () => void, ms: number) => number
  clearTimeout: (id: number) => void
}

type PersistedKey = 'geo.sim.layoutMode' | 'geo.sim.quality' | 'geo.sim.labelsLod'

type UsePersistedSimulatorPrefsDeps = {
  layoutMode: Ref<LayoutMode>
  quality: Ref<Quality>
  labelsLod: Ref<LabelsLod>
  requestResizeAndLayout: () => void

  debounceMs?: number
  storage?: StorageLike
  timers?: TimersLike
}

type UsePersistedSimulatorPrefsReturn = {
  loadFromStorage: () => void
  dispose: () => void
}

function isOneOf<T extends string>(v: string, allowed: readonly T[]): v is T {
  return (allowed as readonly string[]).includes(v)
}

function safeGet(storage: StorageLike, key: PersistedKey): string {
  try {
    return String(storage.getItem(key) ?? '')
  } catch {
    return ''
  }
}

function safeSet(storage: StorageLike, key: PersistedKey, value: string) {
  try {
    storage.setItem(key, value)
  } catch {
    // ignore
  }
}

export function usePersistedSimulatorPrefs(deps: UsePersistedSimulatorPrefsDeps): UsePersistedSimulatorPrefsReturn {
  const debounceMs = deps.debounceMs ?? 250

  const storage: StorageLike =
    deps.storage ??
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
    (typeof window !== 'undefined' ? window.localStorage : ({ getItem: () => null, setItem: () => undefined } as any))

  const timers: TimersLike =
    deps.timers ??
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
    (typeof window !== 'undefined'
      ? { setTimeout: (fn, ms) => window.setTimeout(fn, ms), clearTimeout: (id) => window.clearTimeout(id) }
      : ({ setTimeout: () => 0, clearTimeout: () => undefined } as any))

  const allowedLayoutModes: readonly LayoutMode[] = [
    'admin-force',
    'community-clusters',
    'balance-split',
    'type-split',
    'status-split',
  ]

  const allowedQuality: readonly Quality[] = ['low', 'med', 'high']
  const allowedLabelsLod: readonly LabelsLod[] = ['off', 'selection', 'neighbors']

  const timersByKey = new Map<PersistedKey, number>()

  function scheduleSave(key: PersistedKey, value: string) {
    const prev = timersByKey.get(key)
    if (prev !== undefined) timers.clearTimeout(prev)

    const id = timers.setTimeout(() => {
      timersByKey.delete(key)
      safeSet(storage, key, value)
    }, debounceMs)

    timersByKey.set(key, id)
  }

  const stopHandles: WatchStopHandle[] = [
    watch(deps.layoutMode, (v) => {
      scheduleSave('geo.sim.layoutMode', v)
    }),
    watch(deps.quality, (v) => {
      scheduleSave('geo.sim.quality', v)
      deps.requestResizeAndLayout()
    }),
    watch(deps.labelsLod, (v) => {
      scheduleSave('geo.sim.labelsLod', v)
    }),
  ]

  function loadFromStorage() {
    const lm = safeGet(storage, 'geo.sim.layoutMode')
    if (isOneOf(lm, allowedLayoutModes)) deps.layoutMode.value = lm

    const q = safeGet(storage, 'geo.sim.quality')
    if (isOneOf(q, allowedQuality)) deps.quality.value = q

    const l = safeGet(storage, 'geo.sim.labelsLod')
    if (isOneOf(l, allowedLabelsLod)) deps.labelsLod.value = l
  }

  function dispose() {
    for (const stop of stopHandles) stop()
    for (const id of timersByKey.values()) timers.clearTimeout(id)
    timersByKey.clear()
  }

  return { loadFromStorage, dispose }
}
