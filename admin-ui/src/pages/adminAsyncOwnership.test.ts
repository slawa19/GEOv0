/* eslint-disable @typescript-eslint/no-explicit-any -- script-setup state and deferred API envelopes are inspected through Vue runtime test hooks */
import { createPinia } from 'pinia'
import { nextTick, reactive } from 'vue'
import { shallowMount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AuditLogPage from './AuditLogPage.vue'
import EquivalentsPage from './EquivalentsPage.vue'
import IncidentsPage from './IncidentsPage.vue'
import LiquidityPage from './LiquidityPage.vue'
import ParticipantsPage from './ParticipantsPage.vue'
import TrustlinesPage from './TrustlinesPage.vue'

const apiMock = vi.hoisted(() => ({
  listParticipants: vi.fn(),
  freezeParticipant: vi.fn(),
  unfreezeParticipant: vi.fn(),
  listTrustlines: vi.fn(),
  listAuditLog: vi.fn(),
  listIncidents: vi.fn(),
  abortTx: vi.fn(),
  listEquivalents: vi.fn(),
  getEquivalentUsage: vi.fn(),
  createEquivalent: vi.fn(),
  updateEquivalent: vi.fn(),
  setEquivalentActive: vi.fn(),
  deleteEquivalent: vi.fn(),
  liquiditySummary: vi.fn(),
}))

const routing = vi.hoisted(() => ({
  route: null as unknown as { path: string; query: Record<string, unknown> },
  push: vi.fn(),
  replace: vi.fn(),
}))

const ui = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  prompt: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))
vi.mock('vue-router', () => ({
  useRoute: () => routing.route,
  useRouter: () => ({ push: routing.push, replace: routing.replace }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: ui.success, error: ui.error },
  ElMessageBox: { prompt: ui.prompt },
}))

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (reason: unknown) => void
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function ok<T>(data: T) {
  return { success: true as const, data }
}

function paginated<T>(items: T[]) {
  return ok({ items, page: 1, per_page: 20, total: items.length })
}

function setupState(wrapper: VueWrapper) {
  return (wrapper.vm.$ as unknown as { setupState: Record<string, any> }).setupState
}

async function settle() {
  await Promise.resolve()
  await nextTick()
  await Promise.resolve()
}

function mountPage(component: Parameters<typeof shallowMount>[0], path: string, query: Record<string, unknown> = {}) {
  routing.route = reactive({ path, query })
  return shallowMount(component, {
    global: {
      plugins: [createPinia()],
      config: { warnHandler: () => undefined },
      stubs: {
        ElAlert: true,
        ElButton: true,
        ElCard: true,
        ElCol: true,
        ElDescriptions: true,
        ElDescriptionsItem: true,
        ElDialog: true,
        ElDivider: true,
        ElDrawer: true,
        ElEmpty: true,
        ElForm: true,
        ElFormItem: true,
        ElInput: true,
        ElInputNumber: true,
        ElLink: true,
        ElOption: true,
        ElPagination: true,
        ElRow: true,
        ElSelect: true,
        ElSkeleton: true,
        ElStatistic: true,
        ElSwitch: true,
        ElTabPane: true,
        ElTable: true,
        ElTableColumn: true,
        ElTabs: true,
        ElTag: true,
        ElText: true,
        ElTooltip: true,
      },
    },
  })
}

