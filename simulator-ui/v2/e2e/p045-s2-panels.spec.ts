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
      // Advisor 2026-10-17: a disabled primary must READ as disabled (until then no `.ds-btn:disabled` rule existed).
      const opacity = await confirm.evaluate((el) => Number.parseFloat(getComputedStyle(el).opacity))
      expect(opacity, 'a disabled Confirm is dimmed').toBeLessThanOrEqual(0.6)
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
      // Decision 1, meta 10.5: where a figure comes from (`--ds-typo-section-label-font-size`, 0.7rem on the 15 px root).
      const meta = await page.locator('[data-testid="mp-figures-source"]').evaluate((el) => Number.parseFloat(getComputedStyle(el).fontSize))
      expect(meta, 'the source of a figure is meta text, 10.5 px').toBe(10.5)
      await page.locator('#mp-amount').fill('1.00')
      await expect(page.locator('[data-testid="manual-payment-confirm"]')).toBeEnabled()
      // Counter-check of the dimming: an enabled Confirm is not dimmed.
      expect(await page.locator('[data-testid="manual-payment-confirm"]').evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
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

    // Codex §15 P3 (2026-10-17): the exact Escape sequence, not "closed after 1-4 presses". Escape is pressed with the focus on
    // the window's Cancel (a button: Escape in a field is not a dismiss, useWindowController `isFormLikeTarget`); the interact
    // window goes one step back per press, then closes (`back-then-close`, useWindowManager `handleEsc`).
    test('keyboard: Escape walks the payment back step by step, then closes; the focus returns to the action bar', async ({ page }) => {
      await open(page, theme)
      await openPayment(page)
      await toConfirmStep(page)
      const step = async () => await page.evaluate(() => {
        if (!document.querySelector('[data-testid="manual-payment-panel"]')) return 'closed'
        const value = (id: string) => (document.getElementById(id) as HTMLSelectElement | null)?.value ?? ''
        const amount = document.getElementById('mp-amount') ? 'amount' : 'no-amount'
        return `from=${value('mp-from')} to=${value('mp-to')} ${amount}`
      })
      const press = async () => {
        await page.locator('[data-testid="manual-payment-cancel"]').focus()
        await page.keyboard.press('Escape')
      }
      await expect.poll(step).toBe('from=alice to=bob amount')
      await press()
      await expect.poll(step, 'Escape 1: back to the recipient step').toBe('from=alice to= no-amount')
      await press()
      await expect.poll(step, 'Escape 2: back to the sender step').toBe('from= to= no-amount')
      await press()
      await expect.poll(step, 'Escape 3: closed').toBe('closed')
      await expect(page.locator('[data-testid="actionbar-payment"]'), 'the focus returns to the action that opened it').toBeFocused()
    })

    test('keyboard: Escape walks the trust-line creation back step by step, then closes', async ({ page }) => {
      await open(page, theme)
      await page.locator('[data-testid="actionbar-trustline"]').click()
      await page.locator('#tl-from__trigger').click()
      await page.locator('#tl-from__surface [role="option"][data-option-value="alice"]').click()
      await page.locator('#tl-to__trigger').click()
      await page.locator('#tl-to__surface [role="option"][data-option-value="carol"]').click()
      const step = async () => await page.evaluate(() => {
        if (!document.querySelector('[data-testid="trustline-panel"]')) return 'closed'
        const has = (id: string) => (document.getElementById(id) ? id : '-')
        return `${has('tl-from__trigger')} ${has('tl-to__trigger')} ${has('tl-limit')}`
      })
      const press = async () => {
        await page.locator('[data-testid="trustline-cancel"]').focus()
        await page.keyboard.press('Escape')
      }
      await expect.poll(step, 'the create step').toBe('- tl-to__trigger tl-limit')
      await press()
      await expect.poll(step, 'Escape 1: back to the "To" step').toBe('- tl-to__trigger -')
      await press()
      await expect.poll(step, 'Escape 2: back to the "From" step').toBe('tl-from__trigger - -')
      await press()
      await expect.poll(step, 'Escape 3: closed').toBe('closed')
      await expect(page.locator('[data-testid="actionbar-trustline"]')).toBeFocused()
    })

    test('keyboard: clearing opens with the focus on Confirm (Enter confirms) and closes on ONE Escape', async ({ page }) => {
      await open(page, theme)
      await page.locator('[data-testid="actionbar-clearing"]').focus()
      await page.keyboard.press('Enter')
      await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
      await expect(page.locator('[data-testid="clearing-confirm"]'), 'the first focus is Confirm (`data-initial-focus`)').toBeFocused()
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-testid="clearing-panel"]'), 'one Escape closes the clearing window').toBeHidden()
      await expect(page.locator('[data-testid="actionbar-clearing"]')).toBeFocused()
    })

    test('keyboard: Tab order of the payment confirm step; Enter in the amount sends', async ({ page }) => {
      const bodies: Array<Record<string, unknown>> = []
      await mockApp(page, { paymentRealBodies: bodies })
      await page.setViewportSize({ width: 1366, height: 768 })
      await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
      await ready(page, true)
      await openPayment(page)
      await toConfirmStep(page)
      await page.locator('#mp-amount').fill('1.00')
      const seen: string[] = []
      for (let i = 0; i < 4; i += 1) {
        await page.keyboard.press('Tab')
        seen.push(await page.evaluate(() => { const a = document.activeElement as HTMLElement | null; return a?.id || a?.getAttribute('data-testid') || 'none' }))
      }
      // [Cancel][Confirm] in the DOM as on screen; the window keeps the focus inside (wraps to "From").
      expect(seen).toEqual(['manual-payment-cancel', 'manual-payment-confirm', 'mp-from__trigger', 'mp-to__trigger'])
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

  // Codex §15 P2 (2026-10-17): the cut name has a reveal a keyboard reaches - "Подробнее" shows the running network's name in
  // full, as text - and the first view gains no focus stop for it.
  test(`(г5b) [${theme}] behind "Подробнее" the whole network name is shown as text`, async ({ page }) => {
    await mockApp(page, { paymentRealBodies: [] })
    await page.route(/\/simulator\/scenarios$/i, (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ api_version: 'simulator-api/1', items: [{ api_version: 'simulator-api/1',
        scenario_id: 'greenfield-village-100-realistic-v2', name: LONG_NAME, participants_count: 3, trustlines_count: 1, equivalents: ['UAH'] }] }),
    }))
    await page.setViewportSize({ width: 1280, height: 720 })
    await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}`)
    await ready(page, true)
    const full = page.locator('[data-testid="details-network-name"]')
    await expect(full, 'not on the first view').toHaveCount(0)
    await page.locator('[data-testid="details-toggle"]').focus()
    await page.keyboard.press('Enter')
    await expect(full).toBeVisible()
    await expect(full).toContainText(LONG_NAME)
    const cut = await full.locator('.ds-value').evaluate((el) => el.scrollWidth > el.clientWidth + 0.5 || getComputedStyle(el).textOverflow === 'ellipsis')
    expect(cut, 'shown whole, not cut').toBe(false)
  })
}
