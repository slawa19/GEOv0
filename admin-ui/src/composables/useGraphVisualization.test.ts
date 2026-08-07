import cytoscape, { type Core } from 'cytoscape'
import { mount } from '@vue/test-utils'
import { computed, defineComponent, h, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import type { Participant } from '../pages/graph/graphTypes'
import { useGraphVisualization, type SelectedInfo } from './useGraphVisualization'

describe('useGraphVisualization', () => {
  it('restores the current node selection after rebuild and ignores an absent node', () => {
    let cy: Core | null = cytoscape({ headless: true, styleEnabled: true, elements: [] })
    const selected = ref<SelectedInfo | null>({
      kind: 'node',
      pid: 'PID_A',
      degree: 0,
      inDegree: 0,
      outDegree: 0,
    })
    const participants = ref<Participant[]>([
      { pid: 'PID_A', display_name: 'A', type: 'person', status: 'active' },
      { pid: 'PID_B', display_name: 'B', type: 'person', status: 'active' },
    ])
    let graph!: ReturnType<typeof useGraphVisualization>
    const wrapper = mount(
      defineComponent({
        setup() {
          graph = useGraphVisualization({
            cyRoot: ref(null),
            getCy: () => cy,
            setCy: (next) => {
              cy = next
            },
            threshold: ref('0.10'),
            typeFilter: ref<string[]>([]),
            minDegree: ref(0),
            hideIsolates: ref(false),
            showIncidents: ref(false),
            participants,
            filteredTrustlines: computed(() => []),
            incidentRatioByPid: computed(() => new Map<string, number>()),
            selected,
            drawerOpen: ref(false),
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

    graph.rebuildGraph()
    expect(cy!.getElementById('PID_A').hasClass('selected-node')).toBe(true)

    selected.value = { kind: 'node', pid: 'PID_B', degree: 0, inDegree: 0, outDegree: 0 }
    graph.rebuildGraph()
    expect(cy!.getElementById('PID_A').hasClass('selected-node')).toBe(false)
    expect(cy!.getElementById('PID_B').hasClass('selected-node')).toBe(true)

    selected.value = { kind: 'node', pid: 'PID_MISSING', degree: 0, inDegree: 0, outDegree: 0 }
    expect(() => graph.rebuildGraph()).not.toThrow()
    expect(cy!.nodes('.selected-node')).toHaveLength(0)

    wrapper.unmount()
  })
})
