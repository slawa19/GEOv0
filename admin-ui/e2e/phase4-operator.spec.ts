import { expect, test, type Page, type Request } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('admin-ui.locale', 'en')
  })
})

test('liquidity to trustlines preserves the active query contract', async ({ page }) => {
  await page.goto('/liquidity?equivalent=UAH&threshold=0.42')
  await page.getByRole('button', { name: 'Open Trustlines', exact: true }).click()

  await expect(page).toHaveURL((url) => {
    return (
      url.pathname === '/trustlines' &&
      url.searchParams.get('equivalent') === 'UAH' &&
      url.searchParams.get('threshold') === '0.42'
    )
  })
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
