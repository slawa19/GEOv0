import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem('admin-ui.locale', 'en')
    window.localStorage.setItem('admin-ui.role', 'admin')
  })
})

test('canonical config save and auditor read-only stay truthful', async ({ page }) => {
  await page.goto('/config')

  const routingRow = page.locator('.el-table__row', { hasText: 'ROUTING_MAX_PATHS' }).first()
  const editor = routingRow.locator('input').last()
  const save = page.getByRole('button', { name: 'Save', exact: true })

  await expect(editor).toBeVisible()
  const original = Number(await editor.inputValue())
  const changed = original + 1
  await editor.fill(String(changed))
  await expect(save).toBeEnabled()
  await save.click()

  await expect(page.getByText('Saved (1 keys)', { exact: true })).toBeVisible()
  await expect(editor).toHaveValue(String(changed))
  await expect(save).toBeDisabled()

  const roleSelect = page.locator('.header__right .el-select').nth(1)
  await roleSelect.click()
  await page.getByRole('option', { name: 'auditor (read-only)', exact: true }).click()

  await expect(editor).toBeDisabled()
  await expect(save).toBeDisabled()
})

test('liquidity to trustlines preserves the active query contract', async ({ page }) => {
  await page.goto('/liquidity?scenario=happy&equivalent=UAH&threshold=0.42')
  await page.getByRole('button', { name: 'Open Trustlines', exact: true }).click()

  await expect(page).toHaveURL((url) => {
    return (
      url.pathname === '/trustlines' &&
      url.searchParams.get('scenario') === 'happy' &&
      url.searchParams.get('equivalent') === 'UAH' &&
      url.searchParams.get('threshold') === '0.42'
    )
  })
})

test('rapid participant and trustline filters commit only the latest visible result', async ({ page }) => {
  await page.goto('/participants?scenario=slow')
  const participantsTable = page.getByTestId('participants-table')
  await expect(participantsTable).toBeVisible()

  const participantSearch = page.getByRole('textbox', { name: 'Search PID / name' })
  await participantSearch.fill('GreenField')
  await expect(page).toHaveURL((url) => url.searchParams.get('q') === 'GreenField')
  await expect(page.locator('.el-skeleton')).toBeVisible()
  await participantSearch.fill('Hilltop')

  await expect(page).toHaveURL((url) => {
    return url.pathname === '/participants' && url.searchParams.get('scenario') === 'slow' && url.searchParams.get('q') === 'Hilltop'
  })
  await expect(participantsTable).toContainText('Hilltop Greenhouses')
  await expect(participantsTable).not.toContainText('GreenField Cooperative')

  await page.goto('/trustlines?scenario=slow')
  const trustlinesTable = page.locator('.el-table').first()
  await expect(trustlinesTable).toBeVisible()

  const equivalentFilter = page.getByRole('textbox', { name: 'Equivalent (e.g. UAH)' })
  await equivalentFilter.fill('UAH')
  await expect(page).toHaveURL((url) => url.searchParams.get('equivalent') === 'UAH')
  await expect(page.locator('.el-skeleton')).toBeVisible()
  await equivalentFilter.fill('HOUR')

  await expect(page).toHaveURL((url) => {
    return url.pathname === '/trustlines' && url.searchParams.get('scenario') === 'slow' && url.searchParams.get('equivalent') === 'HOUR'
  })
  await expect(trustlinesTable.locator('.el-table__body-wrapper tbody tr').first()).toContainText('HOUR')
  await expect(trustlinesTable).not.toContainText('UAH')
})
