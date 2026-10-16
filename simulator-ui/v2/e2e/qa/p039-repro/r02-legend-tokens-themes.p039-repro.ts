/**
 * 039 T3901 - R2 (legend), the browser half of R3 (data colour tokens, the same in every theme) and of R4 (what the
 * switcher offers), plus the measurement P-9 asks for: does the canvas follow the theme today.
 *
 * Corrected forms (P-2, P-6) and the review of 2026-10-12:
 *  - R2: the legend is VISIBLE; it has an entry for each known role (debtor, creditor, frozen, hub), found by role and
 *    not by counting strings; every entry carries words; the swatch of each coloured role has, as its computed
 *    colour, the RESOLVED value of that role's data token. A hidden legend, or four entries saying "x", fails.
 *  - R3: the tokens resolve to one value in every theme the switcher OFFERS (switched live, in the same mounted
 *    application), and, apart from the UI, under every theme id the design system has tokens for - the frozen
 *    `shadcn` included (the attribute is set on the root directly: no option of the switcher is needed for it).
 *  - R4: two options, `shadcn` not among them (since slice B, PR 3: exactly `saas` and `hud`).
 * The names looked for (selectors, role ids, token names) are in `contract.ts` - the one place a slice renames them.
 *
 * SLICE B must turn the three red tests green (legend, data tokens, switcher); plain failing tests, no `test.fail`.
 *
 * STATE AFTER SLICE B, PR 1 (2026-10-12). R2 and the browser half of R3 are GREEN and were re-read against the design
 * decisions of slice B:
 *  - R2 asked for all four entries on the ring of six even persons. Decision 7 says an entry is shown ONLY for a
 *    feature the snapshot contains (and a hub is only `type: "hub"`), so the first form now contradicts the
 *    contract: the scene of R2 is the ring with a net debtor, a net creditor, a frozen participant and a hub in it,
 *    and the ring as it is must show NO debtor, creditor, frozen or hub entry (the path round the first form: a
 *    legend that always lists everything).
 *  - R3: the neutral token is walked with the four.
 * The counted checks of the same things are in the stand's gate: `../data-colours.qa.ts`.
 * R4 (the switcher) was RED until PR 3 of the slice (themes and the stand, 2026-10-16): it now asks for exactly
 * `saas` and `hud` (decision 6: `library` left the switcher with `shadcn`); the counted check is `../themes.qa.ts`.
 *
 * LIMITS. (1) "The canvas paints with the same colours as the legend" is NOT judged: the legend's swatch is compared
 * with the token, and whether the renderer consumes that token is the mapping tier's (`src/p039-repro/r03-...`) and the
 * implementing slice's to show. (2) The empty-scene exception of the legend (P-2) is not exercised. (3) Words of an
 * entry are required to exist (two letters or more), not to be right - their wording is the owner's. (4) Contrast is
 * not here: axe runs in the stand's own matrix (`../surfaces.qa.ts`).
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, loopCounters, ringSnapshot, waitLoopAsleep } from '../stand.js'
import { ALL_THEME_IDS, COLOURED_ROLES, DATA_TOKEN, LEGEND, LEGEND_ENTRIES_ALL, LEGEND_ROLES, NEUTRAL_TOKEN, THEME } from './contract.js'

const note = (id: string, row: unknown) => {
  console.log(`[039 ${id}]`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-${id}`, description: JSON.stringify(row) })
}

const switcher = (page: Page) => page.getByRole(THEME.switcher.role, { name: THEME.switcher.name })

/** The themes the switcher of the running app really offers - read from the UI, never assumed. */
async function offeredThemes(page: Page): Promise<string[]> {
  const options = await switcher(page).locator('option').evaluateAll((els) => els.map((o) => (o as HTMLOptionElement).value))
  if (options.length === 0) throw new Error('039: the theme switcher offers no option - the test has nothing to iterate over')
  return options
}

async function switchTheme(page: Page, theme: string): Promise<void> {
  await switcher(page).selectOption(theme)
  await expect(page.locator(THEME.root)).toHaveAttribute(THEME.attribute, theme)
}

/** Raw values of custom properties at the themed root. */
const tokensAtRoot = (page: Page, names: readonly string[]) =>
  page.evaluate(({ names, root }) => {
    const el = document.querySelector(root)!
    return Object.fromEntries(names.map((n) => [n, getComputedStyle(el).getPropertyValue(n).trim()]))
  }, { names: [...names], root: THEME.root })

