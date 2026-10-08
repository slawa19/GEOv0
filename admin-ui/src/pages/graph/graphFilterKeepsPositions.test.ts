import cytoscape, { type Core, type LayoutOptions } from 'cytoscape'
import { mount } from '@vue/test-utils'
import { computed, defineComponent, h, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { THROTTLE_GRAPH_REBUILD_MS } from '../../constants/timing'
import { useGraphVisualization } from '../../composables/useGraphVisualization'
import type { Participant, Trustline } from '../../types/domain'
import { useGraphPageWatchers } from './useGraphPageWatchers'

// 035 B3 (F-035-12): a filter (type, minimum degree, threshold, isolates) used to be a full `cy.elements().remove()`
// plus a new layout, so the operator lost the places of the nodes. The real chain is mounted here - the page's
// watchers driving the real visualization composable on a headless Core - and the observable is the node position.

const line = (from: string, to: string): Trustline => ({
  from,
  to,
  equivalent: 'UAH',
  limit: '10.00',
  used: '1.00',
  available: '9.00',
  status: 'active',
  created_at: '2026-01-01T00:00:00Z',
})

function mountPage() {
  const participants = ref<Participant[]>([
    { pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' },
    { pid: 'PID_B', display_name: 'Bob', type: 'business', status: 'active' },
    { pid: 'PID_C', display_name: 'Carol', type: 'person', status: 'active' },
  ])
  const trustlines = ref<Trustline[]>([line('PID_A', 'PID_B'), line('PID_B', 'PID_C')])
  const typeFilter = ref<string[]>([])
  const minDegree = ref(0)
  const layouts: LayoutOptions[] = []
  let cy!: Core
  let graph!: ReturnType<typeof useGraphVisualization>

  const wrapper = mount(
    defineComponent({
      setup() {
        const selected = ref(null)
        const searchQuery = ref('')
        const focusPid = ref('')
        const focusMode = ref(false)
        const focusRootPid = ref('')
        const focusDepth = ref<1 | 2>(1)
        const zoom = ref(1)
        const layoutName = ref<'fcose' | 'grid' | 'circle'>('grid')
        const layoutSpacing = ref(1)
        const threshold = ref('0.10')
        const hideIsolates = ref(false)
        const showLabels = ref(true)
        const labelModeBusiness = ref<'off' | 'name' | 'pid' | 'both'>('name')
        const labelModePerson = ref<'off' | 'name' | 'pid' | 'both'>('name')
        const autoLabelsByZoom = ref(false)
        const minZoomLabelsAll = ref(1)
        const minZoomLabelsPerson = ref(1)
        graph = useGraphVisualization({
          cyRoot: ref(document.createElement('div')),
          createCy: () => {
            cy = cytoscape({ headless: true, styleEnabled: true, elements: [] })
            const original = cy.layout.bind(cy)
            vi.spyOn(cy, 'layout').mockImplementation((o) => {
              layouts.push(o)
              return original(o)
            })
            return cy
          },
          threshold,
          typeFilter,
          minDegree,
          hideIsolates,
          participants,
          filteredTrustlines: computed(() => trustlines.value),
          selected,
          drawerOpen: ref(false),
          drawerTab: ref('summary'),
          searchQuery,
          focusPid,
          focusMode,
          focusRootPid,
          focusDepth,
          setFocusRoot: () => undefined,
          showLabels,
          labelModeBusiness,
          labelModePerson,
          autoLabelsByZoom,
          minZoomLabelsAll,
          minZoomLabelsPerson,
          zoom,
          layoutName,
          layoutSpacing,
          activeConnectionKey: ref(''),
          extractPidFromText: () => null,
        })
        useGraphPageWatchers({
          eq: ref(''),
          statusFilter: ref<string[]>(['active']),
          threshold,
          hideIsolates,
          typeFilter,
          minDegree,
          focusMode,
          focusDepth,
          focusRootPid,
          ensureFocusRootPid: () => undefined,
          refreshForFocusMode: async () => true,
          refreshSnapshotForEq: async () => true,
          invalidateDataOwnership: () => undefined,
          selected,
          showLabels,
          labelModeBusiness,
          labelModePerson,
          autoLabelsByZoom,
          minZoomLabelsAll,
          minZoomLabelsPerson,
          searchQuery,
          focusPid,
          zoom,
          layoutName,
          layoutSpacing,
          graphViz: graph,
        })
        return () => h('div')
      },
    }),
  )
  return { wrapper, graph: () => graph, cy: () => cy, layouts, typeFilter, minDegree, participants }
}

async function settleFilter() {
  await nextTick()
  vi.advanceTimersByTime(THROTTLE_GRAPH_REBUILD_MS + 1)
}

const at = (cy: Core, pid: string) => ({ ...cy.getElementById(pid).position() })

describe('a filter on the admin graph keeps the places of the nodes (035 B3, F-035-12)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('keeps a dragged node where it was when its neighbours are filtered out and when they come back', async () => {
    const page = mountPage()
    expect(page.graph().initCy()).toBe(true)
    const cy = page.cy()
    cy.getElementById('PID_A').position({ x: 500, y: 300 })
    cy.getElementById('PID_B').position({ x: -200, y: 50 })
    cy.getElementById('PID_C').position({ x: 40, y: -400 })
    const layoutsBefore = page.layouts.length

    // Minimum degree 2 keeps only Bob (the one node with two lines); Alice and Carol leave the Core.
    page.minDegree.value = 2
    await settleFilter()
    expect(cy.nodes().map((n) => n.id())).toEqual(['PID_B'])
    expect(at(cy, 'PID_B')).toEqual({ x: -200, y: 50 })

    page.minDegree.value = 0
    await settleFilter()
    expect(cy.nodes().map((n) => n.id()).sort()).toEqual(['PID_A', 'PID_B', 'PID_C'])
    expect(at(cy, 'PID_A')).toEqual({ x: 500, y: 300 })
    expect(at(cy, 'PID_B')).toEqual({ x: -200, y: 50 })
    expect(at(cy, 'PID_C')).toEqual({ x: 40, y: -400 })
    expect(page.layouts.length).toBe(layoutsBefore)

    page.wrapper.unmount()
  })

  it('still lays out a node it has never placed, and a full rebuild still lays out everything', async () => {
    const page = mountPage()
    expect(page.graph().initCy()).toBe(true)
    const cy = page.cy()
    cy.getElementById('PID_A').position({ x: 500, y: 300 })
    const layoutsBefore = page.layouts.length

    // A node the Core has not seen yet appears with the filter: it has no place, so the layout runs.
    page.participants.value = [...page.participants.value, { pid: 'PID_D', display_name: 'Dan', type: 'person', status: 'active' }]
    page.minDegree.value = 0
    page.typeFilter.value = ['person', 'business']
    await settleFilter()
    expect(cy.getElementById('PID_D').length).toBe(1)
    expect(page.layouts.length).toBe(layoutsBefore + 1)

    // The explicit relayout (not a filter) places the nodes anew.
    cy.getElementById('PID_A').position({ x: 9000, y: 9000 })
    page.graph().rebuildGraph({ fit: true })
    expect(at(cy, 'PID_A')).not.toEqual({ x: 9000, y: 9000 })

    page.wrapper.unmount()
  })
})
