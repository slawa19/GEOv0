import { flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import IntegrityPage from './IntegrityPage.vue'
import type { IntegrityStatusResponse } from '../api/adminContracts'
import { setLocale, t } from '../i18n'
import { mountPage } from '../test/pageHarness'

/**
 * 054-D-18: the per-equivalent status in the Integrity table was the raw wire value (`warning`) while the overall
 * status above it is worded (`integrity.status.*`); in Russian the table read `warning` next to «предупреждение».
 */

const apiMock = vi.hoisted(() => ({
  integrityStatus: vi.fn(),
  integritySummary: vi.fn(),
  integrityVerify: vi.fn(),
  listEquivalents: vi.fn(),
}))
vi.mock('../api', () => ({ api: apiMock }))

type Entry = IntegrityStatusResponse['equivalents'][string]
const INVARIANTS: Entry['invariants'] = {
  zero_sum: { status: 'not_verified', reason: 'check_withdrawn' },
  trust_limits: { passed: true, violations: 0 },
  debt_symmetry: { passed: true, violations: 0 },
}

beforeEach(() => {
  for (const m of Object.values(apiMock)) m.mockReset()
  apiMock.integritySummary.mockResolvedValue({ equivalents: [] })
  apiMock.listEquivalents.mockResolvedValue({ items: [] })
  apiMock.integrityStatus.mockResolvedValue({
    status: 'critical',
    last_check: '2026-10-07T10:00:00Z',
    equivalents: {
      AAA: { status: 'healthy', checksum: 'c', invariants: INVARIANTS },
      BBB: { status: 'warning', checksum: 'c', invariants: INVARIANTS },
      CCC: { status: 'critical', checksum: 'c', invariants: INVARIANTS },
    },
    alerts: [],
  } satisfies IntegrityStatusResponse)
})

afterEach(() => setLocale('en'))

describe('054-D-18 the per-equivalent integrity status is worded', () => {
  it('each row shows the localized status, not the wire value', async () => {
    setLocale('ru')
    const { wrapper } = await mountPage(IntegrityPage, '/integrity')
    await flushPromises()

    for (const [code, value] of [['AAA', 'healthy'], ['BBB', 'warning'], ['CCC', 'critical']] as const) {
      const row = wrapper.findAll('tr').find((tr) => tr.find('.mono').exists() && tr.find('.mono').text() === code)
      if (!row) throw new Error(`row ${code} not found`)
      const tag = row.find('.el-tag')
      expect(tag.text()).toBe(t(`integrity.status.${value}`))
      expect(tag.text()).not.toBe(value)
    }
    wrapper.unmount()
  })
})
