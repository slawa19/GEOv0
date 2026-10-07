import { expect, test } from '@playwright/test'

import { adminApi, okJson } from './backend'

type TrustlineList = {
  items: Array<{ equivalent: string; from: string; to: string; from_display_name?: string | null }>
}

/** A trust line of the seed under EUR, read from the backend the UI is pointed at. */
async function seededEurTrustline() {
  const api = await adminApi()
  try {
    const list = await okJson<TrustlineList>(
      await api.get('/api/v1/admin/trustlines', { params: { equivalent: 'EUR', page: 1, per_page: 1 } }),
      'read EUR trust lines',
    )
    const line = list.items[0]
    if (!line) throw new Error('The seed has no EUR trust line')
    return line
  } finally {
    await api.dispose()
  }
}

test('graph: loads, supports equivalent filter, and opens node details by keyboard', async ({ page }) => {
  const eur = await seededEurTrustline()
  const pid = String(eur.from).trim()
  expect(pid.length).toBeGreaterThan(0)
  const nodeOptionName = `Node: ${String(eur.from_display_name || pid).trim()} — ${pid}`

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
  // 032 S5 (F-1): the participant analytics are removed; three tabs remain.
  await expect(drawerContent.getByRole('tab')).toHaveText(['Summary', 'Connections', 'Balance'])

  await page.keyboard.press('Escape')
  await expect(drawerContent).toBeHidden()
  await expect(openButton).toBeFocused()
})

test('graph: opens edge details by keyboard', async ({ page }) => {
  // Pick an edge that exists under EUR.
  const eur = await seededEurTrustline()
  const from = String(eur.from).trim()
  const to = String(eur.to).trim()
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
