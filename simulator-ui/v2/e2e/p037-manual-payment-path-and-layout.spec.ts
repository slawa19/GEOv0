/**
 * Programme 037, `T3701` - reproducers that need a browser: the PATH (number of user actions), the page reload on
 * entry (a known DEBT, characterised below - not a met budget) and the 390x844 LAYOUT of the manual payment flow.
 *
 * Backend is mocked exactly like `manual-operations-interact.spec.ts` (E-2): `page.route`, no real server. The mock
 * is a trimmed copy of that file's `mockRealInteractApp`, kept in `helpers/p037Mock.ts` (shared with the panel-container
 * spec); it is copied, not imported, because `manual-operations-interact.spec.ts` is a spec file and exports nothing.
 *
 * Every test records the number it measured with `console.log('P037 ...')` and a test annotation, so the
 * "before" figures survive in the run output even when the assertion at the end is the failing one.
 *
 * What this does NOT see: the real `/session/ensure` + real backend (mocked); a physical touch device (Playwright
 * `hasTouch` + `tap()` emulate touch events, not a phone's browser chrome).
 */
import { expect, test, type Page } from '@playwright/test'
import { Counter, PAYMENT_ID, mockApp, ready } from './helpers/p037Mock.js'

/** The ordinary path on the current panel: Send Payment, From, To, amount, Confirm. */
async function payAliceToBob(page: Page, bodies: Array<Record<string, unknown>>) {
  await mockApp(page, { paymentRealBodies: bodies })
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await ready(page, true)

  const loads: string[] = []
  page.on('framenavigated', (f) => { if (f === page.mainFrame()) loads.push(f.url()) })

  const c = new Counter(page, false)
  // The opening click is counted separately: the budget is "from Send Payment to submit".
  await page.locator('[data-testid="actionbar-payment"]').click()
  await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()

  await c.pick('From', 'mp-from', 'alice')
  await c.chooseOpen('To', 'mp-to', 'bob')
  await c.type('Amount', '#mp-amount', '1.00')
  await c.press('Confirm', '[data-testid="manual-payment-confirm"]')

  await expect(page.getByLabel('Success notification')).toContainText('Payment sent: 1.00 UAH')
  expect(bodies, 'precondition: the mocked payment-real was reached exactly once').toHaveLength(1)
  return { c, loads }
}

