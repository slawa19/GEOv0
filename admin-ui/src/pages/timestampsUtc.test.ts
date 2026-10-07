import { describe, expect, it, vi } from 'vitest'

import AuditLogPage from './AuditLogPage.vue'
import IntegrityPage from './IntegrityPage.vue'
import ParticipantsPage from './ParticipantsPage.vue'
import { mountPage, paginated, settle } from '../test/pageHarness'

/**
 * 032 S7 (D-13): time is shown in one format, UTC, by one function (`formatTs`), on every screen.
 *
 * Red on `01553c42`: the audit log, the participant drawer and the integrity "last check" printed the raw ISO
 * string the server sent (`2026-08-08T10:00:00Z`), while Trustlines printed `YYYY-MM-DD HH:mm:ss` - the same
 * moment written two ways, and the audit log is read against server logs, which are UTC.
 */

const apiMock = vi.hoisted(() => ({
  listAuditLog: vi.fn(),
  listParticipants: vi.fn(),
  integrityStatus: vi.fn(),
  integritySummary: vi.fn(),
  listEquivalents: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

const RAW = '2026-08-08T10:00:00Z'
const SHOWN = '2026-08-08 10:00:00'

describe('timestamps', () => {
  it('Audit log: table and drawer', async () => {
    apiMock.listAuditLog.mockResolvedValue(
      paginated([{ id: 'A1', timestamp: RAW, actor_id: 'admin', actor_role: 'admin', action: 'x', object_type: 'participant', object_id: 'P1', reason: null }]),
    )
    const { wrapper } = await mountPage(AuditLogPage, '/audit-log')
    expect(wrapper.find('.el-table').text()).toContain(SHOWN)
    expect(wrapper.find('.el-table').text()).not.toContain(RAW)

    await wrapper.find('.el-table__body tr').trigger('click')
    await settle()
    const drawer = wrapper.find('.el-drawer').text()
    expect(drawer).toContain(SHOWN)
    expect(drawer).not.toContain(RAW)
    wrapper.unmount()
  })

  it('Participants: created at in the drawer', async () => {
    apiMock.listParticipants.mockResolvedValue(
      paginated([{ pid: 'P1', display_name: 'One', type: 'person', status: 'active', verification_level: 0, created_at: RAW }]),
    )
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')
    await wrapper.find('.el-table__body tr').trigger('click')
    await settle()
    const drawer = wrapper.find('.el-drawer').text()
    expect(drawer).toContain(SHOWN)
    expect(drawer).not.toContain(RAW)
    wrapper.unmount()
  })

  it('Integrity: last check', async () => {
    apiMock.integrityStatus.mockResolvedValue({ status: 'healthy', last_check: RAW, equivalents: {}, alerts: [] })
    apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
    const { wrapper } = await mountPage(IntegrityPage, '/integrity')
    const descriptions = wrapper.find('.el-descriptions').text()
    expect(descriptions).toContain(SHOWN)
    expect(descriptions).not.toContain(RAW)
    wrapper.unmount()
  })
})
