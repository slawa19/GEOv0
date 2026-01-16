import { watch, type Ref } from 'vue'

import type { AnalyticsToggles } from './graphAnalyticsToggles'

export const STORAGE_KEYS = {
  showLegend: 'geo.graph.showLegend',
  layoutSpacing: 'geo.graph.layoutSpacing',
  toolbarTab: 'geo.graph.toolbarTab',
  drawerEq: 'geo.graph.analytics.drawerEq',
  analyticsToggles: 'geo.graph.analytics.toggles.v1',
} as const

type ToolbarTab = 'filters' | 'display' | 'navigate'

export function useGraphPageStorage(input: {
  showLegend: Ref<boolean>
  layoutSpacing: Ref<number>
  toolbarTab: Ref<ToolbarTab>
  drawerEq: Ref<string>
  analytics: Ref<AnalyticsToggles>

  storage?: Storage
}) {
  const storage = input.storage ?? window.localStorage

  function restore() {
    try {
      const rawLegend = storage.getItem(STORAGE_KEYS.showLegend)
      if (rawLegend !== null) input.showLegend.value = rawLegend === '1'

      const rawTab = storage.getItem(STORAGE_KEYS.toolbarTab)
      if (rawTab === 'filters' || rawTab === 'display' || rawTab === 'navigate') input.toolbarTab.value = rawTab

      const rawSpacing = storage.getItem(STORAGE_KEYS.layoutSpacing)
      if (rawSpacing !== null) {
        const parsed = Number(rawSpacing)
        if (Number.isFinite(parsed)) input.layoutSpacing.value = parsed
      }

      const rawDrawerEq = storage.getItem(STORAGE_KEYS.drawerEq)
      if (rawDrawerEq) input.drawerEq.value = String(rawDrawerEq)

      const rawToggles = storage.getItem(STORAGE_KEYS.analyticsToggles)
      if (rawToggles) {
        const parsed = JSON.parse(rawToggles) as Partial<AnalyticsToggles>
        input.analytics.value = {
          ...input.analytics.value,
          ...parsed,
        }
      }
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

  watch(
    input.analytics,
    (v) => {
      try {
        storage.setItem(STORAGE_KEYS.analyticsToggles, JSON.stringify(v))
      } catch {
        // ignore
      }
    },
    { deep: true }
  )

  watch(input.toolbarTab, (v) => {
    try {
      storage.setItem(STORAGE_KEYS.toolbarTab, v)
    } catch {
      // ignore
    }
  })

  return { restore }
}
