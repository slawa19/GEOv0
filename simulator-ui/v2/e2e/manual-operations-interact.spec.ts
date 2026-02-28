import { expect, test } from '@playwright/test'

type Participant = { pid: string; name: string }
type Trustline = {
  from_pid: string
  from_name?: string
  to_pid: string
  to_name?: string
  equivalent: string
  limit: string
  used: string
  reverse_used?: string
  available: string
  status: 'active' | string
}

function makeSnapshot(opts: {
  eq: string
  nodes: Array<{ id: string; name?: string }>
  links: Array<{
    source: string
    target: string
    trust_limit?: string
    used?: string
    available?: string
    status?: 'active' | string
    /** Optional (not always present in backend snapshot; see 14.7 note in data cache). */
    reverse_used?: string
  }>
}) {
  return {
    equivalent: opts.eq,
    generated_at: new Date('2026-02-01T00:00:00Z').toISOString(),
    palette: {
      node_colors: {},
      edge_colors: {},
      node_badges: {},
      node_shapes: {},
      edge_widths: {},
      edge_alphas: {},
    },
    limits: { max_particles: 120 },
    nodes: opts.nodes.map((n) => ({
      id: n.id,
      name: n.name ?? n.id,
      type: 'person',
      status: 'active',
      links_count: 0,
      net_balance_atoms: '0',
      net_sign: 0,
      net_balance: '0',
      viz_color_key: 'default',
      viz_shape_key: 'default',
      viz_size: { w: 24, h: 24 },
      viz_badge_key: '',
    })),
    links: opts.links.map((l) => ({
      source: l.source,
      target: l.target,
      trust_limit: l.trust_limit ?? '100',
      used: l.used ?? '0',
      ...(l.reverse_used != null ? { reverse_used: l.reverse_used } : {}),
      available: l.available ?? '100',
      status: l.status ?? 'active',
      viz_color_key: 'default',
      viz_width_key: 'default',
      viz_alpha_key: 'default',
    })),
  }
}

async function waitAppReady(page: any) {
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
  // Also wait for ActionBar to be present (interact UI).
  await expect(page.locator('[data-testid="actionbar-payment"]')).toBeVisible({ timeout: 20_000 })
}

async function getSelectValues(page: any, css: string): Promise<string[]> {
  const loc = page.locator(css)
  await expect(loc).toBeVisible()
  return await loc.evaluate((el: any) => {
    const sel = el as HTMLSelectElement
    return Array.from(sel.options)
      .map((o) => String(o.value ?? '').trim())
      .filter((v) => v)
  })
}

async function getSelectValue(page: any, css: string): Promise<string> {
  const loc = page.locator(css)
  await expect(loc).toBeVisible()
  return await loc.evaluate((el: any) => String((el as HTMLSelectElement).value ?? ''))
}

