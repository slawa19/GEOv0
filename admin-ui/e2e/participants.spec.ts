import { expect, test } from '@playwright/test'

test('participants page loads and shows table', async ({ page }) => {
  await page.goto('/participants')

  await expect(page.getByText('Participants', { exact: true })).toBeVisible()

  const table = page.locator('.el-table')
  await expect(table).toBeVisible()

  // Wait for at least one row (fixtures) OR an explicit empty-state.
  const firstRow = page.locator('.el-table__body-wrapper tbody tr').first()
  const emptyState = page.getByText('No participants', { exact: true })

  await expect(firstRow.or(emptyState)).toBeVisible()
})

test('participants: can freeze/unfreeze and filter by status', async ({ page }) => {
  await page.goto('/participants')

  const table = page.getByTestId('participants-table')
  await expect(table).toBeVisible()

  // Pick a row where actions are available (has a Freeze button).
  const freezeBtn = page.getByTestId('participants-freeze-btn').first()
  await expect(freezeBtn).toBeVisible()
  const row = freezeBtn.locator('xpath=ancestor::tr[1]')
  const pid = (await row.locator('td').first().innerText()).trim()
  expect(pid.length).toBeGreaterThan(0)

  // Freeze.
  await freezeBtn.click()
  await page.locator('.el-message-box__input input').fill('e2e freeze')
  await page.getByRole('button', { name: 'Confirm' }).click()

  // Table should now offer unfreeze for the same PID.
  const rowForPid = table.locator('.el-table__body-wrapper tbody tr', { hasText: pid }).first()
  const unfreezeBtn = rowForPid.getByTestId('participants-unfreeze-btn')
  await expect(unfreezeBtn).toBeVisible()

  // Filter by suspended status (freeze).
  const statusSelect = page.getByTestId('participants-filter-status')
  await statusSelect.click()
  await page.getByRole('option', { name: 'suspended', exact: true }).click()

  // Expect our PID still visible and status suspended (scope to the table body).
  await expect(rowForPid).toBeVisible()
  await expect(rowForPid).toContainText('suspended')

  // Clear filter back to Any status so the row remains visible after unfreeze.
  await statusSelect.click()
  await page.getByRole('option', { name: 'Any status', exact: true }).click()

  // Unfreeze again to keep fixtures stable for future runs.
  await expect(unfreezeBtn).toBeVisible()
  await unfreezeBtn.click()
  await page.locator('.el-message-box__input input').fill('e2e unfreeze')
  await page.getByRole('button', { name: 'Confirm' }).click()

  const rowAfter = table.locator('.el-table__body-wrapper tbody tr', { hasText: pid }).first()
  await expect(rowAfter.getByTestId('participants-freeze-btn')).toBeVisible()
})
