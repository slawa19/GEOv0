import { flushPromises } from '@vue/test-utils'
import { ElMessage, ElMessageBox } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import IntegrityPage from './IntegrityPage.vue'
import {
  detectedIssues,
  invariantOutcome,
  outcomeLabelKey,
  outcomeTagType,
  overLimitAllowed,
  growthNotVerified,
} from './integrityOutcome'
import type { IntegrityStatusResponse } from '../api/adminContracts'
import { t } from '../i18n'
import { deferred, mountPage } from '../test/pageHarness'

/**
 * 032 S7 (D-4). One pure function decides the outcome of an invariant, and the Integrity screen renders only that
 * outcome. Before: `zero_sum` had its own neutral branch, `trust_limits` printed `failed` for anything that was not
 * `passed: true` (an absent entry too), `debt_symmetry` printed the raw `passed` value, and `over_limit_allowed` and
 * `growth: not_verified` (026 `T2601`: "reported") were never shown.
 */

type Equivalents = IntegrityStatusResponse['equivalents']
type Entry = Equivalents[string]
type Invariants = Entry['invariants']

const WITHDRAWN = { status: 'not_verified', reason: 'check_withdrawn' } as const
const GROWTH = { status: 'not_verified', reason: 'requires_operation_prestate' } as const
const OVER = {
  debtor_id: 'PID_DEBTOR',
  creditor_id: 'PID_CREDITOR',
  equivalent_id: 'eq-uuid',
  debt_amount: '12.12340000',
  trust_limit: '10.00000000',
  excess: '2.12340000',
}

function equivalent(invariants: Invariants, status: Entry['status'] = 'healthy'): Entry {
  return { status, checksum: 'c', invariants }
}

const HEALTHY: Invariants = {
  zero_sum: WITHDRAWN,
  trust_limits: { passed: true, violations: 0, details: null, over_limit_allowed: [], growth: GROWTH },
  debt_symmetry: { passed: true, violations: 0 },
}

describe('invariantOutcome', () => {
  it('tells passed, failed, not verified and absent apart', () => {
    expect(invariantOutcome({ passed: true, violations: 0 })).toBe('passed')
    expect(invariantOutcome({ passed: false, violations: 2 })).toBe('failed')
    expect(invariantOutcome(WITHDRAWN)).toBe('not_verified')
    expect(invariantOutcome(undefined)).toBe('absent')
    expect(invariantOutcome(null)).toBe('absent')
  })

  it('never reads a neutral outcome as a verdict', () => {
    for (const neutral of ['not_verified', 'absent'] as const) {
      expect(outcomeTagType(neutral)).toBe('info')
      expect(outcomeLabelKey(neutral)).not.toBe('common.failed')
      expect(outcomeLabelKey(neutral)).not.toBe('common.passed')
    }
    expect(outcomeTagType('passed')).toBe('success')
    expect(outcomeTagType('failed')).toBe('danger')
    expect(outcomeLabelKey('passed')).toBe('common.passed')
    expect(outcomeLabelKey('failed')).toBe('common.failed')
  })

  it('reports over-limit debts and the unverified growth of a trust_limits entry, and only of that', () => {
    const entry = { passed: true, violations: 0, details: null, over_limit_allowed: [OVER], growth: GROWTH }
    expect(overLimitAllowed(entry)).toEqual([OVER])
    expect(growthNotVerified(entry)).toBe(true)
    // Older rows and the other invariants carry neither.
    expect(overLimitAllowed({ passed: true, violations: 0 })).toEqual([])
    expect(growthNotVerified({ passed: true, violations: 0 })).toBe(false)
    expect(overLimitAllowed(undefined)).toEqual([])
    expect(growthNotVerified(WITHDRAWN)).toBe(false)
  })

  it('lists a detected issue only for a failed verdict, in the stable order', () => {
    const failing: Equivalents = {
      UAH: equivalent({ ...HEALTHY, debt_symmetry: { passed: false, violations: 1 } }, 'warning'),
      EUR: equivalent(
        {
          zero_sum: { passed: false, violations: 1 },
          trust_limits: { passed: false, violations: 1, details: null, over_limit_allowed: [], growth: GROWTH },
          debt_symmetry: { passed: true, violations: 0 },
        },
        'critical',
      ),
    }
    expect(detectedIssues(failing)).toEqual(['zero_sum', 'trust_limits', 'debt_symmetry'])
    // Counter-check against a vacuous detector: neutral and passing outcomes are not issues.
    expect(detectedIssues({ UAH: equivalent(HEALTHY) })).toEqual([])
    expect(detectedIssues({ UAH: equivalent({}) })).toEqual([])
    expect(detectedIssues({})).toEqual([])
  })
})

