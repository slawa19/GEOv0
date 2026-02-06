import { expect, test } from '@playwright/test'

/** Wait until the app signals data-ready and give the canvas time to render + generate glow sprites. */
async function waitReady(page: any) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  // Allow the render loop to complete at least one full frame (sprite generation can take >50ms).
  await page.waitForTimeout(400)
}

/** Tolerance for sub-pixel anti-aliasing / sprite caching differences between runs. */
const SCREENSHOT_OPTS = { animations: 'disabled' as const, maxDiffPixelRatio: 0.01 }

test.describe('Simulator demo scenes (fixtures, test-mode)', () => {
  test('Scene A — Overview', async ({ page }) => {
    await page.goto('/?scene=A')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-A.png', SCREENSHOT_OPTS)
  })

  test('Scene B — Focus', async ({ page }) => {
    // Ensure focus is actually visible (node glow + incident edges + node card)
    await page.goto('/?scene=B&focus=PID_U0002_3c6ef362')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-B.png', SCREENSHOT_OPTS)
  })

  test('Scene C — Statuses', async ({ page }) => {
    await page.goto('/?scene=C')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-C.png', SCREENSHOT_OPTS)
  })

  test('Scene D — Tx burst (single tx)', async ({ page }) => {
    await page.goto('/?scene=D')
    await waitReady(page)

    await page.getByRole('button', { name: 'Single Tx' }).click()
    // Wait for active-edge/node overlay to render (glow sprite + composite).
    await page.waitForTimeout(300)

    await expect(page).toHaveScreenshot('scene-D-tx.png', SCREENSHOT_OPTS)
  })

  test('Scene E — Clearing (first deterministic step)', async ({ page }) => {
    await page.goto('/?scene=E')
    await waitReady(page)

    await page.getByRole('button', { name: 'Run Clearing' }).click()
    // Wait for active-node overlay to render (clearing glow sprites).
    await page.waitForTimeout(300)

    await expect(page).toHaveScreenshot('scene-E-clearing.png', SCREENSHOT_OPTS)
  })
})