test.describe('037 A2 - manual payment path (current panel, mocked backend)', () => {
  test('RESULT: the payment stays on screen with its id and its route by name, and the request carried a key', async ({ page }, testInfo) => {
    const bodies: Array<Record<string, unknown>> = []
    const { c, loads } = await payAliceToBob(page, bodies)

    const panel = page.locator('[data-testid="manual-payment-panel"]')
    await expect(panel).toBeVisible()
    await expect(page.locator('[data-testid="mp-result-payment-id"]')).toHaveText(PAYMENT_ID)
    await expect(page.locator('[data-testid="mp-result-route-chain"]')).toHaveText('Alice → Bob')
    await expect(page.locator('[data-testid="mp-result-parties"]')).toHaveText('Alice → Bob')

    const numbers = {
      userActionsAfterSendPayment: c.steps.length,
      steps: c.steps,
      pageNavigationsDuringThePath: loads.length,
      paymentRealBodyKeys: Object.keys(bodies[0] ?? {}).sort(),
      panelStillOpenAfterSuccess: await panel.isVisible(),
      paymentIdVisibleOnScreen: await page.getByText(PAYMENT_ID).first().isVisible(),
    }
    console.log(`P037 PATH-A2 ${JSON.stringify(numbers)}`)
    testInfo.annotations.push({ type: 'P037-PATH-A2', description: JSON.stringify(numbers) })

    expect(bodies[0]!.idempotency_key, 'the request carries the key of the intent').toMatch(/^[A-Za-z0-9._:-]{1,128}$/)
    expect(loads, 'the flow itself must not navigate').toHaveLength(0)

    // The result is dismissed by the user, not by a reset: "Close" closes the panel.
    await page.locator('[data-testid="mp-result-close"]').click()
    await expect(panel).toBeHidden()
  })

  test('RESULT: "Another payment" leaves the result for the recipient step with the same sender', async ({ page }) => {
    await payAliceToBob(page, [])
    await page.locator('[data-testid="mp-result-another"]').click()
    await expect(page.locator('[data-testid="mp-result"]')).toBeHidden()
    await expect(page.locator('#mp-to__trigger')).toBeVisible()
  })

  test('UNRESOLVED: a request that got no answer leaves a banner; no other payment can be sent; the repeat is the same key and body; it survives a reload', async ({ page }) => {
    const bodies: Array<Record<string, unknown>> = []
    await mockApp(page, { paymentRealBodies: bodies, paymentRealNetworkFailures: { left: 1 } })
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await ready(page, true)

    const c = new Counter(page, false)
    await page.locator('[data-testid="actionbar-payment"]').click()
    await c.pick('From', 'mp-from', 'alice')
    await c.chooseOpen('To', 'mp-to', 'bob')
    await c.type('Amount', '#mp-amount', '1.00')
    await c.press('Confirm', '[data-testid="manual-payment-confirm"]')

    const banner = page.locator('[data-testid="mp-outcome-unknown"]')
    await expect(banner).toBeVisible()
    await expect(page.locator('[data-testid="mp-outcome-unknown-intent"]')).toHaveText('Unresolved payment: 1.00 UAH, Alice → Bob.')
    await expect(page.locator('[data-testid="manual-payment-confirm"]')).toHaveCount(0)
    await expect(page.locator('#mp-amount')).toHaveCount(0)
    expect(bodies, 'only the first request left').toHaveLength(1)

    // The page is reloaded: the unresolved payment comes back with the panel.
    await page.reload()
    await ready(page, true)
    await page.locator('[data-testid="actionbar-payment"]').click()
    await expect(page.locator('[data-testid="mp-outcome-unknown"]')).toBeVisible()

    await page.locator('[data-testid="mp-retry"]').click()
    await expect(page.locator('[data-testid="mp-result-payment-id"]')).toHaveText(PAYMENT_ID)
    expect(bodies).toHaveLength(2)
    expect(bodies[1]!.idempotency_key).toBe(bodies[0]!.idempotency_key)
    expect({ ...bodies[1]!, client_action_id: null }).toEqual({ ...bodies[0]!, client_action_id: null })
  })

  // The budget: not more than five user actions after "Send Payment" - the SAME budget and the SAME way of counting as before
  // (one click, tap or fill = one action). What changed is the product: the recipient list opens by itself after the sender is
  // chosen (PR B2, decision 037-B PANEL-PLUS), so "To" is one action - choosing - and the test asserts the list is already open.
  test('BUDGET: not more than 5 user actions after "Send Payment"', async ({ page }) => {
    const { c } = await payAliceToBob(page, [])
    expect(c.steps, 'the path, step by step').toEqual(['From: open list', 'From: choose option', 'To: choose option', 'Amount', 'Confirm'])
    expect(c.steps.length, `current path: ${c.steps.join(' -> ')}`).toBeLessThanOrEqual(5)
  })

  // KNOWN DEBT, not a met budget (BACKLOG 037-3, owner: the simulator mode-entry/bootstrap maintainer; decision 037-B
  // 2026-10-10: POSTPONE-TO-BACKLOG). `goInteract` still reloads the page; the target is ZERO document navigations.
  // This test CHARACTERISES the debt: entry happened (positive), the ActionBar is ready, exactly one navigation of the
  // document, and the window marker is gone. It is not an expected-failure test: a broken entry (segment disabled, the
  // ActionBar never appears) fails here for ITS OWN reason instead of hiding behind the debt. If the entry stops
  // reloading, this test goes RED on purpose - replace it by the positive zero-reload assertion (BACKLOG 037-3).
  test('ENTRY (known debt 037-3, characterisation): switching into Interact from Auto-Run still reloads the page once', async ({ page }, testInfo) => {
    await mockApp(page, { paymentRealBodies: [] })
    await page.goto('/?mode=real&e2eReal=1')
    await ready(page, false)

    await page.evaluate(() => { (window as unknown as { __p037Marker?: number }).__p037Marker = 1 })
    let navigations = 0
    page.on('framenavigated', (f) => { if (f === page.mainFrame()) navigations += 1 })

    const interactSegment = page.getByRole('button', { name: 'Interact', exact: true })
    await expect(interactSegment).toBeVisible()
    await expect(interactSegment, 'precondition: the Interact segment is available in real mode').toBeEnabled()
    await interactSegment.click()

    // The entry itself, stated positively: the address says Interact and the Interact ActionBar is ready.
    await page.waitForURL(/ui=interact/, { timeout: 15_000 })
    await expect(page.locator('[data-testid="actionbar-payment"]'), 'the entry did not reach a ready Interact screen').toBeVisible({ timeout: 20_000 })

    const markerSurvived = await page.evaluate(() => (window as unknown as { __p037Marker?: number }).__p037Marker === 1)
    const numbers = { navigations, markerSurvived }
    console.log(`P037 ENTRY ${JSON.stringify(numbers)}`)
    testInfo.annotations.push({ type: 'P037-ENTRY', description: JSON.stringify(numbers) })

    const improved = 'the entry no longer reloads the page: replace this characterisation by the positive zero-reload assertion (BACKLOG 037-3)'
    expect(navigations, improved).toBe(1)
    expect(markerSurvived, improved).toBe(false)
  })
})

