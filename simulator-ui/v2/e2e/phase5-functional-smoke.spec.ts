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
  } = {},
) {
  let currentRunId = options.initialRunId ?? null
  let currentRunState: RunState = options.initialRunState ?? 'paused'
  let runSequence = 0
  let rejectNextPayment = false
  let failNextCreate = false

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
        await fulfillJson(route, 422, { code: 'NO_ROUTE', message: 'No route between selected participants' })
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
  }
}

async function waitReady(page: Page) {
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
}

async function activateButton(page: Page, name: string) {
  const button = page.getByRole('button', { name, exact: true })
  await expect(button).toBeVisible()
  await button.focus()
  await button.press('Enter')
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
  test('fixture bootstrap and visible scene switch', async ({ page }) => {
    await page.goto('/?mode=fixtures&scene=A&e2eReal=1')
    await waitReady(page)

    await expect(page.locator('[data-scene="A"]')).toBeVisible()
    const sceneSelect = page.getByRole('combobox', { name: 'Scene' })
    await expect(sceneSelect).toBeVisible()
    await sceneSelect.selectOption('B')
    await expect(page.locator('[data-scene="B"]')).toBeVisible()
  })

  test('stale run falls back to real preview and scenario switch remains live', async ({ page }) => {
    await installRealMocks(page, {
      initialRunId: 'stale-phase5-run',
      staleRunIds: ['stale-phase5-run'],
    })

    await page.goto('/?mode=real&e2eReal=1')
    await waitReady(page)
    const navigator = page.getByRole('region', { name: 'Graph navigator' })
    await navigator.getByText('Inspect graph', { exact: true }).click()
    await expect(navigator.getByLabel('Node').locator('option')).toHaveCount(2)

    const scenarioSelect = page.getByRole('combobox', { name: 'Scenario' })
    await scenarioSelect.selectOption('S2')
    await expect(navigator.getByLabel('Node').locator('option')).toHaveCount(3)
  })

  test('terminal run supports Start to Stop to Start and exposes a later start error', async ({ page }) => {
    const mock = await installRealMocks(page)
    await page.goto('/?mode=real&e2eReal=1')
    await waitReady(page)

    await activateButton(page, 'Start')
    await expect(page.getByLabel('Run state')).toContainText('running')
    await activateButton(page, 'Stop')
    await expect(page.getByLabel('Run state')).toContainText('stopped')

    await activateButton(page, 'Start')
    await expect(page.getByLabel('Run state')).toContainText('running')
    expect(mock.createRequests).toHaveLength(2)

    await activateButton(page, 'Stop')
    mock.failOneCreate()
    await activateButton(page, 'Start')
    await expect(page.getByLabel('Error')).toContainText('HTTP 500 Internal Server Error for /simulator/runs')
    await expect(page.getByRole('button', { name: 'Start', exact: true })).toBeEnabled()
  })

  test('keyboard operations and DOM graph navigator cover the supported interaction paths', async ({ page }) => {
    test.setTimeout(60_000)
    const mock = await installRealMocks(page)
    await page.goto('/?mode=real&e2eReal=1')
    await waitReady(page)
    await activateButton(page, 'Start')
    await expect(page.getByLabel('Run state')).toContainText('running')
    await expect.poll(() => page.evaluate(() => window.localStorage.getItem('geo.sim.v2.runId'))).toBe('phase5-run-1')
    await page.goto('/?mode=real&ui=interact&e2eReal=1')
    await waitReady(page)
    await expect.poll(async () => ({
      runId: await page.evaluate(() => window.localStorage.getItem('geo.sim.v2.runId')),
      unhandledPaths: mock.unhandledPaths,
    })).toMatchObject({
      runId: 'phase5-run-1',
      unhandledPaths: [],
    })

    await activateButton(page, 'Send Payment')
    await chooseOverlayOption(page, 'From', 'alice', /Alice/)
    await expect.poll(() => mock.paymentTargetRequests).toContain('alice')
    await chooseOverlayOption(page, 'To', 'bob', /Bob/)
    const amount = page.getByLabel('Amount')
    await amount.fill('1.00')
    await amount.press('Enter')
    await expect(page.getByLabel('Success notification')).toContainText('Payment sent: 1.00 UAH')
    expect(mock.paymentRequests).toHaveLength(1)

    mock.rejectOnePayment()
    await activateButton(page, 'Send Payment')
    await chooseOverlayOption(page, 'From', 'alice', /Alice/)
    await chooseOverlayOption(page, 'To', 'bob', /Bob/)
    await page.getByLabel('Amount').fill('2.00')
    await page.getByLabel('Amount').press('Enter')
    await expect(page.getByLabel('Error notification')).toContainText('No route between selected participants')
    await activateButton(page, 'Cancel')
    await expect(page.getByLabel('Manual payment panel')).toBeHidden()

    await activateButton(page, 'Manage Trustline')
    await chooseTrustlineFromWhenRequested(page)
    await chooseOverlayOption(page, 'To', 'carol', /Carol/, true)
    await page.getByLabel('Limit').fill('15.00')
    await activateButton(page, 'Create')
    await expect(page.getByLabel('Success notification')).toContainText('Trustline created: alice → carol')
    expect(mock.trustlineCreateRequests).toHaveLength(1)

    await activateButton(page, 'Manage Trustline')
    await chooseTrustlineFromWhenRequested(page)
    await chooseOverlayOption(page, 'To', 'bob', /Bob/, true)
    await page.getByLabel('New limit').fill('20.00')
    await activateButton(page, 'Update')
    await expect(page.getByLabel('Success notification')).toContainText('Limit updated: 20.00 UAH')
    expect(mock.trustlineUpdateRequests).toHaveLength(1)

    await activateButton(page, 'Manage Trustline')
    await chooseTrustlineFromWhenRequested(page)
    await chooseOverlayOption(page, 'To', 'bob', /Bob/, true)
    await expect(page.locator('[data-testid="tl-close-blocked"]')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Close TL' })).toBeDisabled()
    await activateButton(page, 'Cancel')

    await activateButton(page, 'Run Clearing')
    await activateButton(page, 'Confirm')
    await expect(page.getByTestId('clearing-panel')).toContainText('Clearing preview')
    await expect(page.getByLabel('Success notification')).toContainText('Clearing done: 1/1 cycles')
    expect(mock.clearingRequests).toHaveLength(1)

    const navigator = page.getByRole('region', { name: 'Graph navigator' })
    await expect(navigator).toBeVisible()
    const navigatorSummary = navigator.getByText('Inspect graph', { exact: true })
    await navigatorSummary.focus()
    await navigatorSummary.press('Enter')
    const nodeSelect = navigator.getByLabel('Node')
    await expect(nodeSelect).toBeVisible()
    await nodeSelect.focus()
    await nodeSelect.press('End')
    await expect(nodeSelect).toHaveValue('bob')
    const inspectNode = navigator.getByRole('button', { name: 'Inspect node' })
    await inspectNode.focus()
    await inspectNode.press('Enter')
    await expect(navigator.locator('[data-testid="graph-navigator-status"]')).toContainText('Node details opened: Bob')
    await expect(page.getByRole('region', { name: 'Node details: Bob' })).toBeVisible()

    await chooseFirstNativeOptionByKeyboard(navigator.getByLabel('Edge'))
    const inspectEdge = navigator.getByRole('button', { name: 'Inspect edge' })
    await inspectEdge.focus()
    await inspectEdge.press('Enter')
    await expect(page.getByRole('region', { name: 'Trustline details: alice to bob' })).toBeVisible()
  })
})
