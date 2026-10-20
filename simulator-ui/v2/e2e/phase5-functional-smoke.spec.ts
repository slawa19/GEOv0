import { expect, test, type Page, type Route } from '@playwright/test'

type RunState = 'created' | 'running' | 'paused' | 'stopped' | 'error'

type NodeInput = { id: string; name: string }
type LinkInput = {
  source: string
  target: string
  trustLimit: string
  used: string
  available: string
}

type PaymentRequest = {
  from_pid?: string
  to_pid?: string
  amount?: string
}

type TrustlineRequest = {
  from_pid?: string
  to_pid?: string
  limit?: string
  new_limit?: string
}

const participants = [
  { pid: 'alice', name: 'Alice', type: 'person', status: 'active' },
  { pid: 'bob', name: 'Bob', type: 'person', status: 'active' },
  { pid: 'carol', name: 'Carol', type: 'person', status: 'active' },
]

const scenarioOne = makeSnapshot(
  [
    { id: 'alice', name: 'Alice' },
    { id: 'bob', name: 'Bob' },
  ],
  [
    {
      source: 'alice',
      target: 'bob',
      trustLimit: '10.00',
      used: '2.00',
      available: '8.00',
    },
  ],
)

const scenarioTwo = makeSnapshot(
  [
    { id: 'alice', name: 'Alice' },
    { id: 'bob', name: 'Bob' },
    { id: 'carol', name: 'Carol' },
  ],
  [
    {
      source: 'alice',
      target: 'bob',
      trustLimit: '20.00',
      used: '5.00',
      available: '15.00',
    },
  ],
)

function makeSnapshot(nodes: NodeInput[], links: LinkInput[]) {
  return {
    equivalent: 'UAH',
    generated_at: '2026-08-09T00:00:00Z',
    palette: { default: { color: '#64748b', label: 'Default' } },
    limits: { max_particles: 120 },
    nodes: nodes.map((node) => ({
      id: node.id,
      name: node.name,
      type: 'person',
      status: 'active',
      links_count: links.filter((link) => link.source === node.id || link.target === node.id).length,
      net_balance_atoms: '0',
      net_sign: 0,
      net_balance: '0',
      viz_color_key: 'default',
      viz_shape_key: 'default',
      viz_size: { w: 24, h: 24 },
      viz_badge_key: '',
    })),
    links: links.map((link) => ({
      source: link.source,
      target: link.target,
      trust_limit: link.trustLimit,
      used: link.used,
      available: link.available,
      status: 'active',
      viz_color_key: 'default',
      viz_width_key: 'default',
      viz_alpha_key: 'default',
    })),
  }
}

function runStatus(runId: string, state: RunState) {
  return {
    api_version: 'simulator-api/1',
    run_id: runId,
    scenario_id: 'S1',
    mode: 'real',
    state,
    sim_time_ms: 0,
    intensity_percent: 0,
    ops_sec: 0,
    queue_depth: 0,
  }
}