test.describe('037 T3701 - 390x844 layout (current flow, mocked backend)', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: false })

  type Violation = { what: string; detail: string }

  /** Rects of the panel's container, its controls, and what covers the centre of each control. */
  async function measure(page: Page, phase: string) {
    return await page.evaluate((phase) => {
      const W = window.innerWidth
      const H = window.innerHeight
      const out: { phase: string; viewport: { W: number; H: number }; scrollWidth: number; container: unknown;
        controls: Array<{ label: string; rect: { x: number; y: number; w: number; h: number }; inside: boolean; reachable: boolean }> } = {
        phase, viewport: { W, H }, scrollWidth: document.documentElement.scrollWidth, container: null, controls: [],
      }
      const panel = document.querySelector('[data-testid="manual-payment-panel"]') as HTMLElement | null
      const shell = (panel?.closest('.ws-shell') as HTMLElement | null) ?? panel
      if (shell) {
        const r = shell.getBoundingClientRect()
        out.container = { class: shell.className, x: r.x, y: r.y, w: r.width, h: r.height,
          inside: r.left >= 0 && r.top >= 0 && r.right <= W && r.bottom <= H }
      }
      const controls = panel
        ? Array.from(panel.querySelectorAll<HTMLElement>('button, input, select, textarea, [role="combobox"], [role="button"]'))
        : []
      for (const el of controls) {
        const r = el.getBoundingClientRect()
        if (r.width === 0 || r.height === 0) continue
        const cx = Math.min(Math.max(r.x + r.width / 2, 0), W - 1)
        const cy = Math.min(Math.max(r.y + r.height / 2, 0), H - 1)
        const hit = document.elementFromPoint(cx, cy)
        out.controls.push({
          label: el.getAttribute('data-testid') || el.id || el.textContent?.trim().slice(0, 24) || el.tagName,
          rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
          inside: r.left >= 0 && r.top >= 0 && r.right <= W && r.bottom <= H,
          reachable: !!hit && (el === hit || el.contains(hit) || hit.contains(el)),
        })
      }
      return out
    }, phase)
  }

  function violations(m: Awaited<ReturnType<typeof measure>>): Violation[] {
    const v: Violation[] = []
    const c = m.container as { inside: boolean; x: number; y: number; w: number; h: number } | null
    if (!c) v.push({ what: 'container', detail: 'no payment panel container found' })
    else if (!c.inside) v.push({ what: 'container-outside-viewport', detail: JSON.stringify(c) })
    if (m.scrollWidth > m.viewport.W) v.push({ what: 'horizontal-scroll', detail: `scrollWidth=${m.scrollWidth} > ${m.viewport.W}` })
    if (m.controls.length === 0) v.push({ what: 'controls', detail: 'no controls measured (vacuous)' })
    for (const k of m.controls) {
      if (!k.inside) v.push({ what: 'control-outside-viewport', detail: `${k.label} ${JSON.stringify(k.rect)}` })
      if (!k.reachable) v.push({ what: 'control-covered', detail: `${k.label} ${JSON.stringify(k.rect)}` })
    }
    return v
  }

  test('LAYOUT: on each step of the current flow the container and every control fit 390x844 and are reachable by touch', async ({ page }, testInfo) => {
    await mockApp(page, { paymentRealBodies: [] })
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await ready(page, true)

    const c = new Counter(page, true)
    const measurements: Array<Awaited<ReturnType<typeof measure>>> = []

    await c.press('open payment', '[data-testid="actionbar-payment"]')
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
    measurements.push(await measure(page, 'pick-from'))

    await c.press('From: open list', '#mp-from__trigger')
    await expect(page.locator('#mp-from__surface')).toBeVisible()
    const surfaceFrom = await page.locator('#mp-from__surface').boundingBox()
    await c.press('From: choose', '#mp-from__surface [role="option"][data-option-value="alice"]')

    // 037 B2: the recipient list opens by itself after the sender is chosen. Close it (not a user step of the path) to measure
    // the step with its controls uncovered; the list itself is measured by the B1 container spec.
    await expect(page.locator('#mp-to__surface')).toBeVisible()
    await page.locator('#mp-to__trigger').tap()
    await expect(page.locator('#mp-to__surface')).toBeHidden()
    measurements.push(await measure(page, 'pick-to'))
    await c.press('To: open list', '#mp-to__trigger')
    await c.press('To: choose', '#mp-to__surface [role="option"][data-option-value="bob"]')

    await expect(page.locator('#mp-amount')).toBeVisible()
    measurements.push(await measure(page, 'confirm'))

    // The result screen (A2) is a step of the flow too: it must fit and be reachable by touch.
    await page.locator('#mp-amount').fill('1.00')
    await c.press('Confirm', '[data-testid="manual-payment-confirm"]')
    await expect(page.locator('[data-testid="mp-result"]')).toBeVisible()
    measurements.push(await measure(page, 'result'))

    const perPhase = measurements.map((m) => ({ phase: m.phase, scrollWidth: m.scrollWidth, container: m.container,
      controls: m.controls, violations: violations(m) }))
    const surfaceInside = surfaceFrom ? surfaceFrom.x >= 0 && surfaceFrom.x + surfaceFrom.width <= 390 : null
    console.log(`P037 LAYOUT ${JSON.stringify({ perPhase, fromListSurface: surfaceFrom, fromListSurfaceInside: surfaceInside })}`)
    testInfo.annotations.push({ type: 'P037-LAYOUT', description: JSON.stringify(perPhase.map((p) => ({ phase: p.phase, violations: p.violations }))) })

    for (const p of perPhase) {
      expect.soft(p.violations, `phase ${p.phase}`).toEqual([])
    }
    expect.soft(surfaceInside, 'the dropdown surface of the From list fits the width').toBe(true)
  })

  test('LAYOUT: the banner of an unresolved payment, and its two-step discard, fit 390x844 and are reachable by touch', async ({ page }, testInfo) => {
    const bodies: Array<Record<string, unknown>> = []
    await mockApp(page, { paymentRealBodies: bodies, paymentRealNetworkFailures: { left: 1 } })
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await ready(page, true)

    const c = new Counter(page, true)
    await c.press('open payment', '[data-testid="actionbar-payment"]')
    await c.pick('From', 'mp-from', 'alice')
    await c.chooseOpen('To', 'mp-to', 'bob')
    await page.locator('#mp-amount').fill('1.00')
    await c.press('Confirm', '[data-testid="manual-payment-confirm"]')
    await expect(page.locator('[data-testid="mp-outcome-unknown"]')).toBeVisible()
    // The application's own error toast shows the same sentence for a few seconds, over the lower part of a phone screen.
    await expect(page.getByLabel('Error notification')).toBeHidden({ timeout: 15_000 })

    const banner = await measure(page, 'unresolved-banner')
    await c.press('Discard', '[data-testid="mp-discard"]')
    await expect(page.locator('[data-testid="mp-discard-warning"]')).toBeVisible()
    const discard = await measure(page, 'unresolved-discard-step')
    console.log(`P037 LAYOUT-UNRESOLVED ${JSON.stringify({ banner: { container: banner.container, violations: violations(banner) }, discard: { container: discard.container, violations: violations(discard) } })}`)
    testInfo.annotations.push({ type: 'P037-LAYOUT-UNRESOLVED', description: JSON.stringify({ banner: violations(banner), discard: violations(discard) }) })

    expect(violations(banner)).toEqual([])
    expect(violations(discard)).toEqual([])
  })

  test('LAYOUT: with the figures from the snapshot only (the trustlines answer failed) the confirm step still fits and is reachable', async ({ page }, testInfo) => {
    await mockApp(page, { paymentRealBodies: [], trustlinesStatus: 500 })
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await ready(page, true)

    const c = new Counter(page, true)
    await c.press('open payment', '[data-testid="actionbar-payment"]')
    await c.pick('From', 'mp-from', 'alice')
    await c.chooseOpen('To', 'mp-to', 'bob')
    await expect(page.locator('#mp-amount')).toBeVisible()
    const source = await page.locator('[data-testid="mp-figures-source"]').getAttribute('data-figures-source')
    const m = await measure(page, 'confirm-snapshot-only')
    console.log(`P037 LAYOUT-SNAPSHOT ${JSON.stringify({ source, container: m.container, violations: violations(m) })}`)
    testInfo.annotations.push({ type: 'P037-LAYOUT-SNAPSHOT', description: JSON.stringify({ source, violations: violations(m) }) })

    expect(source, 'precondition: the figures are NOT the server\'s').not.toBe('server')
    expect(violations(m)).toEqual([])
  })
})
