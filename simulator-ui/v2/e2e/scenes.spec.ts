import { expect, test, type Page } from '@playwright/test'

/** Wait until the app signals data-ready and give the canvas time to render + generate glow sprites. */
async function waitReady(page: Page) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  // Allow the render loop to complete at least one full frame (sprite generation can take >50ms).
  await page.waitForTimeout(400)
}

/** Tolerance for sub-pixel anti-aliasing / sprite caching differences between runs. */
const SCREENSHOT_OPTS = { animations: 'disabled' as const, maxDiffPixelRatio: 0.01 }

// 048 S1 (decision 4, 2026-10-18): the A/B/C "scene" and `?scene=` are gone - they had no reader (the offline snapshot
// is loaded by the equivalent alone), so every address below showed the same snapshot before as well. The addresses
// lost `?scene=`; the test titles and the snapshot file names are kept, the pictures did not change.
test.describe('Simulator demo scenes (fixtures, test-mode)', () => {
  test('Scene A — Overview', async ({ page }) => {
    await page.goto('/')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-A.png', SCREENSHOT_OPTS)
  })

  test('Scene B — Focus', async ({ page }) => {
    // Ensure focus is actually visible (node glow + incident edges + node card)
    await page.goto('/?focus=PID_U0002_3c6ef362')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-B.png', SCREENSHOT_OPTS)
  })

  test('Scene C — Statuses', async ({ page }) => {
    await page.goto('/')
    await waitReady(page)
    await expect(page).toHaveScreenshot('scene-C.png', SCREENSHOT_OPTS)
  })

  // 034 S8 (F-034-D5): the two cases below are RENDER checks of the active-edge / active-node highlight on the
  // base canvas, driven by the e2e-only stubs `e2eTxOnce` / `e2eClearingOnce` (`useSimulatorApp.ts`) behind the
  // e2e-only buttons. They used to be named "Tx burst" and "Clearing", which they are not.
  // What they do NOT check: a payment, its route, a clearing, any FX (sparks, pulses, bursts), any change of debts
  // or balances - no event playlist is read. `?scene=D` and `?scene=E` were never scenes either (the loader accepted
  // A, B, C until 048 S1 removed the parameter); both showed the same snapshot. Movement is NOT checked anywhere yet: the 038 stand (`e2e/qa/`, slice S0) only
  // characterizes that a payment's spark exists and is visible and reproduces the straightened beam (R3); the
  // motion, its direction and the edges off the route are to be measured by its slice S1, and of a clearing it
  // measures only the fade of the amount label.
  // The snapshot file names are kept (`scene-D-tx.png`, `scene-E-clearing.png`): the pictures did not change.
  test('Render of the active-edge highlight (e2e stub: first link and its two nodes; not a payment)', async ({ page }) => {
    await page.goto('/')
    await waitReady(page)

    await page.getByRole('button', { name: 'Single Tx' }).click()
    // Wait for active-edge/node overlay to render (glow sprite + composite).
    await page.waitForTimeout(300)

    await expect(page).toHaveScreenshot('scene-D-tx.png', SCREENSHOT_OPTS)
  })

  test('Render of the active-node highlight (e2e stub: first four nodes; not a clearing)', async ({ page }) => {
    await page.goto('/')
    await waitReady(page)

    await page.getByRole('button', { name: 'Run Clearing' }).click()
    // Wait for active-node overlay to render (clearing glow sprites).
    await page.waitForTimeout(300)

    await expect(page).toHaveScreenshot('scene-E-clearing.png', SCREENSHOT_OPTS)
  })
})