async function fulfillJson(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

async function requestJson<T>(route: Route): Promise<T> {
  return JSON.parse(route.request().postData() ?? '{}') as T
}

async function installRealMocks(
  page: Page,
  options: {
    initialRunId?: string | null
    initialRunState?: RunState
    staleRunIds?: string[]
    holdClearing?: boolean
  } = {},
) {
  let currentRunId = options.initialRunId ?? null
  let currentRunState: RunState = options.initialRunState ?? 'paused'
  let runSequence = 0
  let rejectNextPayment = false
  let failNextCreate = false
  let releaseClearingGate: () => void = () => undefined
  const clearingGate = new Promise<void>((resolve) => {
    releaseClearingGate = resolve
  })

  const staleRunIds = new Set(options.staleRunIds ?? [])
  const unhandledPaths: string[] = []
  const createRequests: unknown[] = []
  const paymentRequests: PaymentRequest[] = []
  const paymentTargetRequests: string[] = []
  const trustlineCreateRequests: TrustlineRequest[] = []
  const trustlineUpdateRequests: TrustlineRequest[] = []
  const clearingRequests: unknown[] = []

  await page.addInitScript(
    ({ initialRunId }: { initialRunId: string | null }) => {
      if (sessionStorage.getItem('phase5-mocks-initialized') === '1') return
      localStorage.clear()
      localStorage.setItem('geo.sim.v2.apiBase', '/api/v1')
      localStorage.setItem('geo.sim.v2.selectedScenarioId', 'S1')
      if (initialRunId) localStorage.setItem('geo.sim.v2.runId', initialRunId)
      sessionStorage.setItem('phase5-mocks-initialized', '1')
    },
    { initialRunId: currentRunId },
  )

  await page.route('**/api/v1/simulator/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace(/^\/api\/v1/, '')
    const method = request.method()

    if (path === '/simulator/session/ensure') {
      await fulfillJson(route, 200, { actor_kind: 'anon', owner_id: 'phase5-owner' })
      return
    }

    if (path === '/simulator/scenarios' && method === 'GET') {
      await fulfillJson(route, 200, {
        api_version: 'simulator-api/1',
        items: [
          {
            api_version: 'simulator-api/1',
            scenario_id: 'S1',
            name: 'Scenario one',
            participants_count: scenarioOne.nodes.length,
            trustlines_count: scenarioOne.links.length,
            equivalents: ['UAH'],
          },
          {
            api_version: 'simulator-api/1',
            scenario_id: 'S2',
            name: 'Scenario two',
            participants_count: scenarioTwo.nodes.length,
            trustlines_count: scenarioTwo.links.length,
            equivalents: ['UAH'],
          },
        ],
      })
      return
    }

    const previewMatch = path.match(/^\/simulator\/scenarios\/([^/]+)\/graph\/preview$/)
    if (previewMatch && method === 'GET') {
      await fulfillJson(route, 200, decodeURIComponent(previewMatch[1]!) === 'S2' ? scenarioTwo : scenarioOne)
      return
    }

    if (path === '/simulator/runs/active' && method === 'GET') {
      const active = currentRunId && ['created', 'running', 'paused'].includes(currentRunState) ? currentRunId : null
      await fulfillJson(route, 200, { run_id: active })
      return
    }

    if (path === '/simulator/runs' && method === 'POST') {
      createRequests.push(await requestJson(route))
      if (failNextCreate) {
        failNextCreate = false
        await fulfillJson(route, 500, { code: 'START_FAILED', message: 'Phase 5 mocked start failure' })
        return
      }
      runSequence += 1
      currentRunId = `phase5-run-${runSequence}`
      currentRunState = 'running'
      await fulfillJson(route, 200, { run_id: currentRunId })
      return
    }

    const runMatch = path.match(/^\/simulator\/runs\/([^/]+)(\/.*)?$/)
    if (!runMatch) {
      await fulfillJson(route, 404, { detail: `Unhandled Phase 5 mock route: ${method} ${path}` })
      return
    }

    const requestedRunId = decodeURIComponent(runMatch[1]!)
    const suffix = runMatch[2] ?? ''

    if (staleRunIds.has(requestedRunId)) {
      await fulfillJson(route, 404, { detail: 'Not Found' })
      return
    }

    if (suffix === '/events' && method === 'GET') {
      await route.fulfill({
        status: 200,
        headers: {
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-cache',
        },
        body: ':phase5-ready\n\n',
      })
      return
    }

    if (suffix === '/graph/snapshot' && method === 'GET') {
      await fulfillJson(route, 200, scenarioOne)
      return
    }

    if (suffix === '/stop' && method === 'POST') {
      currentRunId = requestedRunId
      currentRunState = 'stopped'
      await fulfillJson(route, 200, runStatus(requestedRunId, 'stopped'))
      return
    }

    if (suffix === '/pause' && method === 'POST') {
      currentRunState = 'paused'
      await fulfillJson(route, 200, runStatus(requestedRunId, 'paused'))
      return
    }

    if (suffix === '/resume' && method === 'POST') {
      currentRunState = 'running'
      await fulfillJson(route, 200, runStatus(requestedRunId, 'running'))
      return
    }

    if (suffix === '/intensity' && method === 'POST') {
      currentRunId = requestedRunId
      await fulfillJson(route, 200, runStatus(requestedRunId, currentRunState))
      return
    }

    if (suffix === '/actions/participants-list' && method === 'GET') {
      await fulfillJson(route, 200, { items: participants })
      return
    }

    if (suffix === '/actions/trustlines-list' && method === 'GET') {
      await fulfillJson(route, 200, {
        items: [
          {
            from_pid: 'alice',
            from_name: 'Alice',
            to_pid: 'bob',
            to_name: 'Bob',
            equivalent: 'UAH',
            limit: '10.00',
            used: '2.00',
            reverse_used: '0.00',
            available: '8.00',
            status: 'active',
          },
          {
            from_pid: 'bob',
            from_name: 'Bob',
            to_pid: 'alice',
            to_name: 'Alice',
            equivalent: 'UAH',
            limit: '100.00',
            used: '0.00',
            reverse_used: '2.00',
            available: '100.00',
            status: 'active',
          },
        ],
      })
      return
    }

    if (suffix === '/payment-targets' && method === 'GET') {
      const fromPid = url.searchParams.get('from_pid')
      paymentTargetRequests.push(fromPid ?? '')
      const items = fromPid === 'alice' ? [{ to_pid: 'bob', hops: 1 }] : [{ to_pid: 'alice', hops: 1 }]
      await fulfillJson(route, 200, { items })
      return
    }

    if (suffix === '/actions/payment-real' && method === 'POST') {
      const body = await requestJson<PaymentRequest>(route)
      paymentRequests.push(body)
      if (rejectNextPayment) {
        rejectNextPayment = false
        // The wire of the real `/actions/payment-real` on a routing refusal (`app/api/v1/simulator.py`, RoutingException
        // branch): 409, the action's own code, and `details` with the payment's context and the core's machine reason.
        await fulfillJson(route, 409, {
          code: 'NO_ROUTE',
          message: 'No route between selected participants',
          details: {
            equivalent: 'UAH',
            from_pid: body.from_pid,
            to_pid: body.to_pid,
            requested: body.amount,
            reason: 'no_route',
            max_available: '0.00',
          },
        })
        return
      }
      await fulfillJson(route, 200, {
        ok: true,
        payment_id: `payment-${paymentRequests.length}`,
        from_pid: body.from_pid,
        to_pid: body.to_pid,
        equivalent: 'UAH',
        amount: body.amount,
        status: 'committed',
        routes: [],
      })
      return
    }

    if (suffix === '/actions/trustline-create' && method === 'POST') {
      const body = await requestJson<TrustlineRequest>(route)
      trustlineCreateRequests.push(body)
      await fulfillJson(route, 200, {
        ok: true,
        trustline_id: 'alice-to-carol',
        from_pid: body.from_pid,
        to_pid: body.to_pid,
        equivalent: 'UAH',
        limit: body.limit,
      })
      return
    }

    if (suffix === '/actions/trustline-update' && method === 'POST') {
      const body = await requestJson<TrustlineRequest>(route)
      trustlineUpdateRequests.push(body)
      await fulfillJson(route, 200, {
        ok: true,
        trustline_id: 'alice-to-bob',
        old_limit: '10.00',
        new_limit: body.new_limit,
      })
      return
    }

    if (suffix === '/actions/trustline-close' && method === 'POST') {
      await fulfillJson(route, 409, {
        code: 'TL_CLOSE_CONFLICT',
        message: 'Cannot close trustline while used amount remains',
      })
      return
    }

    if (suffix === '/actions/clearing-real' && method === 'POST') {
      clearingRequests.push(await requestJson(route))
      if (options.holdClearing) await clearingGate
      await fulfillJson(route, 200, {
        ok: true,
        equivalent: 'UAH',
        cleared_cycles: 1,
        total_cleared_amount: '2.00',
        cycles: [
          {
            cleared_amount: '2.00',
            edges: [{ from: 'alice', to: 'bob' }],
          },
        ],
      })
      return
    }

    if (!suffix && method === 'GET') {
      currentRunId = requestedRunId
      await fulfillJson(route, 200, runStatus(requestedRunId, currentRunState))
      return
    }

    unhandledPaths.push(`${method} ${path}`)
    await fulfillJson(route, 404, { detail: `Unhandled Phase 5 mock route: ${method} ${path}` })
  })

  return {
    createRequests,
    unhandledPaths,
    paymentRequests,
    paymentTargetRequests,
    trustlineCreateRequests,
    trustlineUpdateRequests,
    clearingRequests,
    rejectOnePayment() {
      rejectNextPayment = true
    },
    failOneCreate() {
      failNextCreate = true
    },
    releaseClearing() {
      releaseClearingGate()
    },
  }
}