async function mockRealInteractApp(page: any, o: {
  scenarioId?: string
  eq?: string
  runId?: string
  snapshot: any
  participants: Participant[]
  trustlinesList?: Trustline[]
  trustlinesListStatus?: number
  paymentTargetsByFromPid: Record<string, Array<{ to_pid: string; hops?: number }>>
  onPaymentReal?: (req: any) => { status: number; body: any }
  onTrustlineClose?: (req: any) => { status: number; body: any }
}) {
  const eq = (o.eq ?? 'UAH').trim().toUpperCase()
  const scenarioId = o.scenarioId ?? 'greenfield-village-100-realistic-v2'
  const runId = o.runId ?? 'run-e2e-1'

  // Determinism: isolate from developer machine persisted settings.
  await page.addInitScript(
    ({ scenarioId, runId }: { scenarioId: string; runId: string }) => {
      try {
        localStorage.clear()
        localStorage.setItem('geo.sim.v2.apiBase', '/api/v1')
        localStorage.setItem('geo.sim.v2.selectedScenarioId', scenarioId)
        // Ensure Interact Actions API has an active run_id immediately.
        // This prevents the "known-empty" To dropdown state caused by missing run_id.
        localStorage.setItem('geo.sim.v2.runId', runId)
      } catch {
        // ignore
      }
    },
    { scenarioId, runId },
  )

  // Session bootstrap (anonymous cookie auth).
  await page.route('**/simulator/session/ensure', async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ actor_kind: 'anon', owner_id: 'owner-e2e' }),
    })
  })

  await page.route('**/simulator/runs/active', async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ run_id: null }) })
  })

  await page.route(/\/simulator\/scenarios$/i, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [{ scenario_id: scenarioId, label: 'Greenfield-village-100' }],
      }),
    })
  })

  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o.snapshot) })
  })

  // Run creation + status.
  await page.route(/\/simulator\/runs$/i, async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ run_id: runId }) })
  })

  await page.route(`**/simulator/runs/${encodeURIComponent(runId)}/pause`, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: runId,
        scenario_id: scenarioId,
        state: 'paused',
        sim_time_ms: 0,
        intensity_percent: 0,
        ops_sec: 0,
        queue_depth: 0,
      }),
    })
  })

  await page.route(`**/simulator/runs/${encodeURIComponent(runId)}`, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        run_id: runId,
        scenario_id: scenarioId,
        state: 'paused',
        sim_time_ms: 0,
        intensity_percent: 0,
        ops_sec: 0,
        queue_depth: 0,
      }),
    })
  })

  await page.route(new RegExp(`/simulator/runs/${runId}/graph/snapshot`, 'i'), async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(o.snapshot) })
  })

  // SSE (events endpoint): return a tiny valid stream and close.
  await page.route(new RegExp(`/simulator/runs/${runId}/events`, 'i'), async (route: any) => {
    await route.fulfill({
      status: 200,
      headers: {
        'content-type': 'text/event-stream; charset=utf-8',
        'cache-control': 'no-cache',
      },
      body: ':ok\n\n',
    })
  })

  // Interact mode endpoints.
  await page.route(`**/simulator/runs/${encodeURIComponent(runId)}/actions/participants-list`, async (route: any) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: o.participants }),
    })
  })

  await page.route(new RegExp(`/simulator/runs/${runId}/actions/trustlines-list`, 'i'), async (route: any) => {
    const st = o.trustlinesListStatus ?? 200
    if (st !== 200) {
      await route.fulfill({ status: st, contentType: 'application/json', body: JSON.stringify({ detail: 'boom' }) })
      return
    }
    const items = Array.isArray(o.trustlinesList) ? o.trustlinesList : []
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items }),
    })
  })

  await page.route(new RegExp(`/simulator/runs/${runId}/payment-targets`, 'i'), async (route: any) => {
    const url = new URL(route.request().url())
    const fromPid = String(url.searchParams.get('from_pid') ?? '').trim()
    const items = (o.paymentTargetsByFromPid[fromPid] ?? []).map((it) => ({
      to_pid: it.to_pid,
      hops: it.hops ?? 1,
    }))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items }),
    })
  })

  await page.route(`**/simulator/runs/${encodeURIComponent(runId)}/actions/payment-real`, async (route: any) => {
    const req = JSON.parse((await route.request().postData()) ?? '{}')
    const resp = o.onPaymentReal?.(req) ?? { status: 200, body: { ok: true } }
    await route.fulfill({ status: resp.status, contentType: 'application/json', body: JSON.stringify(resp.body) })
  })

  await page.route(`**/simulator/runs/${encodeURIComponent(runId)}/actions/trustline-close`, async (route: any) => {
    const req = JSON.parse((await route.request().postData()) ?? '{}')
    const resp = o.onTrustlineClose?.(req) ?? { status: 200, body: { ok: true, trustline_id: `${req.from_pid}→${req.to_pid}` } }
    await route.fulfill({ status: resp.status, contentType: 'application/json', body: JSON.stringify(resp.body) })
  })

  // trustline-update is not needed in E-3 (we only validate disabled UI),
  // but define it as a safety no-op so we can click Update in future refactors.
  await page.route(`**/simulator/runs/${encodeURIComponent(runId)}/actions/trustline-update`, async (route: any) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, trustline_id: 'tl-1' }) })
  })

  // Navigate into real+interact mode.
  await page.goto('/?mode=real&ui=interact&e2eReal=1')
  await waitAppReady(page)

  return { eq, runId, scenarioId }
}

