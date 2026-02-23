import type { Ref, WatchStopHandle } from 'vue'
import { watch } from 'vue'
import type { LayoutMode } from '../layout/forceLayout'
import { normalizeUiThemeId, type LabelsLod, type Quality, type UiThemeId } from '../types/uiPrefs'

type StorageLike = {
  getItem: (key: string) => string | null
  setItem: (key: string, value: string) => void
  removeItem?: (key: string) => void
}

type TimersLike = {
  setTimeout: (fn: () => void, ms: number) => number
  clearTimeout: (id: number) => void
}

type PersistedKey =
  | 'geo.sim.layoutMode'
  | 'geo.sim.quality'
  | 'geo.sim.labelsLod'
  | 'geo.uiTheme'
  | 'geo.sim.v2.desiredMode'
  | 'geo.sim.v2.fxDebugRun'
  | 'geo.sim.v2.runId'

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

function safeRemove(storage: StorageLike, key: PersistedKey) {
  try {
    storage.removeItem?.(key)
  } catch {
    // ignore
  }
}

export function usePersistedSimulatorPrefs(deps: UsePersistedSimulatorPrefsDeps): UsePersistedSimulatorPrefsReturn {
  const debounceMs = deps.debounceMs ?? 250

  const storage: StorageLike =
    deps.storage ??
    (() => {
      try {
        return (((globalThis as any).window as any)?.localStorage ?? { getItem: () => null, setItem: () => undefined }) as any
      } catch {
        return { getItem: () => null, setItem: () => undefined } as any
      }
    })()

  const timers: TimersLike =
    deps.timers ??
    (() => {
      const w = (globalThis as any).window as any
      return w
        ? { setTimeout: (fn: () => void, ms: number) => w.setTimeout(fn, ms), clearTimeout: (id: number) => w.clearTimeout(id) }
        : ({ setTimeout: () => 0, clearTimeout: () => undefined } as any)
    })()

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

// ---------------------------------------------------------------------------
// useSimulatorStorage — lightweight composable for ad-hoc storage operations
// used by SimulatorAppRoot (theme, mode-switch helpers, fxDebugRun flag).
// Unlike usePersistedSimulatorPrefs it has no reactive watchers and requires
// no layout/quality dependencies.
// ---------------------------------------------------------------------------

export function useSimulatorStorage(storage?: StorageLike) {
  const _storage: StorageLike & { removeItem: (key: string) => void } =
    storage != null
      ? {
          getItem: (k) => { try { return storage.getItem(k) } catch { return null } },
          setItem: (k, v) => { try { storage.setItem(k, v) } catch { /* ignore */ } },
          removeItem: (k) => { try { storage.removeItem?.(k) } catch { /* ignore */ } },
        }
      : ((globalThis as any).window as any)?.localStorage
        ? {
            getItem: (k) => { try { return ((globalThis as any).window as any).localStorage.getItem(k) } catch { return null } },
            setItem: (k, v) => { try { ((globalThis as any).window as any).localStorage.setItem(k, v) } catch { /* ignore */ } },
            removeItem: (k) => { try { ((globalThis as any).window as any).localStorage.removeItem(k) } catch { /* ignore */ } },
          }
        : { getItem: () => null, setItem: () => undefined, removeItem: () => undefined }

  /** Read persisted UI theme (null if absent). */
  function readUiTheme(): UiThemeId | null {
    const v = _storage.getItem('geo.uiTheme')
    if (!v) return null
    return normalizeUiThemeId(v)
  }

  /** Persist UI theme selection. */
  function writeUiTheme(theme: UiThemeId) {
    _storage.setItem('geo.uiTheme', theme)
  }

  /**
   * Force desiredMode = 'real' on next load.
   * Called before navigating away from Demo UI to avoid a topology-only preview snapshot.
   */
  function forceDesiredModeReal() {
    _storage.setItem('geo.sim.v2.desiredMode', 'real')
  }

  /** Returns true when an active FX Debug run was persisted. */
  function isFxDebugRun(): boolean {
    return _storage.getItem('geo.sim.v2.fxDebugRun') === '1'
  }

  /**
   * Clear the persisted FX Debug run state (runId + fxDebugRun flag).
   * Called when exiting Demo UI to prevent a stale fixtures-run snapshot on next load.
   */
  function clearFxDebugRunState() {
    if (!isFxDebugRun()) return
    _storage.removeItem('geo.sim.v2.fxDebugRun')
    _storage.removeItem('geo.sim.v2.runId')
  }

  return {
    readUiTheme,
    writeUiTheme,
    forceDesiredModeReal,
    isFxDebugRun,
    clearFxDebugRunState,
  }
}
