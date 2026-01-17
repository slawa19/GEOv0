import { watch, type ComputedRef, type Ref } from 'vue'

import { api } from '../../api'
import { assertSuccess } from '../../api/envelope'
import { THROTTLE_GRAPH_REBUILD_MS, THROTTLE_LAYOUT_SPACING_MS } from '../../constants/timing'
import { throttle } from '../../utils/throttle'

import type { LabelMode, SelectedInfo } from '../../composables/useGraphVisualization'
import type { ClearingCycles } from './graphTypes'

export function useGraphPageWatchers(opts: {
  isRealMode: ComputedRef<boolean>

  eq: Ref<string>
  statusFilter: Ref<string[]>
  threshold: Ref<string>
  showIncidents: Ref<boolean>
  hideIsolates: Ref<boolean>

  typeFilter: Ref<string[]>
  minDegree: Ref<number>

  focusMode: Ref<boolean>
  focusDepth: Ref<1 | 2>
  focusRootPid: Ref<string>
  ensureFocusRootPid: () => void
  refreshForFocusMode: () => Promise<void>

  selected: Ref<SelectedInfo | null>
  clearingCycles: Ref<ClearingCycles | null>

  showLabels: Ref<boolean>
  labelModeBusiness: Ref<LabelMode>
  labelModePerson: Ref<LabelMode>
  autoLabelsByZoom: Ref<boolean>
  minZoomLabelsAll: Ref<number>
  minZoomLabelsPerson: Ref<number>

  searchQuery: Ref<string>
  focusPid: Ref<string>

  zoom: Ref<number>
  layoutName: Ref<'fcose' | 'grid' | 'circle'>
  layoutSpacing: Ref<number>

  graphViz: {
    rebuildGraph: (opts?: { fit?: boolean }) => void
    runLayout: () => void

    clearCycleHighlight: () => void
    clearConnectionHighlight: () => void
    applySelectedHighlight: (pid: string) => void

    applyStyle: () => void
    updateLabelsForZoom: () => void

    updateSearchHighlights: () => void
    syncZoomFromControl: (z: number) => void
  }
}) {
  const throttledRebuild = throttle(() => {
    opts.graphViz.rebuildGraph({ fit: false })
  }, THROTTLE_GRAPH_REBUILD_MS)

  const throttledLayoutSpacing = throttle(() => {
    opts.graphViz.runLayout()
  }, THROTTLE_LAYOUT_SPACING_MS)

  watch([opts.eq, opts.statusFilter, opts.threshold, opts.showIncidents, opts.hideIsolates], () => {
    throttledRebuild()
  })

  watch([opts.typeFilter, opts.minDegree], () => {
    throttledRebuild()
  })

  watch([opts.focusMode, opts.focusDepth, opts.focusRootPid], () => {
    if (opts.focusMode.value) opts.ensureFocusRootPid()
    void (async () => {
      await opts.refreshForFocusMode()
      opts.graphViz.rebuildGraph({ fit: true })
    })()
  })

  watch(
    () => (opts.selected.value && opts.selected.value.kind === 'node' ? opts.selected.value.pid : ''),
    (pid) => {
      opts.graphViz.clearCycleHighlight()
      opts.graphViz.clearConnectionHighlight()
      void (async () => {
        if (opts.isRealMode.value && !opts.focusMode.value) {
          const p = String(pid || '').trim()
          if (p) {
            try {
              const cc = await api.clearingCycles({ participant_pid: p })
              opts.clearingCycles.value = (assertSuccess(cc) as ClearingCycles | null) ?? null
            } catch {
              // keep previous
            }
          }
        }
        opts.graphViz.applySelectedHighlight(pid)
      })()
    },
  )

  watch(
    [
      opts.showLabels,
      opts.labelModeBusiness,
      opts.labelModePerson,
      opts.autoLabelsByZoom,
      opts.minZoomLabelsAll,
      opts.minZoomLabelsPerson,
    ],
    () => {
      opts.graphViz.applyStyle()
      opts.graphViz.updateLabelsForZoom()
    },
  )

  watch(opts.searchQuery, () => {
    // If user edits the query manually, clear explicit selection.
    opts.focusPid.value = ''
    opts.graphViz.updateSearchHighlights()
  })

  watch(opts.zoom, (z) => {
    opts.graphViz.syncZoomFromControl(z)
  })

  watch(opts.layoutName, () => {
    opts.graphViz.runLayout()
  })

  watch(opts.layoutSpacing, () => {
    throttledLayoutSpacing()
  })
}
