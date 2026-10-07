import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import GraphAnalyticsDrawer from './GraphAnalyticsDrawer.vue'
import { formatMoneyByEquivalent } from '../../composables/useEquivalentPrecision'
import type { BalanceRow } from '../../types/domain'

const PRECISION = new Map([['EUR', 2], ['HOUR', 1]])

function row(equivalent: string, net: string): BalanceRow {
  return {
    equivalent,
    outgoing_limit: '0',
    outgoing_used: '0',
    incoming_limit: '0',
    incoming_used: '0',
    total_debt: '0',
    total_credit: '0',
    net,
  }
}

function mountDrawer(over: Record<string, unknown> = {}) {
  return mount(GraphAnalyticsDrawer, {
    props: {
      modelValue: true,
      tab: 'summary',
      eq: 'ALL',
      connectionsIncomingPage: 1,
      connectionsOutgoingPage: 1,
      selected: { kind: 'node', pid: 'PID_A', degree: 0, inDegree: 0, outDegree: 0 },
      availableEquivalents: ['EUR', 'HOUR'],
      reloadCurrentView: vi.fn(),
      money: (value: string, equivalent: unknown) => formatMoneyByEquivalent(value, equivalent, PRECISION),
      metricsLoading: false,
      metricsError: null,
      selectedBalanceRows: [],
      selectedConnectionsIncoming: [],
      selectedConnectionsOutgoing: [],
      selectedConnectionsIncomingPaged: [],
      selectedConnectionsOutgoingPaged: [],
      connectionsPageSize: 10,
      onConnectionRowClick: vi.fn(),
      ...over,
    },
    global: {
      stubs: {
        'el-drawer': { template: '<section><slot /></section>' },
        'el-button': { template: '<button v-bind="$attrs"><slot /></button>' },
        'el-tabs': { template: '<div><slot /></div>' },
        'el-tab-pane': {
          props: ['name', 'label'],
          template: '<section data-testid="drawer-tab" :data-name="name"><slot /></section>',
        },
        'el-card': { template: '<div><slot name="header" /><slot /></div>' },
        'el-alert': { props: ['title'], template: '<div data-testid="drawer-alert">{{ title }}</div>' },
        'el-skeleton': { template: '<div data-testid="drawer-skeleton" />' },
        TooltipLabel: { props: ['label'], template: '<span>{{ label }}</span>' },
        CopyIconButton: true,
        'el-descriptions': true,
        'el-descriptions-item': true,
        'el-divider': true,
        'el-select': true,
        'el-option': true,
        'el-empty': true,
        'el-pagination': true,
        'el-table': true,
        'el-table-column': true,
      },
    },
  })
}

function summaryNetLines(wrapper: ReturnType<typeof mountDrawer>) {
  return wrapper
    .get('[data-testid="drawer-tab"][data-name="summary"]')
    .findAll('[data-testid="graph-summary-net"]')
    .map((line) => line.text())
}

describe('GraphAnalyticsDrawer (032 S5, F-1)', () => {
  it('has exactly the summary, connections and balance tabs', () => {
    const wrapper = mountDrawer()
    expect(wrapper.findAll('[data-testid="drawer-tab"]').map((tab) => tab.attributes('data-name'))).toEqual([
      'summary',
      'connections',
      'balance',
    ])
  })

  it('prints the net position of the selected equivalent from balance_rows', () => {
    const wrapper = mountDrawer({ eq: 'EUR', selectedBalanceRows: [row('EUR', '-1.5')] })
    expect(summaryNetLines(wrapper)).toEqual(['-1.50 EUR'])
  })

  it('without an equivalent prints one net line per equivalent and never their sum', () => {
    const wrapper = mountDrawer({ selectedBalanceRows: [row('EUR', '-1.50'), row('HOUR', '4.0')] })
    const lines = summaryNetLines(wrapper)
    expect(lines).toEqual(['-1.50 EUR', '4.0 HOUR'])
    // A cross-equivalent total (2.50 in either precision) appears nowhere in the summary.
    const summary = wrapper.get('[data-testid="drawer-tab"][data-name="summary"]').text()
    expect(summary).not.toContain('2.50')
    expect(summary).not.toContain('2.5')
  })

  it('shows loading while the metrics are in flight and the error on failure, never a substitute figure', () => {
    const loading = mountDrawer({ metricsLoading: true })
    expect(loading.findAll('[data-testid="drawer-skeleton"]').length).toBeGreaterThan(0)
    expect(summaryNetLines(loading)).toEqual([])

    const failed = mountDrawer({ metricsError: 'metrics unavailable' })
    const summary = failed.get('[data-testid="drawer-tab"][data-name="summary"]')
    expect(summary.get('[data-testid="drawer-alert"]').text()).toBe('metrics unavailable')
    expect(summaryNetLines(failed)).toEqual([])
  })

  it('delegates refresh to the current-view reload supplied by GraphPage', async () => {
    const reloadCurrentView = vi.fn()
    const wrapper = mountDrawer({ reloadCurrentView })

    await wrapper.get('[data-testid="refresh-current-graph-view"]').trigger('click')

    expect(reloadCurrentView).toHaveBeenCalledTimes(1)
  })
})
