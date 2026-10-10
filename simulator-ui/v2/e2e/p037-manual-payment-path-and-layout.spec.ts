/**
 * Programme 037, `T3701` - reproducers that need a browser: the PATH (number of user actions, page reload on
 * entry, a result screen with `payment_id`) and the 390x844 LAYOUT of the CURRENT manual payment flow.
 *
 * Backend is mocked exactly like `manual-operations-interact.spec.ts` (E-2): `page.route`, no real server.
 * The mock below is a trimmed copy of that file's `mockRealInteractApp`; it is copied, not imported, because a
 * spec file exports nothing and `manual-operations-interact.spec.ts` is rewritten by slice B (its E-1/E-2/E-4
 * assertions must survive there, not here).
 *
 * Every test records the number it measured with `console.log('P037 ...')` and a test annotation, so the
 * "before" figures survive in the run output even when the assertion at the end is the failing one.
 * The assertions are the TARGET of the spec (<= 5 actions, no reload, result screen, 390x844 fits) - red now.
 *
 * What this does NOT see: the wizard (does not exist); the real `/session/ensure` + real backend (mocked);
 * a physical touch device (Playwright `hasTouch` + `tap()` emulate touch events, not a phone's browser chrome).
 */
import { expect, test, type Page, type Route } from '@playwright/test'

type Participant = { pid: string; name: string }

const PARTICIPANTS: Participant[] = [
  { pid: 'alice', name: 'Alice' },
  { pid: 'bob', name: 'Bob' },
  { pid: 'carol', name: 'Carol' },
]
const RUN_ID = 'run-p037'
const SCENARIO_ID = 'greenfield-village-100-realistic-v2'
const PAYMENT_ID = 'payment-p037-1'

function snapshot() {
  return {
    equivalent: 'UAH',
    generated_at: new Date('2026-02-01T00:00:00Z').toISOString(),
    palette: { default: { color: '#64748b', label: 'Default' } },
    limits: { max_particles: 120 },
    nodes: PARTICIPANTS.map((p) => ({
      id: p.pid, name: p.name, type: 'person', status: 'active', links_count: 0, net_balance_atoms: '0',
      net_sign: 0, net_balance: '0', viz_color_key: 'default', viz_shape_key: 'default',
      viz_size: { w: 24, h: 24 }, viz_badge_key: '',
    })),
    links: [
      // payment alice -> bob is carried by the line bob -> alice (creditor -> debtor)
      { source: 'bob', target: 'alice', trust_limit: '100', used: '0', available: '100', status: 'active',
        viz_color_key: 'default', viz_width_key: 'default', viz_alpha_key: 'default' },
    ],
  }
}

