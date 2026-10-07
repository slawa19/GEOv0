import { expect, test } from '@playwright/test'

import { adminApi, okJson } from './backend'

// TESTS THAT CHANGE SERVER STATE. They run in the `chromium-mutations` project of
// `playwright.config.ts`: after every read-only test, one at a time, and each restores what it
// changed in `finally` - through the API, so a failed UI step cannot leave the shared backend changed.
// Merged 2026-10-07 (032 S4) from `e2e/participants.spec.ts`, `e2e/phase4-operator.spec.ts` and
// `e2e-real/phase4-admin-real-contract.spec.ts`; the duplicate freeze/unfreeze test became one.
test.describe.configure({ mode: 'serial' })

type ConfigList = { items: Array<{ key: string; value: unknown; mutable: boolean }> }
type ParticipantList = { items: Array<{ pid: string; status: string }> }
type AuditList = {
  items: Array<{ action: string; object_id?: string | null; after_state?: Record<string, unknown> | null }>
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('admin-ui.locale', 'en')
  })
})

async function clearingEnabled(api: Awaited<ReturnType<typeof adminApi>>): Promise<boolean> {
  const config = await okJson<ConfigList>(await api.get('/api/v1/admin/config'), 'read config')
  const item = config.items.find((candidate) => candidate.key === 'CLEARING_ENABLED')
  if (!item || typeof item.value !== 'boolean') throw new Error('CLEARING_ENABLED is not a boolean config key')
  expect(item.mutable).toBe(true)
  return item.value
}

test('config change is saved, audited, and restored', async ({ page }) => {
  const api = await adminApi()
  let original: boolean | undefined

  try {
    original = await clearingEnabled(api)

    await page.goto('/config')
    const row = page.locator('.el-table__row', { hasText: 'CLEARING_ENABLED' })
    const toggle = row.locator('.el-switch')
    const save = page.getByRole('button', { name: 'Save', exact: true })

    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(save).toBeEnabled()
    await save.click()
    await expect(page.getByText('Saved (1 keys)', { exact: true })).toBeVisible()
    await expect(save).toBeDisabled()
    await expect(row.getByRole('switch')).toHaveAttribute('aria-checked', String(!original))

    // Durable: the server holds the new value, not only the page.
    expect(await clearingEnabled(api)).toBe(!original)

    const audit = await okJson<AuditList>(
      await api.get('/api/v1/admin/audit-log', { params: { action: 'admin.config.patch', page: 1, per_page: 10 } }),
      'read config audit',
    )
    expect(
      audit.items.some(
        (item) => item.action === 'admin.config.patch' && item.after_state?.CLEARING_ENABLED === !original,
      ),
    ).toBe(true)
  } finally {
    if (original !== undefined) {
      await okJson(
        await api.patch('/api/v1/admin/config', {
          data: { updates: { CLEARING_ENABLED: original }, reason: 'admin e2e restore' },
        }),
        'restore config',
      )
      expect(await clearingEnabled(api)).toBe(original)
    }
    await api.dispose()
  }
})

test('participant freeze and unfreeze are visible, filterable, audited, and cleaned up', async ({ page }) => {
  const api = await adminApi()
  let pid: string | undefined

  try {
    const participants = await okJson<ParticipantList>(
      await api.get('/api/v1/admin/participants', { params: { page: 1, per_page: 200 } }),
      'read participants',
    )
    pid = participants.items.find((item) => item.status.toLowerCase() === 'active')?.pid
    if (!pid) throw new Error('No active participant is available in the seed')

    await page.goto(`/participants?q=${encodeURIComponent(pid)}`)
    const table = page.getByTestId('participants-table')
    const row = table.locator('.el-table__body-wrapper tbody tr', { hasText: pid }).first()
    await expect(row).toBeVisible()

    await row.getByTestId('participants-freeze-btn').click()
    await page.locator('.el-message-box__input input').fill('admin e2e freeze')
    await page.getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(row.getByTestId('participants-unfreeze-btn')).toBeVisible()

    const freezeAudit = await okJson<AuditList>(
      await api.get('/api/v1/admin/audit-log', {
        params: { action: 'admin.participants.freeze', object_id: pid, page: 1, per_page: 10 },
      }),
      'read participant freeze audit',
    )
    expect(freezeAudit.items.some((item) => item.action === 'admin.participants.freeze' && item.object_id === pid)).toBe(
      true,
    )

    // The status filter finds the frozen participant.
    const statusSelect = page.getByTestId('participants-filter-status')
    await statusSelect.click()
    await page.getByRole('option', { name: 'suspended', exact: true }).click()
    await expect(page).toHaveURL((url) => url.searchParams.get('status') === 'suspended')
    await expect(row).toBeVisible()
    await expect(row).toContainText('suspended')
    await statusSelect.click()
    await page.getByRole('option', { name: 'Any status', exact: true }).click()

    await row.getByTestId('participants-unfreeze-btn').click()
    await page.locator('.el-message-box__input input').fill('admin e2e unfreeze')
    await page.getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(row.getByTestId('participants-freeze-btn')).toBeVisible()

    const unfreezeAudit = await okJson<AuditList>(
      await api.get('/api/v1/admin/audit-log', {
        params: { action: 'admin.participants.unfreeze', object_id: pid, page: 1, per_page: 10 },
      }),
      'read participant unfreeze audit',
    )
    expect(
      unfreezeAudit.items.some((item) => item.action === 'admin.participants.unfreeze' && item.object_id === pid),
    ).toBe(true)
  } finally {
    if (pid) {
      const cleanup = await okJson<ParticipantList>(
        await api.get('/api/v1/admin/participants', { params: { q: pid, page: 1, per_page: 10 } }),
        'read participant cleanup status',
      )
      const status = cleanup.items.find((item) => item.pid === pid)?.status.toLowerCase()
      if (status === 'suspended') {
        await okJson(
          await api.post(`/api/v1/admin/participants/${encodeURIComponent(pid)}/unfreeze`, {
            data: { reason: 'admin e2e safety cleanup' },
          }),
          'restore participant to active',
        )
      }
    }
    await api.dispose()
  }
})