async function waitReady(page: Page) {
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
  // 043 S1: the controls this matrix walks (scene, scenario, run lifecycle, navigator) live behind "Подробнее";
  // it is turned on from the keyboard, like everything else here. Remembered by the browser: a no-op after a reload.
  const details = page.locator('[data-testid="details-toggle"]')
  if ((await details.getAttribute('aria-expanded')) !== 'true') {
    await details.focus()
    await details.press('Enter')
  }
  await expect(details).toHaveAttribute('aria-expanded', 'true')
}

async function activateButton(page: Page, name: string) {
  const button = page.getByRole('button', { name, exact: true })
  await expect(button).toBeVisible()
  await button.focus()
  await button.press('Enter')
}

// 046 wave 1, full (2026-10-19): first-screen controls by test id - their names are the dictionary's (language of the page).
async function activateTestId(page: Page, testId: string) {
  const button = page.getByTestId(testId)
  await expect(button).toBeVisible()
  await button.focus()
  await button.press('Enter')
}

/**
 * 043 S2 (decision 2, 2026-10-17): opening the demo starts a run by itself, so a page of this matrix opens with the run
 * of the opening already living. Where a test needs the state the person had before S2 (no live run, Start offered),
 * it stops that run the way a person does - Stop under "Подробнее" - instead of a test-only switch in the product.
 */
