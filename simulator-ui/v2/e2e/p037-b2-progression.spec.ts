/**
 * Programme 037, PR B2 - the progression of the manual payment panel in a REAL browser (focus is a browser matter):
 * From -> To -> Amount by mouse and by keyboard, the Escape chain through the window manager, the panel started from an edge
 * (reversed direction), and an unresolved payment restored after a reload.
 *
 * The rule under test (decision 037-B, Q1): the recipient list opens by itself ONLY as the predictable continuation of an
 * explicit choice of the sender; choosing the recipient may focus the amount and NEVER sends. The component-level guards
 * (what must NOT open the list) are in `src/components/ManualPaymentPanel.progression.test.ts`.
 *
 * What this does NOT see: a screen reader (the announcement is a live region; only its text is checked), a real phone.
 */
import { expect, test, type Page } from '@playwright/test'

import { PAYMENT_ID, mockApp, ready } from './helpers/p037Mock.js'

const focusIn = (page: Page, css: string) => page.evaluate((css) => {
  const root = document.querySelector(css)
  return !!root && root.contains(document.activeElement)
}, css)

const activeId = (page: Page) => page.evaluate(() => (document.activeElement as HTMLElement | null)?.id ?? '')

async function openPanel(page: Page, bodies: Array<Record<string, unknown>>, opts: Parameters<typeof mockApp>[1] extends infer O ? Partial<O> : never = {}) {
  await mockApp(page, { paymentRealBodies: bodies, ...opts })
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)
  await page.locator('[data-testid="actionbar-payment"]').click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
}

