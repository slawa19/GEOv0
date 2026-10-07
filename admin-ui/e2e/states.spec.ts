import { expect, test, type Page } from '@playwright/test'

// DEGRADED AND RARE STATES BY REPLACING ONE RESPONSE (032 S4, 2026-10-07). They used to be mock
// scenarios (`?scenario=error500|admin_forbidden403|integrity_unauthorized401|empty`) and the
// mock's integrity dataset; with the mock gone, `page.route` answers one request of the real
// backend's API and every other request still goes to the seeded backend. `slow` is the barrier in
// `phase4-operator.spec.ts`.

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('admin-ui.locale', 'en')
  })
})

async function answer(page: Page, urlGlob: string, status: number, body: unknown) {
  await page.route(urlGlob, (route) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }),
  )
}

function geoError(code: string, message: string) {
  return { error: { code, message, request_id: 'e2e-request-id' } }
}

test('error500: audit log shows the load error, not an empty list', async ({ page }) => {
  await answer(page, '**/api/v1/admin/audit-log?*', 500, geoError('INTERNAL_ERROR', 'Internal error'))
  await page.goto('/audit-log')

  await expect(page.locator('.el-alert--error')).toBeVisible()
  await expect(page.locator('.el-alert--error')).toContainText('500')
})

test('403: an admin list refused by the server shows the refusal', async ({ page }) => {
  await answer(page, '**/api/v1/admin/participants?*', 403, geoError('FORBIDDEN', 'Insufficient permissions'))
  await page.goto('/participants')

  await expect(page.locator('.el-alert--error')).toBeVisible()
  await expect(page.locator('.el-alert--error')).toContainText('403')
  await expect(page.getByTestId('participants-table')).toHaveCount(0)
})

test('401: integrity refused for an expired session shows the refusal', async ({ page }) => {
  await answer(page, '**/api/v1/integrity/status', 401, geoError('UNAUTHORIZED', 'Session expired'))
  await page.goto('/integrity')

  await expect(page.locator('.el-alert--error')).toBeVisible()
  await expect(page.locator('.el-alert--error')).toContainText('401')
})

test('empty: an empty participant list shows the empty state', async ({ page }) => {
  await answer(page, '**/api/v1/admin/participants?*', 200, { items: [], page: 1, per_page: 20, total: 0 })
  await page.goto('/participants')

  await expect(page.getByText('No participants', { exact: true })).toBeVisible()
  await expect(page.getByTestId('participants-table')).toHaveCount(0)
})

function integrityStatus(status: 'warning' | 'critical') {
  const healthy = {
    status: 'healthy',
    checksum: '',
    last_verified: '2026-01-10T23:50:00Z',
    invariants: {
      zero_sum: { status: 'not_verified', reason: 'check_withdrawn' },
      trust_limits: { passed: true, violations: 0 },
      debt_symmetry: { passed: true, violations: 0 },
    },
    unverified: ['zero_sum'],
  }
  return {
    status,
    last_check: '2026-01-10T23:58:00Z',
    equivalents: {
      UAH: {
        ...healthy,
        status,
        invariants: {
          ...healthy.invariants,
          debt_symmetry: { passed: false, violations: 2, details: null },
        },
      },
      EUR: healthy,
    },
    alerts: ['UAH: debt_symmetry violations=2'],
  }
}

for (const [status, alertClass] of [
  ['warning', '.el-alert--warning'],
  ['critical', '.el-alert--error'],
] as const) {
  test(`integrity ${status}: the overall status and the failing equivalent are shown`, async ({ page }) => {
    await answer(page, '**/api/v1/integrity/status', 200, integrityStatus(status))
    await page.goto('/integrity')

    await expect(page.locator(alertClass).first()).toBeVisible()
    await expect(page.locator('.el-descriptions')).toContainText(status)
    const uahRow = page.locator('.el-table__row', { hasText: 'UAH' }).first()
    await expect(uahRow).toContainText(status)
  })
}
