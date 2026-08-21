import { expect, request, test, type APIRequestContext, type APIResponse } from '@playwright/test'

type FeatureFlags = {
  multipath_enabled: boolean
  full_multipath_enabled: boolean
  clearing_enabled: boolean
}

type ParticipantList = {
  items: Array<{ pid: string; status: string }>
}

type AuditList = {
  items: Array<{
    action: string
    object_id?: string | null
    after_state?: Record<string, unknown> | null
  }>
}

const backendOrigin = process.env.PHASE4_BACKEND_ORIGIN ?? 'http://127.0.0.1:18141'
const adminToken = process.env.PHASE4_ADMIN_TOKEN

async function expectOk(response: APIResponse, operation: string): Promise<void> {
  if (!response.ok()) throw new Error(`${operation} failed with HTTP ${response.status()}`)
}

async function createAdminApi(): Promise<APIRequestContext> {
  if (!adminToken) throw new Error('PHASE4_ADMIN_TOKEN is required')
  return request.newContext({
    baseURL: backendOrigin,
    extraHTTPHeaders: { 'X-Admin-Token': adminToken },
  })
}

test('feature flag mutation is durable, audited, and restored', async ({ page }) => {
  const api = await createAdminApi()
  let original: FeatureFlags | undefined

  try {
    const initialResponse = await api.get('/api/v1/admin/feature-flags')
    await expectOk(initialResponse, 'read feature flags')
    original = (await initialResponse.json()) as FeatureFlags

    await page.goto('/config?tab=featureFlags')
    const row = page.locator('.el-table__row', { hasText: 'CLEARING_ENABLED' })
    const toggle = row.locator('.el-switch')
    const save = page.getByRole('button', { name: 'Save', exact: true })

    await expect(toggle).toBeVisible()
    await toggle.click()
    await expect(save).toBeEnabled()
    await save.click()
    await expect(page.getByText('Saved (1 keys)', { exact: true })).toBeVisible()
    await expect(save).toBeDisabled()
    await expect(row.getByRole('switch')).toHaveAttribute('aria-checked', String(!original.clearing_enabled))

    const changedResponse = await api.get('/api/v1/admin/feature-flags')
    await expectOk(changedResponse, 'read changed feature flags')
    const changed = (await changedResponse.json()) as FeatureFlags
    expect(changed.clearing_enabled).toBe(!original.clearing_enabled)

    const auditResponse = await api.get('/api/v1/admin/audit-log', {
      params: { action: 'admin.config.patch', page: 1, per_page: 10 },
    })
    await expectOk(auditResponse, 'read config audit')
    const audit = (await auditResponse.json()) as AuditList
    expect(
      audit.items.some(
        (item) =>
          item.action === 'admin.config.patch' &&
          item.after_state?.CLEARING_ENABLED === !original.clearing_enabled,
      ),
    ).toBe(true)
  } finally {
    if (original) {
      const restoreResponse = await api.patch('/api/v1/admin/feature-flags', { data: original })
      await expectOk(restoreResponse, 'restore feature flags')
    }
    await api.dispose()
  }
})

test('participant freeze and unfreeze are visible, audited, and cleaned up', async ({ page }) => {
  const api = await createAdminApi()
  let pid: string | undefined

  try {
    const participantsResponse = await api.get('/api/v1/admin/participants', {
      params: { page: 1, per_page: 200 },
    })
    await expectOk(participantsResponse, 'read participants')
    const participants = (await participantsResponse.json()) as ParticipantList
    pid = participants.items.find((item) => item.status.toLowerCase() === 'active')?.pid
    if (!pid) throw new Error('No active participant is available for the disposable smoke')

    await page.goto(`/participants?q=${encodeURIComponent(pid)}`)
    const table = page.getByTestId('participants-table')
    const row = table.locator('.el-table__body-wrapper tbody tr', { hasText: pid }).first()
    await expect(row).toBeVisible()

    await row.getByTestId('participants-freeze-btn').click()
    await page.locator('.el-message-box__input input').fill('phase4 real-contract freeze')
    await page.getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(row.getByTestId('participants-unfreeze-btn')).toBeVisible()

    const freezeAuditResponse = await api.get('/api/v1/admin/audit-log', {
      params: { action: 'admin.participants.freeze', object_id: pid, page: 1, per_page: 10 },
    })
    await expectOk(freezeAuditResponse, 'read participant freeze audit')
    const freezeAudit = (await freezeAuditResponse.json()) as AuditList
    expect(
      freezeAudit.items.some(
        (item) => item.action === 'admin.participants.freeze' && item.object_id === pid,
      ),
    ).toBe(true)

    await row.getByTestId('participants-unfreeze-btn').click()
    await page.locator('.el-message-box__input input').fill('phase4 real-contract unfreeze')
    await page.getByRole('button', { name: 'Confirm', exact: true }).click()
    await expect(row.getByTestId('participants-freeze-btn')).toBeVisible()

    const unfreezeAuditResponse = await api.get('/api/v1/admin/audit-log', {
      params: { action: 'admin.participants.unfreeze', object_id: pid, page: 1, per_page: 10 },
    })
    await expectOk(unfreezeAuditResponse, 'read participant unfreeze audit')
    const unfreezeAudit = (await unfreezeAuditResponse.json()) as AuditList
    expect(
      unfreezeAudit.items.some(
        (item) => item.action === 'admin.participants.unfreeze' && item.object_id === pid,
      ),
    ).toBe(true)
  } finally {
    if (pid) {
      const cleanupStatusResponse = await api.get('/api/v1/admin/participants', {
        params: { q: pid, page: 1, per_page: 10 },
      })
      await expectOk(cleanupStatusResponse, 'read participant cleanup status')
      const cleanupParticipants = (await cleanupStatusResponse.json()) as ParticipantList
      const cleanupStatus = cleanupParticipants.items
        .find((item) => item.pid === pid)
        ?.status.toLowerCase()
      if (cleanupStatus === 'suspended' || cleanupStatus === 'frozen') {
        const cleanupResponse = await api.post(
          `/api/v1/admin/participants/${encodeURIComponent(pid)}/unfreeze`,
          { data: { reason: 'phase4 real-contract safety cleanup' } },
        )
        await expectOk(cleanupResponse, 'restore participant to active')
      }
    }
    await api.dispose()
  }
})
