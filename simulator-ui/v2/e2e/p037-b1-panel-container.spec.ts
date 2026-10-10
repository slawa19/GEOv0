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

import { Counter, PAYMENT_ID, mockApp, ready } from './helpers/p037Mock.js'

type Fit = {
  selector: string
  found: boolean
  rect: { x: number; y: number; w: number; h: number } | null
  insideViewport: boolean
  insideShell: boolean
  uncoveredPoints: number
  of: number
}

type StepMeasure = {
  step: string
  viewport: { W: number; H: number }
  documentScrollWidth: number
  pageScrollTop: number
  shell: { x: number; y: number; w: number; h: number; inside: boolean } | null
  fits: Fit[]
  /** The scrolling body of the panel: where long content scrolls INSIDE the window. */
  body: { scrollHeight: number; clientHeight: number } | null
}

/** Measure without touching the page: no scroll, no click. */
async function measure(page: Page, step: string, selectors: string[]): Promise<StepMeasure> {
  return await page.evaluate(({ step, selectors }) => {
    const W = window.innerWidth
    const H = window.innerHeight
    const tol = 0.5
    const panel = document.querySelector('[data-testid="manual-payment-panel"]') as HTMLElement | null
    const shellEl = (panel?.closest('.ws-shell') as HTMLElement | null) ?? null
    const sr = shellEl?.getBoundingClientRect() ?? null
    const fits = selectors.map((selector): Fit => {
      const el = document.querySelector(selector) as HTMLElement | null
      if (!el) return { selector, found: false, rect: null, insideViewport: false, insideShell: false, uncoveredPoints: 0, of: 5 }
      const r = el.getBoundingClientRect()
      // The HUD theme cuts two corners of buttons at 8px (clip-path): probe 10px in from the sides, 3px in from top/bottom.
      const ix = Math.min(10, r.width / 2 - 1)
      const iy = Math.min(3, r.height / 2 - 1)
      const pts: Array<[number, number]> = [
        [r.left + ix, r.top + iy], [r.right - ix, r.top + iy], [r.left + ix, r.bottom - iy], [r.right - ix, r.bottom - iy],
        [r.left + r.width / 2, r.top + r.height / 2],
      ]
      let uncovered = 0
      for (const [x, y] of pts) {
        if (x < 0 || y < 0 || x >= W || y >= H) continue
        const hit = document.elementFromPoint(x, y)
        if (hit && (hit === el || el.contains(hit))) uncovered += 1
      }
      return {
        selector, found: true,
        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
        insideViewport: r.left >= -tol && r.top >= -tol && r.right <= W + tol && r.bottom <= H + tol,
        insideShell: !!sr && r.left >= sr.left - tol && r.top >= sr.top - tol && r.right <= sr.right + tol && r.bottom <= sr.bottom + tol,
        uncoveredPoints: uncovered, of: 5,
      }
    })
    return {
      step,
      viewport: { W, H },
      documentScrollWidth: document.documentElement.scrollWidth,
      pageScrollTop: document.scrollingElement?.scrollTop ?? 0,
      shell: sr && { x: Math.round(sr.x), y: Math.round(sr.y), w: Math.round(sr.width), h: Math.round(sr.height),
        inside: sr.left >= -tol && sr.top >= -tol && sr.right <= W + tol && sr.bottom <= H + tol },
      fits,
      body: (() => { const b = panel?.querySelector<HTMLElement>(':scope > .ds-panel__body'); return b ? { scrollHeight: b.scrollHeight, clientHeight: b.clientHeight } : null })(),
    }
  }, { step, selectors })
}

