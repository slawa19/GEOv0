/**
 * Programme 045, slice S2 - the structure of the three action panels (spec 045, decisions 2-3; Verification plan 1 (г)).
 *
 * Measured in the DOM at 1366x768 (the top bar at 1280x720), in both product themes (`saas`, `hud`), on the mocked backend
 * of the 037 specs (`helpers/p037Mock.ts`):
 *
 *   (г1) the title of each action panel carries no "(ESC to close)"; Escape still closes the window;
 *   (г2) the payment window offers Confirm on the participant-choice step already, `disabled`, and says why (`title`);
 *   (г3) the confirm step of the payment shows at most two information rows (until S2: the summary, the direct capacity, the
 *        shortest path, the estimate, the route note, the 1-hop hint and the reason - each a row of its own);
 *   (г4) Cancel and the primary action share one row, Cancel first ([Cancel][Primary]) - payment, trust line, clearing;
 *   (г5) the network name of the first top bar stays on one line with a long scenario name, the whole name in `title`.
 *
 * An "information row" is a child of the panel body that is on screen, holds text, and is neither a row of controls
 * (`.ds-controls__row`), nor the row of buttons, nor the visually hidden live region (`.mp-sr`). Sizes are read from
 * `getBoundingClientRect`, i.e. what is on screen. What this does NOT see: the frozen themes, phone viewports (037 B1
 * holds those), the result steps, the wording of any line (the mount tests hold those by `data-testid`).
 */
import { expect, test, type Page } from '@playwright/test'

import { mockApp, ready, settleShell } from './helpers/p037Mock.js'

const THEMES = ['saas', 'hud'] as const
type Theme = (typeof THEMES)[number]

const LONG_NAME = 'Greenfield village of one hundred households, realistic economy v2'

async function open(page: Page, theme: Theme, viewport = { width: 1366, height: 768 }): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  await page.setViewportSize(viewport)
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
  await ready(page, true)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
}

async function openPayment(page: Page): Promise<void> {
  await page.locator('[data-testid="actionbar-payment"]').click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
  await expect(page.locator('#mp-from__trigger')).toBeVisible()
  await settleShell(page)
}

async function toConfirmStep(page: Page): Promise<void> {
  await page.locator('#mp-from__trigger').click()
  await page.locator('#mp-from__surface [role="option"][data-option-value="alice"]').click()
  await page.locator('#mp-to__surface [role="option"][data-option-value="bob"]').click()
  await expect(page.locator('#mp-amount')).toBeVisible()
  await settleShell(page)
}

/** The information rows of a panel body (see the header). */
async function infoRows(page: Page, panelTestId: string): Promise<string[]> {
  return await page.locator(`[data-testid="${panelTestId}"] > .ds-panel__body`).evaluate((body) =>
    Array.from(body.children)
      .filter((el) => {
        const r = el.getBoundingClientRect()
        if (r.width === 0 || r.height === 0) return false
        if (el.matches('.ds-controls__row, .ds-row--actions, .mp-sr')) return false
        return (el.textContent ?? '').trim() !== ''
      })
      .map((el) => (el.getAttribute('data-testid') ?? el.className) + ': ' + (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 60)))
}

/** Cancel and the primary button: the same row, Cancel on the left. */
async function cancelThenPrimary(page: Page, cancelCss: string, primaryCss: string) {
  const box = async (css: string) => (await page.locator(css).boundingBox())!
  const c = await box(cancelCss)
  const p = await box(primaryCss)
  return { sameRow: Math.abs(c.y - p.y) < 1, cancelFirst: c.x + c.width <= p.x + 0.5, c, p }
}

