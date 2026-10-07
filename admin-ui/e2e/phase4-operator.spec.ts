import { expect, test, type Page, type Request } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('admin-ui.locale', 'en')
  })
})

// 032 S5 (F-3): the Dashboard shows one row per seeded equivalent, read from the real backend - never an empty table.
test('dashboard shows a row per seeded equivalent with its own sums', async ({ page }) => {
  await page.goto('/dashboard')
  const card = page.getByTestId('dashboard-equivalents')
  const rows = card.locator('.el-table__body-wrapper tbody tr')
  await expect(rows.first()).toBeVisible()
  for (const code of ['EUR', 'HOUR', 'UAH']) {
    const row = rows.filter({ hasText: code }).first()
    await expect(row).toBeVisible()
    // The total limit column carries a decimal amount, not the "no precision" dash.
    await expect(row.locator('td').nth(3)).toHaveText(/^\d+(\.\d+)?$/)
  }
})

// 032 S5 (F-4): the old incidents address lands on Integrity, where the holds live.
test('the removed incidents screen redirects to Integrity', async ({ page }) => {
  await page.goto('/incidents')
  await expect(page).toHaveURL((url) => url.pathname === '/integrity')
  await expect(page.getByTestId('integrity-holds')).toBeVisible()
})

// 032 S5 (F-4): a hold and a refused clear, by substituting the two answers (`page.route`) - the seed holds no
// equivalent, and holding one for real needs a FAILED reconciliation, i.e. a corrupted ledger.
test('integrity shows a held equivalent and explains a refused clear as text', async ({ page }) => {
  await page.route('**/api/v1/integrity/summary', (route) =>
    route.fulfill({
      json: {
        equivalents: [
          { equivalent: 'UAH', status: 'critical', checked_at: '2026-10-07T10:00:00Z', hold: true },
          { equivalent: 'HOUR', status: 'healthy', checked_at: '2026-10-07T10:00:00Z', hold: false },
        ],
      },
    }),
  )
  let clearBody: unknown = null
  await page.route('**/api/v1/admin/equivalents/UAH/integrity-hold/clear', (route) => {
    clearBody = route.request().postDataJSON()
    return route.fulfill({
      status: 409,
      json: {
        error: {
          code: 'E010',
          message: 'Equivalent UAH can be cleared only after a later PASSED reconciliation result',
          details: { reason: 'no_later_passed_reconciliation_result', latest_status: 'FAILED', recheck_status: null },
        },
      },
    })
  })

  await page.goto('/integrity')
  const held = page.getByTestId('integrity-hold-UAH')
  await expect(held).toContainText('on hold')
  await expect(page.getByTestId('integrity-hold-HOUR').getByTestId('integrity-hold-clear')).toHaveCount(0)

  await held.getByTestId('integrity-hold-clear').click()
  const dialog = page.getByRole('dialog')
  await dialog.getByRole('textbox').fill('reconciled again')
  await dialog.getByRole('button', { name: 'Clear', exact: true }).click()

  await expect(page.getByTestId('integrity-hold-refusal')).toContainText(
    'The latest reconciliation is not PASSED; wait for the next one',
  )
  expect(clearBody).toEqual({ reason: 'reconciled again' })
})

/**
 * Holds the first list request that matches `isStale` until the test releases it - a barrier, not a
 * timer - so the stale response is guaranteed to arrive AFTER the newer one has been rendered. This
 * is what the mock `?scenario=slow` used to approximate with a fixed delay.
 */
async function holdFirstMatching(page: Page, urlGlob: string, isStale: (request: Request) => boolean) {
  let release!: () => void
  const released = new Promise<void>((resolve) => {
    release = resolve
  })
  let seen!: () => void
  const held = new Promise<void>((resolve) => {
    seen = resolve
  })
  let holding = false
  await page.route(urlGlob, async (route) => {
    if (!holding && isStale(route.request())) {
      holding = true
      seen()
      await released
    }
    await route.continue()
  })
  return { held, release }
}

/** Two animation frames: the response handler and the render it schedules have both run. */
async function settled(page: Page) {
  await page.evaluate(
    () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))),
  )
}

test('rapid participant and trustline filters commit only the latest visible result', async ({ page }) => {
  // Two seeded participants whose names do not match each other's search.
  const stale = 'Riverside Fishing'
  const latest = 'Marina & Boat'

  const participantsGate = await holdFirstMatching(
    page,
    '**/api/v1/admin/participants?*',
    (request) => new URL(request.url()).searchParams.get('q') === stale,
  )
  await page.goto('/participants')
  const participantsTable = page.getByTestId('participants-table')
  await expect(participantsTable).toBeVisible()

  const participantSearch = page.getByRole('textbox', { name: 'Search PID / name' })
  await participantSearch.fill(stale)
  await participantsGate.held
  await expect(page.locator('.el-skeleton')).toBeVisible()
  await participantSearch.fill(latest)

  await expect(page).toHaveURL((url) => url.pathname === '/participants' && url.searchParams.get('q') === latest)
  await expect(participantsTable).toContainText('Marina & Boat Services')
  const staleParticipants = page.waitForResponse(
    (response) => new URL(response.url()).searchParams.get('q') === stale,
  )
  participantsGate.release()
  await staleParticipants
  await settled(page)
  await expect(participantsTable).toContainText('Marina & Boat Services')
  await expect(participantsTable).not.toContainText('Riverside Fishing Co-operative')

  const trustlinesGate = await holdFirstMatching(
    page,
    '**/api/v1/admin/trustlines?*',
    (request) => new URL(request.url()).searchParams.get('equivalent') === 'UAH',
  )
  await page.goto('/trustlines')
  const trustlinesTable = page.locator('.el-table').first()
  await expect(trustlinesTable).toBeVisible()

  const equivalentFilter = page.getByRole('textbox', { name: 'Equivalent (e.g. UAH)' })
  await equivalentFilter.fill('UAH')
  await trustlinesGate.held
  await expect(page.locator('.el-skeleton')).toBeVisible()
  await equivalentFilter.fill('HOUR')

  await expect(page).toHaveURL((url) => url.pathname === '/trustlines' && url.searchParams.get('equivalent') === 'HOUR')
  const firstRow = trustlinesTable.locator('.el-table__body-wrapper tbody tr').first()
  await expect(firstRow).toContainText('HOUR')
  const staleTrustlines = page.waitForResponse(
    (response) => new URL(response.url()).searchParams.get('equivalent') === 'UAH',
  )
  trustlinesGate.release()
  await staleTrustlines
  await settled(page)
  await expect(firstRow).toContainText('HOUR')
  await expect(trustlinesTable).not.toContainText('UAH')
})
