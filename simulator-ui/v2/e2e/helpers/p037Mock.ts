/**
 * Shared mocked-backend scaffolding for the 037 browser specs (path, layout, panel container): the manual-payment
 * Interact app on `page.route`, the action counter. Not a spec: Playwright only collects `*.spec.ts`.
 */
import { expect, type Page, type Route } from '@playwright/test'

export type Participant = { pid: string; name: string }

export const PARTICIPANTS: Participant[] = [
  { pid: 'alice', name: 'Alice' },
  { pid: 'bob', name: 'Bob' },
  { pid: 'carol', name: 'Carol' },
]
export const RUN_ID = 'run-p037'
export const SCENARIO_ID = 'greenfield-village-100-realistic-v2'
export const PAYMENT_ID = 'payment-p037-1'

export function snapshot(more: Participant[] = []) {
  return {
    equivalent: 'UAH',
    generated_at: new Date('2026-02-01T00:00:00Z').toISOString(),
    palette: { default: { color: '#64748b', label: 'Default' } },
    limits: { max_particles: 120 },
    nodes: [...PARTICIPANTS, ...more].map((p) => ({
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

export async function mockApp(
  page: Page,
  o: { paymentRealBodies: Array<Record<string, unknown>>; trustlinesStatus?: number; paymentRealNetworkFailures?: { left: number };
    /** N more participants (`x01`..) that Alice can pay: a recipient list longer than the screen. */ extraTargets?: number;
    /** Three routes of four steps through `x01`..`x03` (needs `extraTargets` >= 3): a result longer than a phone screen. */ longRoutes?: boolean },
) {
  await page.addInitScript(({ scenarioId, runId }) => {
    try {
      localStorage.clear()
      localStorage.setItem('geo.sim.v2.apiBase', '/api/v1')
      localStorage.setItem('geo.sim.v2.selectedScenarioId', scenarioId)
      localStorage.setItem('geo.sim.v2.runId', runId)
    } catch { /* ignore */ }
  }, { scenarioId: SCENARIO_ID, runId: RUN_ID })

  const extras = (): Participant[] =>
    Array.from({ length: o.extraTargets ?? 0 }, (_, i) => ({ pid: `x${String(i + 1).padStart(2, '0')}`, name: `Extra ${String(i + 1).padStart(2, '0')}` }))
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
  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, (r) => json(r, snapshot(extras())))
  await page.route(/\/simulator\/runs$/i, (r) => json(r, { run_id: RUN_ID }))
  await page.route(`**/simulator/runs/${RUN_ID}/pause`, (r) => json(r, runBody))
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, runBody))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/graph/snapshot`, 'i'), (r) => json(r, snapshot(extras())))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/events`, 'i'), (r) =>
    r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' }, body: ':ok\n\n' }))
  await page.route(`**/simulator/runs/${RUN_ID}/actions/participants-list`, (r) => json(r, { items: [...PARTICIPANTS, ...extras()] }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) =>
    o.trustlinesStatus && o.trustlinesStatus !== 200 ? json(r, { code: 'BOOM', message: 'down' }, o.trustlinesStatus) : json(r, {
      items: [{ from_pid: 'bob', from_name: 'Bob', to_pid: 'alice', to_name: 'Alice', equivalent: 'UAH',
        limit: '100.00', used: '0.00', reverse_used: '0.00', available: '100.00', status: 'active' }],
    }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/payment-targets`, 'i'), (r) => {
    const from = new URL(r.request().url()).searchParams.get('from_pid')
    json(r, { items: from === 'alice' ? [{ to_pid: 'bob', hops: 1 }, ...extras().map((e) => ({ to_pid: e.pid, hops: 1 }))] : [] })
  })
  await page.route(`**/simulator/runs/${RUN_ID}/actions/payment-real`, async (r) => {
    const req = JSON.parse((await r.request().postData()) ?? '{}') as Record<string, unknown>
    o.paymentRealBodies.push(req)
    if (o.paymentRealNetworkFailures && o.paymentRealNetworkFailures.left > 0) {
      o.paymentRealNetworkFailures.left -= 1
      await r.abort('failed') // the request left; no answer came back
      return
    }
    await json(r, {
      ok: true, payment_id: PAYMENT_ID, from_pid: req.from_pid, to_pid: req.to_pid, equivalent: req.equivalent,
      amount: String(req.amount), status: 'COMMITTED', client_action_id: req.client_action_id ?? null,
      routes: o.longRoutes
        ? [1, 2, 3].map(() => ({ hops: [
            { from: req.from_pid, to: 'x01', amount: String(req.amount) }, { from: 'x01', to: 'x02', amount: String(req.amount) },
            { from: 'x02', to: 'x03', amount: String(req.amount) }, { from: 'x03', to: req.to_pid, amount: String(req.amount) },
          ] }))
        : [{ hops: [{ from: req.from_pid, to: req.to_pid, amount: String(req.amount) }] }],
    })
  })
}

export async function ready(page: Page, withActionBar: boolean) {
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
  if (withActionBar) await expect(page.locator('[data-testid="actionbar-payment"]')).toBeVisible({ timeout: 20_000 })
}

/** One user action = one click/tap/fill. Counted, never inferred. */
export class Counter {
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
  /** One action: choose in a list that is ALREADY open (the recipient list opens by itself after the sender). It is asserted, not assumed. */
  async chooseOpen(label: string, selectId: string, value: string) {
    await expect(this.page.locator(`#${selectId}__surface`), `${label}: the list is already open`).toBeVisible()
    await this.press(`${label}: choose option`, `#${selectId}__surface [role="option"][data-option-value="${value}"]`)
  }
  async pick(label: string, selectId: string, value: string) {
    await this.press(`${label}: open list`, `#${selectId}__trigger`)
    await this.press(`${label}: choose option`, `#${selectId}__surface [role="option"][data-option-value="${value}"]`)
  }
}

