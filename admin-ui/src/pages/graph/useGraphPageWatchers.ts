import { onScopeDispose, watch, type ComputedRef, type Ref } from 'vue'

import { THROTTLE_GRAPH_REBUILD_MS, THROTTLE_LAYOUT_SPACING_MS } from '../../constants/timing'
import { throttle } from '../../utils/throttle'
import { useLatestRequest } from '../../composables/useLatestRequest'

import type { GraphRebuildOptions, LabelMode, SelectedInfo } from '../../composables/useGraphVisualization'

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
  refreshForFocusMode: () => Promise<boolean>
  refreshSnapshotForEq: () => Promise<boolean>
  refreshClearingCyclesForParticipant: (pid: string) => Promise<boolean>

  selected: Ref<SelectedInfo | null>

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

  graphEffectRequests?: ReturnType<typeof useLatestRequest>
  applyGraphView?: (opts: GraphRebuildOptions) => boolean

  graphViz: {
    rebuildGraph: (opts?: GraphRebuildOptions) => void
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
  const graphEffectRequests = opts.graphEffectRequests ?? useLatestRequest()
  const applyGraphView = opts.applyGraphView ?? ((rebuildOptions: GraphRebuildOptions) => {
    opts.graphViz.rebuildGraph(rebuildOptions)
    return true
  })

  const throttledRebuild = throttle(() => {
    applyGraphView({ fit: false })
  }, THROTTLE_GRAPH_REBUILD_MS)

  const throttledLayoutSpacing = throttle(() => {
    opts.graphViz.runLayout()
  }, THROTTLE_LAYOUT_SPACING_MS)

  onScopeDispose(() => {
    throttledRebuild.cancel()
    throttledLayoutSpacing.cancel()
  })

  watch([opts.threshold, opts.showIncidents, opts.hideIsolates], () => {
    throttledRebuild()
  })

  watch(opts.statusFilter, () => {
    if (!opts.focusMode.value) throttledRebuild()
  })

  async function refreshGraph(
    refresh: () => Promise<boolean>,
    rebuildOptions: { fit: boolean },
  ) {
    const request = graphEffectRequests.begin()
    const applied = await refresh()
    if (!applied || !request.isCurrent()) return
    applyGraphView(rebuildOptions)
  }

  watch(opts.eq, () => {
    if (opts.focusMode.value) return
    void refreshGraph(opts.refreshSnapshotForEq, { fit: false })
  })

  watch([opts.typeFilter, opts.minDegree], () => {
    throttledRebuild()
  })

  watch([opts.focusMode, opts.focusDepth, opts.focusRootPid], () => {
    if (opts.focusMode.value) opts.ensureFocusRootPid()
    void refreshGraph(opts.refreshForFocusMode, { fit: true })
  })

  watch(
    [
      () => String(opts.eq.value || '').trim().toUpperCase(),
      () => (opts.statusFilter.value || [])
        .map((status) => String(status || '').trim().toLowerCase())
        .filter(Boolean)
        .sort()
        .join('\u0000'),
    ],
    () => {
      if (!opts.focusMode.value) return
      void refreshGraph(opts.refreshForFocusMode, { fit: true })
    },
  )

  watch(
    () => (opts.selected.value && opts.selected.value.kind === 'node' ? opts.selected.value.pid : ''),
    (pid) => {
      opts.graphViz.clearCycleHighlight()
      opts.graphViz.clearConnectionHighlight()
      opts.graphViz.applySelectedHighlight(pid)

      if (opts.isRealMode.value && !opts.focusMode.value) {
        const p = String(pid || '').trim()
        void opts.refreshClearingCyclesForParticipant(p).catch(() => {
          // Keep the current cycle data; visual selection is independent of this fetch.
        })
      }
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
