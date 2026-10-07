import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage.vue'
import { setLocale } from '../i18n'

/**
 * 032 S5 (F-2, F-3, D-3): the Dashboard carries one row per equivalent from the narrowed
 * `GET /admin/liquidity/summary`, with each row's money printed at the row's OWN precision - a
 * stopped equivalent included (`include_inactive: true`), because its lines and debts still exist.
 * Written first, red on `58876244`: the Dashboard had no per-equivalent row at all, and Liquidity
 * (the only place with sums) took precision from active equivalents only, so a stopped equivalent
 * printed a dash.
 */

const apiMock = vi.hoisted(() => ({
  listEquivalents: vi.fn(),
  liquiditySummary: vi.fn(),
  integritySummary: vi.fn(),
  listAuditLog: vi.fn(),
  participantsStats: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

const EQUIVALENTS = [
  { code: 'HOUR', precision: 1, description: 'Hour', is_active: false },
  { code: 'UAH', precision: 2, description: 'Hryvnia', is_active: true },
]

const SUMMARIES: Record<string, unknown> = {
  HOUR: {
    equivalent: 'HOUR', updated_at: '2026-10-07T10:00:00Z', active_trustlines: 3,
    total_limit: '10.05', total_used: '2.5', total_available: '7.55',
  },
  UAH: {
    equivalent: 'UAH', updated_at: '2026-10-07T10:00:00Z', active_trustlines: 7,
    total_limit: '100.5', total_used: '95', total_available: '5.5',
  },
}

beforeEach(() => {
  setLocale('ru')
  localStorage.clear()
  for (const m of Object.values(apiMock)) m.mockReset()
  apiMock.listEquivalents.mockResolvedValue({ items: EQUIVALENTS })
  apiMock.liquiditySummary.mockImplementation(async (params: { equivalent?: string }) => SUMMARIES[String(params.equivalent)])
  apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
  apiMock.listAuditLog.mockResolvedValue({ items: [], page: 1, per_page: 10, total: 0 })
  apiMock.participantsStats.mockResolvedValue({
    participants_by_status: { active: 5 }, participants_by_type: { person: 5 }, total_participants: 5,
  })
})

async function mountDashboard(): Promise<{ wrapper: VueWrapper; router: Router }> {
  setActivePinia(createPinia())
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/dashboard', component: DashboardPage },
      { path: '/integrity', component: { template: '<div />' } },
    ],
  })
  await router.push('/dashboard')
  await router.isReady()
  const wrapper = mount(DashboardPage, { global: { plugins: [ElementPlus, router] } })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return { wrapper, router }
}

function rowCells(wrapper: VueWrapper, code: string): string[] {
  const row = wrapper.find(`[data-testid="dashboard-equivalents"]`).findAll('tbody tr')
    .find((tr) => tr.findAll('td')[0]?.text().trim() === code)
  expect(row, `no row for ${code}`).toBeTruthy()
  return row!.findAll('td').map((td) => td.text().trim())
}

describe('Dashboard equivalent rows (032 F-3)', () => {
  it('asks the summary once per equivalent, the stopped one included, and never across equivalents', async () => {
    const { wrapper } = await mountDashboard()
    expect(apiMock.listEquivalents).toHaveBeenCalledWith({ include_inactive: true })
    const asked = apiMock.liquiditySummary.mock.calls.map(([p]) => (p as { equivalent?: string }).equivalent).sort()
    expect(asked).toEqual(['HOUR', 'UAH'])
    wrapper.unmount()
  })

  it('prints a stopped equivalent sums at its own precision', async () => {
    const { wrapper } = await mountDashboard()
    // HOUR (precision 1, inactive): the sums, not a dash; precision is a minimum, never a maximum.
    const hour = rowCells(wrapper, 'HOUR')
    expect(hour).toContain('3')
    expect(hour).toContain('10.05')
    expect(hour).toContain('2.5')
    expect(hour).toContain('7.55')
    expect(hour.join(' ')).not.toContain('—')
    // UAH (precision 2): padded to its own minimum.
    const uah = rowCells(wrapper, 'UAH')
    expect(uah).toContain('100.50')
    expect(uah).toContain('95.00')
    expect(uah).toContain('5.50')
    wrapper.unmount()
  })

  it('028 F-028-48 (moved from the removed Liquidity screen): money totals are text, never a float', async () => {
    apiMock.listEquivalents.mockResolvedValue({ items: [{ code: 'UAH', precision: 2, description: '', is_active: true }] })
    apiMock.liquiditySummary.mockResolvedValue({
      equivalent: 'UAH', updated_at: '2026-10-07T10:00:00Z', active_trustlines: 1,
      total_limit: '12345678901234567.89', total_used: '0.1', total_available: '12345678901234567.79',
    })
    const { wrapper } = await mountDashboard()
    const uah = rowCells(wrapper, 'UAH')
    expect(uah).toContain('12345678901234567.89')
    expect(uah).toContain('0.10')
    expect(uah).toContain('12345678901234567.79')
    wrapper.unmount()
  })

  it('a failed summary of one equivalent prints dashes, not fabricated zeros, and names the failure', async () => {
    apiMock.liquiditySummary.mockImplementation(async (params: { equivalent?: string }) => {
      if (params.equivalent === 'HOUR') throw new Error('summary unavailable')
      return SUMMARIES[String(params.equivalent)]
    })
    const { wrapper } = await mountDashboard()
    const hour = rowCells(wrapper, 'HOUR')
    expect(hour.slice(2, 6)).toEqual(['—', '—', '—', '—'])
    expect(wrapper.find('[data-testid="dashboard-equivalents"]').text()).toContain('summary unavailable')
    // The other equivalent is unaffected.
    expect(rowCells(wrapper, 'UAH')).toContain('100.50')
    wrapper.unmount()
  })

  it('warns about held equivalents and links to the Integrity screen', async () => {
    apiMock.integritySummary.mockResolvedValue({
      equivalents: [
        { equivalent: 'UAH', status: 'critical', checked_at: '2026-10-07T10:00:00Z', hold: true },
        { equivalent: 'HOUR', status: 'healthy', checked_at: '2026-10-07T10:00:00Z', hold: false },
      ],
    })
    const { wrapper, router } = await mountDashboard()
    const alert = wrapper.find('[data-testid="dashboard-holds"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('UAH')
    expect(alert.text()).not.toContain('HOUR')
    await alert.find('[data-testid="dashboard-holds-open"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/integrity')
    wrapper.unmount()
  })

  it('shows no hold warning when nothing is held', async () => {
    const { wrapper } = await mountDashboard()
    expect(wrapper.find('[data-testid="dashboard-holds"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
