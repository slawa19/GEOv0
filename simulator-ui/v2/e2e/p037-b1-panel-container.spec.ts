/**
 * Programme 037, PR B1 - the manual payment panel FITS the screen on every step, on four phone-sized viewports:
 * 390x844, 375x667, 360x640 (portrait) and 844x390 (landscape).
 *
 * "Fits" is one rule, all of it (decision 037-B, 2026-10-10):
 *   1. the window shell lies wholly inside the viewport;
 *   2. the document has no horizontal overflow;
 *   3. each primary control of the step (Confirm / Cancel / Close / Retry / Discard ..., and the active input) is seen
 *      WHOLE - inside the viewport and inside the shell - and nothing covers it: all four corners and the centre hit the
 *      control itself, not a neighbour or a clipping ancestor (a centre-only hit test lets a half-clipped button pass);
 *   4. a long list scrolls INSIDE its own surface and keeps inside the viewport; the document does not scroll;
 *   5. a recipient list longer than the screen is reachable by keyboard (ArrowDown) without clipping.
 *
 * Every figure is measured in the page BEFORE the next Playwright action: a click scrolls its target into view by
 * itself, which would hide exactly the clipping this spec is after.
 *
 * What this does NOT see: a real phone (browser chrome, on-screen keyboard - Playwright cannot raise it); real-backend
 * latency; more than 31 recipients; the HUD theme only (the default of the app).
 */
import { expect, test, type Page } from '@playwright/test'

import { measure, problems, type StepMeasure } from './helpers/p037Fit.js'
import { Counter, PAYMENT_ID, mockApp, ready } from './helpers/p037Mock.js'

/** The open dropdown surface: inside the viewport, scrolls on its own, every option reachable. */
async function measureSurface(page: Page, id: string, step: string) {
  return await page.evaluate(({ id, step }) => {
    const W = window.innerWidth
    const H = window.innerHeight
    const s = document.getElementById(`${id}__surface`)
    if (!s) return { step, found: false as const }
    const r = s.getBoundingClientRect()
    const options = Array.from(s.querySelectorAll<HTMLElement>('[role="option"]'))
    return {
      step, found: true as const,
      rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      insideViewport: r.left >= -0.5 && r.top >= -0.5 && r.right <= W + 0.5 && r.bottom <= H + 0.5,
      options: options.length,
      scrollable: s.scrollHeight > s.clientHeight + 1,
      // an option is reachable when the surface can scroll to it: either it is wholly in the visible box already, or the surface scrolls
      allReachable: s.scrollHeight > s.clientHeight + 1 || options.every((o) => { const b = o.getBoundingClientRect(); return b.top >= r.top - 0.5 && b.bottom <= r.bottom + 0.5 }),
      pageScrollTop: document.scrollingElement?.scrollTop ?? 0,
    }
  }, { id, step })
}

const VIEWPORTS = [
  { w: 390, h: 844 },
  { w: 375, h: 667 },
  { w: 360, h: 640 },
  { w: 844, h: 390 },
]