async function proveListOwner(args: {
  component: Parameters<typeof shallowMount>[0]
  path: string
  request: ReturnType<typeof vi.fn>
  oldEnvelope: unknown
  newEnvelope: unknown
  currentValue: (state: Record<string, any>) => unknown
  expected: unknown
}) {
  const staleSuccess = deferred<any>()
  const staleFailure = deferred<any>()
  const latest = deferred<any>()
  const afterUnmount = deferred<any>()
  args.request
    .mockImplementationOnce(() => staleSuccess.promise)
    .mockImplementationOnce(() => staleFailure.promise)
    .mockImplementationOnce(() => latest.promise)
    .mockImplementationOnce(() => afterUnmount.promise)

  const wrapper = mountPage(args.component, args.path)
  await nextTick()
  const state = setupState(wrapper)
  const staleFailureTask = state.load()
  const latestTask = state.load()

  latest.resolve(args.newEnvelope)
  await latestTask
  expect(args.currentValue(state)).toEqual(args.expected)
  expect(state.error).toBeNull()
  expect(state.loading).toBe(false)

  staleSuccess.resolve(args.oldEnvelope)
  await settle()
  staleFailure.reject(new Error('stale failure'))
  await staleFailureTask
  await settle()
  expect(args.currentValue(state)).toEqual(args.expected)
  expect(state.error).toBeNull()
  expect(state.loading).toBe(false)

  const pendingTask = state.load()
  await nextTick()
  const beforeUnmount = {
    value: args.currentValue(state),
    error: state.error,
    loading: state.loading,
  }
  wrapper.unmount()
  afterUnmount.resolve(args.oldEnvelope)
  await pendingTask
  expect({ value: args.currentValue(state), error: state.error, loading: state.loading }).toEqual(beforeUnmount)
}

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
const auditNew = { ...auditOld, id: 'NEW', action: 'new' }

const incidentOld = {
  tx_id: 'OLD',
  state: 'PREPARED',
  initiator_pid: 'P1',
  equivalent: 'USD',
  age_seconds: 200,
  sla_seconds: 120,
}
const incidentNew = { ...incidentOld, tx_id: 'NEW' }

const equivalentOld = { code: 'OLD', precision: 2, description: 'Old', is_active: true }
const equivalentNew = { code: 'NEW', precision: 2, description: 'New', is_active: true }

