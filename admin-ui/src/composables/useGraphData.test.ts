import { describe, expect, it } from 'vitest'
import { ref } from 'vue'

import {
  computeIncidentRatioByPid,
  computePrimaryEquivalent,
  filterTrustlinesByEqAndStatus,
  normalizeEqCode,
  useGraphData,
} from './useGraphData'
import type { Equivalent, Incident, Trustline } from '../pages/graph/graphTypes'

describe('useGraphData', () => {
  it('normalizeEqCode trims and uppercases', () => {
    expect(normalizeEqCode(' eur ')).toBe('EUR')
    expect(normalizeEqCode('')).toBe('')
  })

  it('filterTrustlinesByEqAndStatus filters by eq and status', () => {
    const trustlines: Trustline[] = [
      { equivalent: 'EUR', from: 'A', to: 'B', limit: '1', used: '0', available: '1', status: 'active', created_at: 't' },
      { equivalent: 'EUR', from: 'A', to: 'C', limit: '1', used: '0', available: '1', status: 'closed', created_at: 't' },
      { equivalent: 'USD', from: 'A', to: 'D', limit: '1', used: '0', available: '1', status: 'active', created_at: 't' },
    ]

    expect(
      filterTrustlinesByEqAndStatus({ trustlines, equivalent: 'EUR', statusFilter: ['active', 'closed'] }).map(
        (t) => t.to
      )
    ).toEqual(['B', 'C'])

    expect(
      filterTrustlinesByEqAndStatus({ trustlines, equivalent: 'EUR', statusFilter: ['active'] }).map((t) => t.to)
    ).toEqual(['B'])

    expect(
      filterTrustlinesByEqAndStatus({ trustlines, equivalent: '', statusFilter: ['active'] }).map((t) => t.to)
    ).toEqual(['B', 'D'])
  })

  it('computePrimaryEquivalent picks equivalent with most active trustlines', () => {
    const trustlines = [
      { equivalent: 'UAH', status: 'active' },
      { equivalent: 'UAH', status: 'active' },
      { equivalent: 'EUR', status: 'active' },
      { equivalent: 'EUR', status: 'closed' },
      { equivalent: ' usd ', status: 'active' },
    ]

    const equivalents = [{ code: 'EUR' }, { code: 'UAH' }, { code: 'USD' }]

    expect(computePrimaryEquivalent(trustlines, equivalents)).toBe('UAH')
  })

  it('computePrimaryEquivalent falls back to first equivalent when no active trustlines', () => {
    const trustlines = [
      { equivalent: 'UAH', status: 'closed' },
      { equivalent: 'UAH', status: 'frozen' },
    ]
    const equivalents = [{ code: 'EUR' }, { code: 'UAH' }]

    expect(computePrimaryEquivalent(trustlines, equivalents)).toBe('EUR')
  })

  it('computePrimaryEquivalent returns empty string when no data', () => {
    expect(computePrimaryEquivalent([], [])).toBe('')
  })

  it('computeIncidentRatioByPid filters by eq and keeps max ratio per pid', () => {
    const incidents: Incident[] = [
      { tx_id: '1', state: 'open', initiator_pid: 'PID_A', equivalent: 'EUR', age_seconds: 10, sla_seconds: 10 }, // 1.0
      { tx_id: '2', state: 'open', initiator_pid: 'PID_A', equivalent: 'EUR', age_seconds: 30, sla_seconds: 10 }, // 3.0
      { tx_id: '3', state: 'open', initiator_pid: 'PID_B', equivalent: 'EUR', age_seconds: 10, sla_seconds: 0 }, // 0
      { tx_id: '4', state: 'open', initiator_pid: 'PID_A', equivalent: 'USD', age_seconds: 999, sla_seconds: 1 },
    ]

    const m = computeIncidentRatioByPid({ incidents, equivalent: 'EUR' })
    expect(m.get('PID_A')).toBe(3)
    expect(m.get('PID_B')).toBeUndefined()
    expect(m.has('PID_C')).toBe(false)
  })

  it('availableEquivalents merges dataset + trustlines (no ALL option)', () => {
    const eq = ref('')
    const isRealMode = ref(false)
    const focusMode = ref(false)
    const focusRootPid = ref('')
    const focusDepth = ref(1)
    const statusFilter = ref<string[]>([])

    const g = useGraphData({ eq, isRealMode, focusMode, focusRootPid, focusDepth, statusFilter })
    g.equivalents.value = [{ code: 'eur', precision: 2, description: '', is_active: true } satisfies Equivalent]
    const tlBase = {
      from: 'A',
      to: 'B',
      limit: '0',
      used: '0',
      available: '0',
      status: 'active',
      created_at: 't',
    } satisfies Omit<Trustline, 'equivalent'>
    g.trustlines.value = [
      { ...tlBase, equivalent: ' usd ' },
      { ...tlBase, equivalent: 'EUR' },
    ]

    expect(g.availableEquivalents.value).toEqual(['EUR', 'USD'])
  })
})
