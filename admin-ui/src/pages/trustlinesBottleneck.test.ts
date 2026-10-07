import { describe, expect, it, vi } from 'vitest'

import TrustlinesPage from './TrustlinesPage.vue'
import { setLocale, t } from '../i18n'
import { mountPage, settle } from '../test/pageHarness'
import type { Trustline } from '../types/domain'

/**
 * 032 S7 (D-19): the bottleneck highlight and the threshold of the Trustlines screen.
 *
 * Red on `01553c42`: a closed line was highlighted when its available/limit fell under the threshold (a closed line
 * carries no capacity to be short of), the threshold accepted any text ("5" marked every line a bottleneck) and the
 * status column printed the raw wire value (`active`) next to the localized drawer.
 */

const apiMock = vi.hoisted(() => ({ listTrustlines: vi.fn(), listEquivalents: vi.fn() }))
vi.mock('../api', () => ({ api: apiMock }))

function line(to: string, patch: Partial<Trustline>): Trustline {
  return {
    equivalent: 'UAH', from: 'A', to, limit: '100', used: '95', available: '5', status: 'active',
    created_at: '2026-10-01T00:00:00Z', close_requested_at: null, ...patch,
  }
}

async function screen(items: Trustline[], query: Record<string, string> = {}) {
  apiMock.listTrustlines.mockResolvedValue({ items, page: 1, per_page: 20, total: items.length })
  apiMock.listEquivalents.mockResolvedValue({ items: [{ code: 'UAH', precision: 2, description: '', is_active: true }] })
  return mountPage(TrustlinesPage, '/trustlines', query)
}

function flagged(wrapper: Awaited<ReturnType<typeof screen>>['wrapper']): string[] {
  return wrapper
    .findAll('.el-table__body tbody tr')
    .filter((tr) => tr.find('.bottleneck').exists())
    .map((tr) => tr.text())
}

describe('Trustlines: bottleneck highlight', () => {
  it('marks an active line whose available share is under the threshold', async () => {
    const { wrapper } = await screen([line('SHORT', {}), line('ROOMY', { used: '10', available: '90' })])
    expect(flagged(wrapper)).toHaveLength(1)
    expect(flagged(wrapper)[0]).toContain('SHORT')
    wrapper.unmount()
  })

  it('never marks a closed line, whatever its numbers say', async () => {
    const { wrapper } = await screen([line('CLOSED', { status: 'closed' }), line('ACTIVE', {})])
    const marked = flagged(wrapper)
    expect(marked).toHaveLength(1)
    expect(marked[0]).toContain('ACTIVE')
    expect(marked[0]).not.toContain('CLOSED')
    wrapper.unmount()
  })

  it('never marks a line with no limit (a requested close, or a zero limit)', async () => {
    const { wrapper } = await screen([line('ZERO', { limit: '0', used: '7', available: '-7' })])
    expect(flagged(wrapper)).toEqual([])
    wrapper.unmount()
  })

  it('marks nothing and says so when the threshold is not a number from 0 to 1', async () => {
    const { wrapper } = await screen([line('SHORT', {})], { threshold: '5' })
    expect(flagged(wrapper)).toEqual([])
    const input = wrapper.find('input[placeholder="' + t('trustlines.filter.thresholdPlaceholder') + '"]')
    expect(input.attributes('aria-invalid')).toBe('true')
    wrapper.unmount()
  })

  it('uses the threshold the operator typed when it is valid', async () => {
    const { wrapper } = await screen([line('SHORT', {})])
    expect(flagged(wrapper)).toHaveLength(1)
    await wrapper.find('input[placeholder="' + t('trustlines.filter.thresholdPlaceholder') + '"]').setValue('0.01')
    await settle()
    expect(flagged(wrapper)).toEqual([])
    wrapper.unmount()
  })
})

describe('Trustlines: status column', () => {
  it('prints the localized status, not the wire value', async () => {
    setLocale('ru')
    const { wrapper } = await screen([line('A1', {}), line('B1', { status: 'closed' })])
    const cells = wrapper.findAll('.el-table__body tbody tr').map((tr) => tr.findAll('td')[6]?.text())
    expect(cells).toEqual([t('trustlines.status.active'), t('trustlines.status.closed')])
    expect(cells).not.toContain('active')
    wrapper.unmount()
    setLocale('en')
  })
})
