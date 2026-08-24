import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage.vue'
import TrustlinesPage from './TrustlinesPage.vue'
import type { Trustline } from '../types/domain'

/**
 * RT-012-6 (F-012-7) — reproducer on the real money callers of `admin-ui`.
 *
 * `DashboardPage.vue:106-107` and `TrustlinesPage.vue:124-125` both define
 * `function money(v: string) { return formatDecimalFixed(v, 2) }` and apply it to every
 * amount in their tables, whatever equivalent the row belongs to. The two is written out
 * at the call site, so this is not a test of the `digits = 2` default of
 * `formatDecimalFixed` (`utils/decimal.ts:85`) — no production caller omits that argument.
 *
 * The right form already exists next door: `LiquidityPage.vue:217-220` resolves the
 * precision of the equivalent and passes it. This reproducer asserts on rendered table
 * cells rather than on `money` itself, so it stays true for any fix that gets the digits
 * right, not only for one particular signature.
 *
 * `seeds/equivalents.json` ships HOUR with precision 1 and UAH with precision 2, so a
 * table holding both cannot be correct with a constant digit count.
 */

const PRECISION_BY_EQUIVALENT: Record<string, number> = {
  HOUR: 1,
  UAH: 2,
}

/** Same amount in both rows: the digits on screen must still differ. */
const LIMIT = '12.3'

const apiMock = vi.hoisted(() => ({
  listTrustlines: vi.fn(),
  listEquivalents: vi.fn(),
  trustlineBottlenecks: vi.fn(),
  listAuditLog: vi.fn(),
  listIncidents: vi.fn(),
  participantsStats: vi.fn(),
  health: vi.fn(),
  healthDb: vi.fn(),
  migrations: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

function ok<T>(data: T) {
  return { success: true as const, data }
}

function paginated<T>(items: T[]) {
  return ok({ items, page: 1, per_page: 20, total: items.length })
}

function trustline(equivalent: string): Trustline {
  return {
    equivalent,
    from: `FROM_${equivalent}`,
    to: `TO_${equivalent}`,
    limit: LIMIT,
    used: '0',
    available: LIMIT,
    status: 'active',
    created_at: '2026-08-24T00:00:00Z',
  }
}

const ROWS = Object.keys(PRECISION_BY_EQUIVALENT).map(trustline)

async function mountPage(component: object, path: string): Promise<VueWrapper> {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path, component }],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(component, { global: { plugins: [pinia, router, ElementPlus] } })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return wrapper
}

function fractionDigits(rendered: string): number {
  const dot = rendered.indexOf('.')
  return dot < 0 ? 0 : rendered.length - dot - 1
}

/**
 * Reads the rendered limit cell of every row, keyed by the row's equivalent.
 * The equivalent is the first column on both pages and the limit is the fourth;
 * the shape is asserted rather than assumed, so a column move fails loudly.
 */
function renderedLimitByEquivalent(wrapper: VueWrapper): Map<string, string> {
  const byEquivalent = new Map<string, string>()

  for (const row of wrapper.findAll('tbody tr')) {
    const cells = row.findAll('td').map((cell) => cell.text().trim())
    if (cells.length < 4) continue

    const equivalent = cells[0]
    if (!(equivalent in PRECISION_BY_EQUIVALENT)) continue

    const limitCell = cells[3]
    expect(
      Number(limitCell),
      `Column layout changed: the fourth cell of the ${equivalent} row reads "${limitCell}", which is `
        + `not the trust limit ${LIMIT}. This reproducer is reading the wrong column and must be `
        + 're-anchored before its verdict means anything.',
    ).toBeCloseTo(Number(LIMIT), 6)

    byEquivalent.set(equivalent, limitCell)
  }

  expect(
    [...byEquivalent.keys()].sort(),
    'Both equivalents must reach the rendered table, otherwise there is nothing to compare.',
  ).toEqual(Object.keys(PRECISION_BY_EQUIVALENT).sort())

  return byEquivalent
}

beforeEach(() => {
  for (const mock of Object.values(apiMock)) mock.mockReset()

  apiMock.listTrustlines.mockResolvedValue(paginated(ROWS))
  apiMock.trustlineBottlenecks.mockResolvedValue(ok({ items: ROWS, threshold: '0.10' }))
  apiMock.listEquivalents.mockResolvedValue(
    paginated(
      Object.entries(PRECISION_BY_EQUIVALENT).map(([code, precision]) => ({
        code,
        precision,
        active: true,
      })),
    ),
  )
  apiMock.listAuditLog.mockResolvedValue(paginated([]))
  apiMock.listIncidents.mockResolvedValue(paginated([]))
  apiMock.participantsStats.mockResolvedValue(
    ok({ participants_by_status: {}, participants_by_type: {} }),
  )
  apiMock.health.mockResolvedValue(ok({ status: 'healthy' }))
  apiMock.healthDb.mockResolvedValue(ok({ status: 'healthy' }))
  apiMock.migrations.mockResolvedValue(ok({ current: 'head' }))
})

describe.each([
  { name: 'DashboardPage bottlenecks table', component: DashboardPage, path: '/' },
  { name: 'TrustlinesPage table', component: TrustlinesPage, path: '/trustlines' },
])('RT-012-6: $name renders money by the row equivalent', ({ component, path }) => {
  it('shows each amount with as many fraction digits as its own equivalent declares', async () => {
    const wrapper = await mountPage(component, path)
    const rendered = renderedLimitByEquivalent(wrapper)

    for (const [equivalent, precision] of Object.entries(PRECISION_BY_EQUIVALENT)) {
      const cell = rendered.get(equivalent) as string

      expect(
        fractionDigits(cell),
        `The row is an ${equivalent} trust line and ${equivalent} declares precision ${precision}, `
          + `but the operator sees "${cell}" — ${fractionDigits(cell)} fraction digits. Extra digits `
          + 'assert a resolution the unit does not have, and an operator comparing a limit against a '
          + 'threshold reads a number the ledger cannot express.',
      ).toBe(precision)
    }

    wrapper.unmount()
  })

  it('reacts to the equivalent: two rows of different precision must not render one amount identically', async () => {
    const wrapper = await mountPage(component, path)
    const rendered = renderedLimitByEquivalent(wrapper)

    const asHour = rendered.get('HOUR') as string
    const asUah = rendered.get('UAH') as string

    expect(
      PRECISION_BY_EQUIVALENT.HOUR === PRECISION_BY_EQUIVALENT.UAH,
      'Counter-check premise: HOUR and UAH must declare different precision, otherwise this case proves nothing.',
    ).toBe(false)

    expect(
      asHour,
      `Both rows carry the limit ${LIMIT}; HOUR (precision ${PRECISION_BY_EQUIVALENT.HOUR}) renders as `
        + `"${asHour}" and UAH (precision ${PRECISION_BY_EQUIVALENT.UAH}) as "${asUah}". Identical output `
        + 'means the page formats every equivalent the same way, so the digits it shows tell the operator '
        + 'nothing about the unit being displayed.',
    ).not.toBe(asUah)

    wrapper.unmount()
  })
})
