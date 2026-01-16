import { describe, expect, it } from 'vitest'
import { computed, ref } from 'vue'

import { useGraphAnalytics } from './useGraphAnalytics'

describe('useGraphAnalytics (fixtures-first)', () => {
  it('computes selectedBalanceRows from trustlines + debts', () => {
    const selected = ref<any>({ kind: 'node', pid: 'PID_A' })

    const participants = ref<any[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
      { pid: 'PID_C', display_name: 'Carol' },
    ])

    const trustlines = ref<any[]>([
      // PID_A is creditor (outgoing limit)
      { from: 'PID_A', to: 'PID_B', equivalent: 'EUR', limit: '10.00', used: '3.00', available: '7.00', status: 'active', created_at: 't' },
      // PID_A is debtor (incoming limit)
      { from: 'PID_C', to: 'PID_A', equivalent: 'EUR', limit: '5.00', used: '1.00', available: '4.00', status: 'active', created_at: 't' },
    ])

    const debts = ref<any[]>([
      { debtor: 'PID_A', creditor: 'PID_B', equivalent: 'EUR', amount: '3.00' },
      { debtor: 'PID_C', creditor: 'PID_A', equivalent: 'EUR', amount: '1.50' },
    ])

    const participantByPid = computed(() => {
      const m = new Map<string, any>()
      for (const p of participants.value) m.set(p.pid, p)
      return m
    })

    const g = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),

      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['ALL', 'EUR']),
      participantByPid,

      participants,
      trustlines,
      debts,
      incidents: ref<any[]>([]),
      auditLog: ref<any[]>([]),
      transactions: ref<any[]>([]),
      clearingCycles: ref<any>(null),

      selected,
    })

    expect(g.selectedBalanceRows.value).toEqual([
      {
        equivalent: 'EUR',
        outgoing_limit: '10.00',
        outgoing_used: '3.00',
        incoming_limit: '5.00',
        incoming_used: '1.00',
        total_debt: '3.00',
        total_credit: '1.50',
        net: '-1.50',
      },
    ])
  })

  it('computes concentration (top shares + HHI) from debts', () => {
    const selected = ref<any>({ kind: 'node', pid: 'PID_A' })

    const participants = ref<any[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
      { pid: 'PID_C', display_name: 'Carol' },
      { pid: 'PID_D', display_name: 'Dan' },
      { pid: 'PID_E', display_name: 'Eve' },
    ])

    const debts = ref<any[]>([
      // outgoing (you owe): total 4.00 => shares 0.75 + 0.25, HHI = 0.625
      { debtor: 'PID_A', creditor: 'PID_B', equivalent: 'EUR', amount: '3.00' },
      { debtor: 'PID_A', creditor: 'PID_C', equivalent: 'EUR', amount: '1.00' },
      // incoming (owed to you): total 4.00 => shares 0.5 + 0.5, HHI = 0.5
      { debtor: 'PID_D', creditor: 'PID_A', equivalent: 'EUR', amount: '2.00' },
      { debtor: 'PID_E', creditor: 'PID_A', equivalent: 'EUR', amount: '2.00' },
    ])

    const participantByPid = computed(() => {
      const m = new Map<string, any>()
      for (const p of participants.value) m.set(p.pid, p)
      return m
    })

    const g = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold: ref('0.10'),
      analyticsEq: computed(() => 'EUR'),

      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['ALL', 'EUR']),
      participantByPid,

      participants,
      trustlines: ref<any[]>([]),
      debts,
      incidents: ref<any[]>([]),
      auditLog: ref<any[]>([]),
      transactions: ref<any[]>([]),
      clearingCycles: ref<any>(null),

      selected,
    })

    const c = g.selectedConcentration.value
    expect(c.eq).toBe('EUR')

    expect(c.outgoing.top1).toBeCloseTo(0.75)
    expect(c.outgoing.top5).toBeCloseTo(1)
    expect(c.outgoing.hhi).toBeCloseTo(0.75 ** 2 + 0.25 ** 2)
    expect(c.outgoing.level.label).toBe('high')

    expect(c.incoming.top1).toBeCloseTo(0.5)
    expect(c.incoming.top5).toBeCloseTo(1)
    expect(c.incoming.hhi).toBeCloseTo(0.5)
    expect(c.incoming.level.label).toBe('high')
  })

  it('flags bottlenecks in selectedCapacity using threshold', () => {
    const selected = ref<any>({ kind: 'node', pid: 'PID_A' })
    const threshold = ref('0.20')

    const participants = ref<any[]>([
      { pid: 'PID_A', display_name: 'Alice' },
      { pid: 'PID_B', display_name: 'Bob' },
      { pid: 'PID_C', display_name: 'Carol' },
    ])

    const trustlines = ref<any[]>([
      // 1/10 = 0.1 < 0.2 => bottleneck
      { from: 'PID_A', to: 'PID_B', equivalent: 'EUR', limit: '10.00', used: '9.00', available: '1.00', status: 'active', created_at: 't' },
      // 4/10 = 0.4 >= 0.2 => not bottleneck
      { from: 'PID_C', to: 'PID_A', equivalent: 'EUR', limit: '10.00', used: '6.00', available: '4.00', status: 'active', created_at: 't' },
    ])

    const participantByPid = computed(() => {
      const m = new Map<string, any>()
      for (const p of participants.value) m.set(p.pid, p)
      return m
    })

    const g = useGraphAnalytics({
      isRealMode: computed(() => false),
      threshold,
      analyticsEq: computed(() => 'EUR'),

      precisionByEq: computed(() => new Map([['EUR', 2]])),
      availableEquivalents: computed(() => ['ALL', 'EUR']),
      participantByPid,

      participants,
      trustlines,
      debts: ref<any[]>([]),
      incidents: ref<any[]>([]),
      auditLog: ref<any[]>([]),
      transactions: ref<any[]>([]),
      clearingCycles: ref<any>(null),

      selected,
    })

    const cap = g.selectedCapacity.value
    expect(cap).not.toBeNull()
    expect(cap!.bottlenecks.length).toBe(1)

    const first = cap!.bottlenecks[0]
    if (!first) throw new Error('Expected bottlenecks[0] to be present')
    expect(first.dir).toBe('out')
    expect(first.other).toBe('PID_B')
  })
})
