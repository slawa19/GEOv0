import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it, vi } from 'vitest'

import TrustlinesPage from './TrustlinesPage.vue'
import type { Trustline } from '../types/domain'

/**
 * 026 S4 (`T2603.2`): a trust line whose close the creditor requested stays `active` with limit 0 until the debt it
 * supports is repaid. The operator must see the request on the list, not an ordinary active line with zero trust.
 * The control row (an active line with limit 0 and NO request) must not carry the indicator: a zero limit alone is
 * not a request (owner В1).
 */

const apiMock = vi.hoisted(() => ({ listTrustlines: vi.fn(), listEquivalents: vi.fn() }))
vi.mock('../api', () => ({ api: apiMock }))

function row(to: string, closeRequestedAt: string | null): Trustline {
  return { equivalent: 'UAH', from: 'A', to, limit: '0', used: '7', available: '-7', status: 'active',
    created_at: '2026-10-01T00:00:00Z', close_requested_at: closeRequestedAt }
}

describe('TrustlinesPage: requested close', () => {
  it('marks only the line whose close is requested', async () => {
    const items = [row('REQUESTED', '2026-10-02T08:00:00Z'), row('PLAIN_ZERO', null)]
    apiMock.listTrustlines.mockResolvedValue({ items, page: 1, per_page: 20, total: 2 })
    apiMock.listEquivalents.mockResolvedValue({ items: [{ code: 'UAH', precision: 2 }] })
    setActivePinia(createPinia())
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/trustlines', component: TrustlinesPage }] })
    await router.push('/trustlines')
    await router.isReady()
    const wrapper = mount(TrustlinesPage, { global: { plugins: [router, ElementPlus] } })
    await flushPromises()

    const marked = wrapper.findAll('tbody tr').map((tr) => [
      tr.findAll('td').some((td) => td.text().includes('REQUESTED')) ? 'REQUESTED' : 'PLAIN_ZERO',
      tr.find('[data-testid="tl-close-requested"]').exists(),
    ])
    expect(marked).toEqual([['REQUESTED', true], ['PLAIN_ZERO', false]])
    wrapper.unmount()
  })
})