for (const vp of VIEWPORTS) {
  test.describe(`037 B1 - payment panel container ${vp.w}x${vp.h}`, () => {
    test.use({ viewport: { width: vp.w, height: vp.h }, hasTouch: true, isMobile: false })

    test('selection, confirm and result steps fit; a long recipient list scrolls inside its surface and is reachable by keyboard', async ({ page }, testInfo) => {
      await mockApp(page, { paymentRealBodies: [], extraTargets: 30 })
      await page.goto('/?mode=real&ui=interact&e2eReal=1')
      await ready(page, true)

      const c = new Counter(page, true)
      const steps: StepMeasure[] = []
      const surfaces: Array<Awaited<ReturnType<typeof measureSurface>>> = []

      await c.press('open payment', '[data-testid="actionbar-payment"]')
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
      steps.push(await measure(page, 'pick-from', ['#mp-from__trigger', '[data-testid="manual-payment-cancel"]']))

      await c.press('From: open list', '#mp-from__trigger')
      await expect(page.locator('#mp-from__surface')).toBeVisible()
      surfaces.push(await measureSurface(page, 'mp-from', 'pick-from list'))
      await c.press('From: choose', '#mp-from__surface [role="option"][data-option-value="alice"]')

      // 037 B2: the recipient list opens by itself after the sender is chosen - the long list (31 recipients) is measured as it
      // opens (touch); it is then closed to measure the step with its controls uncovered, and walked with the keyboard.
      await expect(page.locator('#mp-to__surface')).toBeVisible()
      surfaces.push(await measureSurface(page, 'mp-to', 'pick-to list (opened by itself)'))
      await page.locator('#mp-to__trigger').tap()
      await expect(page.locator('#mp-to__surface')).toBeHidden()
      await expect.poll(async () => (await page.locator('#mp-to__trigger').isEnabled())).toBe(true)
      steps.push(await measure(page, 'pick-to', ['#mp-from__trigger', '#mp-to__trigger', '[data-testid="manual-payment-cancel"]']))
      await page.locator('#mp-to__trigger').focus()
      await page.keyboard.press('Enter')
      await expect(page.locator('#mp-to__surface')).toBeVisible()
      const list = await page.evaluate(() => {
        const opts = Array.from(document.querySelectorAll<HTMLElement>('#mp-to__surface [role="option"]'))
        const a = document.activeElement as HTMLElement | null
        return { total: opts.length, recipients: opts.filter((o) => (o.getAttribute('data-option-value') ?? '') !== '').length,
          activeIndex: a ? opts.indexOf(a) : -1, lastValue: opts[opts.length - 1]?.getAttribute('data-option-value') ?? null }
      })
      // The fixture must really be long: 31 recipients (bob + 30), not a list that shrank to fit.
      expect(list.recipients, 'fixture: the recipient list under test has 31 recipients').toBe(31)
      expect(list.activeIndex, 'the keyboard walk starts inside the list').toBeGreaterThanOrEqual(0)
      for (let i = 0; i < list.total - 1 - list.activeIndex; i += 1) await page.keyboard.press('ArrowDown')
      const keyboard = await page.evaluate((lastValue) => {
        const s = document.getElementById('mp-to__surface')!
        const a = document.activeElement as HTMLElement | null
        const sr = s.getBoundingClientRect()
        const ar = a?.getBoundingClientRect()
        return {
          activeIsOption: !!a && a.getAttribute('role') === 'option' && s.contains(a),
          activeInsideSurface: !!ar && ar.top >= sr.top - 0.5 && ar.bottom <= sr.bottom + 0.5,
          activeInsideViewport: !!ar && ar.top >= 0 && ar.bottom <= window.innerHeight,
          activeLabel: a?.textContent?.trim() ?? null,
          activeIsLast: !!a && a.getAttribute('data-option-value') === lastValue,
          pageScrollTop: document.scrollingElement?.scrollTop ?? 0,
        }
      }, list.lastValue)
      await page.keyboard.press('Escape')
      await expect(page.locator('#mp-to__surface')).toBeHidden()

      await c.press('To: open list', '#mp-to__trigger')
      await c.press('To: choose', '#mp-to__surface [role="option"][data-option-value="bob"]')

      await expect(page.locator('#mp-amount')).toBeVisible()
      steps.push(await measure(page, 'confirm', ['#mp-amount', '[data-testid="manual-payment-confirm"]', '[data-testid="manual-payment-cancel"]']))
      await page.locator('#mp-amount').fill('1.00')
      await expect(page.locator('[data-testid="manual-payment-confirm"]')).toBeEnabled()
      steps.push(await measure(page, 'confirm-with-amount', ['#mp-amount', '[data-testid="manual-payment-confirm"]', '[data-testid="manual-payment-cancel"]']))

      await c.press('Confirm', '[data-testid="manual-payment-confirm"]')
      await expect(page.locator('[data-testid="mp-result"]')).toBeVisible()
      await expect(page.locator('[data-testid="mp-result-payment-id"]')).toHaveText(PAYMENT_ID)
      // The application's own success toast covers a corner of a phone screen for a few seconds; the container is not its owner.
      await expect(page.getByLabel('Success notification')).toBeHidden({ timeout: 15_000 })
      steps.push(await measure(page, 'result', ['[data-testid="mp-result-another"]', '[data-testid="mp-result-close"]']))

      const report = { viewport: vp, steps: steps.map((s) => ({ step: s.step, shell: s.shell, body: s.body, problems: problems(s) })), surfaces, keyboard }
      console.log(`P037 B1 CONTAINER ${JSON.stringify(report)}`)
      testInfo.annotations.push({ type: 'P037-B1', description: JSON.stringify(report.steps.map((s) => ({ step: s.step, problems: s.problems }))) })

      for (const s of steps) expect.soft(problems(s), `step ${s.step} in ${vp.w}x${vp.h}`).toEqual([])
      for (const s of surfaces) {
        if (!s.found) { expect.soft(s.found, `${s.step}: the list surface is open`).toBe(true); continue }
        expect.soft(s.insideViewport, `${s.step}: the surface lies wholly inside the viewport ${JSON.stringify(s.rect)}`).toBe(true)
        expect.soft(s.allReachable, `${s.step}: every option is reachable (visible or the surface scrolls)`).toBe(true)
        expect.soft(s.pageScrollTop, `${s.step}: the page did not scroll`).toBe(0)
      }
      expect.soft(keyboard.activeIsOption, 'keyboard: the active element after ArrowDown is a list option').toBe(true)
      expect.soft(keyboard.activeIsLast, `keyboard: ArrowDown reached the LAST recipient (${keyboard.activeLabel})`).toBe(true)
      expect.soft(keyboard.activeInsideSurface, `keyboard: the active option (${keyboard.activeLabel}) is whole inside the surface`).toBe(true)
      expect.soft(keyboard.activeInsideViewport, 'keyboard: the active option is inside the viewport').toBe(true)
      expect.soft(keyboard.pageScrollTop, 'keyboard: the page did not scroll').toBe(0)
    })

    test('a result longer than the screen scrolls INSIDE the window; its buttons stay whole in view', async ({ page }, testInfo) => {
      await mockApp(page, { paymentRealBodies: [], extraTargets: 5, longRoutes: true })
      await page.goto('/?mode=real&ui=interact&e2eReal=1')
      await ready(page, true)

      const c = new Counter(page, true)
      await c.press('open payment', '[data-testid="actionbar-payment"]')
      await c.pick('From', 'mp-from', 'alice')
      await c.chooseOpen('To', 'mp-to', 'bob')
      await page.locator('#mp-amount').fill('1.00')
      await c.press('Confirm', '[data-testid="manual-payment-confirm"]')
      await expect(page.locator('[data-testid="mp-result-route-3"]')).toBeVisible()
      await expect(page.getByLabel('Success notification')).toBeHidden({ timeout: 15_000 })

      // Scroll the body to its end and look at the LAST step of the LAST route: it must lie wholly above the sticky button row
      // (not under it) and wholly inside the visible body. Measured before any click.
      const tail = await page.evaluate(() => {
        const body = document.querySelector<HTMLElement>('[data-testid="manual-payment-panel"] > .ds-panel__body')!
        body.scrollTop = body.scrollHeight
        const hops = Array.from(document.querySelectorAll<HTMLElement>('[data-testid="mp-result-hop"]'))
        const last = hops[hops.length - 1]
        const row = document.querySelector<HTMLElement>('[data-testid="mp-result"] > .ds-row--actions')!
        const lr = last.getBoundingClientRect()
        const rr = row.getBoundingClientRect()
        const br = body.getBoundingClientRect()
        const mid = document.elementFromPoint(lr.left + lr.width / 2, lr.top + lr.height / 2)
        return { hops: hops.length, lastText: last.textContent?.trim() ?? '', lastBottom: Math.round(lr.bottom), rowTop: Math.round(rr.top),
          lastAboveRow: lr.bottom <= rr.top + 0.5, lastInsideBody: lr.top >= br.top - 0.5 && lr.bottom <= br.bottom + 0.5,
          lastReceivesPointer: !!mid && (mid === last || last.contains(mid)) }
      })
      console.log(`P037 B1 CONTAINER-LONG-RESULT-TAIL ${JSON.stringify({ viewport: vp, tail })}`)
      expect.soft(tail.hops, 'fixture: three routes of four steps').toBe(12)
      expect.soft(tail.lastAboveRow, `the last step of the last route (${tail.lastText}) lies above the sticky row: bottom ${tail.lastBottom}, row top ${tail.rowTop}`).toBe(true)
      expect.soft(tail.lastInsideBody, 'the last step is whole inside the visible body').toBe(true)
      expect.soft(tail.lastReceivesPointer, 'nothing covers the last step').toBe(true)
      await page.evaluate(() => { document.querySelector<HTMLElement>('[data-testid="manual-payment-panel"] > .ds-panel__body')!.scrollTop = 0 })

      const m = await measure(page, 'long-result', ['[data-testid="mp-result-another"]', '[data-testid="mp-result-close"]'])
      const scrolls = !!m.body && m.body.scrollHeight > m.body.clientHeight + 1
      console.log(`P037 B1 CONTAINER-LONG-RESULT ${JSON.stringify({ viewport: vp, shell: m.shell, body: m.body, scrolls, problems: problems(m) })}`)
      testInfo.annotations.push({ type: 'P037-B1-LONG-RESULT', description: JSON.stringify({ body: m.body, problems: problems(m) }) })

      expect.soft(problems(m), `long result in ${vp.w}x${vp.h}`).toEqual([])
      // Not vacuous: on a short screen this result really is longer than the window, so the inner scroll is what keeps the buttons reachable.
      if (vp.h <= 667) expect.soft(scrolls, `on ${vp.w}x${vp.h} the long result must scroll inside the window`).toBe(true)
    })

    test('each panel re-shapes only ITS OWN window: the payment rules do not reach the clearing window, the trustline window keeps the manager layout', async ({ page }) => {
      await mockApp(page, { paymentRealBodies: [] })
      await page.goto('/?mode=real&ui=interact&e2eReal=1')
      await ready(page, true)

      const shellStyle = (testid: string) => page.evaluate((testid) => {
        const el = document.querySelector(`[data-testid="${testid}"]`)
        const shell = el?.closest('.ws-shell') as HTMLElement | null
        if (!shell) return null
        const cs = getComputedStyle(shell)
        return { display: cs.display, dropdownMax: cs.getPropertyValue('--ds-ov-dropdown-maxh').trim(), bodyDisplay: getComputedStyle(shell.querySelector(':scope > .ws-body')!).display }
      }, testid)

      // Positive control: in this very viewport the payment window IS re-shaped (otherwise "unchanged" below proves nothing).
      await page.locator('[data-testid="actionbar-payment"]').tap()
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
      expect(await shellStyle('manual-payment-panel'), 'control: the payment window is re-shaped here').toMatchObject({ display: 'flex', dropdownMax: '240px', bodyDisplay: 'flex' })
      await page.locator('[data-testid="manual-payment-cancel"]').tap()
      await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeHidden()

      await page.locator('[data-testid="actionbar-clearing"]').tap()
      await expect(page.locator('[data-testid="clearing-panel"]')).toBeVisible()
      // 037 C: the clearing panel has its OWN override (its own selector, its own file); what it must NOT have is the payment panel's
      // (the dropdown cap): the payment selector does not match this window.
      expect(await shellStyle('clearing-panel'), 'the clearing window: its own re-shape, not the payment one').toEqual({ display: 'flex', dropdownMax: '', bodyDisplay: 'flex' })
      await page.keyboard.press('Escape')
      await expect(page.locator('[data-testid="clearing-panel"]')).toBeHidden()

      await page.locator('[data-testid="actionbar-trustline"]').tap()
      await expect(page.locator('[data-testid="trustline-panel"]')).toBeVisible()
      expect(await shellStyle('trustline-panel'), 'the trustline window').toEqual({ display: 'block', dropdownMax: '', bodyDisplay: 'block' })
    })

    test('an unresolved payment (no answer) and its two-step discard fit', async ({ page }, testInfo) => {
      await mockApp(page, { paymentRealBodies: [], paymentRealNetworkFailures: { left: 1 } })
      await page.goto('/?mode=real&ui=interact&e2eReal=1')
      await ready(page, true)

      const c = new Counter(page, true)
      await c.press('open payment', '[data-testid="actionbar-payment"]')
      await c.pick('From', 'mp-from', 'alice')
      await c.chooseOpen('To', 'mp-to', 'bob')
      await page.locator('#mp-amount').fill('1.00')
      await c.press('Confirm', '[data-testid="manual-payment-confirm"]')
      await expect(page.locator('[data-testid="mp-outcome-unknown"]')).toBeVisible()
      await expect(page.getByLabel('Error notification')).toBeHidden({ timeout: 15_000 })

      const banner = await measure(page, 'unresolved', ['[data-testid="mp-retry"]', '[data-testid="mp-discard"]', '[data-testid="manual-payment-cancel"]'])
      await c.press('Discard', '[data-testid="mp-discard"]')
      await expect(page.locator('[data-testid="mp-discard-warning"]')).toBeVisible()
      const discard = await measure(page, 'discard-step', ['[data-testid="mp-discard-confirm"]', '[data-testid="mp-discard-keep"]', '[data-testid="manual-payment-cancel"]'])

      const report = { viewport: vp, steps: [banner, discard].map((s) => ({ step: s.step, shell: s.shell, problems: problems(s) })) }
      console.log(`P037 B1 CONTAINER-UNRESOLVED ${JSON.stringify(report)}`)
      testInfo.annotations.push({ type: 'P037-B1-UNRESOLVED', description: JSON.stringify(report.steps.map((s) => ({ step: s.step, problems: s.problems }))) })

      expect.soft(problems(banner), `unresolved banner in ${vp.w}x${vp.h}`).toEqual([])
      expect.soft(problems(discard), `discard step in ${vp.w}x${vp.h}`).toEqual([])
    })
  })
}