describe('mounted non-Graph list request ownership', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    routing.push.mockResolvedValue(undefined)
    routing.replace.mockResolvedValue(undefined)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('Participants keeps the latest result and ignores pending work after unmount', async () => {
    await proveListOwner({
      component: ParticipantsPage,
      path: '/participants',
      request: apiMock.listParticipants,
      oldEnvelope: paginated([participantOld]),
      newEnvelope: paginated([participantNew]),
      currentValue: (state) => state.items.map((item: typeof participantNew) => item.pid),
      expected: ['NEW'],
    })
  })

  it('Trustlines keeps the latest result and ignores pending work after unmount', async () => {
    await proveListOwner({
      component: TrustlinesPage,
      path: '/trustlines',
      request: apiMock.listTrustlines,
      oldEnvelope: paginated([trustlineOld]),
      newEnvelope: paginated([trustlineNew]),
      currentValue: (state) => state.items.map((item: typeof trustlineNew) => item.from),
      expected: ['NEW'],
    })
  })

  it('Audit keeps the latest result and ignores pending work after unmount', async () => {
    await proveListOwner({
      component: AuditLogPage,
      path: '/audit-log',
      request: apiMock.listAuditLog,
      oldEnvelope: paginated([auditOld]),
      newEnvelope: paginated([auditNew]),
      currentValue: (state) => state.items.map((item: typeof auditNew) => item.id),
      expected: ['NEW'],
    })
  })

  it('Incidents keeps the latest result and ignores pending work after unmount', async () => {
    await proveListOwner({
      component: IncidentsPage,
      path: '/incidents',
      request: apiMock.listIncidents,
      oldEnvelope: paginated([incidentOld]),
      newEnvelope: paginated([incidentNew]),
      currentValue: (state) => state.items.map((item: typeof incidentNew) => item.tx_id),
      expected: ['NEW'],
    })
  })

  it('Equivalents keeps the latest result and ignores pending work after unmount', async () => {
    await proveListOwner({
      component: EquivalentsPage,
      path: '/equivalents',
      request: apiMock.listEquivalents,
      oldEnvelope: ok({ items: [equivalentOld] }),
      newEnvelope: ok({ items: [equivalentNew] }),
      currentValue: (state) => state.items.map((item: typeof equivalentNew) => item.code),
      expected: ['NEW'],
    })
  })

  it('Liquidity keeps the latest composite result and ignores pending work after unmount', async () => {
    const staleSuccess = deferred<any>()
    const staleFailure = deferred<any>()
    const latest = deferred<any>()
    const afterUnmount = deferred<any>()
    apiMock.listEquivalents.mockResolvedValue(ok({ items: [equivalentNew] }))
    apiMock.liquiditySummary
      .mockImplementationOnce(() => staleSuccess.promise)
      .mockImplementationOnce(() => staleFailure.promise)
      .mockImplementationOnce(() => latest.promise)
      .mockImplementationOnce(() => afterUnmount.promise)

    const oldSummary = { updated_at: '2026-08-08T10:00:00Z', total_limit: '1' }
    const newSummary = { updated_at: '2026-08-08T11:00:00Z', total_limit: '2' }
    const wrapper = mountPage(LiquidityPage, '/liquidity')
    await settle()
    const state = setupState(wrapper)
    const staleFailureTask = state.load()
    await settle()
    const latestTask = state.load()
    await settle()

    latest.resolve(ok(newSummary))
    await latestTask
    staleSuccess.resolve(ok(oldSummary))
    await settle()
    staleFailure.reject(new Error('stale failure'))
    await staleFailureTask
    expect(state.summary.total_limit).toBe('2')
    expect(state.error).toBeNull()
    expect(state.loading).toBe(false)

    const pendingTask = state.load()
    await settle()
    const beforeUnmount = { summary: state.summary, error: state.error, loading: state.loading }
    wrapper.unmount()
    afterUnmount.resolve(ok(oldSummary))
    await pendingTask
    expect({ summary: state.summary, error: state.error, loading: state.loading }).toEqual(beforeUnmount)
  })

  it('cancels pending page debounces on unmount before they can start late requests', async () => {
    vi.useFakeTimers()
    apiMock.listParticipants.mockResolvedValue(paginated([participantNew]))
    let wrapper = mountPage(ParticipantsPage, '/participants')
    await settle()
    let state = setupState(wrapper)
    state.q = 'later'
    await nextTick()
    const participantCalls = apiMock.listParticipants.mock.calls.length
    wrapper.unmount()
    await vi.runAllTimersAsync()
    expect(apiMock.listParticipants).toHaveBeenCalledTimes(participantCalls)

    apiMock.listTrustlines.mockResolvedValue(paginated([trustlineNew]))
    wrapper = mountPage(TrustlinesPage, '/trustlines')
    await settle()
    state = setupState(wrapper)
    state.equivalent = 'EUR'
    await nextTick()
    const trustlineCalls = apiMock.listTrustlines.mock.calls.length
    wrapper.unmount()
    await vi.runAllTimersAsync()
    expect(apiMock.listTrustlines).toHaveBeenCalledTimes(trustlineCalls)

    apiMock.listAuditLog.mockResolvedValue(paginated([auditNew]))
    wrapper = mountPage(AuditLogPage, '/audit-log')
    await settle()
    state = setupState(wrapper)
    state.q = 'later'
    await nextTick()
    const auditCalls = apiMock.listAuditLog.mock.calls.length
    wrapper.unmount()
    await vi.runAllTimersAsync()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(auditCalls)

    apiMock.listEquivalents.mockResolvedValue(ok({ items: [equivalentNew] }))
    apiMock.liquiditySummary.mockResolvedValue(ok({ updated_at: '2026-08-08T11:00:00Z' }))
    wrapper = mountPage(LiquidityPage, '/liquidity')
    await settle()
    routing.route.query = { equivalent: 'EUR', threshold: '0.25' }
    await nextTick()
    const liquidityCalls = apiMock.liquiditySummary.mock.calls.length
    wrapper.unmount()
    await vi.runAllTimersAsync()
    expect(apiMock.liquiditySummary).toHaveBeenCalledTimes(liquidityCalls)
  })
})