async function stopTheOpeningsRun(page: Page) {
  await expect(runState(page), 'the opening started its run').toHaveAttribute('data-run-state', 'running')
  await activateTestId(page, 'run-stop')
  await expect(runState(page)).toHaveAttribute('data-run-state', 'stopped')
}

/**
 * 048 S1 (decision 4): the "Run <state>" badge of the service row is gone; the state of the run is the first row's
 * (`network-state`), which names it for a machine in `data-run-state` - the words are the interface language's.
 */
function runState(page: Page) {
  return page.getByTestId('network-state')
}

async function chooseOverlayOption(
  page: Page,
  triggerName: string,
  optionValue: string,
  optionName: RegExp,
  triggerMayDisappear = false,
) {
  const trigger = page.getByRole('button', { name: triggerName, exact: true })
  await expect(trigger).toBeVisible()
  await expect(trigger).toBeEnabled()
  await trigger.focus()
  await trigger.press('Enter')

  const option = page.locator(`[role="listbox"]:visible [role="option"][data-option-value="${optionValue}"]`)
  await expect(option).toBeVisible()
  await expect(option).toHaveText(optionName)
  await option.focus()
  await option.press('Enter')
  if (!triggerMayDisappear || (await trigger.count()) > 0) await expect(trigger).toContainText(optionName)
}

async function chooseTrustlineFromWhenRequested(page: Page) {
  const fromTrigger = page.getByRole('button', { name: 'From', exact: true })
  if ((await fromTrigger.count()) > 0) {
    await chooseOverlayOption(page, 'From', 'alice', /Alice/, true)
  }
}

async function chooseFirstNativeOptionByKeyboard(select: ReturnType<Page['getByLabel']>) {
  await expect(select).toBeVisible()
  await select.focus()
  await select.press('Home')
  await select.press('Enter')
}

