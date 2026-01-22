import { expect, test } from '@playwright/test'

async function waitReady(page: any) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  await page.waitForTimeout(50)
}

test.describe('Simulator demo scenes (fixtures, test-mode)', () => {
  test('Scene A — Overview', async ({ page }) => {
    await page.goto('/?scene=A')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-A.png', { animations: 'disabled' })
  })

  test('Scene B — Focus', async ({ page }) => {
    await page.goto('/?scene=B')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-B.png', { animations: 'disabled' })
  })

  test('Scene C — Statuses', async ({ page }) => {
    await page.goto('/?scene=C')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-C.png', { animations: 'disabled' })
  })

  test('Scene D — Tx burst (single tx)', async ({ page }) => {
    await page.goto('/?scene=D')
    await waitReady(page)

    await page.getByRole('button', { name: 'Single Tx' }).click()
    await page.waitForTimeout(50)

    await expect(page).toHaveScreenshot('scene-D-tx.png', { animations: 'disabled' })
  })

  test('Scene E — Clearing (first deterministic step)', async ({ page }) => {
    await page.goto('/?scene=E')
    await waitReady(page)

    await page.getByRole('button', { name: 'Run Clearing' }).click()
    await page.waitForTimeout(50)

    await expect(page).toHaveScreenshot('scene-E-clearing.png', { animations: 'disabled' })
  })
})
