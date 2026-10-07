import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import IntegrityPage from './IntegrityPage.vue'
import { setLocale } from '../i18n'

/**
 * 032 S5 (F-4, D-21): the integrity holds are shown and cleared on the Integrity screen.
 *
 * Written first, red on `58876244`: the screen read only `GET /integrity/status` and had no
 * notion of a hold, so an operator could not see that money in an equivalent was held, let alone
 * lift it. The test drives the REAL client (`api` -> `realApi`) over a stubbed `fetch`, so the
 * refusal text is derived from the body the server actually sends (`IntegrityHoldClearRefusal`),
 * not from a hand-built exception.
 */

type FetchCall = { url: string; method: string; body: unknown }

const healthyStatus = { status: 'healthy', last_check: '2026-10-07T10:00:00Z', equivalents: {}, alerts: [] }

function summary(holds: Record<string, boolean>) {
  return {
    equivalents: Object.entries(holds).map(([equivalent, hold]) => ({
      equivalent,
      status: hold ? 'critical' : 'healthy',
      checked_at: '2026-10-07T10:00:00Z',
      hold,
    })),
  }
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function refusal(details: Record<string, unknown>): Response {
  return json(409, {
    error: { code: 'E010', message: 'Conflict', details, request_id: 'rid-1' },
  })
}

const equivalentUah = {
  code: 'UAH',
  symbol: 'UAH',
  description: 'Hryvnia',
  precision: 2,
  metadata: {},
  is_active: true,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-07T10:00:00Z',
}

let calls: FetchCall[] = []
let clearResponse: () => Response = () => json(200, equivalentUah)
let holds: Record<string, boolean> = { UAH: true, HOUR: false }

beforeEach(() => {
  setLocale('ru')
  localStorage.clear()
  localStorage.setItem('admin-ui.adminToken', 'test-admin-token')
  calls = []
  holds = { UAH: true, HOUR: false }
  clearResponse = () => json(200, equivalentUah)
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = String(init?.method || 'GET')
      calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (url.endsWith('/api/v1/integrity/status')) return json(200, healthyStatus)
      if (url.endsWith('/api/v1/integrity/summary')) return json(200, summary(holds))
      if (url.endsWith('/api/v1/admin/equivalents/UAH/integrity-hold/clear')) return clearResponse()
      return json(404, { error: { code: 'E404', message: `unexpected ${method} ${url}` } })
    }),
  )
  vi.spyOn(ElMessageBox, 'prompt').mockResolvedValue({ value: 'reconciled again', action: 'confirm' } as never)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

async function mountIntegrity(): Promise<VueWrapper> {
  setActivePinia(createPinia())
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/integrity', component: IntegrityPage }] })
  await router.push('/integrity')
  await router.isReady()
  const wrapper = mount(IntegrityPage, { global: { plugins: [ElementPlus, router] } })
  await flushPromises()
  await nextTick()
  await flushPromises()
  return wrapper
}

function holdRow(wrapper: VueWrapper, code: string) {
  return wrapper.find(`[data-testid="integrity-hold-${code}"]`)
}

describe('Integrity screen: equivalent holds (032 F-4)', () => {
  it('marks the held equivalent and offers to clear only it', async () => {
    const wrapper = await mountIntegrity()

    const uah = holdRow(wrapper, 'UAH')
    expect(uah.exists()).toBe(true)
    expect(uah.text()).toContain('на удержании')
    expect(uah.find('[data-testid="integrity-hold-clear"]').exists()).toBe(true)
    // Not held: listed without the action, so the operator sees the whole set and what is held.
    const hour = holdRow(wrapper, 'HOUR')
    expect(hour.exists()).toBe(true)
    expect(hour.find('[data-testid="integrity-hold-clear"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('clears the hold with the required reason and reloads the summary', async () => {
    const success = vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const wrapper = await mountIntegrity()
    holds = { UAH: false, HOUR: false }

    await holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]').trigger('click')
    await flushPromises()
    await nextTick()
    await flushPromises()

    const clear = calls.filter((c) => c.url.endsWith('/integrity-hold/clear'))
    expect(clear).toHaveLength(1)
    expect(clear[0]?.method).toBe('POST')
    expect(clear[0]?.body).toEqual({ reason: 'reconciled again' })
    expect(success).toHaveBeenCalledTimes(1)
    // The screen trusts the server's answer, not its own optimism: the summary is read again.
    expect(calls.filter((c) => c.url.endsWith('/integrity/summary')).length).toBeGreaterThanOrEqual(2)
    expect(holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not call the server when the reason prompt is cancelled', async () => {
    vi.spyOn(ElMessageBox, 'prompt').mockRejectedValue('cancel')
    const wrapper = await mountIntegrity()
    await holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]').trigger('click')
    await flushPromises()
    expect(calls.filter((c) => c.url.endsWith('/integrity-hold/clear'))).toHaveLength(0)
    wrapper.unmount()
  })

  it.each([
    [{ reason: 'no_integrity_hold' }, 'Удержание уже снято'],
    [
      { reason: 'no_later_passed_reconciliation_result', latest_status: null, recheck_status: null },
      'Сверка ещё не давала результата',
    ],
    [
      { reason: 'no_later_passed_reconciliation_result', latest_status: 'FAILED', recheck_status: null },
      'Последняя сверка не PASSED; дождитесь следующей',
    ],
    [
      { reason: 'no_later_passed_reconciliation_result', latest_status: 'UNVERIFIABLE', recheck_status: null },
      'Последняя сверка не PASSED; дождитесь следующей',
    ],
    [
      { reason: 'no_later_passed_reconciliation_result', latest_status: 'PASSED', recheck_status: 'FAILED' },
      'Проверка при снятии не прошла, удержание остаётся',
    ],
    [
      { reason: 'no_later_passed_reconciliation_result', latest_status: 'PASSED', recheck_status: 'UNVERIFIABLE' },
      'Проверить сейчас нельзя',
    ],
  ])('shows the refusal %j as text, not as a code', async (details, text) => {
    const error = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    clearResponse = () => refusal(details)
    const wrapper = await mountIntegrity()

    await holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]').trigger('click')
    await flushPromises()
    await nextTick()

    const shown = wrapper.find('[data-testid="integrity-hold-refusal"]')
    expect(shown.exists()).toBe(true)
    expect(shown.text()).toContain(text)
    expect(shown.text()).not.toContain(String(details.reason))
    // One message, the page's own - not a generic toast with the decorated HTTP line beside it.
    expect(error).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('falls back to a general text that names the code for an unforeseen refusal', async () => {
    clearResponse = () => json(500, { error: { code: 'E999', message: 'boom' } })
    const wrapper = await mountIntegrity()
    await holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]').trigger('click')
    await flushPromises()
    await nextTick()
    const shown = wrapper.find('[data-testid="integrity-hold-refusal"]')
    expect(shown.exists()).toBe(true)
    expect(shown.text()).toContain('E999')
    wrapper.unmount()
  })

  it('speaks English when the locale is English', async () => {
    setLocale('en')
    clearResponse = () => refusal({ reason: 'no_integrity_hold' })
    const wrapper = await mountIntegrity()
    expect(holdRow(wrapper, 'UAH').text()).toContain('on hold')
    await holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]').trigger('click')
    await flushPromises()
    await nextTick()
    expect(wrapper.find('[data-testid="integrity-hold-refusal"]').text()).toContain('The hold is already cleared')
    wrapper.unmount()
  })
})

