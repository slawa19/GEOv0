import { ElMessage, ElMessageBox } from 'element-plus'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import EquivalentsPage from './EquivalentsPage.vue'
import { t } from '../i18n'
import { mountPage, settle } from '../test/pageHarness'

/**
 * 054-D-12: a cleared "Precision" field must not reach the server as precision 0 (`Number(null) === 0`, a step of
 * one whole unit). Create: the canon's default (`AdminEquivalentCreateRequest.precision`, default 2); edit: the key is
 * left out of the PATCH, so the step is not changed (`AdminEquivalentUpdateRequest.precision` is optional).
 * 054-D-13: a refused stop/start/delete re-reads the list, so the screen shows what the server holds.
 */

const apiMock = vi.hoisted(() => ({
  listEquivalents: vi.fn(),
  getEquivalentUsage: vi.fn(),
  createEquivalent: vi.fn(),
  updateEquivalent: vi.fn(),
  setEquivalentActive: vi.fn(),
  deleteEquivalent: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

const equivalent = { code: 'UAH', precision: 2, description: 'Hryvnia', is_active: true }
const inactive = { code: 'EUR', precision: 2, description: 'Euro', is_active: false }

function buttonByText(root: ParentNode, text: string): HTMLButtonElement {
  const found = [...root.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === text)
  if (!found) throw new Error(`no button "${text}"`)
  return found as HTMLButtonElement
}

async function clearPrecision(dialog: HTMLElement) {
  const input = dialog.querySelector('.el-input-number input') as HTMLInputElement
  input.value = ''
  input.dispatchEvent(new Event('input'))
  input.dispatchEvent(new Event('change'))
  await settle()
}

beforeEach(() => {
  for (const m of Object.values(apiMock)) m.mockReset()
  apiMock.listEquivalents.mockResolvedValue({ items: [equivalent, inactive] })
  apiMock.getEquivalentUsage.mockResolvedValue({ code: 'EUR', trustlines: 0, debts: 0, integrity_checkpoints: 0 })
  vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({ value: 'because', action: 'confirm' } as never)
})

describe('054-D-12 a cleared precision field is not sent as 0', () => {
  it('create: a cleared field sends the canon default 2, not 0', async () => {
    apiMock.createEquivalent.mockResolvedValue({ created: { ...equivalent, code: 'NEW' } })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    await buttonByText(wrapper.element, t('common.create')).click()
    await settle()
    const dialog = wrapper.find('.el-dialog').element as HTMLElement
    await clearPrecision(dialog)

    buttonByText(dialog, t('common.create')).click()
    await settle()

    expect(apiMock.createEquivalent).toHaveBeenCalledTimes(1)
    expect(apiMock.createEquivalent.mock.calls[0]?.[0].precision).toBe(2)
    wrapper.unmount()
  })

  it('create: a typed precision is sent as typed (0 included)', async () => {
    apiMock.createEquivalent.mockResolvedValue({ created: { ...equivalent, code: 'NEW' } })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    await buttonByText(wrapper.element, t('common.create')).click()
    await settle()
    const dialog = wrapper.find('.el-dialog').element as HTMLElement
    const input = dialog.querySelector('.el-input-number input') as HTMLInputElement
    input.value = '0'
    input.dispatchEvent(new Event('input'))
    input.dispatchEvent(new Event('change'))
    await settle()

    buttonByText(dialog, t('common.create')).click()
    await settle()

    expect(apiMock.createEquivalent.mock.calls[0]?.[0].precision).toBe(0)
    wrapper.unmount()
  })

  it('edit: a cleared field leaves precision out of the PATCH (the step is not changed)', async () => {
    apiMock.updateEquivalent.mockResolvedValue({ updated: equivalent })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    await buttonByText(wrapper.element, t('common.edit')).click()
    await settle()
    const edit = wrapper
      .findAll('.el-dialog')
      .map((d) => d.element as HTMLElement)
      .find((d) => d.textContent?.includes(t('equivalents.dialog.editing', { code: 'UAH' })))!
    await clearPrecision(edit)

    buttonByText(edit, t('common.save')).click()
    await settle()

    expect(apiMock.updateEquivalent).toHaveBeenCalledTimes(1)
    const [code, patch] = apiMock.updateEquivalent.mock.calls[0]!
    expect(code).toBe('UAH')
    expect(patch).toEqual({ description: 'Hryvnia' })
    expect('precision' in patch).toBe(false)
    wrapper.unmount()
  })

  it('edit: an unchanged precision is still sent (the existing PATCH shape is kept)', async () => {
    apiMock.updateEquivalent.mockResolvedValue({ updated: equivalent })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    await buttonByText(wrapper.element, t('common.edit')).click()
    await settle()
    const edit = wrapper
      .findAll('.el-dialog')
      .map((d) => d.element as HTMLElement)
      .find((d) => d.textContent?.includes(t('equivalents.dialog.editing', { code: 'UAH' })))!
    buttonByText(edit, t('common.save')).click()
    await settle()

    expect(apiMock.updateEquivalent.mock.calls[0]?.[1]).toEqual({ precision: 2, description: 'Hryvnia' })
    wrapper.unmount()
  })
})

describe('054-D-13 a refused change re-reads the list', () => {
  it('a refused deactivate reads the list again', async () => {
    apiMock.setEquivalentActive.mockRejectedValue(new Error('409'))
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    const callsBefore = apiMock.listEquivalents.mock.calls.length

    buttonByText(wrapper.element, t('common.deactivate')).click()
    await settle()
    await settle()

    expect(apiMock.setEquivalentActive).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(apiMock.listEquivalents.mock.calls.length).toBe(callsBefore + 1)
    wrapper.unmount()
  })

  it('a refused delete reads the list again', async () => {
    apiMock.listEquivalents.mockResolvedValue({ items: [inactive] })
    apiMock.deleteEquivalent.mockRejectedValue(new Error('409'))
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    const callsBefore = apiMock.listEquivalents.mock.calls.length

    buttonByText(wrapper.element, t('common.delete')).click()
    await settle()
    await settle()

    expect(apiMock.deleteEquivalent).toHaveBeenCalledTimes(1)
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(apiMock.listEquivalents.mock.calls.length).toBe(callsBefore + 1)
    wrapper.unmount()
  })
})
