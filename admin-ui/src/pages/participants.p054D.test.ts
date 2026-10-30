import { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ParticipantsPage from './ParticipantsPage.vue'
import { DEBOUNCE_SEARCH_MS } from '../constants/timing'
import { mountPage, paginated, settle } from '../test/pageHarness'
import { t } from '../i18n'

/**
 * 054-D-13: a refused freeze/unfreeze (409) re-reads the list, as a successful one does.
 * 054-D-14: the search is sent trimmed (the server matches `%q%` literally, so a trailing space finds nothing);
 * the field keeps what the operator typed.
 */

const apiMock = vi.hoisted(() => ({
  listParticipants: vi.fn(),
  freezeParticipant: vi.fn(),
  unfreezeParticipant: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

const active = { pid: 'PidA1', display_name: 'Alice', type: 'person', status: 'active' }
const suspended = { pid: 'PidB2', display_name: 'Bob', type: 'person', status: 'suspended' }

afterEach(() => {
  vi.useRealTimers()
})

beforeEach(() => {
  for (const m of Object.values(apiMock)) m.mockReset()
  apiMock.listParticipants.mockResolvedValue(paginated([active, suspended]))
  vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({ value: 'because', action: 'confirm' } as never)
})

describe('054-D-13 a refused participant status change re-reads the list', () => {
  for (const [action, testId] of [
    ['freezeParticipant', 'participants-freeze-btn'],
    ['unfreezeParticipant', 'participants-unfreeze-btn'],
  ] as const) {
    it(`${action} refused (409) -> listParticipants is called again`, async () => {
      apiMock[action].mockRejectedValue(new Error('409'))
      const { wrapper } = await mountPage(ParticipantsPage, '/participants')
      const callsBefore = apiMock.listParticipants.mock.calls.length

      await wrapper.find(`[data-testid="${testId}"]`).trigger('click')
      await settle()
      await settle()

      expect(apiMock[action]).toHaveBeenCalledTimes(1)
      expect(ElMessage.error).toHaveBeenCalledTimes(1)
      expect(apiMock.listParticipants.mock.calls.length).toBe(callsBefore + 1)
      wrapper.unmount()
    })
  }
})

describe('054-D-13 a refused status change refreshes the open drawer too', () => {
  it('freeze refused (409) from the drawer while the server already holds suspended -> drawer shows suspended', async () => {
    apiMock.freezeParticipant.mockRejectedValue(new Error('409'))
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')
    await wrapper.find('.el-table__body tr').trigger('click')
    await settle()
    const drawerFreeze = () =>
      wrapper.findAll('.drawer-actions button').filter((b) => b.text() === t('participant.drawer.freezeParticipant'))
    const drawerUnfreeze = () =>
      wrapper.findAll('.drawer-actions button').filter((b) => b.text() === t('participant.drawer.unfreezeParticipant'))
    expect(drawerFreeze()).toHaveLength(1)

    apiMock.listParticipants.mockResolvedValue(paginated([{ ...active, status: 'suspended' }, suspended]))
    await drawerFreeze()[0]!.trigger('click')
    await settle()
    await settle()

    expect(apiMock.freezeParticipant).toHaveBeenCalledTimes(1)
    expect(drawerFreeze()).toHaveLength(0)
    expect(drawerUnfreeze()).toHaveLength(1)
    wrapper.unmount()
  })
})

describe('054-D-14 the participant search is sent trimmed', () => {
  it('a trailing space typed in the search is not sent; the PID case is kept', async () => {
    vi.useFakeTimers()
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')
    const input = wrapper.find('input[placeholder]')
    await input.setValue('PidA1 ')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_SEARCH_MS + 50)
    await settle()

    const last = apiMock.listParticipants.mock.calls[apiMock.listParticipants.mock.calls.length - 1]?.[0]
    expect(last.q).toBe('PidA1')
    expect((input.element as HTMLInputElement).value).toBe('PidA1 ')
    wrapper.unmount()
  })

  it('a search of only spaces is no filter', async () => {
    vi.useFakeTimers()
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')
    const input = wrapper.find('input[placeholder]')
    await input.setValue('   ')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_SEARCH_MS + 50)
    await settle()

    const last = apiMock.listParticipants.mock.calls[apiMock.listParticipants.mock.calls.length - 1]?.[0]
    expect(last.q).toBeUndefined()
    wrapper.unmount()
  })
})
