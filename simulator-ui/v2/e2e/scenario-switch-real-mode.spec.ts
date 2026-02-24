import { expect, test } from '@playwright/test'

type ScenarioId = 'S1' | 'S2'

function makeSnapshot(opts: {
  eq: string
  nodes: Array<{ id: string; name: string }>
  links: Array<{ source: string; target: string }>
  linkUsed?: string
  linkAvailable?: string
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
      name: n.name,
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
      trust_limit: '100',
      used: opts.linkUsed ?? '0',
      available: opts.linkAvailable ?? '100',
      status: 'active',
      viz_color_key: 'default',
      viz_width_key: 'default',
      viz_alpha_key: 'default',
    })),
  }
}

async function waitReady(page: any) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  await page.waitForTimeout(250)
}

async function readMetricValue(page: any, label: string): Promise<string> {
  const bar = page.locator('[aria-label="System balance"]')
  await expect(bar).toBeVisible()

  const labelEl = bar.getByText(label, { exact: true })
  await expect(labelEl).toBeVisible()

  // In SystemBalanceBar each metric panel is: <span label> <span value>
  const valueEl = labelEl.locator('xpath=following-sibling::*[1]')
  await expect(valueEl).toBeVisible()
  const v = await valueEl.innerText()
  return String(v || '').trim()
}

test('real mode: switching scenario changes loaded snapshot', async ({ page }) => {
  // Determinism: isolate from developer machine localStorage.
  await page.addInitScript(() => {
    try {
      localStorage.clear()
    } catch {
      // ignore
    }
  })

  const scenarioSnapshots: Record<ScenarioId, any> = {
    S1: makeSnapshot({
      eq: 'UAH',
      nodes: [
        { id: 'A', name: 'Alice' },
        { id: 'B', name: 'Bob' },
      ],
      links: [{ source: 'A', target: 'B' }],
      linkUsed: '1',
      linkAvailable: '99',
    }),
    S2: makeSnapshot({
      eq: 'UAH',
      nodes: [
        { id: 'A', name: 'Alice' },
        { id: 'B', name: 'Bob' },
      ],
      // Same node IDs, different link topology to exercise the "same node IDs" codepath.
      links: [
        { source: 'A', target: 'B' },
        { source: 'B', target: 'A' },
      ],
      linkUsed: '1',
      linkAvailable: '99',
    }),
  }

  let lastPreviewScenario: string | null = null

  await page.route('**/simulator/session/ensure', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ actor_kind: 'anon', owner_id: 'owner-1' }),
    })
  })

  await page.route('**/simulator/runs/active', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ run_id: null }) })
  })

  await page.route(/\/simulator\/scenarios$/i, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [
          { scenario_id: 'S1', label: 'Scenario 1' },
          { scenario_id: 'S2', label: 'Scenario 2' },
        ],
      }),
    })
  })

  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, async (route) => {
    const url = new URL(route.request().url())
    const m = url.pathname.match(/\/simulator\/scenarios\/([^/]+)\/graph\/preview/i)
    const scenarioId = (m?.[1] ? decodeURIComponent(m[1]) : '') as ScenarioId
    lastPreviewScenario = scenarioId || null
    const snap = (scenarioSnapshots as any)[scenarioId] ?? scenarioSnapshots.S1
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(snap) })
  })

  await page.goto('/?mode=real&e2eReal=1')
  await waitReady(page)

  const scenarioSelect = page.getByRole('combobox', { name: 'Scenario' })
  await expect(scenarioSelect).toBeVisible()

  // Select S1 and observe stable metric.
  await scenarioSelect.selectOption('S1')
  await expect.poll(() => lastPreviewScenario).toBe('S1')
  await waitReady(page)

  const debt1 = await readMetricValue(page, 'Total Debt')

  // Select S2 and metric must change (same nodes, different link topology).
  await scenarioSelect.selectOption('S2')
  await expect.poll(() => lastPreviewScenario).toBe('S2')
  await waitReady(page)

  const debt2 = await readMetricValue(page, 'Total Debt')

  expect(debt2).not.toBe(debt1)
})
