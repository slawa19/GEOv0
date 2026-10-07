import { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import EquivalentsPage from './EquivalentsPage.vue'
import { t } from '../i18n'
import { deferred, mountPage, settle } from '../test/pageHarness'

/**
 * 032 S7 (D-19): the operator paths of the Equivalents screen.
 *
 * Written first, red on `01553c42`:
 * - a second click on Create (or Save) while the first request is in flight sent the request again;
 * - a refused delete showed no counters: the page read `e.details.trustlines`, the client keeps the server's
 *   `details` one level deeper (`e.details.details`, see `requestJson`);
 * - the usage line under a code was cached for the life of the page, whatever had been done to the equivalent.
 *
 * The delete refusal is driven through the REAL client over a stubbed `fetch`, so the exception has the shape the
 * client really builds from the server's 409 body (`app/core/equivalents.py::delete_equivalent`).
 */

const { apiMock, useReal } = vi.hoisted(() => ({
  apiMock: {
    listEquivalents: vi.fn(),
    getEquivalentUsage: vi.fn(),
    createEquivalent: vi.fn(),
    updateEquivalent: vi.fn(),
    setEquivalentActive: vi.fn(),
    deleteEquivalent: vi.fn(),
  },
  useReal: { on: false },
}))
// By default the page talks to the mocked client; the refusal test switches to the REAL client over a stubbed fetch.
vi.mock('../api', async (importOriginal) => {
  const real = await importOriginal<typeof import('../api')>()
  return {
    ...real,
    api: new Proxy(real.api, { get: (target, key) => (useReal.on ? Reflect.get(target, key) : Reflect.get(apiMock, key)) }),
  }
})

const equivalent = { code: 'UAH', precision: 2, description: 'Hryvnia', is_active: true }
const inactive = { ...equivalent, is_active: false }

function buttonByText(root: ParentNode, text: string): HTMLButtonElement {
  const found = [...root.querySelectorAll('button')].find((b) => (b.textContent || '').trim() === text)
  if (!found) throw new Error(`no button "${text}"`)
  return found as HTMLButtonElement
}

beforeEach(() => {
  useReal.on = false
  apiMock.listEquivalents.mockResolvedValue({ items: [equivalent] })
  vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({ value: 'because', action: 'confirm' } as never)
})

afterEach(() => {
  vi.unstubAllGlobals()
  useReal.on = false
})

describe('Equivalents: a request in flight is not sent again', () => {
  it('Create is sent once when pressed twice before the answer', async () => {
    const pending = deferred<{ created: typeof equivalent }>()
    apiMock.createEquivalent.mockImplementationOnce(() => pending.promise)
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    await buttonByText(wrapper.element, t('common.create')).click()
    await settle()
    const dialog = wrapper.find('.el-dialog').element as HTMLElement
    const submit = buttonByText(dialog, t('common.create'))
    submit.click()
    await settle()
    submit.click()
    await settle()

    expect(apiMock.createEquivalent).toHaveBeenCalledTimes(1)
    expect(submit.classList.contains('is-loading') || submit.disabled).toBe(true)

    pending.resolve({ created: equivalent })
    await settle()
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('Create can be pressed again after a refusal', async () => {
    apiMock.createEquivalent.mockRejectedValueOnce(new Error('refused')).mockResolvedValueOnce({ created: equivalent })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')
    await buttonByText(wrapper.element, t('common.create')).click()
    await settle()
    const dialog = wrapper.find('.el-dialog').element as HTMLElement

    buttonByText(dialog, t('common.create')).click()
    await settle()
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    buttonByText(dialog, t('common.create')).click()
    await settle()

    expect(apiMock.createEquivalent).toHaveBeenCalledTimes(2)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('Save is sent once when pressed twice before the answer', async () => {
    const pending = deferred<{ updated: typeof equivalent }>()
    apiMock.updateEquivalent.mockImplementationOnce(() => pending.promise)
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    await buttonByText(wrapper.element, t('common.edit')).click()
    await settle()
    const dialogs = wrapper.findAll('.el-dialog').map((d) => d.element as HTMLElement)
    const edit = dialogs.find((d) => d.textContent?.includes(t('equivalents.dialog.editing', { code: 'UAH' })))!
    const save = buttonByText(edit, t('common.save'))
    save.click()
    await settle()
    save.click()
    await settle()

    expect(apiMock.updateEquivalent).toHaveBeenCalledTimes(1)
    pending.resolve({ updated: equivalent })
    await settle()
    wrapper.unmount()
  })
})

describe('Equivalents: a refused delete tells the operator what uses the equivalent', () => {
  function json(status: number, body: unknown): Response {
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }

  it('shows the counters the server sent in the 409 details', async () => {
    useReal.on = true
    localStorage.setItem('admin-ui.adminToken', 'test-admin-token')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = String(init?.method || 'GET')
        if (url.includes('/api/v1/admin/equivalents/UAH/usage')) {
          return json(200, { code: 'UAH', trustlines: 3, debts: 2, integrity_checkpoints: 1 })
        }
        if (url.includes('/api/v1/admin/equivalents/UAH') && method === 'DELETE') {
          // The body `app/core/equivalents.py::delete_equivalent` raises as ConflictException(details=counts).
          return json(409, {
            error: { code: 'E008', message: 'Equivalent is in use', details: { trustlines: 3, debts: 2, integrity_checkpoints: 1 }, request_id: 'rid-del' },
          })
        }
        if (url.includes('/api/v1/admin/equivalents')) return json(200, { items: [inactive] })
        return json(404, { error: { code: 'E404', message: `unexpected ${method} ${url}` } })
      }),
    )
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    await buttonByText(wrapper.element, t('common.delete')).click()
    await settle()
    await settle()

    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    const shown = String(vi.mocked(ElMessage.error).mock.calls[0]?.[0])
    expect(shown).toContain(t('equivalents.deleteFailedWithDetails', { msg: '', trustlines: 3, debts: 2, ic: 1 }).replace(/^ /, ''))
    expect(shown).toContain('rid-del')
    wrapper.unmount()
  })

  it('a refusal without counters still shows the message', async () => {
    apiMock.listEquivalents.mockResolvedValue({ items: [inactive] })
    apiMock.getEquivalentUsage.mockResolvedValue({ code: 'UAH', trustlines: 0, debts: 0, integrity_checkpoints: 0 })
    apiMock.deleteEquivalent.mockRejectedValue(new Error('referenced elsewhere'))
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    await buttonByText(wrapper.element, t('common.delete')).click()
    await settle()

    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(String(vi.mocked(ElMessage.error).mock.calls[0]?.[0])).toContain('referenced elsewhere')
    wrapper.unmount()
  })
})

describe('Equivalents: the usage line follows the equivalent', () => {
  it('is read again after a change of the equivalent, not kept for the life of the page', async () => {
    apiMock.getEquivalentUsage
      .mockResolvedValueOnce({ code: 'UAH', trustlines: 1, debts: 0, integrity_checkpoints: 0 })
      .mockResolvedValueOnce({ code: 'UAH', trustlines: 5, debts: 0, integrity_checkpoints: 0 })
    apiMock.setEquivalentActive.mockResolvedValue({ updated: inactive })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    await wrapper.find('.el-table__body td').trigger('mouseenter')
    await settle()
    expect(wrapper.find('.code__sub').text()).toContain('1')
    expect(apiMock.getEquivalentUsage).toHaveBeenCalledTimes(1)

    await buttonByText(wrapper.element, t('common.deactivate')).click()
    await settle()
    // The change cleared what the page knew about this code ...
    expect(wrapper.find('.code__sub').exists()).toBe(false)

    await wrapper.find('.el-table__body td').trigger('mouseenter')
    await settle()
    // ... so the next look asks the server, and shows its answer.
    expect(apiMock.getEquivalentUsage).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.code__sub').text()).toContain('5')
    wrapper.unmount()
  })
})

describe('Equivalents: a change in flight is not sent again', () => {
  it('Deactivate runs once when pressed again while the first is in flight, and the button is busy', async () => {
    const pending = deferred<{ updated: typeof inactive }>()
    apiMock.setEquivalentActive.mockImplementationOnce(() => pending.promise)
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    const button = buttonByText(wrapper.element, t('common.deactivate'))
    button.click()
    await settle()
    button.click()
    await settle()

    expect(apiMock.setEquivalentActive).toHaveBeenCalledTimes(1)
    expect(ElMessageBox.prompt).toHaveBeenCalledTimes(1)
    expect(button.classList.contains('is-loading')).toBe(true)

    pending.resolve({ updated: inactive })
    await settle()
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('Delete runs once when pressed again while the first is in flight', async () => {
    apiMock.listEquivalents.mockResolvedValue({ items: [inactive] })
    apiMock.getEquivalentUsage.mockResolvedValue({ code: 'UAH', trustlines: 0, debts: 0, integrity_checkpoints: 0 })
    const pending = deferred<{ deleted: string }>()
    apiMock.deleteEquivalent.mockImplementationOnce(() => pending.promise)
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    const button = buttonByText(wrapper.element, t('common.delete'))
    button.click()
    await settle()
    button.click()
    await settle()

    expect(apiMock.deleteEquivalent).toHaveBeenCalledTimes(1)
    pending.resolve({ deleted: 'UAH' })
    await settle()
    wrapper.unmount()
  })
})

describe('Equivalents: a usage answer that was in flight during a change is not kept', () => {
  it('shows the usage read after the change, not the one that was on its way when it happened', async () => {
    const stale = deferred<{ code: string; trustlines: number; debts: number; integrity_checkpoints: number }>()
    apiMock.getEquivalentUsage
      .mockImplementationOnce(() => stale.promise)
      .mockResolvedValueOnce({ code: 'UAH', trustlines: 7, debts: 0, integrity_checkpoints: 0 })
    apiMock.setEquivalentActive.mockResolvedValue({ updated: inactive })
    const { wrapper } = await mountPage(EquivalentsPage, '/equivalents')

    await wrapper.find('.el-table__body td').trigger('mouseenter')
    await settle()
    await buttonByText(wrapper.element, t('common.deactivate')).click()
    await settle()
    stale.resolve({ code: 'UAH', trustlines: 1, debts: 0, integrity_checkpoints: 0 })
    await settle()
    // The old answer must not be shown ...
    expect(wrapper.find('.code__sub').exists()).toBe(false)

    await wrapper.find('.el-table__body td').trigger('mouseenter')
    await settle()
    // ... and the next look shows the fresh one.
    expect(wrapper.find('.code__sub').text()).toContain('7')
    wrapper.unmount()
  })
})