/** A token as the browser resolves it to a colour (`rgb(...)`), read through a probe element inside the themed root. */
const resolvedColour = (page: Page, token: string) =>
  page.evaluate(({ token, root }) => {
    const probe = document.createElement('span')
    probe.style.backgroundColor = `var(${token})`
    document.querySelector(root)!.appendChild(probe)
    const c = getComputedStyle(probe).backgroundColor
    probe.remove()
    return c
  }, { token, root: THEME.root })

/** The ring of six with the four features R2 names put into it: a net debtor, a net creditor, a frozen participant, a hub. */
function ringWithRoles() {
  const s = ringSnapshot()
  Object.assign(s.nodes[0]!, { net_sign: -1, net_balance_atoms: '1000' })
  Object.assign(s.nodes[1]!, { net_sign: 1, net_balance_atoms: '1000' })
  Object.assign(s.nodes[2]!, { status: 'frozen' })
  Object.assign(s.nodes[3]!, { type: 'hub' })
  return s
}

test.beforeEach(async ({ page }, info) => {
  await boot(page, { snapshot: info.title.startsWith('039 R2 -') ? ringWithRoles() : ringSnapshot(), clock: false })
})

test('039 R2 - a legend is visible: an entry in words for debtor, creditor, frozen and hub (the scene has each), and each colour swatch has the colour of its data token', async ({ page }) => {
  await expect(page.locator('canvas.canvas').first(), 'counter-check: the scene is on screen').toBeVisible()
  const legend = page.locator(LEGEND.root)
  note('R2 legend', { legends: await legend.count() })
  await expect(legend, `the legend ${LEGEND.root} is visible on a scene of six participants`).toBeVisible({ timeout: 2000 })
  const rows: Record<string, unknown> = {}
  for (const role of LEGEND_ROLES) {
    const entry = legend.locator(LEGEND.entry(role))
    await expect.soft(entry, `the legend has a visible entry for the role "${role}"`).toBeVisible({ timeout: 1000 })
    const text = (await entry.count()) ? ((await entry.first().textContent()) ?? '').trim() : ''
    expect.soft(/\p{L}{2,}/u.test(text), `the entry for "${role}" says what it is in words (text: "${text}")`).toBe(true)
    rows[role] = { text }
    if (!COLOURED_ROLES.includes(role)) continue
    const swatch = entry.locator(LEGEND.swatch)
    await expect.soft(swatch, `the entry for "${role}" has a visible colour swatch`).toBeVisible({ timeout: 1000 })
    const token = await resolvedColour(page, DATA_TOKEN[role])
    const shown = (await swatch.count()) ? await swatch.first().evaluate((el) => getComputedStyle(el).backgroundColor) : ''
    rows[role] = { text, swatch: shown, token: DATA_TOKEN[role], tokenResolved: token }
    expect.soft(token, `the token ${DATA_TOKEN[role]} resolves to a colour`).not.toMatch(/^(|rgba\(0, 0, 0, 0\)|transparent)$/)
    expect.soft(shown, `the swatch of "${role}" has the resolved colour of ${DATA_TOKEN[role]}`).toBe(token)
  }
  note('R2 legend entries', rows)
  const colours = COLOURED_ROLES.map((r) => (rows[r] as { swatch?: string }).swatch)
  expect.soft(new Set(colours).size, 'debtor, creditor and frozen are three different colours').toBe(COLOURED_ROLES.length)
  const shown = await legend.locator('[data-legend-role]').evaluateAll((els) => els.map((el) => el.getAttribute('data-legend-role')))
  expect.soft(shown, 'entries in the fixed order, only of what this scene contains (no unknown, no organisation)').toEqual(LEGEND_ENTRIES_ALL.filter((e) => e !== 'unknown' && e !== 'organisation'))
})

test('039 R2 (counter-form, slice B) - the ring of six even persons shows NO entry for a debtor, a creditor, a freeze or a hub: the legend lists what the scene contains', async ({ page }) => {
  const legend = page.locator(LEGEND.root)
  await expect(legend).toBeVisible({ timeout: 2000 })
  for (const role of LEGEND_ROLES) await expect.soft(legend.locator(LEGEND.entry(role)), `no entry for "${role}" in a scene without one`).toHaveCount(0)
  expect(await legend.locator('[data-legend-role]').count(), 'counter-check: the legend does have entries (even, person)').toBe(2)
})