for (const theme of THEMES) {
  test.describe(`045 S2 [${theme}] action panels at 1366x768`, () => {
    test('(г1)(г2) payment, choice step: no "ESC to close", Confirm is there and disabled with its reason', async ({ page }) => {
      await open(page, theme)
      await openPayment(page)
      const panel = page.locator('[data-testid="manual-payment-panel"]')
      await expect(panel).not.toContainText('ESC to close')
      const confirm = page.locator('[data-testid="manual-payment-confirm"]')
      await expect(confirm, 'Confirm is offered on the choice step').toBeVisible()
      await expect(confirm).toBeDisabled()
      expect((await confirm.getAttribute('title')) ?? '', 'a disabled Confirm says why').not.toBe('')
      const row = await cancelThenPrimary(page, '[data-testid="manual-payment-cancel"]', '[data-testid="manual-payment-confirm"]')
      expect(row.sameRow && row.cancelFirst, `[Cancel][Confirm] ${JSON.stringify(row)}`).toBe(true)
      await page.keyboard.press('Escape')
      await expect(panel, 'Escape closes the payment window').toBeHidden()
    })

    test('(г3) payment, confirm step: at most two information rows', async ({ page }) => {
      await open(page, theme)
      await openPayment(page)
      await toConfirmStep(page)
      const rows = await infoRows(page, 'manual-payment-panel')
      console.log(`P045S2 (г3) ${theme} ${JSON.stringify(rows)}`)
      expect(rows.length, `information rows: ${JSON.stringify(rows)}`).toBeLessThanOrEqual(2)
      await page.locator('#mp-amount').fill('1.00')
      await expect(page.locator('[data-testid="manual-payment-confirm"]')).toBeEnabled()
      const row = await cancelThenPrimary(page, '[data-testid="manual-payment-cancel"]', '[data-testid="manual-payment-confirm"]')
      expect(row.sameRow && row.cancelFirst, `[Cancel][Confirm] ${JSON.stringify(row)}`).toBe(true)
    })

    test('(г1)(г4) trust line: no "ESC to close", [Cancel][Create], Escape closes', async ({ page }) => {
      await open(page, theme)
      await page.locator('[data-testid="actionbar-trustline"]').click()
      const panel = page.locator('[data-testid="trustline-panel"]')
      await expect(panel).toBeVisible()
      await expect(panel).not.toContainText('ESC to close')
      const create = panel.getByRole('button', { name: 'Create' })
      await expect(create, 'Create is offered from the first step').toBeVisible()
      await expect(create).toBeDisabled()
      expect((await create.getAttribute('title')) ?? '', 'a disabled Create says why').not.toBe('')
      const row = await cancelThenPrimary(page, '[data-testid="trustline-cancel"]', '[data-testid="trustline-create"]')
      expect(row.sameRow && row.cancelFirst, `[Cancel][Create] ${JSON.stringify(row)}`).toBe(true)
      await page.keyboard.press('Escape')
      await expect(panel, 'Escape closes the trust-line window').toBeHidden()
    })

    test('(г1)(г4) clearing: no "ESC to close", [Cancel][Confirm], Escape closes', async ({ page }) => {
      await open(page, theme)
      await page.locator('[data-testid="actionbar-clearing"]').click()
      const panel = page.locator('[data-testid="clearing-panel"]')
      await expect(panel).toBeVisible()
      await expect(panel).not.toContainText('ESC to close')
      const row = await cancelThenPrimary(page, '[data-testid="clearing-cancel"]', '[data-testid="clearing-confirm"]')
      expect(row.sameRow && row.cancelFirst, `[Cancel][Confirm] ${JSON.stringify(row)}`).toBe(true)
      await page.keyboard.press('Escape')
      await expect(panel, 'Escape closes the clearing window').toBeHidden()
    })

    test('keyboard: Tab order of each panel (record) and Enter in the amount confirms', async ({ page }) => {
      const bodies: Array<Record<string, unknown>> = []
      await mockApp(page, { paymentRealBodies: bodies })
      await page.setViewportSize({ width: 1366, height: 768 })
      await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
      await ready(page, true)
      const order = async (panelTestId: string, n: number) => {
        const seen: string[] = []
        for (let i = 0; i < n; i += 1) {
          await page.keyboard.press('Tab')
          seen.push(await page.evaluate((id) => {
            const a = document.activeElement as HTMLElement | null
            if (!a) return 'none'
            const inside = !!a.closest(`[data-testid="${id}"]`)
            return `${inside ? '' : 'OUT:'}${a.id || a.getAttribute('data-testid') || (a.textContent ?? '').trim().slice(0, 16)}`
          }, panelTestId))
        }
        return seen
      }
      await openPayment(page)
      await toConfirmStep(page)
      await page.locator('#mp-amount').focus()
      const payment = await order('manual-payment-panel', 3)
      // Escape in a field is not a dismiss (useWindowController `isFormLikeTarget`); from a button the interact window goes one
      // step back per press, then closes (`back-then-close`, useWindowManager `handleEsc`). Counted, not assumed.
      const stepOf = async () => await page.evaluate(() => {
        const panel = document.querySelector('[data-testid="manual-payment-panel"]')
        if (!panel) return 'closed'
        return `amount:${document.querySelectorAll('#mp-amount').length}|from:${(document.getElementById('mp-from') as HTMLSelectElement | null)?.value ?? ''}|to:${(document.getElementById('mp-to') as HTMLSelectElement | null)?.value ?? ''}`
      })
      let escapes = 0
      const steps: string[] = [await stepOf()]
      while (escapes < 4 && steps[steps.length - 1] !== 'closed') {
        await page.locator('[data-testid="manual-payment-cancel"]').focus()
        await page.keyboard.press('Escape')
        escapes += 1
        const before = steps[steps.length - 1]
        await expect.poll(stepOf, `Escape #${escapes} changes the step`).not.toBe(before)
        steps.push(await stepOf())
      }
      await expect(page.locator('[data-testid="manual-payment-panel"]'), 'Escape closes the window').toBeHidden()
      const back = `${escapes} presses ${steps.join(' > ')}; focus on ${await page.evaluate(() => (document.activeElement as HTMLElement | null)?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none')}`
      await page.locator('[data-testid="actionbar-trustline"]').click()
      await expect(page.locator('[data-testid="trustline-panel"]')).toBeVisible()
      await page.locator('#tl-from__trigger').focus()
      const trustline = await order('trustline-panel', 3)
      console.log(`P045S2 tab ${theme} ${JSON.stringify({ payment, focusAfterEscape: back, trustline })}`)
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-testid="trustline-panel"]')).toBeHidden()

      await openPayment(page)
      await toConfirmStep(page)
      await page.locator('#mp-amount').fill('1.00')
      await page.locator('#mp-amount').press('Enter')
      await expect.poll(() => bodies.length, 'Enter in the amount sends the payment').toBe(1)
    })

    test('record: heights of the three windows (numbers only)', async ({ page }, info) => {
      await open(page, theme)
      const h = async (id: string) => await page.locator(`[data-testid="${id}"]`).evaluate((el) => {
        const r = (el.closest('.ws-shell') ?? el).getBoundingClientRect()
        return `${Math.round(r.width * 10) / 10}x${Math.round(r.height * 10) / 10}`
      })
      const rec: Record<string, string> = {}
      await openPayment(page)
      rec.paymentChoice = await h('manual-payment-panel')
      await toConfirmStep(page)
      rec.paymentConfirm = await h('manual-payment-panel')
      await page.locator('#mp-amount').fill('1.00')
      await settleShell(page)
      rec.paymentConfirmFilled = await h('manual-payment-panel')
      await page.locator('[data-testid="manual-payment-cancel"]').click()
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()
      await page.locator('[data-testid="actionbar-trustline"]').click()
      await settleShell(page, 'trustline-panel')
      rec.trustline = await h('trustline-panel')
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-testid="trustline-panel"]')).toBeHidden()
      await page.locator('[data-testid="actionbar-clearing"]').click()
      await settleShell(page, 'clearing-panel')
      rec.clearing = await h('clearing-panel')
      console.log(`P045S2 record ${theme} ${JSON.stringify(rec)}`)
      info.annotations.push({ type: 'P045S2-record', description: JSON.stringify(rec) })
    })
  })

  test(`(г5) [${theme}] the network name stays on one line at 1280x720 with a long scenario name; the whole name is its title`, async ({ page }) => {
    await mockApp(page, { paymentRealBodies: [] })
    // Registered after `mockApp`: Playwright asks the newest route first.
    await page.route(/\/simulator\/scenarios$/i, (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ api_version: 'simulator-api/1', items: [{ api_version: 'simulator-api/1',
        scenario_id: 'greenfield-village-100-realistic-v2', name: LONG_NAME, participants_count: 3, trustlines_count: 1, equivalents: ['UAH'] }] }),
    }))
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
    await ready(page, true)
    const name = page.locator('[data-testid="network-name"]')
    await expect(name).toContainText(LONG_NAME.slice(0, 10))
    const m = await page.evaluate(() => {
      const box = (css: string) => { const r = document.querySelector(css)!.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left), right: Math.round(r.right) } }
      const value = document.querySelector('[data-testid="network-name"] .ds-value') as HTMLElement
      return {
        name: box('[data-testid="network-name"]'), state: box('[data-testid="network-state"]'), flow: box('[data-testid="flow-toggle"]'),
        actions: box('[data-testid="actionbar-payment"]'),
        cut: value.scrollWidth > value.clientWidth, ellipsis: getComputedStyle(value).textOverflow, title: value.getAttribute('title') ?? '',
      }
    })
    console.log(`P045S2 (г5) ${theme} ${JSON.stringify(m)}`)
    // Before S2 the identity cluster wrapped: the state and the flow button went under the name (top 60 against 16).
    expect(Math.abs(m.state.top - m.name.top), `the state is on the line of the name ${JSON.stringify(m)}`).toBeLessThan(1)
    expect(Math.abs(m.flow.top - m.name.top) <= Math.abs(m.flow.bottom - m.flow.top), `the flow button too ${JSON.stringify(m)}`).toBe(true)
    expect(m.flow.right, 'the cluster ends before the actions').toBeLessThanOrEqual(m.actions.left)
    expect(m.cut && m.ellipsis === 'ellipsis', 'the long name is cut with an ellipsis').toBe(true)
    expect(m.title, 'the whole name is the title').toBe(LONG_NAME)
  })
}
