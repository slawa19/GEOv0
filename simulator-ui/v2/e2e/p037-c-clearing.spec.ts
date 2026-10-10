/**
 * Programme 037, PR C - the result of a clearing on the existing panel, in a real browser: it stays until "Close", names who
 * repaid whom in the right direction, is honest about "nothing cleared" and a refusal, and FITS 390x844, 375x667, 360x640 and
 * 844x390 (the same rule as the payment panel, `helpers/p037Fit.ts`).
 *
 * What this does NOT see: the canvas effect of a clearing (the neighbour's `realFx`); a real backend; a physical phone.
 */
import { expect, test, type Page } from '@playwright/test'

import { measure, problems } from './helpers/p037Fit.js'
import { mockApp, ready } from './helpers/p037Mock.js'

type MockOpts = Parameters<typeof mockApp>[1]

async function open(page: Page, o: Partial<MockOpts> = {}) {
  await mockApp(page, { paymentRealBodies: [], ...o })
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)
}

/** Clearing as the FIRST action of the session: no payment panel was ever opened, so nothing asked for the participants before. */
async function runClearing(page: Page) {
  await page.locator('[data-testid="actionbar-clearing"]').click()
  await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
  await page.locator('[data-testid="clearing-confirm"]').click()
}

const lines = (page: Page) => page.locator('[data-testid="clearing-edge-line"]').allTextContents().then((l) => l.map((t) => t.replace(/\s+/g, ' ').trim()))

test.describe('037 C - the clearing result in a real browser', () => {
  test('the result names who repaid whom (creditor -> debtor, never a payment), as the FIRST action, and stays after the old timers have long passed', async ({ page }) => {
    await open(page)
    await runClearing(page)

    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)
    expect(await lines(page)).toEqual([
      'Bob’s debt to Alice reduced by 7.00 UAH',
      'Carol’s debt to Bob reduced by 7.00 UAH',
      'Alice’s debt to Carol reduced by 7.00 UAH',
      'Carol’s debt to Alice reduced by 3.00 UAH',
      'Alice’s debt to Carol reduced by 3.00 UAH',
    ])
    await expect(page.locator('[data-testid="clearing-total"]')).toContainText('10.00 UAH')

    // The old code left the panel after about one second (800 + 200 ms of dwell). A real browser has to wait that long to see it.
    await page.waitForTimeout(2500)
    await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)
  })

  test('only "Close" removes it; the next clearing replaces it (and the panel is clean again until the answer comes)', async ({ page }) => {
    const requests = { n: 0 }
    await open(page, { clearingRequests: requests })
    await runClearing(page)
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)

    await page.locator('[data-testid="clearing-close"]').click()
    await expect(page.locator('[data-testid="clearing-panel"]')).toBeHidden()

    await runClearing(page)
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)
    expect(requests.n).toBe(2)
  })

  test('nothing to clear is said as such, not shown as a success', async ({ page }) => {
    await open(page, { clearing: 'none' })
    await runClearing(page)

    await expect(page.locator('[data-testid="clearing-nothing"]')).toHaveText('Nothing was cleared: no cycle of debts was found.')
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(0)
    await expect(page.getByLabel('Success notification')).toHaveCount(0)
    await expect(page.locator('[data-testid="clearing-close"]')).toBeEnabled()
  })

  test('a refusal keeps the panel with its message and a working Close - not a spinner for ever', async ({ page }) => {
    await open(page, { clearing: 'refused' })
    await runClearing(page)

    await expect(page.locator('[data-testid="clearing-error"]')).toBeVisible()
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(0)
    await page.locator('[data-testid="clearing-close"]').click()
    await expect(page.locator('[data-testid="clearing-panel"]')).toBeHidden()
  })
})

test.describe('037 C - what a held result survives', () => {
  test('a refusal outlives the error toast: when the toast is gone the explanation and a working Close are still on the panel', async ({ page }) => {
    await open(page, { clearing: 'refused' })
    await runClearing(page)
    await expect(page.locator('[data-testid="clearing-error"]')).toBeVisible()
    const message = (await page.locator('[data-testid="clearing-error"]').textContent())!.trim()

    // The REAL toast: it shows the same sentence for a few seconds and then the application clears the error.
    await expect(page.getByLabel('Error notification')).toBeVisible()
    await expect(page.getByLabel('Error notification')).toBeHidden({ timeout: 15_000 })

    await expect(page.locator('[data-testid="clearing-error"]')).toHaveText(message)
    await expect(page.locator('[data-testid="clearing-running"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="clearing-close"]')).toBeEnabled()
    await page.locator('[data-testid="clearing-close"]').click()
    await expect(page.locator('[data-testid="clearing-panel"]')).toBeHidden()
  })

  test('a click on the empty canvas does not remove a finished clearing result', async ({ page }) => {
    await open(page)
    await runClearing(page)
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)

    await page.mouse.click(900, 560) // empty canvas on this fixture (the nodes are elsewhere)
    await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)
    await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
  })

  test('...and not a committed PAYMENT result either', async ({ page }) => {
    await open(page)
    await page.locator('[data-testid="actionbar-payment"]').click()
    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="alice"]').click()
    await page.locator('#mp-to__surface [data-option-value="bob"]').click()
    await page.keyboard.type('1.00')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="mp-result"]')).toBeVisible()

    await page.mouse.click(900, 560)
    await expect(page.locator('[data-testid="mp-result"]')).toBeVisible()
  })
})

