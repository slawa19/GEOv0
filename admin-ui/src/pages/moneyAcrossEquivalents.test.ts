import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { computed, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import LiquidityPage from './LiquidityPage.vue'
import { useGraphAnalytics } from '../composables/useGraphAnalytics'
import type {
  AuditLogEntry,
  ClearingCycles,
  Debt,
  Incident,
  Participant,
  Transaction,
  Trustline,
} from './graph/graphTypes'
import type { SelectedInfo } from '../composables/useGraphVisualization'

/**
 * F-012-8 (`C-C3-1-007`) — величины разных эквивалентов не складываются в одно число.
 *
 * Проверяются два места, где такая сумма определяла видимое:
 *
 * 1. `LiquidityPage` при `equivalent = ALL`. Продюсер (`app/api/v1/admin.py:750-786`) не
 *    фильтрует по эквиваленту, и `net` каждого участника — сумма долгов во всех единицах.
 *    Само число страница уже прятала, но **состав и порядок** трёх списков задавались именно им.
 * 2. `useGraphAnalytics.selectedCapacity` без выбранного эквивалента: лимиты всех эквивалентов
 *    складывались в атомы одной «ёмкости», и доля использования такой суммы уходила в KPI
 *    вкладки Summary и в панель советов.
 *
 * Ни один ассерт здесь не закрепляет два знака: проверяется, что́ показано, а не сколько знаков.
 */

const apiMock = vi.hoisted(() => ({
  liquiditySummary: vi.fn(),
  listEquivalents: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

function ok<T>(data: T) {
  return { success: true as const, data }
}

const PRECISION_BY_EQUIVALENT: Record<string, number> = { HOUR: 1, UAH: 2 }

/**
 * PID_MIXED крупнее всех только в сумме HOUR + UAH; внутри каждого эквивалента по отдельности
 * он мельче PID_UAH. Ровно это делает ранжирование по кросс-эквивалентной сумме наблюдаемым.
 */
const CROSS_EQUIVALENT_TOP = 'PID_MIXED'
const SINGLE_EQUIVALENT_TOP = 'PID_UAH'

function netRows() {
  return [
    { pid: CROSS_EQUIVALENT_TOP, display_name: 'Mixed', net: '90' },
    { pid: SINGLE_EQUIVALENT_TOP, display_name: 'Uah', net: '60' },
  ]
}

function summary(equivalent: string | null) {
  return {
    equivalent,
    threshold: 0.1,
    updated_at: '2026-08-24T00:00:00Z',
    active_trustlines: 2,
    bottlenecks: 0,
    incidents_over_sla: 0,
    total_limit: '100',
    total_used: '10',
    total_available: '90',
    top_creditors: netRows(),
    top_debtors: [],
    top_by_abs_net: netRows(),
    top_bottleneck_edges: [],
  }
}

async function mountLiquidity(query: Record<string, string>): Promise<VueWrapper> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/liquidity', component: LiquidityPage }],
  })
  await router.push({ path: '/liquidity', query })
  await router.isReady()

  const wrapper = mount(LiquidityPage, { global: { plugins: [pinia, router, ElementPlus] } })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  for (const mock of Object.values(apiMock)) mock.mockReset()

  apiMock.listEquivalents.mockResolvedValue(
    ok({
      items: Object.entries(PRECISION_BY_EQUIVALENT).map(([code, precision]) => ({
        code,
        precision,
        description: code,
        is_active: true,
      })),
    }),
  )
  apiMock.liquiditySummary.mockImplementation((params: { equivalent?: string }) => {
    const code = String(params?.equivalent || '').trim().toUpperCase()
    return Promise.resolve(ok(summary(code && code !== 'ALL' ? code : null)))
  })
})