const apiMock = vi.hoisted(() => ({
  integrityStatus: vi.fn(),
  integritySummary: vi.fn(),
  integrityVerify: vi.fn(),
  clearIntegrityHold: vi.fn(),
  listEquivalents: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

function status(equivalents: Equivalents, overall: IntegrityStatusResponse['status'] = 'healthy'): IntegrityStatusResponse {
  return { status: overall, last_check: '2026-10-07T10:00:00Z', equivalents, alerts: [] }
}

async function screen(equivalents: Equivalents, overall: IntegrityStatusResponse['status'] = 'healthy') {
  apiMock.integrityStatus.mockResolvedValue(status(equivalents, overall))
  const mounted = await mountPage(IntegrityPage, '/integrity')
  await flushPromises()
  return mounted
}

function tableRow(wrapper: Awaited<ReturnType<typeof screen>>['wrapper'], code: string) {
  const row = wrapper.findAll('.el-table__row').find((r) => r.text().includes(code))
  if (!row) throw new Error(`no row for ${code}`)
  return row
}

describe('Integrity screen: the outcome of each invariant', () => {
  beforeEach(() => {
    apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
    apiMock.listEquivalents.mockResolvedValue({ items: [{ code: 'UAH', precision: 2, description: '', is_active: true }] })
  })
  afterEach(() => vi.useRealTimers())

  it('does not write "failed" for a trust_limits entry the server did not send', async () => {
    const { wrapper } = await screen({
      UAH: equivalent({ zero_sum: WITHDRAWN, debt_symmetry: { passed: true, violations: 0 } }),
    })
    const row = tableRow(wrapper, 'UAH')
    expect(row.text()).not.toContain(t('common.failed'))
    expect(row.text()).toContain(t('common.n_a'))
    wrapper.unmount()
  })

  it('writes "failed" for a failed verdict and nowhere else', async () => {
    const { wrapper } = await screen(
      {
        UAH: equivalent(
          { ...HEALTHY, trust_limits: { passed: false, violations: 3, details: null, over_limit_allowed: [], growth: GROWTH } },
          'critical',
        ),
        EUR: equivalent(HEALTHY),
      },
      'critical',
    )
    expect(tableRow(wrapper, 'UAH').text()).toContain(t('common.failed'))
    expect(tableRow(wrapper, 'EUR').text()).not.toContain(t('common.failed'))
    wrapper.unmount()
  })

  it('shows the debts over a lowered limit and that growth is not verified by a snapshot', async () => {
    const { wrapper } = await screen({
      UAH: equivalent({
        ...HEALTHY,
        trust_limits: { passed: true, violations: 0, details: null, over_limit_allowed: [OVER], growth: GROWTH },
      }),
    })
    const row = tableRow(wrapper, 'UAH')
    const over = row.find('[data-testid="integrity-over-limit-allowed"]')
    expect(over.exists()).toBe(true)
    expect(over.text()).toContain('PID_DEBTOR')
    expect(over.text()).toContain('PID_CREDITOR')
    // The money is printed from the decimal string the server sent with the precision of UAH (2) as the MINIMUM of digits:
    // 12.1234 keeps its four digits (a rounding to 2 would say 12.12), 10.00000000 is padded down to 10.00.
    expect(over.text()).toContain(
      t('integrity.overLimitItem', { debtor: 'PID_DEBTOR', creditor: 'PID_CREDITOR', debt: '12.1234', limit: '10.00', excess: '2.1234' }),
    )
    expect(over.text()).not.toContain('12.12340000')
    expect(row.find('[data-testid="integrity-growth-not-verified"]').exists()).toBe(true)
    // An allowed state is not an issue: nothing detected, no failure written.
    expect(row.text()).not.toContain(t('common.failed'))
    expect(wrapper.find('.helpDetected').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows neither marker when there is nothing over the limit', async () => {
    const { wrapper } = await screen({ UAH: equivalent({ ...HEALTHY, trust_limits: { passed: true, violations: 0 } }) })
    const row = tableRow(wrapper, 'UAH')
    expect(row.find('[data-testid="integrity-over-limit-allowed"]').exists()).toBe(false)
    expect(row.find('[data-testid="integrity-growth-not-verified"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('Integrity screen: the latest read owns the status', () => {
  it('an older read that fails late does not replace the status a verify has reloaded', async () => {
    const first = deferred<IntegrityStatusResponse>()
    apiMock.integrityStatus.mockImplementationOnce(() => first.promise)
    apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
    apiMock.listEquivalents.mockResolvedValue({ items: [] })
    apiMock.integrityVerify.mockResolvedValue({})
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
    apiMock.integrityStatus.mockResolvedValueOnce(status({ UAH: equivalent(HEALTHY) }))

    const { wrapper } = await mountPage(IntegrityPage, '/integrity')
    const verify = wrapper.findAll('button').find((b) => b.text() === t('integrity.verify.action'))!
    await verify.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('UAH')

    first.reject(new Error('late failure'))
    await flushPromises()
    expect(wrapper.find('.el-alert--error').exists()).toBe(false)
    expect(wrapper.text()).toContain('UAH')
    wrapper.unmount()
  })
})
