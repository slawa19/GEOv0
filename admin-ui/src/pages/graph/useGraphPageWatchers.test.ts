import { computed, effectScope, nextTick, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { SelectedInfo } from '../../composables/useGraphVisualization'
import { useLatestRequest, type LatestRequest } from '../../composables/useLatestRequest'
import { useGraphPageWatchers } from './useGraphPageWatchers'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => {
    resolve = res
  })
  return { promise, resolve }
}

describe('useGraphPageWatchers', () => {
  it.each([
    { name: 'renders the successful eq refresh and supersedes the mount render', applied: true },
    { name: 'preserves the successful mount render when the eq refresh fails', applied: false },
  ])('$name', async ({ applied }) => {
    const pendingEqRefresh = deferred<boolean>()
    const applyGraphView = vi.fn().mockReturnValue(true)
    const eq = ref('')
    const scope = effectScope()
    let mountRequest!: LatestRequest

    scope.run(() => {
      const graphEffectRequests = useLatestRequest()
      mountRequest = graphEffectRequests.begin()
      useGraphPageWatchers({
        isRealMode: computed(() => false),
        eq,
        statusFilter: ref<string[]>(['active']),
        threshold: ref('0.10'),
        showIncidents: ref(true),
        hideIsolates: ref(true),
        typeFilter: ref<string[]>([]),
        minDegree: ref(0),
        focusMode: ref(false),
        focusDepth: ref<1 | 2>(1),
        focusRootPid: ref(''),
        ensureFocusRootPid: vi.fn(),
        refreshForFocusMode: vi.fn().mockResolvedValue(true),
        refreshSnapshotForEq: vi.fn().mockReturnValue(pendingEqRefresh.promise),
        refreshClearingCyclesForParticipant: vi.fn().mockResolvedValue(true),
        invalidateDataOwnership: vi.fn(),
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
        graphEffectRequests,
        applyGraphView,
        graphViz: {
          rebuildGraph: vi.fn(),
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

    eq.value = 'UAH'
    await nextTick()
    expect(mountRequest.isCurrent()).toBe(true)

    pendingEqRefresh.resolve(applied)
    await pendingEqRefresh.promise
    await nextTick()

    if (applied) {
      expect(mountRequest.isCurrent()).toBe(false)
      expect(applyGraphView).toHaveBeenCalledTimes(1)
      expect(applyGraphView).toHaveBeenLastCalledWith({ fit: false })
    } else {
      expect(mountRequest.isCurrent()).toBe(true)
      expect(applyGraphView).not.toHaveBeenCalled()
    }

    scope.stop()
  })

  it('waits for the mount data owner before starting a watcher refresh', async () => {
    const pendingMount = deferred<void>()
    const refreshSnapshotForEq = vi.fn().mockResolvedValue(false)
    const applyGraphView = vi.fn().mockReturnValue(true)
    const eq = ref('')
    const scope = effectScope()
    let mountRequest!: LatestRequest

    scope.run(() => {
      const graphEffectRequests = useLatestRequest()
      mountRequest = graphEffectRequests.begin()
      useGraphPageWatchers({
        isRealMode: computed(() => false),
        eq,
        statusFilter: ref<string[]>(['active']),
        threshold: ref('0.10'),
        showIncidents: ref(true),
        hideIsolates: ref(true),
        typeFilter: ref<string[]>([]),
        minDegree: ref(0),
        focusMode: ref(false),
        focusDepth: ref<1 | 2>(1),
        focusRootPid: ref(''),
        ensureFocusRootPid: vi.fn(),
        refreshForFocusMode: vi.fn().mockResolvedValue(false),
        refreshSnapshotForEq,
        refreshClearingCyclesForParticipant: vi.fn().mockResolvedValue(true),
        invalidateDataOwnership: vi.fn(),
        waitForPendingGraphLoad: () => pendingMount.promise,
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
        graphEffectRequests,
        applyGraphView,
        graphViz: {
          rebuildGraph: vi.fn(),
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

    eq.value = 'UAH'
    await nextTick()
    expect(refreshSnapshotForEq).not.toHaveBeenCalled()
    expect(mountRequest.isCurrent()).toBe(true)

    applyGraphView({ fit: true })
    pendingMount.resolve()
    await pendingMount.promise
    await nextTick()

    expect(refreshSnapshotForEq).toHaveBeenCalledTimes(1)
    expect(mountRequest.isCurrent()).toBe(true)
    expect(applyGraphView).toHaveBeenCalledTimes(1)
    expect(applyGraphView).toHaveBeenCalledWith({ fit: true })
    scope.stop()
  })

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
        invalidateDataOwnership: vi.fn(),
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

  it('refreshes focus exit without waiting for a hung mount data owner', async () => {
    const pendingFocus = deferred<boolean>()
    const exitFocus = deferred<boolean>()
    const blockedMount = deferred<void>()
    const focusMode = ref(false)
    const waitForPendingGraphLoad = vi.fn(() => blockedMount.promise)
    const invalidateDataOwnership = vi.fn()
    const refreshForFocusMode = vi.fn()
      .mockReturnValueOnce(pendingFocus.promise)
      .mockReturnValueOnce(exitFocus.promise)
    const applyGraphView = vi.fn().mockReturnValue(true)
    const scope = effectScope()

    scope.run(() => {
      useGraphPageWatchers({
        isRealMode: computed(() => true),
        eq: ref('EUR'),
        statusFilter: ref<string[]>(['active']),
        threshold: ref('0.10'),
        showIncidents: ref(true),
        hideIsolates: ref(true),
        typeFilter: ref<string[]>([]),
        minDegree: ref(0),
        focusMode,
        focusDepth: ref<1 | 2>(1),
        focusRootPid: ref('PID_A'),
        ensureFocusRootPid: vi.fn(),
        refreshForFocusMode,
        refreshSnapshotForEq: vi.fn().mockResolvedValue(true),
        refreshClearingCyclesForParticipant: vi.fn().mockResolvedValue(true),
        invalidateDataOwnership,
        waitForPendingGraphLoad,
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
        applyGraphView,
        graphViz: {
          rebuildGraph: vi.fn(),
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

    focusMode.value = true
    await nextTick()
    expect(refreshForFocusMode).toHaveBeenCalledTimes(1)

    focusMode.value = false
    await nextTick()

    expect(invalidateDataOwnership).toHaveBeenCalledTimes(2)
    expect(waitForPendingGraphLoad).not.toHaveBeenCalled()
    expect(refreshForFocusMode).toHaveBeenCalledTimes(2)

    exitFocus.resolve(true)
    await exitFocus.promise
    await nextTick()
    expect(applyGraphView).toHaveBeenCalledTimes(1)
    expect(applyGraphView).toHaveBeenCalledWith({ fit: true })

    pendingFocus.resolve(true)
    await pendingFocus.promise
    await nextTick()
    expect(applyGraphView).toHaveBeenCalledTimes(1)

    scope.stop()
    blockedMount.resolve()
    await blockedMount.promise
  })

  it.each(['older-first', 'latest-first'] as const)(
    'keeps the latest visual selection when cycle refreshes resolve %s',
    async (resolutionOrder) => {
      const older = deferred<boolean>()
      const latest = deferred<boolean>()
      const refreshClearingCyclesForParticipant = vi.fn()
        .mockReturnValueOnce(older.promise)
        .mockReturnValueOnce(latest.promise)
      const applySelectedHighlight = vi.fn()
      const clearCycleHighlight = vi.fn()
      const clearConnectionHighlight = vi.fn()
      const selected = ref<SelectedInfo | null>(null)
      const scope = effectScope()

      scope.run(() => {
        useGraphPageWatchers({
          isRealMode: computed(() => true),
          eq: ref('USD'),
          statusFilter: ref<string[]>(['active']),
          threshold: ref('0.10'),
          showIncidents: ref(true),
          hideIsolates: ref(true),
          typeFilter: ref<string[]>([]),
          minDegree: ref(0),
          focusMode: ref(false),
          focusDepth: ref<1 | 2>(1),
          focusRootPid: ref(''),
          ensureFocusRootPid: vi.fn(),
          refreshForFocusMode: vi.fn().mockResolvedValue(true),
          refreshSnapshotForEq: vi.fn().mockResolvedValue(true),
          refreshClearingCyclesForParticipant,
          invalidateDataOwnership: vi.fn(),
          selected,
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
            rebuildGraph: vi.fn(),
            runLayout: vi.fn(),
            clearCycleHighlight,
            clearConnectionHighlight,
            applySelectedHighlight,
            applyStyle: vi.fn(),
            updateLabelsForZoom: vi.fn(),
            updateSearchHighlights: vi.fn(),
            syncZoomFromControl: vi.fn(),
          },
        })
      })

      selected.value = { kind: 'node', pid: 'PID_A', degree: 1, inDegree: 1, outDegree: 0 }
      await nextTick()
      selected.value = { kind: 'node', pid: 'PID_B', degree: 1, inDegree: 0, outDegree: 1 }
      await nextTick()

      expect(refreshClearingCyclesForParticipant.mock.calls).toEqual([['PID_A'], ['PID_B']])
      expect(applySelectedHighlight.mock.calls).toEqual([['PID_A'], ['PID_B']])
      expect(clearCycleHighlight).toHaveBeenCalledTimes(2)
      expect(clearConnectionHighlight).toHaveBeenCalledTimes(2)

      if (resolutionOrder === 'older-first') {
        older.resolve(false)
        await older.promise
        latest.resolve(true)
        await latest.promise
      } else {
        latest.resolve(true)
        await latest.promise
        older.resolve(false)
        await older.promise
      }
      await nextTick()

      expect(applySelectedHighlight.mock.calls).toEqual([['PID_A'], ['PID_B']])
      scope.stop()
    },
  )

  it('does not rebuild after a pending refresh resolves following scope disposal', async () => {
    const pending = deferred<boolean>()
    const refreshSnapshotForEq = vi.fn().mockReturnValue(pending.promise)
    const rebuildGraph = vi.fn()
    const eq = ref('EUR')
    const scope = effectScope()

    scope.run(() => {
      useGraphPageWatchers({
        isRealMode: computed(() => true),
        eq,
        statusFilter: ref<string[]>(['active']),
        threshold: ref('0.10'),
        showIncidents: ref(true),
        hideIsolates: ref(true),
        typeFilter: ref<string[]>([]),
        minDegree: ref(0),
        focusMode: ref(false),
        focusDepth: ref<1 | 2>(1),
        focusRootPid: ref(''),
        ensureFocusRootPid: vi.fn(),
        refreshForFocusMode: vi.fn().mockResolvedValue(true),
        refreshSnapshotForEq,
        refreshClearingCyclesForParticipant: vi.fn().mockResolvedValue(true),
        invalidateDataOwnership: vi.fn(),
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

    eq.value = 'USD'
    await nextTick()
    expect(refreshSnapshotForEq).toHaveBeenCalledTimes(1)
    scope.stop()
    pending.resolve(true)
    await pending.promise
    await nextTick()

    expect(rebuildGraph).not.toHaveBeenCalled()
  })
})
