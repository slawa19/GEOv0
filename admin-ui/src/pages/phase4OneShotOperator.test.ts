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
  it('keeps untouched JSON rows clean', async () => {
    apiMock.getConfig.mockResolvedValue({
      success: true,
      data: { routing: { max_paths: 3 }, clearing: { max_cycle_len: 6 } },
    })

    const { wrapper } = await mountPage(ConfigPage, '/config')

    expect(wrapper.findAll('textarea')).toHaveLength(2)
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    expect(apiMock.patchConfig).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('patches exactly one key after one JSON value changes', async () => {
    const initial = { routing: { max_paths: 3 }, clearing: { max_cycle_len: 6 } }
    const changed = { routing: { max_paths: 3 }, clearing: { max_cycle_len: 7 } }
    apiMock.getConfig
      .mockResolvedValueOnce({ success: true, data: initial })
      .mockResolvedValueOnce({ success: true, data: changed })
    apiMock.patchConfig.mockResolvedValue({ success: true, data: { updated: ['clearing'] } })
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(ConfigPage, '/config')
    const clearingRow = wrapper.findAll('.el-table__row').find((row) => row.text().includes('clearing'))
    expect(clearingRow).toBeDefined()
    await clearingRow!.find('textarea').setValue(JSON.stringify(changed.clearing, null, 2))
    await primaryButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.patchConfig).toHaveBeenCalledTimes(1)
    expect(apiMock.patchConfig).toHaveBeenCalledWith({ clearing: { max_cycle_len: 7 } })
    expect(apiMock.getConfig).toHaveBeenCalledTimes(2)
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('saves one changed value and reloads the durable visible result', async () => {
    apiMock.getConfig
      .mockResolvedValueOnce({ success: true, data: { TEST_FLAG: false } })
      .mockResolvedValueOnce({ success: true, data: { TEST_FLAG: true } })
    apiMock.patchConfig.mockResolvedValue({ success: true, data: { updated: ['TEST_FLAG'] } })
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

    expect(apiMock.patchConfig).toHaveBeenCalledWith({ TEST_FLAG: true })
    expect(apiMock.getConfig).toHaveBeenCalledTimes(2)
    expect(success).toHaveBeenCalledTimes(1)
    expect(primaryButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('keeps invalid JSON dirty, reports rejection, and never calls the mutation', async () => {
    apiMock.getConfig.mockResolvedValue({ success: true, data: { routing: { max_paths: 3 } } })
    const error = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)

    const { wrapper } = await mountPage(ConfigPage, '/config')
    const textarea = wrapper.find('textarea')
    expect(textarea.exists()).toBe(true)
    await textarea.setValue('{')
    await primaryButton(wrapper).trigger('click')
    await flushPromises()

    expect(apiMock.patchConfig).not.toHaveBeenCalled()
    expect(error).toHaveBeenCalledTimes(1)
    expect((textarea.element as HTMLTextAreaElement).value).toBe('{')
    expect(primaryButton(wrapper).attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('makes auditor mode read-only without issuing a mutation', async () => {
    apiMock.getConfig.mockResolvedValue({ success: true, data: { TEST_FLAG: false } })
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