describe('F-012-8: LiquidityPage does not rank participants by a cross-equivalent sum', () => {
  it('hides the net-position lists while no single equivalent is selected', async () => {
    const wrapper = await mountLiquidity({})
    const text = wrapper.text()

    expect(
      wrapper.find('[data-testid="liquidity-net-cross-equivalent"]').exists(),
      'With ALL selected the operator must be told why the net-position lists are absent.',
    ).toBe(true)
    expect(
      text.includes(CROSS_EQUIVALENT_TOP),
      `The API answered with ${CROSS_EQUIVALENT_TOP} at the top of the net ranking, but that rank comes `
        + 'from adding amounts of different equivalents into one number. Hiding the amount and keeping '
        + 'the order leaves the hidden value deciding what the operator sees.',
    ).toBe(false)

    wrapper.unmount()
  })

  it('shows them again for a single equivalent, on the very same rows', async () => {
    const wrapper = await mountLiquidity({ equivalent: 'UAH' })
    const text = wrapper.text()

    expect(
      wrapper.find('[data-testid="liquidity-net-cross-equivalent"]').exists(),
      'Counter-check: with one equivalent selected the notice must be gone, otherwise the first case '
        + 'proves only that the lists never render.',
    ).toBe(false)
    expect(text.includes(SINGLE_EQUIVALENT_TOP)).toBe(true)
    expect(text.includes(CROSS_EQUIVALENT_TOP)).toBe(true)

    wrapper.unmount()
  })
})

function analyticsFixture(analyticsEq: string | null) {
  const selected = ref<SelectedInfo | null>({
    kind: 'node',
    pid: 'PID_A',
    degree: 0,
    inDegree: 0,
    outDegree: 0,
  })

  // Один и тот же участник держит линии в двух эквивалентах: HOUR (precision 1) и UAH (2).
  const trustlines = ref<Trustline[]>([
    {
      equivalent: 'HOUR',
      from: 'PID_A',
      to: 'PID_B',
      limit: '10.0',
      used: '5.0',
      available: '5.0',
      status: 'active',
      created_at: 't',
    },
    {
      equivalent: 'UAH',
      from: 'PID_A',
      to: 'PID_C',
      limit: '10.00',
      used: '1.00',
      available: '9.00',
      status: 'active',
      created_at: 't',
    },
  ])

  return useGraphAnalytics({
    isRealMode: computed(() => false),
    threshold: ref('0.10'),
    analyticsEq: computed(() => analyticsEq),
    precisionByEq: computed(() => new Map(Object.entries(PRECISION_BY_EQUIVALENT))),
    availableEquivalents: computed(() => Object.keys(PRECISION_BY_EQUIVALENT)),
    participantByPid: computed(() => new Map<string, Participant>()),
    participants: ref<Participant[]>([{ pid: 'PID_A', display_name: 'A' }]),
    trustlines,
    debts: ref<Debt[]>([]),
    incidents: ref<Incident[]>([]),
    auditLog: ref<AuditLogEntry[]>([]),
    transactions: ref<Transaction[]>([]),
    clearingCycles: ref<ClearingCycles | null>(null),
    selected,
  })
}

describe('F-012-8: graph capacity is not summed across equivalents', () => {
  it('reports no usage share while no single equivalent is selected', () => {
    const capacity = analyticsFixture(null).selectedCapacity.value

    expect(capacity, 'The node still has trustlines, so the block itself must exist.').not.toBeNull()
    expect(
      capacity?.out,
      'Outgoing capacity across equivalents would be atoms of HOUR added to atoms of UAH: not a '
        + 'quantity, therefore not a share either.',
    ).toBeNull()
    expect(capacity?.inc).toBeNull()
  })

  it('reports the share of the selected equivalent alone, and it differs per equivalent', () => {
    const hour = analyticsFixture('HOUR').selectedCapacity.value
    const uah = analyticsFixture('UAH').selectedCapacity.value

    // 5.0/10.0 против 1.00/10.00 — доли обязаны отличаться, иначе ассерт не наблюдает эквивалент.
    expect(hour?.out?.pct).toBeCloseTo(0.5, 9)
    expect(uah?.out?.pct).toBeCloseTo(0.1, 9)

    const crossEquivalentPct = (5 + 1) / (10 + 10)
    expect(
      hour?.out?.pct,
      'Counter-check: the share must not equal the one a cross-equivalent sum would produce.',
    ).not.toBeCloseTo(crossEquivalentPct, 9)
    expect(uah?.out?.pct).not.toBeCloseTo(crossEquivalentPct, 9)
  })
})