const VIEWPORTS = [{ w: 390, h: 844 }, { w: 375, h: 667 }, { w: 360, h: 640 }, { w: 844, h: 390 }]

for (const vp of VIEWPORTS) {
  test.describe(`037 C - clearing panel container ${vp.w}x${vp.h}`, () => {
    test.use({ viewport: { width: vp.w, height: vp.h }, hasTouch: true, isMobile: false })

    test('the confirm step and a long result fit; a long result scrolls inside the window with Close in view', async ({ page }, testInfo) => {
      await open(page, { extraTargets: 5, clearing: 'many' })
      await page.locator('[data-testid="actionbar-clearing"]').tap()
      await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
      const confirm = await measure(page, 'confirm', ['[data-testid="clearing-confirm"]', '[data-testid="clearing-cancel"]'], 'clearing-panel')

      await page.locator('[data-testid="clearing-confirm"]').tap()
      await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(3)
      await expect(page.getByLabel('Success notification')).toBeHidden({ timeout: 15_000 })
      const long = await measure(page, 'long-result', ['[data-testid="clearing-close"]'], 'clearing-panel')

      // The last line of the last cycle, with the body scrolled to its end, lies above Close (not under it) and is not covered.
      const tail = await page.evaluate(() => {
        const panel = document.querySelector('[data-testid="clearing-panel"]')!
        const body = panel.querySelector<HTMLElement>(':scope > .ds-panel__body')!
        body.scrollTop = body.scrollHeight
        const all = Array.from(panel.querySelectorAll<HTMLElement>('[data-testid="clearing-edge-line"]'))
        const last = all[all.length - 1]!
        const close = panel.querySelector<HTMLElement>('[data-testid="clearing-close"]')!
        const lr = last.getBoundingClientRect()
        const cr = close.getBoundingClientRect()
        const mid = document.elementFromPoint(lr.left + lr.width / 2, lr.top + lr.height / 2)
        return { lines: all.length, text: last.textContent?.trim() ?? '', above: lr.bottom <= cr.top + 0.5, covered: !(mid && (mid === last || last.contains(mid))),
          scrolls: body.scrollHeight > body.clientHeight + 1 }
      })
      await page.evaluate(() => { document.querySelector<HTMLElement>('[data-testid="clearing-panel"] > .ds-panel__body')!.scrollTop = 0 })

      const report = { viewport: vp, confirm: { shell: confirm.shell, problems: problems(confirm) }, long: { shell: long.shell, body: long.body, problems: problems(long) }, tail }
      console.log(`P037 C CLEARING ${JSON.stringify(report)}`)
      testInfo.annotations.push({ type: 'P037-C', description: JSON.stringify({ confirm: problems(confirm), long: problems(long), tail }) })

      expect.soft(problems(confirm), `clearing confirm in ${vp.w}x${vp.h}`).toEqual([])
      expect.soft(problems(long), `long clearing result in ${vp.w}x${vp.h}`).toEqual([])
      expect.soft(tail.lines, 'fixture: three cycles of five edges').toBe(15)
      expect.soft(tail.above, `the last line (${tail.text}) lies above Close`).toBe(true)
      expect.soft(tail.covered, 'nothing covers the last line').toBe(false)
      if (vp.h <= 667) expect.soft(tail.scrolls, `on ${vp.w}x${vp.h} the long result must scroll inside the window`).toBe(true)
    })

    test('a short result fits', async ({ page }) => {
      await open(page)
      await page.locator('[data-testid="actionbar-clearing"]').tap()
      await page.locator('[data-testid="clearing-confirm"]').tap()
      await expect(page.locator('[data-testid="clearing-cycle"]')).toHaveCount(2)
      await expect(page.getByLabel('Success notification')).toBeHidden({ timeout: 15_000 })
      const result = await measure(page, 'result', ['[data-testid="clearing-close"]'], 'clearing-panel')
      expect.soft(problems(result), `clearing result in ${vp.w}x${vp.h}`).toEqual([])
    })
  })
}
