import { shallowMount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import GraphAnalyticsDrawer from './GraphAnalyticsDrawer.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
}))

describe('GraphAnalyticsDrawer', () => {
  it('delegates refresh to the current-view reload supplied by GraphPage', async () => {
    const reloadCurrentView = vi.fn()
    const wrapper = shallowMount(GraphAnalyticsDrawer, {
      props: {
        modelValue: true,
        tab: 'summary',
        eq: 'EUR',
        analytics: {
          showRank: false,
          showDistribution: false,
          showConcentration: false,
          showCapacity: false,
          showBottlenecks: false,
          showActivity: false,
        },
        connectionsIncomingPage: 1,
        connectionsOutgoingPage: 1,
        selected: { kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 },
        showIncidents: false,
        incidentRatioByPid: new Map(),
        availableEquivalents: ['EUR'],
        analyticsEq: 'EUR',
        threshold: '0.10',
        precisionByEq: new Map([['EUR', 2]]),
        atomsToDecimal: (atoms: bigint) => String(atoms),
        reloadCurrentView,
        money: (value: string) => value,
        pct: (value: number) => String(value),
        selectedRank: null,
        selectedConcentration: {
          eq: null,
          outgoing: { top1: 0, top5: 0, hhi: 0, level: { label: '', type: 'success' } },
          incoming: { top1: 0, top5: 0, hhi: 0, level: { label: '', type: 'success' } },
        },
        selectedCapacity: null,
        selectedActivity: null,
        netDistribution: null,
        selectedBalanceRows: [],
        selectedCounterpartySplit: {
          eq: null,
          totalDebtAtoms: 0n,
          totalCreditAtoms: 0n,
          creditors: [],
          debtors: [],
        },
        selectedConnectionsIncoming: [],
        selectedConnectionsOutgoing: [],
        selectedConnectionsIncomingPaged: [],
        selectedConnectionsOutgoingPaged: [],
        connectionsPageSize: 10,
        onConnectionRowClick: vi.fn(),
        selectedCycles: [],
        isCycleActive: () => false,
        toggleCycleHighlight: vi.fn(),
        summaryToggleItems: [],
        balanceToggleItems: [],
        riskToggleItems: [],
      },
      global: {
        stubs: {
          'el-drawer': { template: '<section><slot /></section>' },
          'el-button': { template: '<button v-bind="$attrs"><slot /></button>' },
          'el-descriptions': true,
          'el-descriptions-item': true,
          'el-divider': true,
          'el-select': true,
          'el-option': true,
          'el-tabs': true,
          'el-tab-pane': true,
          'el-alert': true,
          'el-card': true,
          'el-progress': true,
          'el-tag': true,
          'el-empty': true,
          'el-pagination': true,
          'el-table': true,
          'el-table-column': true,
          'el-collapse': true,
          'el-collapse-item': true,
        },
      },
    })

    await wrapper.get('[data-testid="refresh-current-graph-view"]').trigger('click')

    expect(reloadCurrentView).toHaveBeenCalledTimes(1)
  })
})
