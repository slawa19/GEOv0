import { expect, test } from '@playwright/test'

test('graph: loads, supports equivalent filter, and opens node details by keyboard', async ({ page, request }) => {
  // Pick a PID that is guaranteed to exist when eq=EUR (from trustlines dataset).
  const trustlinesRes = await request.get('/admin-fixtures/v1/datasets/trustlines.json')
  expect(trustlinesRes.ok()).toBe(true)
  const trustlines = (await trustlinesRes.json()) as Array<{ equivalent: string; from: string; to: string }>
  const eur = trustlines.find((t) => String(t.equivalent).trim().toUpperCase() === 'EUR')
  const pid = String(eur?.from || eur?.to || '').trim()
  expect(pid.length).toBeGreaterThan(0)
  const participantsRes = await request.get('/admin-fixtures/v1/datasets/participants.json')
  expect(participantsRes.ok()).toBe(true)
  const participants = (await participantsRes.json()) as Array<{ pid: string; display_name?: string }>
  const participant = participants.find((candidate) => candidate.pid === pid)
  const nodeOptionName = `Node: ${String(participant?.display_name || pid).trim()} — ${pid}`

  await page.goto('/graph')

  await expect(page.getByRole('img', { name: 'Network graph visualization' })).toBeVisible()

  // UI layout sanity (regression insurance): ensure the toolbar uses the intended grid structure.
  await page.getByRole('tab', { name: 'Filters', exact: true }).click()
  await expect(page.locator('.filtersLayout')).toBeVisible()
  await page.getByRole('tab', { name: 'Display', exact: true }).click()
  await expect(page.locator('.displayGrid')).toBeVisible()

  await expect(page.getByTestId('graph-cy')).toBeVisible()

  // Switch equivalent filter (smoke).
  await page.getByRole('tab', { name: 'Filters', exact: true }).click()
  const eqSelect = page.getByTestId('graph-filter-eq')
  await eqSelect.click()
  await page.getByRole('option', { name: 'EUR', exact: true }).click()

  const elementSelect = page.getByRole('combobox', { name: 'Open graph element' })
  await expect(elementSelect).toBeEnabled()
  await elementSelect.fill(pid)
  await expect(page.getByRole('option', { name: nodeOptionName, exact: true })).toBeVisible()
  await elementSelect.press('ArrowDown')
  await elementSelect.press('Enter')
  await expect(page.getByTestId('graph-element-select')).toContainText(nodeOptionName)
  const openButton = page.getByTestId('graph-element-open')
  await openButton.focus()
  await page.keyboard.press('Enter')

  // Drawer should open with node details including PID.
  const drawerContent = page.getByTestId('graph-drawer-content')
  await expect(drawerContent).toBeVisible()
  await expect(drawerContent).toContainText(pid)

  await page.keyboard.press('Escape')
  await expect(drawerContent).toBeHidden()
  await expect(openButton).toBeFocused()
})

test('graph: opens edge details by keyboard', async ({ page, request }) => {
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
  await expect(page.getByRole('img', { name: 'Network graph visualization' })).toBeVisible()

  // Ensure equivalent filter is EUR to guarantee the edge is present.
  await page.getByRole('tab', { name: 'Filters', exact: true }).click()
  const eqSelect = page.getByTestId('graph-filter-eq')
  await eqSelect.click()
  await page.getByRole('option', { name: 'EUR', exact: true }).click()

  const edgeOptionName = `Edge: ${from} → ${to} (EUR)`
  const elementSelect = page.getByRole('combobox', { name: 'Open graph element' })
  await expect(elementSelect).toBeEnabled()
  await elementSelect.fill(edgeOptionName)
  await expect(page.getByRole('option', { name: edgeOptionName, exact: true })).toBeVisible()
  await elementSelect.press('ArrowDown')
  await elementSelect.press('Enter')
  await expect(page.getByTestId('graph-element-select')).toContainText(edgeOptionName)
  const openButton = page.getByTestId('graph-element-open')
  await openButton.focus()
  await page.keyboard.press('Enter')

  const edgeDrawer = page.getByTestId('graph-drawer-edge')
  await expect(edgeDrawer).toBeVisible()
  await expect(edgeDrawer).toContainText('EUR')
  await expect(edgeDrawer).toContainText(from)
  await expect(edgeDrawer).toContainText(to)
})
