import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import ConfigPage from './ConfigPage.vue'
import IntegrityPage from './IntegrityPage.vue'
import { useAuthStore } from '../stores/auth'

const apiMock = vi.hoisted(() => ({
  getConfig: vi.fn(),
  patchConfig: vi.fn(),
  getFeatureFlags: vi.fn(),
  patchFeatureFlags: vi.fn(),
  integrityStatus: vi.fn(),
  integrityVerify: vi.fn(),
  integrityRepairNetMutualDebts: vi.fn(),
  integrityRepairCapDebtsToTrustLimits: vi.fn(),
}))

vi.mock('../api', () => ({ api: apiMock }))

const healthyStatus = {
  status: 'healthy',
  last_check: '2026-08-08T12:00:00Z',
  equivalents: {},
  alerts: [],
}

const debtSymmetryFailure = {
  status: 'warning',
  last_check: '2026-08-08T12:00:00Z',
  equivalents: {
    UAH: {
      status: 'warning',
      checksum: 'phase4-checksum',
      invariants: { debt_symmetry: { passed: false, violations: 1 } },
    },
  },
  alerts: ['Debt symmetry violation'],
}

async function mountPage(
  component: object,
  path: string,
  role: 'admin' | 'auditor' = 'admin',
): Promise<{ wrapper: VueWrapper; router: Router }> {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore(pinia).role = role

  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path, component }],
  })
  await router.push(path)
  await router.isReady()

  const wrapper = mount(component, {
    global: { plugins: [pinia, router, ElementPlus] },
  })
  await flushPromises()
  await nextTick()
  return { wrapper, router }
}

function primaryButton(wrapper: VueWrapper) {
  return wrapper.find('button.el-button--primary')
}

beforeEach(() => {
  localStorage.clear()
  for (const mock of Object.values(apiMock)) mock.mockReset()
})

describe('Phase 4 Config operator workflow', () => {
  it('keeps untouched runtime config rows clean', async () => {
    apiMock.getConfig.mockResolvedValue({
      success: true,
      data: { ROUTING_MAX_PATHS: 3, CLEARING_ENABLED: true },
    })

    const { wrapper } = await mountPage(ConfigPage, '/config')

    expect(wrapper.findAll('textarea')).toHaveLength(0)
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    expect(apiMock.patchConfig).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('patches exactly one canonical numeric key after one value changes', async () => {
    const initial = { ROUTING_MAX_PATHS: 3, ROUTING_MAX_HOPS: 6 }
    const changed = { ROUTING_MAX_PATHS: 4, ROUTING_MAX_HOPS: 6 }
    apiMock.getConfig
      .mockResolvedValueOnce({ success: true, data: initial })
      .mockResolvedValueOnce({ success: true, data: changed })
    apiMock.patchConfig.mockResolvedValue({ success: true, data: { updated: ['ROUTING_MAX_PATHS'] } })
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(ConfigPage, '/config')
    const routingRow = wrapper.findAll('.el-table__row').find((row) => row.text().includes('ROUTING_MAX_PATHS'))
    expect(routingRow).toBeDefined()
    await routingRow!.find('input').setValue('4')
    await primaryButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.patchConfig).toHaveBeenCalledTimes(1)
    expect(apiMock.patchConfig).toHaveBeenCalledWith({ ROUTING_MAX_PATHS: 4 })
    expect(apiMock.getConfig).toHaveBeenCalledTimes(2)
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('saves one changed value and reloads the durable visible result', async () => {
    apiMock.getConfig
      .mockResolvedValueOnce({ success: true, data: { CLEARING_ENABLED: false } })
      .mockResolvedValueOnce({ success: true, data: { CLEARING_ENABLED: true } })
    apiMock.patchConfig.mockResolvedValue({ success: true, data: { updated: ['CLEARING_ENABLED'] } })
    const success = vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(ConfigPage, '/config')
    const toggle = wrapper.find('.el-switch')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    await nextTick()

    const save = primaryButton(wrapper)
    expect(save.attributes('disabled')).toBeUndefined()
    await save.trigger('click')
    await flushPromises()

    expect(apiMock.patchConfig).toHaveBeenCalledWith({ CLEARING_ENABLED: true })
    expect(apiMock.getConfig).toHaveBeenCalledTimes(2)
    expect(success).toHaveBeenCalledTimes(1)
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('makes auditor mode read-only without issuing a mutation', async () => {
    apiMock.getConfig.mockResolvedValue({ success: true, data: { CLEARING_ENABLED: false } })
    const { wrapper } = await mountPage(ConfigPage, '/config', 'auditor')

    expect(wrapper.find('.el-switch').classes()).toContain('is-disabled')
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    await primaryButton(wrapper).trigger('click')
    expect(apiMock.patchConfig).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('Phase 4 feature-flag operator workflow', () => {
  it('preserves the reachable Config flag value and avoids false success when PATCH is rejected', async () => {
    apiMock.getConfig.mockResolvedValue({ success: true, data: { CLEARING_ENABLED: true } })
    apiMock.patchConfig.mockRejectedValue(new Error('flag update rejected'))
    const success = vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const error = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(ConfigPage, '/config')
    const toggle = wrapper.find('.el-switch')
    await toggle.trigger('click')
    await nextTick()
    await primaryButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.patchConfig).toHaveBeenCalledWith({ CLEARING_ENABLED: false })
    expect(apiMock.getConfig).toHaveBeenCalledTimes(1)
    expect(success).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalledTimes(1)
    expect(primaryButton(wrapper).attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})

describe('Phase 4 Integrity operator workflow', () => {
  it('verifies after confirmation and reloads the visible status', async () => {
    apiMock.integrityStatus
      .mockResolvedValueOnce({ success: true, data: debtSymmetryFailure })
      .mockResolvedValueOnce({ success: true, data: healthyStatus })
    apiMock.integrityVerify.mockResolvedValue({ success: true, data: healthyStatus })
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    const success = vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(IntegrityPage, '/integrity')
    await primaryButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.integrityVerify).toHaveBeenCalledTimes(1)
    expect(apiMock.integrityStatus).toHaveBeenCalledTimes(2)
    expect(success).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('reports a repair failure, resets busy state, and does not claim success', async () => {
    apiMock.integrityStatus.mockResolvedValue({ success: true, data: debtSymmetryFailure })
    apiMock.integrityRepairNetMutualDebts.mockRejectedValue(new Error('repair rejected'))
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    const success = vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const error = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(IntegrityPage, '/integrity')
    const repair = wrapper.find('button.el-button--warning')
    expect(repair.exists()).toBe(true)
    await repair.trigger('click')
    await flushPromises()

    expect(apiMock.integrityRepairNetMutualDebts).toHaveBeenCalledTimes(1)
    expect(apiMock.integrityStatus).toHaveBeenCalledTimes(1)
    expect(success).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalledTimes(1)
    expect(repair.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })
})
