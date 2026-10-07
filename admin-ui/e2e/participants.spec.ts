import { expect, test } from '@playwright/test'

import { seededParticipantNames } from './backend'

// THE BLOCKING SMOKE (`npm run test:e2e:smoke`, CI job `ui-smoke`). It asserts a row of the SEED, not
// "a row or the empty state": with the backend down or the database empty there is no such row and
// the smoke is red (032 S4 anti-vacuum; the control run is recorded in the 032 Changelog).
test('participants page loads and shows table', async ({ page }) => {
  const seeded = seededParticipantNames()

  await page.goto('/participants')

  await expect(page.getByRole('main').getByText('Participants', { exact: true })).toBeVisible()

  const table = page.getByTestId('participants-table')
  await expect(table).toBeVisible()

  const firstRow = table.locator('.el-table__body-wrapper tbody tr').first()
  await expect(firstRow).toBeVisible()
  const name = (await firstRow.locator('td').nth(1).innerText()).trim()
  expect(seeded.has(name), `first row shows ${JSON.stringify(name)}, which is not a participant of the seed`).toBe(true)
})
