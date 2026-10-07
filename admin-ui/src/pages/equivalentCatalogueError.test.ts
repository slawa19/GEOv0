import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

import IntegrityPage from './IntegrityPage.vue'
import TrustlinesPage from './TrustlinesPage.vue'
import { ApiException } from '../api/apiException'
import { mountPage, deferred, paginated } from '../test/pageHarness'
import type { Trustline } from '../types/domain'

/**
 * 033 B, item 3. Red on `0c24f030`: a failed `listEquivalents` was swallowed by the page - the sums stayed '—' (right)
 * but nothing said why and nothing could repeat the read, so the operator saw dashes with no cause and no way out.
 * The two screens that print money at the precision of the equivalent now say the catalogue failed, quote the ref
 * of the request and offer a retry that reads the catalogue again; until the answer the sums stay '—'.
 */

const apiMock = vi.hoisted(() => ({
  integrityStatus: vi.fn(),
  integritySummary: vi.fn(),
  listTrustlines: vi.fn(),
  listEquivalents: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

const uah = { code: 'UAH', precision: 2, description: '', is_active: true }
const catalogueDown = () =>
  new ApiException({ status: 500, code: 'E500', message: 'catalogue down', requestId: 'rid-catalogue' })

const OVER = {
  debtor_id: 'PID_DEBTOR',
  creditor_id: 'PID_CREDITOR',
  equivalent_id: 'eq-uuid',
  debt_amount: '12.1',
  trust_limit: '10',
  excess: '2.1',
}

function integrityWithOverLimit() {
  return {
    status: 'healthy',
    last_check: '2026-10-07T10:00:00Z',
    alerts: [],
    equivalents: {
      UAH: {
        status: 'healthy',
        checksum: 'c',
        invariants: {
          zero_sum: { status: 'not_verified', reason: 'check_withdrawn' },
          trust_limits: {
            passed: true,
            violations: 0,
            details: null,
            over_limit_allowed: [OVER],
            growth: { status: 'not_verified', reason: 'requires_operation_prestate' },
          },
          debt_symmetry: { passed: true, violations: 0 },
        },
      },
    },
  }
}

function line(): Trustline {
  return {
    equivalent: 'UAH', from: 'A', to: 'B', limit: '100', used: '95', available: '5', status: 'active',
    created_at: '2026-10-01T00:00:00Z', close_requested_at: null,
  }
}

describe.each([
  {
    name: 'Integrity',
    mount: () => {
      apiMock.integrityStatus.mockResolvedValue(integrityWithOverLimit())
      apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
      return mountPage(IntegrityPage, '/integrity')
    },
    shown: '12.10',
  },
  {
    name: 'Trustlines',
    mount: () => {
      apiMock.listTrustlines.mockResolvedValue(paginated([line()]))
      return mountPage(TrustlinesPage, '/trustlines')
    },
    shown: '95.00',
  },
])('$name: the catalogue of precisions fails (033 B, item 3)', ({ mount, shown }) => {
  it('says so with the ref, keeps the sums as dashes, and a retry reads the catalogue again', async () => {
    apiMock.listEquivalents.mockReset()
    apiMock.listEquivalents.mockRejectedValueOnce(catalogueDown())
    const { wrapper } = await mount()

    const alert = wrapper.find('[data-testid="equivalent-catalogue-error"]')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('catalogue down')
    expect(alert.text()).toContain('(ref: rid-catalogue)')
    // No digit count is guessed while the precision is unknown.
    expect(wrapper.text()).not.toContain(shown)
    expect(wrapper.text()).toContain('—')
    expect(apiMock.listEquivalents).toHaveBeenCalledTimes(1)

    const answer = deferred<{ items: unknown[] }>()
    apiMock.listEquivalents.mockReturnValueOnce(answer.promise)
    await wrapper.find('[data-testid="equivalent-catalogue-retry"]').trigger('click')
    expect(apiMock.listEquivalents).toHaveBeenCalledTimes(2)
    // While the retry is in flight the button cannot be pressed again.
    expect(wrapper.find('[data-testid="equivalent-catalogue-retry"]').attributes('disabled')).toBeDefined()

    answer.resolve({ items: [uah] })
    await flushPromises()
    expect(wrapper.find('[data-testid="equivalent-catalogue-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain(shown)
    wrapper.unmount()
  })

  it('keeps the error when the retry fails too, with the ref of the latest request', async () => {
    apiMock.listEquivalents.mockReset()
    apiMock.listEquivalents.mockRejectedValueOnce(catalogueDown())
    const { wrapper } = await mount()
    apiMock.listEquivalents.mockRejectedValueOnce(
      new ApiException({ status: 500, code: 'E500', message: 'still down', requestId: 'rid-second' }),
    )
    await wrapper.find('[data-testid="equivalent-catalogue-retry"]').trigger('click')
    await flushPromises()
    const alert = wrapper.find('[data-testid="equivalent-catalogue-error"]')
    expect(alert.text()).toContain('(ref: rid-second)')
    expect(alert.text()).not.toContain('rid-catalogue')
    wrapper.unmount()
  })

  it('shows no catalogue error and prints the sums when the catalogue answers', async () => {
    apiMock.listEquivalents.mockReset()
    apiMock.listEquivalents.mockResolvedValue({ items: [uah] })
    const { wrapper } = await mount()
    expect(wrapper.find('[data-testid="equivalent-catalogue-error"]').exists()).toBe(false)
    expect(wrapper.text()).toContain(shown)
    wrapper.unmount()
  })
})
