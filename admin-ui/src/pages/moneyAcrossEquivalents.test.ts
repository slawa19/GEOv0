import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage.vue'

/**
 * F-012-8 (`C-C3-1-007`) — величины разных эквивалентов не складываются в одно число.
 *
 * До 032 S5 это проверялось на двух местах, где такая сумма определяла видимое: ранжирование
 * участников на `LiquidityPage` при `equivalent = ALL` и ёмкость `useGraphAnalytics.selectedCapacity`
 * без выбранного эквивалента. Обе поверхности удалены решением владельца 2026-10-07 (F-1, F-2);
 * инвариант переехал вместе с деньгами туда, где они теперь показываются: строка эквивалента на
 * Dashboard (F-3). Сводка графа печатает net по `balance_rows` — её держит
 * `pages/graph/GraphAnalyticsDrawer.test.ts`.
 *
 * Что проверяется здесь: Dashboard никогда не просит сводку «по всем» (сервер тогда отдаёт `null`
 * вместо сумм, 028 F-028-37), не печатает итоговую строку по эквивалентам и показывает у каждого
 * эквивалента его собственные суммы.
 */

const apiMock = vi.hoisted(() => ({
  listEquivalents: vi.fn(),
  liquiditySummary: vi.fn(),
  integritySummary: vi.fn(),
  listAuditLog: vi.fn(),
  participantsStats: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

const PRECISION_BY_EQUIVALENT: Record<string, number> = { HOUR: 1, UAH: 2 }

function summary(equivalent: string | null, limit: string) {
  return {
    equivalent,
    updated_at: '2026-08-24T00:00:00Z',
    active_trustlines: 2,
    total_limit: equivalent ? limit : null,
    total_used: equivalent ? '10' : null,
    total_available: equivalent ? '0' : null,
  }
}

const LIMITS: Record<string, string> = { HOUR: '10', UAH: '100' }

async function mountDashboard(): Promise<VueWrapper> {
  setActivePinia(createPinia())
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/dashboard', component: DashboardPage }] })
  await router.push('/dashboard')
  await router.isReady()
  const wrapper = mount(DashboardPage, { global: { plugins: [ElementPlus, router] } })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  for (const mock of Object.values(apiMock)) mock.mockReset()
  apiMock.listEquivalents.mockResolvedValue({
    items: Object.entries(PRECISION_BY_EQUIVALENT).map(([code, precision]) => ({
      code, precision, description: code, is_active: true,
    })),
  })
  apiMock.liquiditySummary.mockImplementation(async (params: { equivalent?: string }) => {
    const code = String(params?.equivalent || '').trim().toUpperCase()
    return summary(code || null, LIMITS[code] ?? '0')
  })
  apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
  apiMock.listAuditLog.mockResolvedValue({ items: [], page: 1, per_page: 10, total: 0 })
  apiMock.participantsStats.mockResolvedValue({ participants_by_status: {}, participants_by_type: {}, total_participants: 0 })
})

describe('F-012-8: the Dashboard shows money per equivalent, never across them', () => {
  it('never asks for the summary without an equivalent', async () => {
    const wrapper = await mountDashboard()
    const params = apiMock.liquiditySummary.mock.calls.map(([p]) => p as { equivalent?: string })
    expect(params.length).toBe(2)
    for (const p of params) expect(String(p.equivalent || '').trim()).not.toBe('')
    wrapper.unmount()
  })

  it('prints one row per equivalent with its own sums and no row that adds them up', async () => {
    const wrapper = await mountDashboard()
    const rows = wrapper.find('[data-testid="dashboard-equivalents"]').findAll('tbody tr')
    expect(rows.map((r) => r.findAll('td')[0]?.text().trim())).toEqual(['HOUR', 'UAH'])
    const text = wrapper.find('[data-testid="dashboard-equivalents"]').text()
    // 10 HOUR + 100 UAH would be 110 of nothing: it must not appear in any spelling.
    expect(text).not.toMatch(/\b110(\.0+)?\b/)
    expect(rows[0]?.text()).toContain('10.0')
    expect(rows[1]?.text()).toContain('100.00')
    wrapper.unmount()
  })
})
