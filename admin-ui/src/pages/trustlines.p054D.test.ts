import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import TrustlinesPage from './TrustlinesPage.vue'
import { DEBOUNCE_FILTER_MS } from '../constants/timing'
import { t } from '../i18n'
import { mountPage, paginated, settle } from '../test/pageHarness'

/**
 * 054-D-14: `GET /admin/trustlines` compares `equivalent`, `creditor` and `debtor` exactly
 * (`app/core/admin/graph.py::trustline_page_statements`), so `uah` or `PID ` gave a silently empty list. The equivalent
 * is sent in its canonical form (`^[A-Z0-9_]{1,16}$`: trimmed, upper case); a PID is base58 (case-sensitive), so it is
 * only trimmed. The fields keep what the operator typed.
 */

const apiMock = vi.hoisted(() => ({
  listTrustlines: vi.fn(),
  listEquivalents: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

afterEach(() => {
  vi.useRealTimers()
})

beforeEach(() => {
  for (const m of Object.values(apiMock)) m.mockReset()
  apiMock.listTrustlines.mockResolvedValue(paginated([]))
  apiMock.listEquivalents.mockResolvedValue({ items: [{ code: 'UAH', precision: 2, description: '', is_active: true }] })
})

function inputByPlaceholder(wrapper: Awaited<ReturnType<typeof mountPage>>['wrapper'], placeholder: string) {
  const found = wrapper.findAll('input').find((i) => i.attributes('placeholder') === placeholder)
  if (!found) throw new Error(`no input "${placeholder}"`)
  return found
}

describe('054-D-14 trustline filters are sent normalised', () => {
  it('a lower-case equivalent from the link is sent upper-case', async () => {
    const { wrapper } = await mountPage(TrustlinesPage, '/trustlines', { equivalent: 'uah' })
    await settle()

    const last = apiMock.listTrustlines.mock.calls[apiMock.listTrustlines.mock.calls.length - 1]?.[0]
    expect(last.equivalent).toBe('UAH')
    wrapper.unmount()
  })

  it('typed values with trailing spaces: equivalent upper-cased and trimmed, PIDs trimmed with their case kept', async () => {
    vi.useFakeTimers()
    const { wrapper } = await mountPage(TrustlinesPage, '/trustlines')
    await inputByPlaceholder(wrapper, t('trustlines.filter.equivalentPlaceholder')).setValue('uah ')
    await inputByPlaceholder(wrapper, t('trustlines.creditorFrom')).setValue('PidA1 ')
    await inputByPlaceholder(wrapper, t('trustlines.debtorTo')).setValue(' PidB2')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_FILTER_MS + 50)
    await settle()

    const last = apiMock.listTrustlines.mock.calls[apiMock.listTrustlines.mock.calls.length - 1]?.[0]
    expect(last).toMatchObject({ equivalent: 'UAH', creditor: 'PidA1', debtor: 'PidB2' })
    expect((inputByPlaceholder(wrapper, t('trustlines.creditorFrom')).element as HTMLInputElement).value).toBe('PidA1 ')
    wrapper.unmount()
  })

  it('a filter of only spaces is no filter', async () => {
    vi.useFakeTimers()
    const { wrapper } = await mountPage(TrustlinesPage, '/trustlines')
    await inputByPlaceholder(wrapper, t('trustlines.filter.equivalentPlaceholder')).setValue('  ')
    await inputByPlaceholder(wrapper, t('trustlines.creditorFrom')).setValue('  ')
    await vi.advanceTimersByTimeAsync(DEBOUNCE_FILTER_MS + 50)
    await settle()

    const last = apiMock.listTrustlines.mock.calls[apiMock.listTrustlines.mock.calls.length - 1]?.[0]
    expect(last.equivalent).toBeUndefined()
    expect(last.creditor).toBeUndefined()
    wrapper.unmount()
  })
})
