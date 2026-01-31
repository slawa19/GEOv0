import { expect, test } from '@playwright/test'

test('graph: loads, supports equivalent filter, and opens drawer on node click', async ({ page, request }) => {
  // Pick a PID that is guaranteed to exist when eq=EUR (from trustlines dataset).
  const trustlinesRes = await request.get('/admin-fixtures/v1/datasets/trustlines.json')
  expect(trustlinesRes.ok()).toBe(true)
  const trustlines = (await trustlinesRes.json()) as Array<{ equivalent: string; from: string; to: string }>
  const eur = trustlines.find((t) => String(t.equivalent).trim().toUpperCase() === 'EUR')
  const pid = String(eur?.from || eur?.to || '').trim()
  expect(pid.length).toBeGreaterThan(0)

  await page.goto('/graph')

  await expect(page.getByText('Network Graph', { exact: true })).toBeVisible()

  // UI layout sanity (regression insurance): ensure the toolbar uses the intended grid structure.
  await page.getByRole('tab', { name: 'Filters', exact: true }).click()
  await expect(page.locator('.filtersLayout')).toBeVisible()
  await page.getByRole('tab', { name: 'Display', exact: true }).click()
  await expect(page.locator('.displayGrid')).toBeVisible()

  const cy = page.getByTestId('graph-cy')
  await expect(cy).toBeVisible()

  // Switch equivalent filter (smoke).
  await page.getByRole('tab', { name: 'Filters', exact: true }).click()
  const eqSelect = page.getByTestId('graph-filter-eq')
  await eqSelect.click()
  await page.getByRole('option', { name: 'EUR', exact: true }).click()

  // Deterministically tap the node via the dev-only hook exposed by GraphPage.
  await expect
    .poll(async () => {
      return await page.evaluate((p) => {
        const fn = (globalThis as any).__GEO_TAP_NODE__ as undefined | ((pid: string) => boolean)
        if (typeof fn !== 'function') return false
        return fn(p)
      }, pid)
    })
    .toBe(true)

  // Drawer should open with node details including PID.
  const drawerContent = page.getByTestId('graph-drawer-content')
  await expect(drawerContent).toBeVisible()
  await expect(drawerContent).toContainText(pid)
})

test('graph: opens drawer on edge click', async ({ page, request }) => {
  // Pick an edge that exists under EUR.
  const trustlinesRes = await request.get('/admin-fixtures/v1/datasets/trustlines.json')
  expect(trustlinesRes.ok()).toBe(true)
  const trustlines = (await trustlinesRes.json()) as Array<{ equivalent: string; from: string; to: string }>
  const eur = trustlines.find((t) => String(t.equivalent).trim().toUpperCase() === 'EUR')
  const from = String(eur?.from || '').trim()
  const to = String(eur?.to || '').trim()
  expect(from.length).toBeGreaterThan(0)
  expect(to.length).toBeGreaterThan(0)

  await page.goto('/graph')
  await expect(page.getByText('Network Graph', { exact: true })).toBeVisible()

  // Ensure equivalent filter is EUR to guarantee the edge is present.
  await page.getByRole('tab', { name: 'Filters', exact: true }).click()
  const eqSelect = page.getByTestId('graph-filter-eq')
  await eqSelect.click()
  await page.getByRole('option', { name: 'EUR', exact: true }).click()

  // Tap the edge via dev-only hook.
  await expect
    .poll(async () => {
      return await page.evaluate(
        (args) => {
          const fn = (globalThis as any).__GEO_TAP_EDGE__ as undefined | ((from: string, to: string, eq: string) => boolean)
          if (typeof fn !== 'function') return false
          return fn(args.from, args.to, args.eq)
        },
        { from, to, eq: 'EUR' },
      )
    })
    .toBe(true)

  const edgeDrawer = page.getByTestId('graph-drawer-edge')
  await expect(edgeDrawer).toBeVisible()
  await expect(edgeDrawer).toContainText('EUR')
  await expect(edgeDrawer).toContainText(from)
  await expect(edgeDrawer).toContainText(to)
})