test('039 R3 (browser) - the data colour tokens resolve to ONE value: in every theme the switcher offers (switched live), and under every theme id that has tokens, the frozen one included (today: not declared)', async ({ page }) => {
  const names = [...Object.values(DATA_TOKEN), NEUTRAL_TOKEN]
  const offered = await offeredThemes(page)
  const viaUi: Record<string, Record<string, string>> = {}
  for (const theme of offered) {
    await switchTheme(page, theme)
    viaUi[theme] = await tokensAtRoot(page, names)
  }
  // Apart from the UI: the attribute the switcher writes, set directly. A theme that is out of the switcher keeps its tokens.
  const viaAttribute: Record<string, Record<string, string>> = {}
  for (const theme of ALL_THEME_IDS) {
    viaAttribute[theme] = await page.evaluate(({ theme, root, attribute, names }) => {
      const el = document.querySelector<HTMLElement>(root)!
      const was = el.getAttribute(attribute)
      el.setAttribute(attribute, theme)
      const out = Object.fromEntries(names.map((n) => [n, getComputedStyle(el).getPropertyValue(n).trim()]))
      if (was === null) el.removeAttribute(attribute)
      else el.setAttribute(attribute, was)
      return out
    }, { theme, root: THEME.root, attribute: THEME.attribute, names })
  }
  note('R3 data tokens', { offered, viaUi, viaAttribute })
  // Counter-check of the reader: an existing token of the design system is read in the same way and is not empty.
  expect((await tokensAtRoot(page, ['--ds-text-1']))['--ds-text-1'], 'counter-check: an existing token is read').not.toBe('')
  expect(offered.length, 'counter-check: the switcher offers themes to walk').toBeGreaterThan(0)
  for (const token of names) {
    const values = [...offered.map((t) => viaUi[t]![token]!), ...ALL_THEME_IDS.map((t) => viaAttribute[t]![token]!)]
    expect.soft(values.every((v) => v !== ''), `${token} is declared under every offered theme and every theme id (values: ${JSON.stringify(values)})`).toBe(true)
    expect.soft(new Set(values).size, `${token} has ONE value across the themes`).toBe(1)
  }
})

test('039 R4 (browser) - the switcher of the running app offers two themes, saas and hud; shadcn and library are not among them (base: four)', async ({ page }) => {
  const options = await offeredThemes(page)
  note('R4 switcher options', options)
  expect.soft(options, 'options of the theme switcher').toHaveLength(2)
  expect.soft(options).toEqual(['saas', 'hud'])
  expect.soft(options).not.toContain('shadcn')
  expect.soft(options).not.toContain('library')
})

test('039 measurement (P-6, P-9) - a live switch through the OFFERED themes: what the canvas does, and where its colour tokens are read from', async ({ page }) => {
  const FX = ['--ds-fx-tx-spark-core', '--ds-fx-tx-spark-trail', '--ds-fx-clearing-credit', '--ds-fx-clearing-debt', '--ds-glint', '--ds-text-1'] as const
  const offered = await offeredThemes(page)
  const first = offered[0]!
  const rows: Record<string, unknown> = {}
  await page.evaluate(() => { (window as unknown as { __p039: Record<string, Uint8ClampedArray> }).__p039 = {} })
  for (const theme of offered) {
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
    const px = await page.evaluate(({ theme, first }) => {
      const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
      const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data
      const store = (window as unknown as { __p039: Record<string, Uint8ClampedArray> }).__p039
      store[theme] = d
      const ref = store[first]!
      let differing = 0
      for (let i = 0; i < d.length; i += 4) if (d[i] !== ref[i] || d[i + 1] !== ref[i + 1] || d[i + 2] !== ref[i + 2] || d[i + 3] !== ref[i + 3]) differing += 1
      return { pixels: d.length / 4, differingFromFirstTheme: ref.length === d.length ? differing : -1 }
    }, { theme, first })
    const html = await page.evaluate((names) => Object.fromEntries(names.map((n) => [n, getComputedStyle(document.documentElement).getPropertyValue(n).trim()])), [...FX])
    rows[theme] = { drawsAfterSwitch: (await loopCounters(page)).draws - before, canvas: px, tokensAtRoot: await tokensAtRoot(page, FX), tokensAtHtml: html }
  }
  note('theme switch vs canvas', { offered, comparedWith: first, rows })
  expect(Object.keys(rows).length, 'counter-check: every offered theme was walked').toBe(offered.length)
})
