/**
 * 038 S0 - R6 and R8, MEASUREMENTS (F-038-5). Not a sweep of the catalogue: only the surfaces that open cheaply are
 * opened, in the default theme, at one viewport. The catalogue keys without a way to open them are listed by name,
 * so the numbers below cannot be read as coverage of all 16.
 */
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import { boot, pushEvent, ringSnapshot, screenNodes, txUpdated } from './stand.js'

type Recipe = { open: (page: Page) => Promise<void>; visible: string }
type GeoSim = {
  openNodeCard: (o: { nodeId: string; anchor: { x: number; y: number } | null }) => void
  showEdgeTooltip: (e: Record<string, unknown>) => void
}

/** Ways to put a surface on screen that S0 has. S2 owns the full registry. */
const RECIPES: Record<string, Recipe> = {
  'top-hud-stack': { open: async () => {}, visible: '.ds-ov-top .ds-ov-top-stack' },
  'bottom-hud-stack': { open: async () => {}, visible: '[data-testid="actionbar-payment"]' },
  'wm-interact-window': {
    open: async (page) => page.locator('[data-testid="actionbar-payment"]').click(),
    visible: '.ws-shell',
  },
  'wm-inspector-window': {
    open: async (page) => {
      const at = (await screenNodes(page)).get('a')!
      await page.evaluate((anchor) => (window as unknown as { __geoSim: GeoSim }).__geoSim.openNodeCard({ nodeId: 'a', anchor }), at)
    },
    visible: '.ws-shell',
  },
  'edge-tooltip': {
    open: async (page) => {
      const n = await screenNodes(page)
      const [a, b] = [n.get('a')!, n.get('b')!]
      await page.evaluate((e) => (window as unknown as { __geoSim: GeoSim }).__geoSim.showEdgeTooltip(e), {
        key: 'a→b', fromId: 'a', toId: 'b', amountText: '10.00 UAH', screenX: (a.x + b.x) / 2, screenY: (a.y + b.y) / 2,
        trustLimit: '100', used: '10', available: '90', edgeStatus: 'active',
      })
    },
    visible: '.ds-ov-tooltip',
  },
  'canvas-floating-labels-overlay': {
    open: async (page) => { await pushEvent(page, txUpdated(['a', 'b'])) },
    visible: '.floating-label',
  },
}

const NO_RECIPE_IN_S0 = [
  'interact-select-dropdown', 'hud-dropdown', 'success-toast', 'error-toast', 'interact-history-overlay', 'dev-perf-overlay',
  'canvas-labels-overlay', 'loading-message-overlay', 'error-message-overlay', 'real-metrics-panel',
]

/**
 * The product's catalogue, the single owner of the list. Loaded at run time: the source tree is written for the
 * bundler's module resolution and does not type-check inside the e2e program (NodeNext).
 */
async function catalogueKeys(): Promise<string[]> {
  const mod = (await import(new URL('../../src/ui-kit/overlaySurfaceCatalog.ts', import.meta.url).href)) as { overlaySurfaceCatalog: Record<string, unknown> }
  return Object.keys(mod.overlaySurfaceCatalog)
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()
  const by = (impact: string) => r.violations.filter((v) => v.impact === impact).map((v) => `${v.id}×${v.nodes.length}`)
  const targets = r.violations
    .filter((v) => v.impact === 'critical' || v.impact === 'serious')
    .flatMap((v) => v.nodes.map((n) => `${v.id}: ${n.target.join(' ')}`))
  return {
    critical: by('critical'), serious: by('serious'), moderate: by('moderate'), minor: by('minor'), targets,
    // "incomplete" is what axe could not decide (typically contrast over a canvas): not a pass, not a violation.
    incomplete: r.incomplete.map((v) => `${v.id}×${v.nodes.length}`), passes: r.passes.length,
  }
}

test('R6 - measurement: which catalogue surfaces S0 can open; the rest are named, not hidden', async () => {
  const keys = await catalogueKeys()
  expect(Object.keys(RECIPES).filter((k) => !keys.includes(k)), 'every recipe names a surface of the catalogue').toEqual([])
  const covered = keys.filter((k) => k in RECIPES)
  const missing = keys.filter((k) => !(k in RECIPES))
  console.log('[038 R6]', JSON.stringify({ catalogue: keys.length, covered, missing }))
  expect(keys.length, 'the catalogue has 16 surface types').toBe(16)
  expect(missing, 'every surface without a recipe is listed by name').toEqual(NO_RECIPE_IN_S0)
})

for (const [key, recipe] of Object.entries(RECIPES)) {
  test(`R8 - measurement: axe WCAG 2.1 A/AA with "${key}" on screen (default theme, 1280x720)`, async ({ page }) => {
    await boot(page, { snapshot: ringSnapshot(), clock: false })
    await recipe.open(page)
    await expect(page.locator(recipe.visible).first(), `the surface ${key} is on screen`).toBeVisible()
    const row = { surface: key, theme: await page.locator('.root').getAttribute('data-theme'), ...(await axe(page)) }
    console.log('[038 R8]', JSON.stringify(row))
    test.info().annotations.push({ type: '038-R8', description: JSON.stringify(row) })
    expect(row.passes, 'counter-check: axe did evaluate rules on this page').toBeGreaterThan(0)
  })
}