test.describe('037 B2 - progression in a real browser', () => {
  test('MOUSE: the sender chosen -> the recipient list opens with the focus inside it; the recipient chosen -> the focus is on the amount and nothing is sent', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies)

    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="alice"]').click()

    await expect(page.locator('#mp-to__surface')).toBeVisible()
    expect(await focusIn(page, '#mp-to__surface'), 'the focus moved into the recipient list').toBe(true)
    await expect(page.locator('[data-testid="mp-next-choice"]')).toHaveText('Choose the recipient.')

    await page.locator('#mp-to__surface [data-option-value="bob"]').click()
    await expect(page.locator('#mp-amount')).toBeVisible()
    expect(await activeId(page), 'the amount has the focus').toBe('mp-amount')
    await expect(page.locator('[data-testid="mp-next-choice"]')).toHaveText('')
    expect(bodies, 'choosing the recipient sent nothing').toHaveLength(0)

    await page.keyboard.type('1.00')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="mp-result-payment-id"]')).toHaveText(PAYMENT_ID)
    expect(bodies).toHaveLength(1)
  })

  test('KEYBOARD: Enter / arrows / Enter / arrows / Enter, type, Enter - one payment; Tab and Shift+Tab stay inside the open list', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies)

    // The window puts the focus on its first control (the sender list).
    expect(await activeId(page)).toBe('mp-from__trigger')
    await page.keyboard.press('Enter')
    await expect(page.locator('#mp-from__surface')).toBeVisible()
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')

    await expect(page.locator('#mp-to__surface')).toBeVisible()
    expect(await focusIn(page, '#mp-to__surface')).toBe(true)

    // Tab / Shift+Tab: the focus does not leave the open list.
    const options = await page.locator('#mp-to__surface [role="option"]').count()
    for (let i = 0; i < options + 1; i += 1) {
      await page.keyboard.press('Tab')
      expect(await focusIn(page, '#mp-to__surface'), `Tab #${i + 1}`).toBe(true)
    }
    for (let i = 0; i < options + 1; i += 1) {
      await page.keyboard.press('Shift+Tab')
      expect(await focusIn(page, '#mp-to__surface'), `Shift+Tab #${i + 1}`).toBe(true)
    }

    // ArrowDown to Bob (the entries are: the empty one, Bob), Enter.
    await page.locator('#mp-to__surface [role="option"]').first().focus()
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('Enter')
    await expect(page.locator('#mp-amount')).toBeVisible()
    expect(await activeId(page)).toBe('mp-amount')
    expect(bodies, 'the Enter that chose the recipient did not send').toHaveLength(0)

    await page.keyboard.type('1.00')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="mp-result-payment-id"]')).toHaveText(PAYMENT_ID)
    expect(bodies).toHaveLength(1)
  })

  test('ESCAPE: the first closes the recipient list and nothing else; the next one is the window\'s own step back', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies)
    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="alice"]').click()
    await expect(page.locator('#mp-to__surface')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(page.locator('#mp-to__surface')).toBeHidden()
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
    await expect(page.locator('#mp-from__trigger'), 'the sender is still chosen after the first Escape').toContainText('Alice')
    expect(await activeId(page), 'the focus is back on the recipient trigger').toBe('mp-to__trigger')

    await page.keyboard.press('Escape')
    await expect(page.locator('#mp-from__trigger'), 'the second Escape steps back: the sender is released').not.toContainText('Alice')
    await expect(page.locator('#mp-to__surface')).toBeHidden()
    expect(bodies).toHaveLength(0)
  })

  test('A PANEL STARTED FROM AN EDGE runs the other way, opens no list, and its summary names who pays whom', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await mockApp(page, { paymentRealBodies: bodies })
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await ready(page, true)
    await page.waitForTimeout(800) // the canvas lays the three nodes out

    // The line is bob -> alice (creditor -> debtor); a point on it, measured on this 3-node fixture at 1280x720.
    await page.mouse.click(371, 416)
    const send = page.locator('[data-testid="edge-send-payment"]')
    await expect(send).toBeVisible()
    await send.click()

    await expect(page.locator('#mp-amount')).toBeVisible()
    await expect(page.locator('[data-testid="mp-summary-parties"]')).toHaveText('Alice pays Bob')
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toContainText('Manual payment: Alice → Bob')
    await expect(page.locator('#mp-from__surface')).toHaveCount(0)
    await expect(page.locator('#mp-to__surface')).toHaveCount(0)
    expect(bodies).toHaveLength(0)
  })

  test('AN UNRESOLVED PAYMENT restored after a reload opens no list', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies, { paymentRealNetworkFailures: { left: 1 } })
    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="alice"]').click()
    await page.locator('#mp-to__surface [data-option-value="bob"]').click()
    await page.keyboard.type('1.00')
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-testid="mp-outcome-unknown"]')).toBeVisible()

    await page.reload()
    await ready(page, true)
    await page.locator('[data-testid="actionbar-payment"]').click()
    await expect(page.locator('[data-testid="mp-outcome-unknown"]')).toBeVisible()
    await expect(page.locator('#mp-from__surface')).toHaveCount(0)
    await expect(page.locator('#mp-to__surface')).toHaveCount(0)
  })

  // A key HELD down sends keydown with repeat:true again and again. Playwright's keyboard.down() on a key that is already down does
  // exactly that (the browser's own auto-repeat, without keyup). The gesture that CHOSE something must never reach a send.
  const twoFrames = (page: Page) => page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))))
  async function holdEnter(page: Page, times: number) {
    for (let i = 0; i < times; i += 1) {
      await page.keyboard.down('Enter')
      await twoFrames(page)
    }
  }

  test('HELD ENTER: re-choosing the recipient at the confirm step (the amount holds a sum) and holding Enter sends nothing', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies, { extraTargets: 2 })
    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="alice"]').click()
    await page.locator('#mp-to__surface [data-option-value="bob"]').click()
    await page.locator('#mp-amount').fill('1.00')

    // Reopen the recipient list and choose a recipient with Enter, keeping the key down.
    await page.locator('#mp-to__trigger').click()
    await expect(page.locator('#mp-to__surface')).toBeVisible()
    await page.locator('#mp-to__surface [data-option-value="x01"]').focus()
    await holdEnter(page, 6)
    await page.keyboard.up('Enter')
    await twoFrames(page)

    expect(bodies.length, 'requests sent to payment-real by a held Enter').toBe(0)
    await expect(page.locator('[data-testid="mp-result"]')).toHaveCount(0)
  })

  test('HELD ENTER: through the first pass (the sender chosen, then the recipient, the amount still empty) sends nothing', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies)
    await page.locator('#mp-from__trigger').focus()
    await page.keyboard.press('Enter')
    await page.keyboard.press('ArrowDown')
    await page.keyboard.down('Enter') // chooses the sender and keeps the key down
    await twoFrames(page)
    await holdEnter(page, 4)
    await page.keyboard.up('Enter')
    expect(bodies.length).toBe(0)

    // Recipient by keyboard, Enter held after choosing it: the amount is empty, nothing is sent.
    if (!(await page.locator('#mp-to__surface').isVisible())) { await page.locator('#mp-to__trigger').focus(); await page.keyboard.press('Enter') }
    await page.keyboard.press('ArrowDown')
    await page.keyboard.down('Enter')
    await twoFrames(page)
    await holdEnter(page, 4)
    await page.keyboard.up('Enter')
    await twoFrames(page)
    expect(bodies.length, 'requests sent to payment-real by a held Enter').toBe(0)
  })

  test('CORRECTION: changing the sender at the confirm step opens no recipient list', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await openPanel(page, bodies, { bobCanPay: true })
    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="alice"]').click()
    await page.locator('#mp-to__surface [data-option-value="bob"]').click()
    await page.locator('#mp-amount').fill('1.00')

    await page.locator('#mp-from__trigger').click()
    await page.locator('#mp-from__surface [data-option-value="bob"]').click()
    await twoFrames(page)
    await expect(page.locator('#mp-to__surface')).toHaveCount(0)
    expect(await activeId(page), 'the focus stays on the sender trigger').toBe('mp-from__trigger')
    expect(bodies).toHaveLength(0)
  })
})
