import { type VueWrapper } from '@vue/test-utils'
import { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AuditLogPage from './AuditLogPage.vue'
import EquivalentsPage from './EquivalentsPage.vue'
import ParticipantsPage from './ParticipantsPage.vue'
import TrustlinesPage from './TrustlinesPage.vue'
import { ApiException } from '../api/apiException'
import { t } from '../i18n'
import { deferred, mountPage, paginated, settle } from '../test/pageHarness'

/**
 * Who owns the state of an async page - observed through what an operator can observe: the rows on screen, the
 * error alert, the skeleton, the calls the page makes to the API client and the route it writes. (This file used
 * to read `wrapper.vm.$.setupState`; a refactor of the page's internals must not be able to break it, and a race
 * it no longer covers must still turn it red - the stale-response and unmount cases below are the same cases.)
 */

const apiMock = vi.hoisted(() => ({
  listParticipants: vi.fn(),
  freezeParticipant: vi.fn(),
  unfreezeParticipant: vi.fn(),
  listTrustlines: vi.fn(),
  listAuditLog: vi.fn(),
  listEquivalents: vi.fn(),
  getEquivalentUsage: vi.fn(),
  createEquivalent: vi.fn(),
  updateEquivalent: vi.fn(),
  setEquivalentActive: vi.fn(),
  deleteEquivalent: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

const SETTLE_DEBOUNCE_MS = 1000

const participantOld = {
  pid: 'OLD',
  display_name: 'Old',
  type: 'person',
  status: 'active',
  verification_level: 0,
  created_at: '2026-08-08T10:00:00Z',
}
const participantNew = { ...participantOld, pid: 'NEW', display_name: 'New' }

const trustlineOld = {
  equivalent: 'USD',
  from: 'OLD',
  to: 'P2',
  limit: '10',
  used: '1',
  available: '9',
  status: 'active',
  created_at: '2026-08-08T10:00:00Z',
}
const trustlineNew = { ...trustlineOld, from: 'NEW' }

const auditOld = {
  id: 'OLD',
  timestamp: '2026-08-08T10:00:00Z',
  actor_id: 'admin',
  actor_role: 'admin',
  action: 'old',
  object_type: 'participant',
  object_id: 'OLD',
  reason: null,
}
const auditNew = { ...auditOld, id: 'NEW', action: 'new', object_id: 'NEW' }

const equivalentOld = { code: 'OLD', precision: 2, description: 'Old', is_active: true }
const equivalentNew = { code: 'NEW', precision: 2, description: 'New', is_active: true }

function lastArg(mock: ReturnType<typeof vi.fn>): unknown {
  return mock.mock.calls[mock.mock.calls.length - 1]?.[0]
}

function buttonByText(root: ParentNode, text: string): HTMLButtonElement {
  const found = [...root.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === text)
  if (!found) throw new Error(`no button "${text}"`)
  return found as HTMLButtonElement
}

async function type(wrapper: VueWrapper, selector: string, value: string) {
  await wrapper.find(selector).setValue(value)
  await vi.advanceTimersByTimeAsync(SETTLE_DEBOUNCE_MS)
  await settle()
}

type ListCase = {
  name: string
  component: Parameters<typeof mountPage>[0]
  path: string
  request: ReturnType<typeof vi.fn>
  old: unknown
  next: unknown
  /** Starts one more load of the same list the way an operator would. */
  trigger: (wrapper: VueWrapper, n: number) => Promise<void>
}

const listCases: ListCase[] = [
  {
    name: 'Participants',
    component: ParticipantsPage,
    path: '/participants',
    request: apiMock.listParticipants,
    old: paginated([participantOld]),
    next: paginated([participantNew]),
    trigger: (wrapper, n) => type(wrapper, 'input[data-testid="participants-filter-q"]', `q${n}`),
  },
  {
    name: 'Trustlines',
    component: TrustlinesPage,
    path: '/trustlines',
    request: apiMock.listTrustlines,
    old: paginated([trustlineOld]),
    next: paginated([trustlineNew]),
    trigger: (wrapper, n) => type(wrapper, '.filters .el-input input', `EQ${n}`),
  },
  {
    name: 'Audit',
    component: AuditLogPage,
    path: '/audit-log',
    request: apiMock.listAuditLog,
    old: paginated([auditOld]),
    next: paginated([auditNew]),
    trigger: (wrapper, n) => type(wrapper, '.hdr input', `a${n}`),
  },
  {
    name: 'Equivalents',
    component: EquivalentsPage,
    path: '/equivalents',
    request: apiMock.listEquivalents,
    old: { items: [equivalentOld] },
    next: { items: [equivalentNew] },
    trigger: async (wrapper) => {
      await wrapper.find('.el-switch').trigger('click')
      await settle()
    },
  },
]

describe('mounted list pages: the latest request owns what is shown', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiMock.listEquivalents.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it.each(listCases)('$name keeps the latest result over a stale success and a stale failure', async (c) => {
    const staleSuccess = deferred<unknown>()
    const staleFailure = deferred<unknown>()
    const latest = deferred<unknown>()
    c.request
      .mockImplementationOnce(() => staleSuccess.promise)
      .mockImplementationOnce(() => staleFailure.promise)
      .mockImplementationOnce(() => latest.promise)

    const { wrapper } = await mountPage(c.component, c.path)
    expect(wrapper.find('.el-skeleton').exists()).toBe(true)
    await c.trigger(wrapper, 1)
    await c.trigger(wrapper, 2)

    latest.resolve(c.next)
    await settle()
    expect(wrapper.text()).toContain('NEW')
    expect(wrapper.text()).not.toContain('OLD')
    expect(wrapper.find('.el-alert--error').exists()).toBe(false)
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)

    staleSuccess.resolve(c.old)
    await settle()
    staleFailure.reject(new Error('stale failure'))
    await settle()
    expect(wrapper.text()).toContain('NEW')
    expect(wrapper.text()).not.toContain('OLD')
    expect(wrapper.find('.el-alert--error').exists()).toBe(false)
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(listCases)('$name ignores a response that arrives after the page is gone', async (c) => {
    const first = deferred<unknown>()
    const pending = deferred<unknown>()
    c.request.mockImplementationOnce(() => first.promise).mockImplementationOnce(() => pending.promise)
    const problems = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const warnings = vi.spyOn(console, 'warn').mockImplementation(() => undefined)

    const { wrapper } = await mountPage(c.component, c.path)
    first.resolve(c.next)
    await settle()
    await c.trigger(wrapper, 3)
    const calls = c.request.mock.calls.length
    wrapper.unmount()

    pending.resolve(c.old)
    await settle()
    await vi.runAllTimersAsync()
    expect(c.request).toHaveBeenCalledTimes(calls)
    expect(problems).not.toHaveBeenCalled()
    expect(warnings).not.toHaveBeenCalled()
  })

  it('cancels pending page debounces on unmount before they can start late requests', async () => {
    apiMock.listParticipants.mockResolvedValue(paginated([participantNew]))
    apiMock.listTrustlines.mockResolvedValue(paginated([trustlineNew]))
    apiMock.listAuditLog.mockResolvedValue(paginated([auditNew]))

    for (const [component, path, request, selector] of [
      [ParticipantsPage, '/participants', apiMock.listParticipants, 'input[data-testid="participants-filter-q"]'],
      [TrustlinesPage, '/trustlines', apiMock.listTrustlines, '.filters .el-input input'],
      [AuditLogPage, '/audit-log', apiMock.listAuditLog, '.hdr input'],
    ] as const) {
      const { wrapper } = await mountPage(component, path)
      await wrapper.find(selector).setValue('later')
      const calls = request.mock.calls.length
      wrapper.unmount()
      await vi.runAllTimersAsync()
      expect(request, path).toHaveBeenCalledTimes(calls)
    }
  })
})

describe('mounted list pages: one state at a time', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiMock.listEquivalents.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it.each(listCases)('$name shows the failure alone, never together with the empty state', async (c) => {
    c.request.mockRejectedValue(new ApiException({ status: 500, code: 'E500', message: 'boom', requestId: 'rid-9' }))

    const { wrapper } = await mountPage(c.component, c.path)

    expect(wrapper.findAll('.el-alert--error')).toHaveLength(1)
    expect(wrapper.find('.el-alert--error').text()).toContain('boom')
    expect(wrapper.find('.el-alert--error').text()).toContain('rid-9')
    expect(wrapper.find('.el-empty').exists()).toBe(false)
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)
    expect(wrapper.find('.el-table').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(listCases)('$name shows the empty state alone when the list is empty', async (c) => {
    c.request.mockResolvedValue(c.name === 'Equivalents' ? { items: [] } : paginated([]))

    const { wrapper } = await mountPage(c.component, c.path)

    expect(wrapper.find('.el-empty').exists()).toBe(true)
    expect(wrapper.find('.el-alert--error').exists()).toBe(false)
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(listCases)('$name recovers through the retry button of the failure', async (c) => {
    c.request.mockRejectedValueOnce(new ApiException({ status: 500, code: 'E500', message: 'boom' })).mockResolvedValue(c.next)

    const { wrapper } = await mountPage(c.component, c.path)
    expect(wrapper.find('.el-alert--error').exists()).toBe(true)
    await buttonByText(wrapper.element, t('common.refresh')).click()
    await settle()

    expect(wrapper.find('.el-alert--error').exists()).toBe(false)
    expect(wrapper.text()).toContain('NEW')
    wrapper.unmount()
  })
})

describe('mounted list pages: paging and filters make one request', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiMock.listEquivalents.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it.each(listCases.filter((c) => c.name !== 'Equivalents'))(
    '$name: a filter change on page 2 goes back to page 1 with a single request',
    async (c) => {
      c.request.mockResolvedValue(
        c.name === 'Participants'
          ? { items: [participantNew], page: 1, per_page: 20, total: 40 }
          : c.name === 'Trustlines'
            ? { items: [trustlineNew], page: 1, per_page: 20, total: 40 }
            : { items: [auditNew], page: 1, per_page: 20, total: 40 },
      )
      const { wrapper } = await mountPage(c.component, c.path)
      wrapper.findComponent({ name: 'ElPagination' }).vm.$emit('update:currentPage', 2)
      await settle()
      expect(lastArg(c.request)).toMatchObject({ page: 2 })

      const before = c.request.mock.calls.length
      await c.trigger(wrapper, 7)

      expect(c.request.mock.calls.length - before).toBe(1)
      expect(lastArg(c.request)).toMatchObject({ page: 1 })
      wrapper.unmount()
    },
  )

  it('a page past the last one is corrected to the last page and the rows of that page are shown', async () => {
    apiMock.listParticipants.mockResolvedValue({ items: [participantNew], page: 1, per_page: 20, total: 40 })
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')
    wrapper.findComponent({ name: 'ElPagination' }).vm.$emit('update:currentPage', 2)
    await settle()
    // The list shrank under the operator: page 2 no longer exists.
    apiMock.listParticipants.mockResolvedValue({ items: [participantNew], page: 1, per_page: 20, total: 5 })
    wrapper.findComponent({ name: 'ElPagination' }).vm.$emit('update:currentPage', 3)
    await settle()
    await settle()

    expect(lastArg(apiMock.listParticipants)).toMatchObject({ page: 1 })
    expect(wrapper.text()).toContain('NEW')
    expect(wrapper.find('.el-skeleton').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('selected operator and navigation paths', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({ value: 'operator reason', action: 'confirm' } as never)
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    apiMock.listEquivalents.mockResolvedValue({ items: [] })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('freezes a participant with the reason, reloads, and does nothing after the page is gone', async () => {
    const suspended = { ...participantNew, status: 'suspended' }
    apiMock.listParticipants
      .mockResolvedValueOnce(paginated([participantNew]))
      .mockResolvedValueOnce(paginated([suspended]))
      .mockResolvedValue(paginated([participantNew]))
    apiMock.freezeParticipant.mockResolvedValue({ pid: participantNew.pid, status: 'suspended' })
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')

    await wrapper.find('[data-testid="participants-freeze-btn"]').trigger('click')
    await settle()
    expect(apiMock.freezeParticipant).toHaveBeenCalledWith('NEW', 'operator reason')
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    expect(apiMock.listParticipants).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="participants-unfreeze-btn"]').exists()).toBe(true)

    const pending = deferred<{ pid: string; status: string }>()
    apiMock.unfreezeParticipant.mockImplementationOnce(() => pending.promise)
    await wrapper.find('[data-testid="participants-unfreeze-btn"]').trigger('click')
    await settle()
    wrapper.unmount()
    pending.resolve({ pid: participantNew.pid, status: 'active' })
    await settle()

    expect(apiMock.listParticipants).toHaveBeenCalledTimes(2)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
  })

  it('does not call the server when the reason prompt is cancelled', async () => {
    apiMock.listParticipants.mockResolvedValue(paginated([participantNew]))
    vi.spyOn(ElMessageBox, 'prompt').mockRejectedValue('cancel')
    const { wrapper } = await mountPage(ParticipantsPage, '/participants')

    await wrapper.find('[data-testid="participants-freeze-btn"]').trigger('click')
    await settle()

    expect(apiMock.freezeParticipant).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('covers Equivalent state change, usage-guard failure, navigation, and late unmount', async () => {
    const inactive = { ...equivalentNew, is_active: false }
    apiMock.listEquivalents.mockResolvedValue({ items: [equivalentNew] })
    apiMock.setEquivalentActive.mockResolvedValue({ updated: inactive })
    const { wrapper, router } = await mountPage(EquivalentsPage, '/equivalents')

    await buttonByText(wrapper.element, t('common.deactivate')).click()
    await settle()
    expect(apiMock.setEquivalentActive).toHaveBeenCalledWith('NEW', false, 'operator reason')
    expect(wrapper.text()).toContain(t('common.activate'))

    apiMock.getEquivalentUsage.mockResolvedValue({ code: 'NEW', trustlines: 2, debts: 0, integrity_checkpoints: 0 })
    apiMock.deleteEquivalent.mockRejectedValue(
      new ApiException({ status: 409, code: 'CONFLICT', message: 'equivalent is in use' }),
    )
    await buttonByText(wrapper.element, t('common.delete')).click()
    await settle()
    expect(apiMock.getEquivalentUsage).toHaveBeenCalledWith('NEW')
    expect(ElMessage.error).toHaveBeenCalled()

    await buttonByText(wrapper.element, t('common.audit')).click()
    await settle()
    expect(router.currentRoute.value.path).toBe('/audit-log')
    expect(router.currentRoute.value.query).toEqual({ code: 'NEW', q: 'NEW' })
    wrapper.unmount()
  })

  it('creates an equivalent and does nothing after the page is gone', async () => {
    apiMock.listEquivalents.mockResolvedValue({ items: [equivalentNew] })
    const pending = deferred<{ created: typeof equivalentNew }>()
    apiMock.createEquivalent.mockImplementationOnce(() => pending.promise)
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    const listCalls = apiMock.listEquivalents.mock.calls.length

    await buttonByText(wrapper.element, t('common.create')).click()
    await settle()
    const dialog = wrapper.find('.el-dialog').element as HTMLElement
    buttonByText(dialog, t('common.create')).click()
    await settle()
    wrapper.unmount()
    pending.resolve({ created: equivalentNew })
    await settle()

    expect(apiMock.createEquivalent).toHaveBeenCalledTimes(1)
    expect(apiMock.listEquivalents).toHaveBeenCalledTimes(listCalls)
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
})

describe('Audit search and the route', () => {
  let apiCallsBefore = 0

  beforeEach(() => {
    vi.useFakeTimers()
    apiMock.listAuditLog.mockResolvedValue(paginated([auditNew]))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  const input = (wrapper: VueWrapper) => wrapper.find('.hdr input')
  const lastCall = () => lastArg(apiMock.listAuditLog)

  it('keeps q synchronized both ways while preserving the other query keys', async () => {
    const { wrapper, router } = await mountPage(AuditLogPage, '/audit-log', { scenario: 'slow', q: 'admin' })
    expect((input(wrapper).element as HTMLInputElement).value).toBe('admin')
    expect(lastCall()).toEqual({ page: 1, per_page: 20, q: 'admin' })

    apiMock.listAuditLog.mockResolvedValue({ items: [auditNew], page: 2, per_page: 20, total: 40 })
    wrapper.findComponent({ name: 'ElPagination' }).vm.$emit('update:currentPage', 2)
    await settle()
    expect(lastCall()).toEqual({ page: 2, per_page: 20, q: 'admin' })
    apiCallsBefore = apiMock.listAuditLog.mock.calls.length

    // Typing is written to the route as typed ...
    await input(wrapper).setValue('admin ')
    await settle()
    expect(router.currentRoute.value.query).toEqual({ scenario: 'slow', q: 'admin ' })
    await vi.runAllTimersAsync()
    await settle()
    // ... but a change that does not change the normalized search does not reload.
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(apiCallsBefore)

    // An outside change of the route reaches the input.
    await router.replace({ query: { scenario: 'slow', q: 'from link' } })
    await settle()
    expect((input(wrapper).element as HTMLInputElement).value).toBe('from link')

    // A search that changes the normalized text resets to page 1 with one request.
    apiCallsBefore = apiMock.listAuditLog.mock.calls.length
    await input(wrapper).setValue('admin config ')
    await settle()
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog.mock.calls.length - apiCallsBefore).toBe(1)
    expect(lastCall()).toEqual({ page: 1, per_page: 20, q: 'admin config' })
    wrapper.unmount()
  })

  it('round-trips a whitespace-only q exactly without a normalized reload', async () => {
    const { wrapper, router } = await mountPage(AuditLogPage, '/audit-log', { scenario: 'slow', q: ' \t ' })
    const initialCalls = apiMock.listAuditLog.mock.calls.length
    expect((input(wrapper).element as HTMLInputElement).value).toBe(' \t ')
    expect(lastCall()).toEqual({ page: 1, per_page: 20, q: undefined })

    await input(wrapper).setValue('  \t ')
    await settle()
    expect(router.currentRoute.value.query).toEqual({ scenario: 'slow', q: '  \t ' })
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(initialCalls)

    await input(wrapper).setValue('')
    await settle()
    expect(router.currentRoute.value.query).toEqual({ scenario: 'slow' })
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(initialCalls)
    wrapper.unmount()
  })
})
