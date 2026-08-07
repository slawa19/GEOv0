import { expect, test, type Page } from '@playwright/test'

function makeSnapshot() {
  return {
    equivalent: 'UAH',
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
    nodes: [
      {
        id: 'A',
        name: 'Alice',
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
      },
    ],
    links: [],
  }
}

async function waitReady(page: Page) {
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  await page.waitForTimeout(250)
}

async function readMetricValue(page: Page, label: string): Promise<string> {
  const bar = page.locator('[aria-label="System balance"]')
  await expect(bar).toBeVisible()
  const labelEl = bar.getByText(label, { exact: true })
  await expect(labelEl).toBeVisible()
  return String(await labelEl.locator('xpath=following-sibling::*[1]').innerText()).trim()
}

test('real mode: stale persisted runId does not block first preview load', async ({ page }) => {
  let previewRequestCount = 0

  await page.addInitScript(() => {
    try {
      localStorage.clear()
      localStorage.setItem('geo.sim.v2.runId', 'stale-run-1')
      localStorage.setItem('geo.sim.v2.selectedScenarioId', 'S1')
      localStorage.setItem('geo.sim.v2.apiBase', '/api/v1')
    } catch {
      // ignore
    }
  })

  await page.route('**/simulator/session/ensure', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ actor_kind: 'anon', owner_id: 'owner-1' }),
    })
  })

  await page.route('**/simulator/runs/stale-run-1', async (route) => {
    await route.fulfill({
      status: 404,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Not Found' }),
    })
  })

  await page.route(/\/simulator\/scenarios$/i, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [{ scenario_id: 'S1', label: 'Scenario 1' }] }),
    })
  })

  await page.route(/\/simulator\/scenarios\/[^/]+\/graph\/preview/i, async (route) => {
    previewRequestCount += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(makeSnapshot()),
    })
  })

  await page.goto('/?mode=real&e2eReal=1')
  await waitReady(page)

  await expect.poll(() => previewRequestCount).toBeGreaterThan(0)
  await expect(page.locator('[data-ready="1"]')).toBeVisible()
  await expect(page.getByText('Loading…', { exact: true })).toBeHidden()
  // The empty product fallback is also "ready"; this proves our sentinel snapshot
  // was applied rather than merely observing the fallback state.
  expect(await readMetricValue(page, 'Participants')).toBe('1')
})