test.describe('Manual operations UI — Playwright E2E (Interact, mocked backend)', () => {
  test('E-1: greenfield-village-100 — FROM=shop, TO dropdown contains only participants with trustline to_pid=shop', async ({ page }) => {
    const participants: Participant[] = [
      { pid: 'shop', name: 'Shop' },
      { pid: 'alice', name: 'Alice' },
      { pid: 'bob', name: 'Bob' },
      { pid: 'carol', name: 'Carol' },
    ]

    const snapshot = makeSnapshot({
      eq: 'UAH',
      nodes: participants.map((p) => ({ id: p.pid, name: p.name })),
      links: [
        // Trustlines to_pid=shop (direct capacity for payments FROM=shop)
        { source: 'alice', target: 'shop', trust_limit: '10', used: '0', available: '10' },
        { source: 'bob', target: 'shop', trust_limit: '10', used: '0', available: '10' },
        // Other unrelated trustline
        { source: 'carol', target: 'alice', trust_limit: '10', used: '0', available: '10' },
      ],
    })

    await mockRealInteractApp(page, {
      snapshot,
      participants,
      trustlinesList: [
        {
          from_pid: 'alice',
          to_pid: 'shop',
          equivalent: 'UAH',
          limit: '10.00',
          used: '0.00',
          available: '10.00',
          status: 'active',
        },
        {
          from_pid: 'bob',
          to_pid: 'shop',
          equivalent: 'UAH',
          limit: '10.00',
          used: '0.00',
          available: '10.00',
          status: 'active',
        },
      ],
      paymentTargetsByFromPid: {
        shop: [
          { to_pid: 'alice', hops: 1 },
          { to_pid: 'bob', hops: 1 },
        ],
      },
    })

    await page.locator('[data-testid="actionbar-payment"]').click()
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()

    await page.locator('#mp-from').selectOption('shop')

    await expect.poll(async () => await getSelectValues(page, '#mp-to')).toEqual(['alice', 'bob'])
  })

  test('E-2: FROM=alice -> select TO (filtered) -> send payment succeeds (no NO_ROUTE)', async ({ page }) => {
    const participants: Participant[] = [
      { pid: 'alice', name: 'Alice' },
      { pid: 'bob', name: 'Bob' },
      { pid: 'carol', name: 'Carol' },
    ]

    const snapshot = makeSnapshot({
      eq: 'UAH',
      nodes: participants.map((p) => ({ id: p.pid, name: p.name })),
      links: [
        // Capacity for payment alice -> bob is defined by TL bob -> alice
        { source: 'bob', target: 'alice', trust_limit: '100', used: '0', available: '100' },
      ],
    })

    await mockRealInteractApp(page, {
      snapshot,
      participants,
      trustlinesList: [
        {
          from_pid: 'bob',
          to_pid: 'alice',
          equivalent: 'UAH',
          limit: '100.00',
          used: '0.00',
          available: '100.00',
          status: 'active',
        },
      ],
      paymentTargetsByFromPid: {
        alice: [{ to_pid: 'bob', hops: 1 }],
      },
      onPaymentReal: (req) => {
        expect(req.from_pid).toBe('alice')
        expect(req.to_pid).toBe('bob')
        expect(String(req.amount)).toBe('1.00')
        return { status: 200, body: { ok: true } }
      },
    })

    await page.locator('[data-testid="actionbar-payment"]').click()
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()

    await page.locator('#mp-from').selectOption('alice')
    await expect.poll(async () => await getSelectValues(page, '#mp-to')).toEqual(['bob'])
    await page.locator('#mp-to').selectOption('bob')

    // Confirm step should be active.
    await expect(page.locator('[data-testid="mp-direct-capacity-help"]')).toBeVisible()

    await page.locator('#mp-amount').fill('1.00')
    await page.locator('[data-testid="manual-payment-confirm"]').click()

    // Success toast visible; no ErrorToast with NO_ROUTE.
    await expect(page.getByRole('status')).toContainText('Payment sent: 1.00')
    await expect(page.getByRole('alert')).toBeHidden()
    await expect(page.getByRole('status')).not.toContainText('NO_ROUTE')
  })

  test('E-3: Trustline panel — newLimit < used -> Update disabled + warning visible', async ({ page }) => {
    const participants: Participant[] = [
      { pid: 'alice', name: 'Alice' },
      { pid: 'bob', name: 'Bob' },
    ]

    const snapshot = makeSnapshot({
      eq: 'UAH',
      nodes: participants.map((p) => ({ id: p.pid, name: p.name })),
      links: [{ source: 'alice', target: 'bob', trust_limit: '10', used: '5', available: '5' }],
    })

    await mockRealInteractApp(page, {
      snapshot,
      participants,
      trustlinesList: [
        {
          from_pid: 'alice',
          to_pid: 'bob',
          equivalent: 'UAH',
          limit: '10.00',
          used: '5.00',
          available: '5.00',
          status: 'active',
        },
      ],
      paymentTargetsByFromPid: {},
    })

    await page.locator('[data-testid="actionbar-trustline"]').click()
    await expect(page.locator('[data-testid="trustline-panel"]')).toBeVisible()

    // Pick existing trustline: alice -> bob.
    await page.locator('#tl-from').selectOption('alice')
    await page.locator('#tl-to').selectOption('bob')

    await expect(page.locator('#tl-new-limit')).toBeVisible()
    await page.locator('#tl-new-limit').fill('4.99')

    await expect(page.locator('[data-testid="tl-limit-too-low"]')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Update' })).toBeDisabled()
  })

  test('E-4: Send Payment from EdgeDetailPopup -> pre-fills From/To and opens confirm step', async ({ page }) => {
    const participants: Participant[] = [
      { pid: 'alice', name: 'Alice' },
      { pid: 'bob', name: 'Bob' },
    ]

    // Single edge so clicking near the center can reliably pick it.
    const snapshot = makeSnapshot({
      eq: 'UAH',
      nodes: participants.map((p) => ({ id: p.pid, name: p.name })),
      links: [
        // Edge click opens editing-trustline + EdgeDetailPopup.
        { source: 'alice', target: 'bob', trust_limit: '10', used: '0', available: '10' },
        // Capacity for payment bob -> alice is defined by TL alice -> bob.
      ],
    })

    await mockRealInteractApp(page, {
      snapshot,
      participants,
      trustlinesList: [
        {
          from_pid: 'alice',
          to_pid: 'bob',
          equivalent: 'UAH',
          limit: '10.00',
          used: '0.00',
          available: '10.00',
          status: 'active',
        },
      ],
      paymentTargetsByFromPid: {
        // For payment from=bob, allow to=alice
        bob: [{ to_pid: 'alice', hops: 1 }],
      },
    })

    // Try a few clicks until EdgeDetailPopup appears.
    const canvas = page.locator('canvas.canvas').first()
    await expect(canvas).toBeVisible()
    const box = await canvas.boundingBox()
    expect(box).toBeTruthy()
    const b = box!
    const points = [
      { x: b.x + b.width / 2, y: b.y + b.height / 2 },
      { x: b.x + b.width / 2, y: b.y + b.height * 0.33 },
      { x: b.x + b.width / 2, y: b.y + b.height * 0.66 },
      { x: b.x + b.width * 0.33, y: b.y + b.height / 2 },
      { x: b.x + b.width * 0.66, y: b.y + b.height / 2 },
    ]

    const edgePayBtn = page.locator('[data-testid="edge-send-payment"]')

    let opened = false
    for (const p of points) {
      await page.mouse.click(p.x, p.y)
      // Give the UI a short moment to react to the click (no fixed sleep).
      await edgePayBtn.waitFor({ state: 'visible', timeout: 250 }).catch(() => undefined)
      if (await edgePayBtn.isVisible()) {
        opened = true
        break
      }
    }
    expect(opened).toBe(true)

    await edgePayBtn.click()
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()

    // Confirm step should be opened with prefilled pids (trustline to→from).
    await expect(page.locator('[data-testid="mp-direct-capacity-help"]')).toBeVisible()
    await expect(page.locator('[aria-label="Manual payment panel"]')).toContainText('Manual payment: bob → alice')
    await expect.poll(async () => await getSelectValue(page, '#mp-from')).toBe('bob')
    await expect.poll(async () => await getSelectValue(page, '#mp-to')).toBe('alice')
  })

  test('E-5: TL close with reverse_used > 0 -> backend 409 -> ErrorToast', async ({ page }) => {
    const participants: Participant[] = [
      { pid: 'alice', name: 'Alice' },
      { pid: 'bob', name: 'Bob' },
    ]

    // IMPORTANT: snapshot does NOT contain reverse_used (known limitation),
    // so UI close guard will not block, and backend 409 becomes the last barrier.
    const snapshot = makeSnapshot({
      eq: 'UAH',
      nodes: participants.map((p) => ({ id: p.pid, name: p.name })),
      links: [{ source: 'alice', target: 'bob', trust_limit: '10', used: '0.00', available: '10.00' }],
    })

    await mockRealInteractApp(page, {
      snapshot,
      participants,
      // Make trustlines-list fail so the panel falls back to snapshot-derived trustlines (no reverse_used).
      trustlinesListStatus: 500,
      paymentTargetsByFromPid: {},
      onTrustlineClose: (req) => {
        expect(req.from_pid).toBe('alice')
        expect(req.to_pid).toBe('bob')
        return {
          status: 409,
          body: {
            code: 'TL_CLOSE_CONFLICT',
            message: 'Cannot close trustline: reverse_used > 0',
            details: { reverse_used: '0.01' },
          },
        }
      },
    })

    await page.locator('[data-testid="actionbar-trustline"]').click()
    await expect(page.locator('[data-testid="trustline-panel"]')).toBeVisible()

    await page.locator('#tl-from').selectOption('alice')
    await page.locator('#tl-to').selectOption('bob')

    const closeBtn = page.locator('[data-testid="trustline-close-btn"]')
    await expect(closeBtn).toBeVisible()
    await expect(closeBtn).toBeEnabled()

    // Destructive confirmation: 2 clicks.
    await closeBtn.click()
    await closeBtn.click()

    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('Cannot close trustline: reverse_used > 0')
  })
})

