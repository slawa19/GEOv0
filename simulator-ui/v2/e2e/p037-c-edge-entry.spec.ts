/**
 * Programme 037, PR C - the payment panel opened FROM A LINE (edge popup -> "Pay") fits the screen on 390x844, 375x667, 360x640 and
 * 844x390: the panel opens straight on the confirm step (summary, captions, the sentence about the direction, the amount), the
 * tallest first view of the flow, and the one the container specs of B1/B2 did not cover (they reach the confirm step by choosing).
 *
 * Entered through the DOM graph navigator ("Inspect graph" -> "Inspect edge"), because a click on a line of a phone-sized canvas
 * lands on the action buttons. "Fits" is the B1 rule (`helpers/p037Fit.ts`): the shell wholly in the viewport; the amount and
 * Confirm/Cancel whole and uncovered; measured before the next click, after the window manager settled.
 *
 * The window shell was seen at a NEGATIVE y here (-111 on 375x667, -138 on 360x640). CAUSE (measured; not the window manager and not
 * the panel): the app root (`.root`, overflow hidden, 824px tall on a 667px screen) is scrolled by the BROWSER when the navigator button
 * at its bottom takes the focus (scrollTop 125 / 152), and the whole window layer moves with it - already at the line popup. A person
 * who clicks a line on the canvas does not scroll the root, so the entry resets it (`scrollTop = 0`) and records what it was. Each
 * viewport is entered several times in one test and every entry has to fit.
 */
import { expect, test, type Page } from '@playwright/test'

import { measure, problems } from './helpers/p037Fit.js'
import { mockApp, ready } from './helpers/p037Mock.js'

const VIEWPORTS = [{ w: 390, h: 844 }, { w: 375, h: 667 }, { w: 360, h: 640 }, { w: 844, h: 390 }]
const ENTRIES = 4

async function viaLine(page: Page) {
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)
  const nav = page.getByRole('region', { name: 'Graph navigator' })
  await nav.getByText('Inspect graph', { exact: true }).tap()
  await nav.getByRole('button', { name: 'Inspect edge' }).tap()
  await expect(page.locator('[data-testid="edge-send-payment"]')).toBeVisible()
  // The navigator is an entry for this test only (see the header): undo the scroll its focus caused in the overflow:hidden app root.
  return await page.evaluate(() => { const root = document.querySelector('.root') as HTMLElement; const was = root.scrollTop; root.scrollTop = 0; return was })
}

for (const vp of VIEWPORTS) {
  test.describe(`037 C - panel opened from a line ${vp.w}x${vp.h}`, () => {
    test.use({ viewport: { width: vp.w, height: vp.h }, hasTouch: true, isMobile: false })

    test(`the line popup and the confirm step opened from it fit, ${ENTRIES} entries in a row; the amount is where the focus is`, async ({ page }, testInfo) => {
      await mockApp(page, { paymentRealBodies: [] })
      const entries: Array<{ popup: string[]; confirm: string[]; shell: unknown; popupShell: unknown; focus: string; rootScrolledBy: number; rootScrolledAfter: number }> = []

      for (let i = 0; i < ENTRIES; i += 1) {
        const rootScrolledBy = await viaLine(page)
        const popup = await measure(page, 'edge-popup', ['[data-testid="edge-send-payment"]'], 'edge-detail-popup')
        await page.locator('[data-testid="edge-send-payment"]').tap()
        await expect(page.locator('#mp-amount')).toBeVisible()
        const confirm = await measure(page, 'edge-confirm', ['#mp-amount', '[data-testid="manual-payment-confirm"]', '[data-testid="manual-payment-cancel"]'])
        const focus = await page.evaluate(() => (document.activeElement as HTMLElement | null)?.id ?? '')
        // Opening the panel (and focusing its amount) must not scroll the app root again: that is what carried the window off the top.
        const rootScrolledAfter = await page.evaluate(() => (document.querySelector('.root') as HTMLElement).scrollTop)
        entries.push({ popup: problems(popup), confirm: problems(confirm), shell: confirm.shell, popupShell: popup.shell, focus, rootScrolledBy, rootScrolledAfter })
      }

      console.log(`P037 C EDGE-ENTRY ${JSON.stringify({ viewport: vp, entries })}`)
      testInfo.annotations.push({ type: 'P037-C-EDGE', description: JSON.stringify(entries.map((e) => ({ popup: e.popup, confirm: e.confirm, focus: e.focus }))) })

      entries.forEach((e, i) => {
        expect.soft(e.popup, `entry ${i + 1}: the line popup in ${vp.w}x${vp.h}`).toEqual([])
        expect.soft(e.confirm, `entry ${i + 1}: the confirm step opened from the line in ${vp.w}x${vp.h}`).toEqual([])
        expect.soft(e.focus, `entry ${i + 1}: the focus is on the (empty) amount, so it is in view`).toBe('mp-amount')
        expect.soft(e.rootScrolledAfter, `entry ${i + 1}: opening the panel did not scroll the app root`).toBe(0)
      })
    })
  })
}