describe('Integrity screen: one clear per equivalent at a time (032 S5 review, P2)', () => {
  function deferred<T>() {
    let resolve!: (v: T) => void
    const promise = new Promise<T>((r) => { resolve = r })
    return { promise, resolve }
  }

  it('a second clear of A is not sent while A is in flight, and B finishing does not release A', async () => {
    holds = { UAH: true, EUR: true }
    const pending = new Map<string, ReturnType<typeof deferred<Response>>>()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        const method = String(init?.method || 'GET')
        calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
        if (url.endsWith('/api/v1/integrity/status')) return json(200, healthyStatus)
        if (url.endsWith('/api/v1/integrity/summary')) return json(200, summary(holds))
        const m = /equivalents\/([A-Z]+)\/integrity-hold\/clear$/.exec(url)
        if (m) {
          const d = deferred<Response>()
          pending.set(m[1]!, d)
          return d.promise
        }
        return json(404, { error: { code: 'E404', message: `unexpected ${method} ${url}` } })
      }),
    )
    // Both reason prompts are open at once: the second one is confirmed while the first POST is in flight.
    const promptA = deferred<unknown>()
    const promptB = deferred<unknown>()
    vi.spyOn(ElMessageBox, 'prompt')
      .mockReturnValueOnce(promptA.promise as never)
      .mockReturnValueOnce(promptB.promise as never)
      .mockResolvedValue({ value: 'again', action: 'confirm' } as never)
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const posts = (code: string) => calls.filter((c) => c.url.endsWith(`/${code}/integrity-hold/clear`)).length
    const clearButton = (code: string) => holdRow(wrapper, code).find('[data-testid="integrity-hold-clear"]')

    const wrapper = await mountIntegrity()
    await clearButton('UAH').trigger('click')
    await clearButton('EUR').trigger('click')
    promptA.resolve({ value: 'first', action: 'confirm' })
    await flushPromises()
    expect(posts('UAH')).toBe(1)
    promptB.resolve({ value: 'second', action: 'confirm' })
    await flushPromises()
    expect(posts('EUR')).toBe(1)

    // B finishes while A is still in flight.
    pending.get('EUR')!.resolve(json(200, { ...equivalentUah, code: 'EUR' }))
    await flushPromises()
    await nextTick()

    // A stays blocked: its button is disabled, and a click (even if it reached the handler) sends nothing.
    expect(clearButton('UAH').attributes('disabled')).toBeDefined()
    await clearButton('UAH').trigger('click')
    await flushPromises()
    expect(posts('UAH')).toBe(1)

    pending.get('UAH')!.resolve(json(200, equivalentUah))
    await flushPromises()
    wrapper.unmount()
  })
  it('two prompts for the same equivalent confirmed in turn send one clear', async () => {
    let release!: (r: Response) => void
    clearResponse = () => new Promise<Response>((r) => { release = r }) as unknown as Response
    const first = deferred<unknown>()
    const second = deferred<unknown>()
    vi.spyOn(ElMessageBox, 'prompt')
      .mockReturnValueOnce(first.promise as never)
      .mockReturnValueOnce(second.promise as never)
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    const wrapper = await mountIntegrity()
    const button = holdRow(wrapper, 'UAH').find('[data-testid="integrity-hold-clear"]')
    await button.trigger('click')
    await button.trigger('click')
    first.resolve({ value: 'first', action: 'confirm' })
    await flushPromises()
    second.resolve({ value: 'second', action: 'confirm' })
    await flushPromises()
    expect(calls.filter((c) => c.url.endsWith('/UAH/integrity-hold/clear'))).toHaveLength(1)
    release(json(200, equivalentUah))
    await flushPromises()
    wrapper.unmount()
  })
})
