import { beforeEach, describe, expect, it, vi } from 'vitest'

import DashboardPage from './DashboardPage.vue'
import { ApiException } from '../api/apiException'
import { setLocale, t } from '../i18n'
import { mountPage } from '../test/pageHarness'

/**
 * 032 S7 (D-18, D-13): the Dashboard's cards load independently and print time in the one UTC format.
 *
 * What is held: a card that fails shows ITS failure and does not blank, delay or replace the others (one request
 * per card, each with its own error state), the participant counters are the numbers the server sent, and the
 * audit rows carry the same `YYYY-MM-DD HH:mm:ss` (UTC) as every other screen instead of the raw ISO string.
 */

const apiMock = vi.hoisted(() => ({
  listEquivalents: vi.fn(),
  liquiditySummary: vi.fn(),
  integritySummary: vi.fn(),
  listAuditLog: vi.fn(),
  participantsStats: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

const AUDIT = {
  id: 'A1', timestamp: '2026-10-07T10:11:12Z', actor_id: 'admin', actor_role: 'admin', action: 'admin.config.patch',
  object_type: 'config', object_id: 'CLEARING_ENABLED', reason: null,
}

const fail = (message: string) => new ApiException({ status: 500, code: 'E500', message })

beforeEach(() => {
  setLocale('en')
  apiMock.listEquivalents.mockResolvedValue({ items: [{ code: 'UAH', precision: 2, description: '', is_active: true }] })
  apiMock.liquiditySummary.mockResolvedValue({
    equivalent: 'UAH', updated_at: '2026-10-07T10:00:00Z', active_trustlines: 7,
    total_limit: '100.5', total_used: '95', total_available: '5.5',
  })
  apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
  apiMock.listAuditLog.mockResolvedValue({ items: [AUDIT], page: 1, per_page: 10, total: 1 })
  apiMock.participantsStats.mockResolvedValue({
    participants_by_status: { active: 5, suspended: 2 }, participants_by_type: { person: 6, business: 1 }, total_participants: 7,
  })
})

describe('Dashboard cards', () => {
  it('shows every card when everything answers', async () => {
    const { wrapper } = await mountPage(DashboardPage, '/dashboard')
    expect(wrapper.text()).toContain('active: 5')
    expect(wrapper.text()).toContain('suspended: 2')
    expect(wrapper.find('[data-testid="dashboard-equivalents"]').text()).toContain('UAH')
    expect(wrapper.text()).toContain('CLEARING_ENABLED')
    wrapper.unmount()
  })

  it('a failing participant-stats request leaves the other cards intact', async () => {
    apiMock.participantsStats.mockRejectedValue(fail('stats down'))
    const { wrapper } = await mountPage(DashboardPage, '/dashboard')
    expect(wrapper.text()).toContain('stats down')
    expect(wrapper.find('[data-testid="dashboard-equivalents"]').text()).toContain('UAH')
    expect(wrapper.find('[data-testid="dashboard-equivalents"]').text()).toContain('100.50')
    expect(wrapper.text()).toContain('CLEARING_ENABLED')
    wrapper.unmount()
  })

  it('a failing audit request leaves the other cards intact and shows no empty table beside the failure', async () => {
    apiMock.listAuditLog.mockRejectedValue(fail('audit down'))
    const { wrapper } = await mountPage(DashboardPage, '/dashboard')
    expect(wrapper.text()).toContain('audit down')
    expect(wrapper.text()).toContain('active: 5')
    expect(wrapper.find('[data-testid="dashboard-equivalents"]').text()).toContain('UAH')
    const auditCard = wrapper.findAll('.el-card').find((c) => c.text().includes(t('dashboard.card.recentAudit')))!
    expect(auditCard.find('.el-alert').exists()).toBe(true)
    expect(auditCard.find('.el-table').exists()).toBe(false)
    wrapper.unmount()
  })

  it('a failing equivalents request leaves the other cards intact', async () => {
    apiMock.listEquivalents.mockRejectedValue(fail('catalogue down'))
    const { wrapper } = await mountPage(DashboardPage, '/dashboard')
    expect(wrapper.find('[data-testid="dashboard-equivalents"]').text()).toContain('catalogue down')
    expect(wrapper.text()).toContain('active: 5')
    expect(wrapper.text()).toContain('CLEARING_ENABLED')
    wrapper.unmount()
  })

  it('prints the audit time in the one UTC format', async () => {
    const { wrapper } = await mountPage(DashboardPage, '/dashboard')
    expect(wrapper.text()).toContain('2026-10-07 10:11:12')
    expect(wrapper.text()).not.toContain('2026-10-07T10:11:12Z')
    wrapper.unmount()
  })
})
