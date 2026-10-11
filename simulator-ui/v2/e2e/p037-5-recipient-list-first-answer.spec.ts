/**
 * BACKLOG 037-5 (c) in a REAL browser (focus is a browser matter): the recipient list between the choice of the sender and the
 * server's FIRST answer for him. The list opens with the focus inside it, nothing in it can be taken, and the keyboard keeps
 * working - the focus is never on an option that is, or becomes, disabled - and the answer makes the recipient choosable in place.
 *
 * The routes answer is HELD by the test and released by it: no timer decides the order.
 *
 * What this does NOT see: an update of the routes while the list is open (a stream of run events) - no run event is played here;
 * that the list stays choosable then is the component test "under a stream of run events" in `ManualPaymentPanel.test.ts`. And an
 * option the fresh answer REMOVES while it holds the focus is not covered anywhere.
 */
import { expect, test, type Page } from '@playwright/test'

import { RUN_ID, mockApp, ready } from './helpers/p037Mock.js'

const focusState = (page: Page) => page.evaluate(() => {
  const active = document.activeElement as HTMLElement | null
  const surface = document.querySelector('#mp-to__surface')
  return {
    inList: !!surface && !!active && surface.contains(active),
    option: active?.getAttribute('data-option-value') ?? null,
    disabled: active instanceof HTMLButtonElement ? active.disabled : null,
  }
})

test('037-5 (c): before the first routes answer the open list holds the focus and the keyboard; the answer makes the recipient choosable', async ({ page }) => {
  const bodies: Array<Record<string, unknown>> = []
  await mockApp(page, { paymentRealBodies: bodies })

  let release!: () => void
  const held = new Promise<void>((resolve) => { release = resolve })
  let asked = 0
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/payment-targets`, 'i'), async (route) => {
    asked += 1
    await held
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{ to_pid: 'bob', hops: 1 }] }) })
  })

  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)
  await page.locator('[data-testid="actionbar-payment"]').click()
  await page.locator('#mp-from__trigger').click()
  await page.locator('#mp-from__surface [data-option-value="alice"]').click()

  const bob = page.locator('#mp-to__surface [data-option-value="bob"]')
  await expect(page.locator('#mp-to__surface')).toBeVisible()
  await expect(bob).toBeDisabled()
  await expect.poll(() => asked, 'the routes were asked for and not answered').toBeGreaterThan(0)
  await expect(page.locator('[data-testid="manual-payment-to-help"]')).toContainText('a recipient can be chosen once the server answers')

  // the focus is inside the list, on something that can hold it; the arrows do not throw it out
  let focus = await focusState(page)
  expect(focus, 'focus after the list opened').toMatchObject({ inList: true, disabled: false })
  await page.keyboard.press('ArrowDown')
  focus = await focusState(page)
  expect(focus, 'focus after ArrowDown with nothing to choose').toMatchObject({ inList: true, disabled: false })
  // Tab / Shift+Tab stay in the list although it has ONE entry that can hold the focus: that entry is then also the last
  // focusable of the window, and the window's own Tab containment must not take over what the list has already handled.
  await page.keyboard.press('Tab')
  expect(await focusState(page), 'focus after Tab with nothing to choose').toMatchObject({ inList: true, disabled: false })
  await page.keyboard.press('Shift+Tab')
  expect(await focusState(page), 'focus after Shift+Tab with nothing to choose').toMatchObject({ inList: true, disabled: false })

  // Escape still closes the list and returns the focus to its trigger
  await page.keyboard.press('Escape')
  await expect(page.locator('#mp-to__surface')).toBeHidden()
  await expect.poll(() => page.evaluate(() => document.activeElement?.id)).toBe('mp-to__trigger')

  // open again by keyboard, then the server answers while the list is open
  await page.keyboard.press('Enter')
  await expect(page.locator('#mp-to__surface')).toBeVisible()
  await expect(bob).toBeDisabled()
  release()
  await expect(bob).toBeEnabled()
  focus = await focusState(page)
  expect(focus, 'focus after the answer landed').toMatchObject({ inList: true, disabled: false })

  await page.keyboard.press('ArrowDown')
  await expect.poll(async () => (await focusState(page)).option).toBe('bob')
  await page.keyboard.press('Enter')
  await expect(page.locator('#mp-amount')).toBeFocused()
  expect(bodies, 'choosing the recipient sent nothing').toHaveLength(0)
})
