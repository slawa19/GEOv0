import { computed, effectScope, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { SelectedInfo } from '../../composables/useGraphVisualization'
import { useGraphPageWatchers } from './useGraphPageWatchers'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('useGraphPageWatchers', () => {
  it('refreshes normalized focus filters and only rebuilds for the latest request', async () => {
    const older = deferred<boolean>()
    const latest = deferred<boolean>()
    const refreshForFocusMode = vi.fn()
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(latest.promise)
    const rebuildGraph = vi.fn()
    const eq = ref(' eur ')
    const statusFilter = ref<string[]>(['active'])
    const scope = effectScope()

    scope.run(() => {
      useGraphPageWatchers({
        isRealMode: computed(() => true),
        eq,
        statusFilter,
        threshold: ref('0.10'),
        showIncidents: ref(true),
        hideIsolates: ref(true),
        typeFilter: ref<string[]>([]),
        minDegree: ref(0),
        focusMode: ref(true),
        focusDepth: ref<1 | 2>(1),
        focusRootPid: ref('PID_A'),
        ensureFocusRootPid: vi.fn(),
        refreshForFocusMode,
        refreshSnapshotForEq: vi.fn().mockResolvedValue(true),
        refreshClearingCyclesForParticipant: vi.fn().mockResolvedValue(true),
        selected: ref<SelectedInfo | null>(null),
        showLabels: ref(true),
        labelModeBusiness: ref('name'),
        labelModePerson: ref('name'),
        autoLabelsByZoom: ref(true),
        minZoomLabelsAll: ref(1),
        minZoomLabelsPerson: ref(1),
        searchQuery: ref(''),
        focusPid: ref(''),
        zoom: ref(1),
        layoutName: ref('fcose'),
        layoutSpacing: ref(1),
        graphViz: {
          rebuildGraph,
          runLayout: vi.fn(),
          clearCycleHighlight: vi.fn(),
          clearConnectionHighlight: vi.fn(),
          applySelectedHighlight: vi.fn(),
          applyStyle: vi.fn(),
          updateLabelsForZoom: vi.fn(),
          updateSearchHighlights: vi.fn(),
          syncZoomFromControl: vi.fn(),
        },
      })
    })

    eq.value = ' usd '
    await nextTick()
    expect(refreshForFocusMode).toHaveBeenCalledTimes(1)

    eq.value = 'USD'
    await nextTick()
    expect(refreshForFocusMode).toHaveBeenCalledTimes(1)

    statusFilter.value = ['closed']
    await nextTick()
    expect(refreshForFocusMode).toHaveBeenCalledTimes(2)

    latest.resolve(true)
    await latest.promise
    await nextTick()
    expect(rebuildGraph).toHaveBeenCalledTimes(1)

    older.resolve(true)
    await older.promise
    await nextTick()
    expect(rebuildGraph).toHaveBeenCalledTimes(1)

    scope.stop()
  })
})
