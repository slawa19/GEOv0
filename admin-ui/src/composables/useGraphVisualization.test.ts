import cytoscape, { type Core } from 'cytoscape'
import { mount } from '@vue/test-utils'
import { computed, defineComponent, h, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { Participant, Trustline } from '../pages/graph/graphTypes'
import { graphSelectionAnnouncement, useGraphVisualization, type SelectedInfo } from './useGraphVisualization'

function mountGraph(input?: { trustlines?: Trustline[]; selected?: SelectedInfo | null }) {
  const selected = ref<SelectedInfo | null>(input?.selected ?? null)
  const drawerOpen = ref(false)
  const participants = ref<Participant[]>([
    { pid: 'PID_A', display_name: 'Alice', type: 'person', status: 'active' },
    { pid: 'PID_B', display_name: 'Bob', type: 'person', status: 'active' },
  ])
  let createdCy: Core | null = null
  const createCy = vi.fn(() => {
    createdCy = cytoscape({ headless: true, styleEnabled: true, elements: [] })
    return createdCy
  })
  let graph!: ReturnType<typeof useGraphVisualization>
  const wrapper = mount(
    defineComponent({
      setup() {
        graph = useGraphVisualization({
          cyRoot: ref(document.createElement('div')),
          createCy,
          threshold: ref('0.10'),
          typeFilter: ref<string[]>([]),
          minDegree: ref(0),
          hideIsolates: ref(false),
          showIncidents: ref(false),
          participants,
          filteredTrustlines: computed(() => input?.trustlines ?? []),
          incidentRatioByPid: computed(() => new Map<string, number>()),
          selected,
          drawerOpen,
          drawerTab: ref('summary'),
          searchQuery: ref(''),
          focusPid: ref(''),
          focusMode: ref(false),
          focusRootPid: ref(''),
          focusDepth: ref<1 | 2>(1),
          setFocusRoot: () => undefined,
          showLabels: ref(true),
          labelModeBusiness: ref('name'),
          labelModePerson: ref('name'),
          autoLabelsByZoom: ref(false),
          minZoomLabelsAll: ref(1),
          minZoomLabelsPerson: ref(1),
          zoom: ref(1),
          layoutName: ref('grid'),
          layoutSpacing: ref(1),
          activeCycleKey: ref(''),
          activeConnectionKey: ref(''),
          extractPidFromText: () => null,
        })
        return () => h('div')
      },
    }),
  )
  return { wrapper, graph, selected, drawerOpen, createCy, getCreatedCy: () => createdCy }
}

describe('useGraphVisualization', () => {
  it('announces selection without claiming that closed details are open', () => {
    const node: SelectedInfo = { kind: 'node', pid: 'PID_A', display_name: 'Alice', degree: 0, inDegree: 0, outDegree: 0 }
    const closed = graphSelectionAnnouncement(node, false)
    const open = graphSelectionAnnouncement(node, true)

    expect(closed).toContain('Selected node Alice')
    expect(closed).not.toContain('Details opened')
    expect(open).toContain('Details opened')
  })

  it('owns one Core, restores node selection after rebuild, and destroys once', () => {
    const mounted = mountGraph({
      selected: { kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 },
    })

    expect(mounted.graph.initCy()).toBe(true)
    expect(mounted.graph.initCy()).toBe(true)
    expect(mounted.createCy).toHaveBeenCalledTimes(1)
    const cy = mounted.graph.getCy()
    if (!cy) throw new Error('Expected Cytoscape Core')
    const destroy = vi.spyOn(cy, 'destroy')

    expect(cy.getElementById('PID_A').hasClass('selected-node')).toBe(true)

    mounted.selected.value = { kind: 'node', pid: 'PID_B', degree: 0, inDegree: 0, outDegree: 0 }
    mounted.graph.rebuildGraph()
    expect(cy.getElementById('PID_A').hasClass('selected-node')).toBe(false)
    expect(cy.getElementById('PID_B').hasClass('selected-node')).toBe(true)

    mounted.selected.value = { kind: 'node', pid: 'PID_MISSING', degree: 0, inDegree: 0, outDegree: 0 }
    expect(() => mounted.graph.rebuildGraph()).not.toThrow()
    expect(cy.nodes('.selected-node')).toHaveLength(0)

    mounted.wrapper.unmount()
    expect(destroy).toHaveBeenCalledTimes(1)
    expect(mounted.graph.getCy()).toBeNull()
  })

  it('opens node and edge details through DOM option keys without a Core', () => {
    const trustline: Trustline = {
      from: 'PID_A',
      to: 'PID_B',
      equivalent: 'EUR',
      limit: '10.00',
      used: '2.00',
      available: '8.00',
      status: 'active',
      created_at: '2026-01-01T00:00:00Z',
    }
    const mounted = mountGraph({ trustlines: [trustline] })
    const options = mounted.graph.graphElementOptions()
    const node = options.find((option) => option.kind === 'node' && option.key === 'node:PID_A')
    const edge = options.find((option) => option.kind === 'edge')
    if (!node || !edge) throw new Error('Expected keyboard node and edge options')

    expect(mounted.graph.openElementDetails(node.key)).toBe(true)
    expect(mounted.selected.value).toMatchObject({ kind: 'node', pid: 'PID_A', inDegree: 0, outDegree: 1 })
    expect(mounted.drawerOpen.value).toBe(true)

    mounted.drawerOpen.value = false
    expect(mounted.graph.openElementDetails(edge.key)).toBe(true)
    expect(mounted.selected.value).toMatchObject({
      kind: 'edge',
      equivalent: 'EUR',
      from: 'PID_A',
      to: 'PID_B',
    })
    expect(mounted.drawerOpen.value).toBe(true)
    expect(mounted.graph.openElementDetails('edge:missing')).toBe(false)

    mounted.wrapper.unmount()
  })
})