function problems(m: StepMeasure): string[] {
  const out: string[] = []
  if (!m.shell) out.push('no window shell around the payment panel')
  else if (!m.shell.inside) out.push(`shell outside the viewport ${JSON.stringify(m.shell)} in ${m.viewport.W}x${m.viewport.H}`)
  if (m.documentScrollWidth > m.viewport.W) out.push(`document overflows horizontally: ${m.documentScrollWidth} > ${m.viewport.W}`)
  if (m.pageScrollTop !== 0) out.push(`the page itself scrolled (${m.pageScrollTop})`)
  for (const f of m.fits) {
    if (!f.found) { out.push(`${f.selector}: not found`); continue }
    if (!f.insideViewport) out.push(`${f.selector}: not whole inside the viewport ${JSON.stringify(f.rect)}`)
    if (!f.insideShell) out.push(`${f.selector}: not whole inside the shell ${JSON.stringify(f.rect)} (shell ${JSON.stringify(m.shell)})`)
    if (f.uncoveredPoints < f.of) out.push(`${f.selector}: covered or clipped, ${f.uncoveredPoints}/${f.of} points hit it ${JSON.stringify(f.rect)}`)
  }
  return out
}

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

      await expect.poll(async () => (await page.locator('#mp-to__trigger').isEnabled())).toBe(true)
      steps.push(await measure(page, 'pick-to', ['#mp-from__trigger', '#mp-to__trigger', '[data-testid="manual-payment-cancel"]']))

      // The long list (31 recipients): opened by touch, measured; then by keyboard, walked with ArrowDown.
      await c.press('To: open list', '#mp-to__trigger')
      await expect(page.locator('#mp-to__surface')).toBeVisible()
      surfaces.push(await measureSurface(page, 'mp-to', 'pick-to list (touch)'))
      await page.locator('#mp-to__trigger').tap()
      await expect(page.locator('#mp-to__surface')).toBeHidden()
      await page.locator('#mp-to__trigger').focus()
      await page.keyboard.press('Enter')
      await expect(page.locator('#mp-to__surface')).toBeVisible()
      for (let i = 0; i < 12; i += 1) await page.keyboard.press('ArrowDown')
      const keyboard = await page.evaluate(() => {
        const s = document.getElementById('mp-to__surface')!
        const a = document.activeElement as HTMLElement | null
        const sr = s.getBoundingClientRect()
        const ar = a?.getBoundingClientRect()
        return {
          activeIsOption: !!a && a.getAttribute('role') === 'option' && s.contains(a),
          activeInsideSurface: !!ar && ar.top >= sr.top - 0.5 && ar.bottom <= sr.bottom + 0.5,
          activeInsideViewport: !!ar && ar.top >= 0 && ar.bottom <= window.innerHeight,
          activeLabel: a?.textContent?.trim() ?? null,
          pageScrollTop: document.scrollingElement?.scrollTop ?? 0,
        }
      })
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
      await c.pick('To', 'mp-to', 'bob')
      await page.locator('#mp-amount').fill('1.00')
      await c.press('Confirm', '[data-testid="manual-payment-confirm"]')
      await expect(page.locator('[data-testid="mp-result-route-3"]')).toBeVisible()
      await expect(page.getByLabel('Success notification')).toBeHidden({ timeout: 15_000 })

      const m = await measure(page, 'long-result', ['[data-testid="mp-result-another"]', '[data-testid="mp-result-close"]'])
      const scrolls = !!m.body && m.body.scrollHeight > m.body.clientHeight + 1
      console.log(`P037 B1 CONTAINER-LONG-RESULT ${JSON.stringify({ viewport: vp, shell: m.shell, body: m.body, scrolls, problems: problems(m) })}`)
      testInfo.annotations.push({ type: 'P037-B1-LONG-RESULT', description: JSON.stringify({ body: m.body, problems: problems(m) }) })

      expect.soft(problems(m), `long result in ${vp.w}x${vp.h}`).toEqual([])
      // Not vacuous: on a short screen this result really is longer than the window, so the inner scroll is what keeps the buttons reachable.
      if (vp.h <= 667) expect.soft(scrolls, `on ${vp.w}x${vp.h} the long result must scroll inside the window`).toBe(true)
    })

    test('only the payment panel window is re-shaped: the clearing and the trustline windows keep the manager layout', async ({ page }) => {
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
      expect(await shellStyle('clearing-panel'), 'the clearing window').toEqual({ display: 'block', dropdownMax: '', bodyDisplay: 'block' })
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
      await c.pick('To', 'mp-to', 'bob')
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
