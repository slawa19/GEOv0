import { watch, type Ref } from 'vue'

export const STORAGE_KEYS = {
  showLegend: 'geo.graph.showLegend',
  layoutSpacing: 'geo.graph.layoutSpacing',
  toolbarTab: 'geo.graph.toolbarTab',
  drawerEq: 'geo.graph.analytics.drawerEq',
} as const

type ToolbarTab = 'filters' | 'display'

export function useGraphPageStorage(input: {
  showLegend: Ref<boolean>
  layoutSpacing: Ref<number>
  toolbarTab: Ref<ToolbarTab>
  drawerEq: Ref<string>

  storage?: Storage
}) {
  // Reading `window.localStorage` itself throws when site data is blocked: that must not take the page down.
  // A stand-in whose every call throws goes through the same `try` that every storage use here already has.
  const blocked = {
    getItem: () => { throw new Error('storage blocked') },
    setItem: () => { throw new Error('storage blocked') },
  } as unknown as Storage
  const storage: Storage = (() => {
    try {
      return input.storage ?? window.localStorage
    } catch {
      return blocked
    }
  })()

  function restore() {
    try {
      const rawLegend = storage.getItem(STORAGE_KEYS.showLegend)
      if (rawLegend !== null) input.showLegend.value = rawLegend === '1'

      const rawTab = storage.getItem(STORAGE_KEYS.toolbarTab)
      if (rawTab === 'filters' || rawTab === 'display') input.toolbarTab.value = rawTab
      if (rawTab === 'navigate') input.toolbarTab.value = 'filters'

      const rawSpacing = storage.getItem(STORAGE_KEYS.layoutSpacing)
      if (rawSpacing !== null) {
        const parsed = Number(rawSpacing)
        if (Number.isFinite(parsed)) input.layoutSpacing.value = parsed
      }

      const rawDrawerEq = storage.getItem(STORAGE_KEYS.drawerEq)
      if (rawDrawerEq) input.drawerEq.value = String(rawDrawerEq)
    } catch {
      // ignore storage errors (private mode / blocked)
    }
  }

  watch(input.showLegend, (v) => {
    try {
      storage.setItem(STORAGE_KEYS.showLegend, v ? '1' : '0')
    } catch {
      // ignore
    }
  })

  watch(input.layoutSpacing, (v) => {
    try {
      storage.setItem(STORAGE_KEYS.layoutSpacing, String(v))
    } catch {
      // ignore
    }
  })

  watch(input.drawerEq, (v) => {
    try {
      storage.setItem(STORAGE_KEYS.drawerEq, String(v || 'ALL'))
    } catch {
      // ignore
    }
  })

  watch(input.toolbarTab, (v) => {
    try {
      storage.setItem(STORAGE_KEYS.toolbarTab, v)
    } catch {
      // ignore
    }
  })

  return { restore }
}
