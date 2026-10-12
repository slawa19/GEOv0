/**
 * 039 T3901 - R2 (legend), the browser half of R3 (data colour tokens, the same in every theme) and of R4 (what the
 * switcher does to the page), plus the measurement P-9 asks for: does the canvas follow the theme today.
 *
 * Corrected forms (P-2, P-6): the legend has positive content and a counted number of entries, with computed colours
 * read in the browser; tokens `--ds-data-*` resolve to the same value in every theme, in the SAME mounted application
 * after a live switch; the stand's matrix is two themes.
 *
 * LIMITS. (1) The legend's selector and the token names are the spec's (`[data-testid="legend"]`, `--ds-data-debtor`,
 * `-creditor`, `-frozen`, `-hub`): a fix that names them otherwise leaves these tests red on a correct product - the
 * names are the contract being reproduced. (2) "Legend and canvas use one source" cannot be judged while there is no
 * legend: only the canvas side is measured. (3) The empty-scene exception of the legend (P-2) is not exercised - there
 * is nothing to except yet. (4) Contrast is not here: axe runs in the stand's own matrix (`../surfaces.qa.ts`).
 *
 * SLICE B must turn the three red tests green (legend, data tokens, switcher); plain failing tests, no `test.fail`.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, loopCounters, ringSnapshot, waitLoopAsleep } from '../stand.js'

const THEMES = ['hud', 'saas', 'library', 'shadcn'] as const
const DATA_TOKENS = ['--ds-data-debtor', '--ds-data-creditor', '--ds-data-frozen', '--ds-data-hub'] as const

const note = (id: string, row: unknown) => {
  console.log(`[039 ${id}]`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-${id}`, description: JSON.stringify(row) })
}

async function switchTheme(page: Page, theme: string): Promise<void> {
  await page.getByRole('combobox', { name: 'UI theme' }).selectOption(theme)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
}

const tokensAt = (page: Page, names: readonly string[]) =>
  page.evaluate((names) => {
    const read = (el: Element) => Object.fromEntries(names.map((n) => [n, getComputedStyle(el).getPropertyValue(n).trim()]))
    return { root: read(document.querySelector('.root')!), html: read(document.documentElement) }
  }, [...names])

test.beforeEach(async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false })
})

test('039 R2 - a legend is on screen: four entries with text, each with a colour swatch (today: no legend)', async ({ page }) => {
  await expect(page.locator('canvas.canvas').first(), 'counter-check: the scene is on screen').toBeVisible()
  const legend = page.locator('[data-testid="legend"]')
  const seen = await legend.count()
  note('R2 legend', { legends: seen })
  expect(seen, 'elements [data-testid="legend"] on a scene of six participants').toBe(1)
  const entries = await legend.locator('[data-legend-entry], li').evaluateAll((els) => els.map((el) => (el.textContent ?? '').trim()))
  note('R2 legend entries', entries)
  expect(entries.filter((t) => t.length > 0).length, 'entries of the legend with text (debtor, creditor, frozen, hub)').toBeGreaterThanOrEqual(4)
})

test('039 R3 (browser) - the four data colour tokens resolve, and to the same value in every theme of the same mounted app (today: not declared)', async ({ page }) => {
  const byTheme: Record<string, Awaited<ReturnType<typeof tokensAt>>> = {}
  for (const theme of THEMES) {
    await switchTheme(page, theme)
    byTheme[theme] = await tokensAt(page, DATA_TOKENS)
  }
  note('R3 data tokens by theme', byTheme)
  // Counter-check of the reader: an existing token of the design system is read in the same way and is not empty.
  expect((await tokensAt(page, ['--ds-text-1'])).root['--ds-text-1'], 'counter-check: an existing token is read').not.toBe('')
  for (const token of DATA_TOKENS) {
    const values = THEMES.map((t) => byTheme[t]!.root[token])
    expect.soft(values.every((v) => v !== ''), `${token} is declared in every theme (values: ${JSON.stringify(values)})`).toBe(true)
    expect.soft(new Set(values).size, `${token} has ONE value across the themes`).toBe(1)
  }
})

test('039 R4 (browser) - the switcher of the running app offers two themes, shadcn is not one of them (today: four)', async ({ page }) => {
  const options = await page.getByRole('combobox', { name: 'UI theme' }).locator('option').evaluateAll((els) => els.map((o) => (o as HTMLOptionElement).value))
  note('R4 switcher options', options)
  expect(options.length, 'counter-check: the switcher is found and has options').toBeGreaterThan(0)
  expect.soft(options, 'options of the theme switcher').toHaveLength(2)
  expect.soft(options).not.toContain('shadcn')
})

test('039 measurement (P-6, P-9) - a live switch of the theme: what the canvas does, and where its colour tokens are read from', async ({ page }) => {
  const FX = ['--ds-fx-tx-spark-core', '--ds-fx-tx-spark-trail', '--ds-fx-clearing-credit', '--ds-fx-clearing-debt', '--ds-glint', '--ds-text-1'] as const
  const rows: Record<string, unknown> = {}
  await page.evaluate(() => { (window as unknown as { __p039: Record<string, Uint8ClampedArray> }).__p039 = {} })
  for (const theme of THEMES) {
    await switchTheme(page, theme)
    await waitLoopAsleep(page)
    const before = (await loopCounters(page)).draws
    // A resize makes the product redraw the scene: without a draw after the switch, equal pixels would prove nothing.
    // Two steps, each awaited: two resizes inside one frame are no resize at all.
    const vp = page.viewportSize()!
    await page.setViewportSize({ width: vp.width, height: vp.height - 8 })
    await expect.poll(async () => (await loopCounters(page)).draws - before, { message: `the scene is redrawn after the switch to ${theme}`, timeout: 15_000 }).toBeGreaterThan(0)
    await waitLoopAsleep(page)
    const mid = (await loopCounters(page)).draws
    await page.setViewportSize(vp)
    await expect.poll(async () => (await loopCounters(page)).draws - mid, { message: `the scene is redrawn at the original size (${theme})`, timeout: 15_000 }).toBeGreaterThan(0)
    await waitLoopAsleep(page)
    const px = await page.evaluate((theme) => {
      const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
      const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data
      const store = (window as unknown as { __p039: Record<string, Uint8ClampedArray> }).__p039
      store[theme] = d
      const ref = store.hud!
      let differing = 0
      let painted = 0
      for (let i = 0; i < d.length; i += 4) {
        if (d[i + 3]! > 0) painted += 1
        if (d[i] !== ref[i] || d[i + 1] !== ref[i + 1] || d[i + 2] !== ref[i + 2] || d[i + 3] !== ref[i + 3]) differing += 1
      }
      return { pixels: d.length / 4, painted, differingFromHud: ref.length === d.length ? differing : -1 }
    }, theme)
    const tokens = await tokensAt(page, FX)
    rows[theme] = { drawsAfterSwitch: (await loopCounters(page)).draws - before, canvas: px, tokensAtRoot: tokens.root, tokensAtHtml: tokens.html }
  }
  note('theme switch vs canvas', rows)
  const hud = rows.hud as { canvas: { painted: number } }
  expect(hud.canvas.painted, 'counter-check: the base canvas has painted pixels').toBeGreaterThan(0)
})