describe('selected non-Graph operator and navigation paths', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    routing.push.mockResolvedValue(undefined)
    routing.replace.mockResolvedValue(undefined)
    ui.prompt.mockResolvedValue({ value: 'operator reason' })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('updates Participant drawer state and suppresses post-write effects after unmount', async () => {
    const suspended = { ...participantNew, status: 'suspended' }
    apiMock.listParticipants.mockResolvedValueOnce(paginated([participantNew])).mockResolvedValueOnce(paginated([suspended]))
    apiMock.freezeParticipant.mockResolvedValue(ok({ pid: participantNew.pid, status: 'suspended' }))
    const wrapper = mountPage(ParticipantsPage, '/participants')
    await settle()
    const state = setupState(wrapper)
    state.openRow(state.items[0])
    await state.freeze(state.items[0])
    expect(state.selected.status).toBe('suspended')
    expect(ui.success).toHaveBeenCalledTimes(1)

    const pending = deferred<any>()
    apiMock.unfreezeParticipant.mockImplementationOnce(() => pending.promise)
    const task = state.unfreeze(state.items[0])
    await settle()
    wrapper.unmount()
    pending.resolve(ok({ pid: participantNew.pid, status: 'active' }))
    await task
    expect(apiMock.listParticipants).toHaveBeenCalledTimes(2)
    expect(ui.success).toHaveBeenCalledTimes(1)
  })

  it('trusts the backend Incident reload after abort and leaves no false success after failure', async () => {
    apiMock.listIncidents
      .mockResolvedValueOnce(paginated([incidentNew]))
      .mockResolvedValueOnce(paginated([incidentNew, incidentOld]))
    apiMock.abortTx.mockResolvedValueOnce(ok({ tx_id: incidentNew.tx_id, status: 'aborted' }))
    const wrapper = mountPage(IncidentsPage, '/incidents')
    await settle()
    const state = setupState(wrapper)
    await state.forceAbort(incidentNew)
    expect(state.items.map((item: typeof incidentNew) => item.tx_id)).toEqual([incidentNew.tx_id, incidentOld.tx_id])
    expect(state.total).toBe(2)
    expect(state.lastAbortTxId).toBe(incidentNew.tx_id)
    expect(apiMock.listIncidents).toHaveBeenCalledTimes(2)
    expect(ui.success).toHaveBeenCalledTimes(1)

    apiMock.abortTx.mockRejectedValueOnce(new Error('abort rejected'))
    await state.forceAbort(incidentOld)
    expect(state.lastAbortTxId).toBeNull()
    expect(ui.success).toHaveBeenCalledTimes(1)
    expect(ui.error).toHaveBeenCalledWith('abort rejected')
    wrapper.unmount()
  })

  it('covers Equivalent state change, usage-guard failure, scenario navigation, and late unmount', async () => {
    const inactive = { ...equivalentNew, is_active: false }
    apiMock.listEquivalents.mockResolvedValue(ok({ items: [equivalentNew] }))
    apiMock.setEquivalentActive.mockResolvedValue(ok({ updated: inactive }))
    const wrapper = mountPage(EquivalentsPage, '/equivalents', { scenario: 'slow' })
    await settle()
    const state = setupState(wrapper)
    await state.setActive(state.items[0], false)
    expect(state.items[0].is_active).toBe(false)

    apiMock.getEquivalentUsage.mockResolvedValue(ok({
      code: equivalentNew.code,
      trustlines: 2,
      debts: 0,
      integrity_checkpoints: 0,
    }))
    apiMock.deleteEquivalent.mockResolvedValue({
      success: false,
      error: { code: 'CONFLICT', message: 'equivalent is in use' },
    })
    await state.deleteEq(state.items[0])
    expect(apiMock.getEquivalentUsage).toHaveBeenCalledWith(equivalentNew.code)
    expect(ui.error).toHaveBeenCalled()

    state.goAudit(state.items[0])
    expect(routing.push).toHaveBeenLastCalledWith({
      path: '/audit-log',
      query: { scenario: 'slow', code: equivalentNew.code, q: equivalentNew.code },
    })

    const pending = deferred<any>()
    apiMock.createEquivalent.mockImplementationOnce(() => pending.promise)
    const listCalls = apiMock.listEquivalents.mock.calls.length
    const successCalls = ui.success.mock.calls.length
    const task = state.createEq()
    wrapper.unmount()
    pending.resolve(ok({ created: equivalentNew }))
    await task
    expect(apiMock.listEquivalents).toHaveBeenCalledTimes(listCalls)
    expect(ui.success).toHaveBeenCalledTimes(successCalls)
  })

  it('keeps Audit q synchronized both ways while preserving scenario', async () => {
    vi.useFakeTimers()
    apiMock.listAuditLog.mockResolvedValue(paginated([auditNew]))
    const wrapper = mountPage(AuditLogPage, '/audit-log', { scenario: 'slow', q: 'admin' })
    await settle()
    const state = setupState(wrapper)
    expect(state.q).toBe('admin')
    apiMock.listAuditLog.mockResolvedValue(ok({ items: [auditNew], page: 2, per_page: 20, total: 40 }))
    state.page = 2
    await nextTick()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenLastCalledWith({ page: 2, per_page: 20, q: 'admin' })
    const callsBeforeTyping = apiMock.listAuditLog.mock.calls.length

    state.q = 'admin '
    await nextTick()
    expect(routing.replace).toHaveBeenLastCalledWith({ query: { scenario: 'slow', q: 'admin ' } })
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(callsBeforeTyping)

    routing.route.query = { scenario: 'slow', q: 'admin ' }
    await nextTick()
    expect(state.q).toBe('admin ')

    state.q = 'admin config '
    await nextTick()
    expect(routing.replace).toHaveBeenLastCalledWith({ query: { scenario: 'slow', q: 'admin config ' } })
    routing.route.query = { scenario: 'slow', q: 'admin config ' }
    await nextTick()
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(callsBeforeTyping + 1)
    expect(apiMock.listAuditLog).toHaveBeenLastCalledWith({ page: 1, per_page: 20, q: 'admin config' })

    apiMock.listAuditLog.mockResolvedValue(ok({ items: [auditNew], page: 2, per_page: 20, total: 40 }))
    state.page = 2
    await nextTick()
    await settle()
    const callsBeforePageReset = apiMock.listAuditLog.mock.calls.length

    state.q = 'admin config next '
    await nextTick()
    await vi.runAllTimersAsync()
    await settle()
    expect(state.page).toBe(1)
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(callsBeforePageReset + 1)
    expect(apiMock.listAuditLog).toHaveBeenLastCalledWith({ page: 1, per_page: 20, q: 'admin config next' })

    wrapper.unmount()
  })

  it('round-trips whitespace-only Audit q exactly without a normalized reload', async () => {
    vi.useFakeTimers()
    apiMock.listAuditLog.mockResolvedValue(paginated([auditNew]))
    const wrapper = mountPage(AuditLogPage, '/audit-log', { scenario: 'slow', q: ' \t ' })
    await settle()
    const state = setupState(wrapper)
    const initialCalls = apiMock.listAuditLog.mock.calls.length
    expect(state.q).toBe(' \t ')
    expect(apiMock.listAuditLog).toHaveBeenLastCalledWith({ page: 1, per_page: 20, q: undefined })
    expect(routing.replace).not.toHaveBeenCalled()

    state.q = '  \t '
    await nextTick()
    expect(routing.replace).toHaveBeenLastCalledWith({ query: { scenario: 'slow', q: '  \t ' } })
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(initialCalls)

    routing.route.query = { scenario: 'slow', q: '  \t ' }
    await nextTick()
    expect(state.q).toBe('  \t ')

    state.q = ''
    await nextTick()
    expect(routing.replace).toHaveBeenLastCalledWith({ query: { scenario: 'slow' } })
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.listAuditLog).toHaveBeenCalledTimes(initialCalls)

    wrapper.unmount()
  })

  it('reloads Liquidity after a route filter change and carries threshold to Graph', async () => {
    vi.useFakeTimers()
    apiMock.listEquivalents.mockResolvedValue(ok({ items: [equivalentNew] }))
    apiMock.liquiditySummary.mockResolvedValue(ok({
      updated_at: '2026-08-08T11:00:00Z',
      total_limit: '2',
      total_used: '1',
      total_available: '1',
    }))
    const wrapper = mountPage(LiquidityPage, '/liquidity', { scenario: 'slow' })
    await settle()
    routing.route.query = { scenario: 'slow', equivalent: 'EUR', threshold: '0.25' }
    await nextTick()
    await vi.runAllTimersAsync()
    await settle()
    expect(apiMock.liquiditySummary).toHaveBeenLastCalledWith({ equivalent: 'EUR', threshold: '0.25', limit: 10 })

    const state = setupState(wrapper)
    state.goGraph()
    expect(routing.push).toHaveBeenLastCalledWith({
      path: '/graph',
      query: { scenario: 'slow', equivalent: 'EUR', threshold: '0.25' },
    })
    wrapper.unmount()
  })
})
