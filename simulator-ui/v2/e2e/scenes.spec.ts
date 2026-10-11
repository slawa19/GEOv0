import { expect, test, type Page } from '@playwright/test'

/** Wait until the app signals data-ready and give the canvas time to render + generate glow sprites. */
async function waitReady(page: Page) {
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

  // 034 S8 (F-034-D5): the two cases below are RENDER checks of the active-edge / active-node highlight on the
  // base canvas, driven by the e2e-only stubs `e2eTxOnce` / `e2eClearingOnce` (`useSimulatorApp.ts`) behind the
  // e2e-only buttons. They used to be named "Tx burst" and "Clearing", which they are not.
  // What they do NOT check: a payment, its route, a clearing, any FX (sparks, pulses, bursts), any change of debts
  // or balances - no event playlist is read. `?scene=D` and `?scene=E` are not scenes: the loader accepts A, B, C
  // only, so both show scene A. Movement of a payment and of a clearing is checked by the 038 stand (`e2e/qa/`).
  // The snapshot file names are kept (`scene-D-tx.png`, `scene-E-clearing.png`): the pictures did not change.
  test('Render of the active-edge highlight (e2e stub: first link and its two nodes; not a payment)', async ({ page }) => {
    await page.goto('/?scene=D')
    await waitReady(page)

    await page.getByRole('button', { name: 'Single Tx' }).click()
    // Wait for active-edge/node overlay to render (glow sprite + composite).
    await page.waitForTimeout(300)

    await expect(page).toHaveScreenshot('scene-D-tx.png', SCREENSHOT_OPTS)
  })

  test('Render of the active-node highlight (e2e stub: first four nodes; not a clearing)', async ({ page }) => {
    await page.goto('/?scene=E')
    await waitReady(page)

    await page.getByRole('button', { name: 'Run Clearing' }).click()
    // Wait for active-node overlay to render (clearing glow sprites).
    await page.waitForTimeout(300)

    await expect(page).toHaveScreenshot('scene-E-clearing.png', SCREENSHOT_OPTS)
  })
})