async function mockApp(page: Page, o: { paymentRealBodies: Array<Record<string, unknown>>; trustlinesStatus?: number }) {
  await page.addInitScript(({ scenarioId, runId }) => {
    try {
      localStorage.clear()
      localStorage.setItem('geo.sim.v2.apiBase', '/api/v1')
      localStorage.setItem('geo.sim.v2.selectedScenarioId', scenarioId)
      localStorage.setItem('geo.sim.v2.runId', runId)
    } catch { /* ignore */ }
  }, { scenarioId: SCENARIO_ID, runId: RUN_ID })

  const json = (route: Route, body: unknown, status = 200) =>
    route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
  const runBody = {
    api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'paused',
    sim_time_ms: 0, intensity_percent: 0, ops_sec: 0, queue_depth: 0,
  }

  await page.route('**/simulator/session/ensure', (r) => json(r, { actor_kind: 'anon', owner_id: 'owner-p037' }))
  await page.route('**/simulator/runs/active', (r) => json(r, { run_id: null }))
  await page.route(/\/simulator\/scenarios$/i, (r) =>
    json(r, {
      api_version: 'simulator-api/1',
      items: [{ api_version: 'simulator-api/1', scenario_id: SCENARIO_ID, name: 'Greenfield-village-100',
        participants_count: 3, trustlines_count: 1, equivalents: ['UAH'] }],
    }))
  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, (r) => json(r, snapshot()))
  await page.route(/\/simulator\/runs$/i, (r) => json(r, { run_id: RUN_ID }))
  await page.route(`**/simulator/runs/${RUN_ID}/pause`, (r) => json(r, runBody))
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, runBody))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/graph/snapshot`, 'i'), (r) => json(r, snapshot()))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/events`, 'i'), (r) =>
    r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' }, body: ':ok\n\n' }))
  await page.route(`**/simulator/runs/${RUN_ID}/actions/participants-list`, (r) => json(r, { items: PARTICIPANTS }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) =>
    o.trustlinesStatus && o.trustlinesStatus !== 200 ? json(r, { code: 'BOOM', message: 'down' }, o.trustlinesStatus) : json(r, {
      items: [{ from_pid: 'bob', from_name: 'Bob', to_pid: 'alice', to_name: 'Alice', equivalent: 'UAH',
        limit: '100.00', used: '0.00', reverse_used: '0.00', available: '100.00', status: 'active' }],
    }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/payment-targets`, 'i'), (r) => {
    const from = new URL(r.request().url()).searchParams.get('from_pid')
    json(r, { items: from === 'alice' ? [{ to_pid: 'bob', hops: 1 }] : [] })
  })
  await page.route(`**/simulator/runs/${RUN_ID}/actions/payment-real`, async (r) => {
    const req = JSON.parse((await r.request().postData()) ?? '{}') as Record<string, unknown>
    o.paymentRealBodies.push(req)
    await json(r, {
      ok: true, payment_id: PAYMENT_ID, from_pid: req.from_pid, to_pid: req.to_pid, equivalent: req.equivalent,
      amount: String(req.amount), status: 'COMMITTED', client_action_id: req.client_action_id ?? null,
      routes: [{ hops: [{ from: req.from_pid, to: req.to_pid, amount: String(req.amount) }] }],
    })
  })
}

async function ready(page: Page, withActionBar: boolean) {
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
  if (withActionBar) await expect(page.locator('[data-testid="actionbar-payment"]')).toBeVisible({ timeout: 20_000 })
}

/** One user action = one click/tap/fill. Counted, never inferred. */
class Counter {
  readonly steps: string[] = []
  constructor(private readonly page: Page, private readonly touch: boolean) {}
  async press(label: string, css: string) {
    this.steps.push(label)
    const loc = this.page.locator(css)
    await expect(loc).toBeVisible()
    await expect(loc).toBeEnabled()
    if (this.touch) await loc.tap()
    else await loc.click()
  }
  async type(label: string, css: string, value: string) {
    this.steps.push(label)
    await this.page.locator(css).fill(value)
  }
  async pick(label: string, selectId: string, value: string) {
    await this.press(`${label}: open list`, `#${selectId}__trigger`)
    await this.press(`${label}: choose option`, `#${selectId}__surface [role="option"][data-option-value="${value}"]`)
  }
}

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
  await c.pick('To', 'mp-to', 'bob')
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

  // SLICE B (the wizard), not A2: the budget is five actions and the entry does not reload. Marked as an EXPECTED failure so
  // the day the wizard lands this test goes red and has to be turned into a plain assertion.
  test('BUDGET (slice B, expected to fail until the wizard): not more than 5 user actions after "Send Payment"', async ({ page }) => {
    test.fail(true, 'slice B: the five-action wizard is not built yet; the current panel needs six')
    const { c } = await payAliceToBob(page, [])
    expect(c.steps.length, `current path: ${c.steps.join(' -> ')}`).toBeLessThanOrEqual(5)
  })

  test('ENTRY (slice B, expected to fail until it is built): switching into Interact from another mode does not reload the page', async ({ page }, testInfo) => {
    test.fail(true, 'slice B: `goInteract` still reloads the page (SimulatorAppRoot.vue)')
    await mockApp(page, { paymentRealBodies: [] })
    await page.goto('/?mode=real&e2eReal=1')
    await ready(page, false)

    await page.evaluate(() => { (window as unknown as { __p037Marker?: number }).__p037Marker = 1 })
    let navigations = 0
    page.on('framenavigated', (f) => { if (f === page.mainFrame()) navigations += 1 })

    const interactSegment = page.getByRole('button', { name: 'Interact', exact: true })
    await expect(interactSegment).toBeVisible()
    const enabled = await interactSegment.isEnabled()
    if (!enabled) {
      console.log('P037 ENTRY {"interactSegmentEnabled":false}')
      testInfo.annotations.push({ type: 'P037-ENTRY', description: 'Interact segment disabled in the mocked state: UNVERIFIED in browser' })
      expect(enabled).toBe(true)
      return
    }
    await interactSegment.click()
    await page.waitForURL(/ui=interact/, { timeout: 15_000 })
    await page.waitForLoadState('load')
    const markerSurvived = await page.evaluate(() => (window as unknown as { __p037Marker?: number }).__p037Marker === 1)
    const numbers = { navigations, markerSurvived }
    console.log(`P037 ENTRY ${JSON.stringify(numbers)}`)
    testInfo.annotations.push({ type: 'P037-ENTRY', description: JSON.stringify(numbers) })

    // TARGET (slice B, spec "Путь" + "Чего нет" item 9): entry without a page reload (state kept).
    expect(markerSurvived, 'entering Interact reloaded the page (window state lost)').toBe(true)
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

    await expect.poll(async () => (await page.locator('#mp-to__trigger').isEnabled())).toBe(true)
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

  test('LAYOUT: with the figures from the snapshot only (the trustlines answer failed) the confirm step still fits and is reachable', async ({ page }, testInfo) => {
    await mockApp(page, { paymentRealBodies: [], trustlinesStatus: 500 })
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await ready(page, true)

    const c = new Counter(page, true)
    await c.press('open payment', '[data-testid="actionbar-payment"]')
    await c.pick('From', 'mp-from', 'alice')
    await c.pick('To', 'mp-to', 'bob')
    await expect(page.locator('#mp-amount')).toBeVisible()
    const source = await page.locator('[data-testid="mp-figures-source"]').getAttribute('data-figures-source')
    const m = await measure(page, 'confirm-snapshot-only')
    console.log(`P037 LAYOUT-SNAPSHOT ${JSON.stringify({ source, container: m.container, violations: violations(m) })}`)
    testInfo.annotations.push({ type: 'P037-LAYOUT-SNAPSHOT', description: JSON.stringify({ source, violations: violations(m) }) })

    expect(source, 'precondition: the figures are NOT the server\'s').not.toBe('server')
    expect(violations(m)).toEqual([])
  })
})