test.describe('Phase 5 frozen non-visual functional matrix', () => {
  // 048 S1 (decision 4): the A/B/C "scene" had no reader but its own select (the offline snapshot is loaded by the
  // equivalent alone); the select, `?scene=` and `data-scene` are gone. Rewritten from "visible scene switch": an old
  // link with `?scene=` still boots the offline snapshot, and even the developer's "Подробнее" has no Scene select
  // (the counter-check: `?debug=1` is in force - "Источник" is there).
  test('fixture bootstrap; an old ?scene= link boots, and no Scene select is offered', async ({ page }) => {
    await page.goto('/?mode=fixtures&scene=B&debug=1&e2eReal=1')
    await waitReady(page)

    expect(await page.locator('.root').getAttribute('data-scene'), 'no data-scene on the root').toBeNull()
    const details = page.locator('[data-testid="details-toggle"]')
    if ((await details.getAttribute('aria-expanded')) !== 'true') await details.click() // this matrix may store it open
    await expect(page.locator('[data-testid="source-select"]'), 'the developer\'s row is there').toBeVisible()
    await expect(page.locator('[aria-label="Scene"]')).toHaveCount(0)
  })

  test('stale run falls back to real preview and scenario switch remains live', async ({ page }) => {
    await installRealMocks(page, {
      initialRunId: 'stale-phase5-run',
      staleRunIds: ['stale-phase5-run'],
    })

    await page.goto('/?mode=real&e2eReal=1')
    await waitReady(page)
    // 043 S2: the stale run fell back (the opening then started a new run, which this test stops to look at the preview).
    await stopTheOpeningsRun(page)
    const navigator = page.getByTestId('graph-navigator')
    await navigator.getByTestId('graph-navigator-summary').click()
    await expect(navigator.locator('#graph-navigator-node').locator('option')).toHaveCount(2)

    const scenarioSelect = page.getByTestId('scenario-select')
    await scenarioSelect.selectOption('S2')
    await expect(navigator.locator('#graph-navigator-node').locator('option')).toHaveCount(3)
  })

  test('terminal run supports Start to Stop to Start and exposes a later start error', async ({ page }) => {
    const mock = await installRealMocks(page)
    await page.goto('/?mode=real&e2eReal=1')
    await waitReady(page)

    // 043 S2: the first Start of this test is the opening's own (decision 2); then Stop -> Start as before.
    await stopTheOpeningsRun(page)

    await activateTestId(page, 'run-start')
    await expect(runState(page)).toHaveAttribute('data-run-state', 'running')
    expect(mock.createRequests).toHaveLength(2)

    await activateTestId(page, 'run-stop')
    mock.failOneCreate()
    await activateTestId(page, 'run-start')
    // 048 S3 (decision 3): the line is a sentence of the interface - no «HTTP 500 … for /…»; the status is the title.
    await expect(page.getByTestId('top-error')).toContainText('The server could not complete the request.')
    await expect(page.getByTestId('top-error')).not.toContainText('HTTP')
    await expect(page.getByTestId('top-error').getByTestId('error-text')).toHaveAttribute('title', 'HTTP 500')
    await expect(page.getByTestId('run-start')).toBeEnabled()
  })

  test('keyboard operations and DOM graph navigator cover the supported interaction paths', async ({ page }) => {
    test.setTimeout(60_000)
    const mock = await installRealMocks(page)
    await page.goto('/?mode=real&e2eReal=1')
    await waitReady(page)
    // 043 S2: the run is the opening's own (decision 2), not a Start press.
    await expect(runState(page)).toHaveAttribute('data-run-state', 'running')
    await expect.poll(() => page.evaluate(() => window.localStorage.getItem('geo.sim.v2.runId'))).toBe('phase5-run-1')
    // 046 wave 0: the texts below are asserted in an explicitly chosen language, not the browser's. 046 wave 2: that
    // language is English - the action windows now speak the page's language, and this matrix holds their English texts
    // (the Russian of the refusal is held by `utils/paymentRefusalText.test.ts` and the windows' Russian by
    // `components/interactWindows.ru.p046.test.ts` / `e2e/p046-w2-windows-ru.spec.ts`).
    await page.goto('/?mode=real&ui=interact&e2eReal=1&lang=en')
    await waitReady(page)
    await expect.poll(async () => ({
      runId: await page.evaluate(() => window.localStorage.getItem('geo.sim.v2.runId')),
      unhandledPaths: mock.unhandledPaths,
    })).toMatchObject({
      runId: 'phase5-run-1',
      unhandledPaths: [],
    })

    await activateTestId(page, 'actionbar-payment')
    await chooseOverlayOption(page, 'From', 'alice', /Alice/)
    await expect.poll(() => mock.paymentTargetRequests).toContain('alice')
    await chooseOverlayOption(page, 'To', 'bob', /Bob/)
    const amount = page.getByLabel('Amount')
    await amount.fill('1.00')
    await amount.press('Enter')
    // 049 S2 (decisions 6, 7): no success toast - the result is in the window and in the history line; the payment's number
    // is not drawn, it is the result's `data-payment-id`.
    await expect(page.getByTestId('recent-actions')).toContainText('Payment 1.00 UAH: Alice → Bob')
    await expect(page.getByTestId('success-toast')).toHaveCount(0)
    // 037 A2: a committed payment stays on screen, with the focus on its result; closing it returns the focus to the opener.
    await expect(page.locator('[data-testid="mp-result"]')).toHaveAttribute('data-payment-id', 'payment-1')
    await expect(page.locator('[data-testid="mp-result"]')).toBeFocused()
    await activateButton(page, 'Close')
    await expect(page.locator('[data-testid="actionbar-payment"]')).toBeFocused()
    expect(mock.paymentRequests).toHaveLength(1)

    mock.rejectOnePayment()
    await activateTestId(page, 'actionbar-payment')
    await chooseOverlayOption(page, 'From', 'alice', /Alice/)
    await chooseOverlayOption(page, 'To', 'bob', /Bob/)
    await page.getByLabel('Amount').fill('2.00')
    await page.getByLabel('Amount').press('Enter')
    // 028 F-028-51 (T2884): a payment refusal reads as the client's text, not the server's `message`. The "(available now:
    // 0.00 UAH)" part comes only from the answer's `details.max_available`, so this fails if the mock's `details` are lost
    // and the text would otherwise be reached through the code alone (029 T2990, finding 4).
    // 043 S2 (review B3, 2026-10-17): the refusal is in the language of the page - English here (`?lang=en`, 046 wave 2).
    // 048 S3: in the window's own block (the error toast is gone).
    await expect(page.getByTestId('manual-payment-error')).toContainText('No payment route between these participants (available now: 0.00 UAH).')
    await activateButton(page, 'Cancel')
    await expect(page.getByLabel('Payment panel')).toBeHidden()

    await activateTestId(page, 'actionbar-trustline')
    await chooseTrustlineFromWhenRequested(page)
    await chooseOverlayOption(page, 'To', 'carol', /Carol/, true)
    await page.getByLabel('Limit').fill('15.00')
    await activateButton(page, 'Create')
    await expect(page.getByTestId('recent-actions')).toContainText('Trustline created: Alice → Carol') // 044 `T4405`: names
    await expect(page.locator('[data-testid="actionbar-trustline"]')).toBeFocused()
    expect(mock.trustlineCreateRequests).toHaveLength(1)

    await activateTestId(page, 'actionbar-trustline')
    await chooseTrustlineFromWhenRequested(page)
    await chooseOverlayOption(page, 'To', 'bob', /Bob/, true)
    await page.getByLabel('New limit').fill('20.00')
    await activateButton(page, 'Update')
    await expect(page.getByTestId('recent-actions')).toContainText('Trustline updated: Alice → Bob → limit 20.00 UAH')
    await expect(page.locator('[data-testid="actionbar-trustline"]')).toBeFocused()
    expect(mock.trustlineUpdateRequests).toHaveLength(1)

    await activateTestId(page, 'actionbar-trustline')
    await chooseTrustlineFromWhenRequested(page)
    await chooseOverlayOption(page, 'To', 'bob', /Bob/, true)
    // INTENTIONAL, 026 `T2603.2` (owner В1): a close with debt is a request - announced, not blocked.
    await expect(page.locator('[data-testid="tl-close-request-note"]')).toBeVisible()
    // 046 round 1 (advisor, 2026-10-18): the button of a line with debt says what it does - "Request close" (was "Close TL").
    await expect(page.getByRole('button', { name: 'Request close' })).toBeEnabled()
    await activateButton(page, 'Cancel')

    await activateTestId(page, 'actionbar-clearing')
    await activateButton(page, 'Confirm')
    // 037 C: the result stays until the user closes it (it used to be a "preview" that left on a timer).
    await expect(page.getByTestId('clearing-panel')).toContainText('Clearing result')
    // 046 (advisor, 2026-10-18): "{creditor} → {debtor}: debt reduced by ..." (was "{debtor}’s debt to {creditor} ...").
    await expect(page.getByTestId('clearing-panel')).toContainText('debt reduced by')
    await expect(page.getByTestId('recent-actions')).toContainText('Clearing: 1 cycle')
    expect(mock.clearingRequests).toHaveLength(1)
    await activateButton(page, 'Close')
    await expect(page.getByTestId('clearing-panel')).toBeHidden()

    const navigator = page.getByTestId('graph-navigator')
    await expect(navigator).toBeVisible()
    const navigatorSummary = navigator.getByTestId('graph-navigator-summary')
    await navigatorSummary.focus()
    await navigatorSummary.press('Enter')
    const nodeSelect = navigator.locator('#graph-navigator-node')
    await expect(nodeSelect).toBeVisible()
    await nodeSelect.focus()
    await nodeSelect.press('End')
    await expect(nodeSelect).toHaveValue('bob')
    const inspectNode = navigator.getByTestId('graph-navigator-open-node')
    await inspectNode.focus()
    await inspectNode.press('Enter')
    await expect(navigator.locator('[data-testid="graph-navigator-status"]')).toContainText('Participant opened: Bob')
    await expect(page.getByRole('region', { name: 'Node details: Bob' })).toBeVisible()

    await chooseFirstNativeOptionByKeyboard(navigator.locator('#graph-navigator-edge'))
    const inspectEdge = navigator.getByTestId('graph-navigator-open-edge')
    await inspectEdge.focus()
    await inspectEdge.press('Enter')
    await expect(page.getByRole('region', { name: 'Trustline details: alice to bob' })).toBeVisible()
  })

  test('reduced motion disables HUD and clearing progress animations in Chromium', async ({ page }) => {
    test.setTimeout(45_000)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    const mock = await installRealMocks(page, { holdClearing: true })

    try {
      await page.goto('/?mode=real&e2eReal=1')
      await waitReady(page)
      await expect(page.locator('.root')).toHaveAttribute('data-motion', 'reduced')

      await page.locator('.root').evaluate((root) => {
        const panel = document.createElement('section')
        panel.id = 'phase5-reduced-motion-panel'
        panel.className = 'ds-panel--elevated'
        const header = document.createElement('header')
        header.className = 'ds-panel__header'
        panel.appendChild(header)
        root.appendChild(panel)
      })
      const hudPanel = page.locator('#phase5-reduced-motion-panel')
      await expect(hudPanel).toBeVisible()
      await expect
        .poll(() => hudPanel.evaluate((element) => getComputedStyle(element).animationName))
        .toBe('none')
      const hudHeader = hudPanel.locator('.ds-panel__header')
      await expect
        .poll(() => hudHeader.evaluate((element) => getComputedStyle(element, '::before').animationName))
        .toBe('none')

      // 043 S2: the run is the opening's own (decision 2), not a Start press.
      await expect(runState(page)).toHaveAttribute('data-run-state', 'running')
      await page.goto('/?mode=real&ui=interact&e2eReal=1')
      await waitReady(page)
      await activateTestId(page, 'actionbar-clearing')
      await activateButton(page, 'Confirm')
      const spinner = page.locator('[data-testid="clearing-panel"] .cp-spinner')
      await expect(spinner).toBeVisible()
      await expect.poll(() => spinner.evaluate((element) => getComputedStyle(element).animationName)).toBe('none')
    } finally {
      mock.releaseClearing()
    }
  })
})
