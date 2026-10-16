/**
 * 039 slice B, PR 3 - which theme the running app shows, and what it writes back (spec 039, "Решения по проекту
 * среза B", point 6; owner's decision R1): `saas` (the default) and `hud`; explicit URL -> stored choice -> `saas`; a
 * removed or unknown id becomes `saas` - in storage rewritten once, in the URL by `replaceState` without a reload;
 * `popstate` follows the same rules, and the data palette of PR 1 is resolved again after such a switch.
 *
 * The rules themselves are walked case by case at the unit tier (`src/composables/useUiTheme.test.ts`); here they are
 * held in the real page: the root's wiring of `popstate`, the real `localStorage`, the attribute on `.root`.
 *
 * LIMITS. `boot()` clears `localStorage` in an init script on every navigation, so a stored value is seeded by a
 * second init script and a reload is not exercised here (a reload is a first load over storage: the unit tier). The
 * re-resolution of the palette is seen through the legend's swatch; that the canvas reads the same palette is
 * `data-colours.qa.ts`, test 4.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, loopCounters, ringSnapshot, waitLoopAsleep } from './stand.js'

const KEY = 'geo.uiTheme'
const root = (page: Page) => page.locator('.root')
const themeParam = (page: Page) => page.evaluate(() => new URLSearchParams(window.location.search).get('theme'))
const stored = (page: Page, key = KEY) => page.evaluate((k) => localStorage.getItem(k), key)
const switcher = (page: Page) => page.getByRole('combobox', { name: 'UI theme' })

/** A stored value for the next navigation only (the stand's own init script has cleared storage just before). */
const seedStored = (value: string) => async (page: Page) => {
  await page.addInitScript(([k, v]) => {
    // Only once per page: a value rewritten by the app must not be re-seeded by a later navigation of the same page.
    if (sessionStorage.getItem('p039b3-seeded')) return
    sessionStorage.setItem('p039b3-seeded', '1')
    localStorage.setItem(k!, v!)
  }, [KEY, value])
}

test('039 B3: the first visit shows saas and writes nothing; the switcher offers exactly saas and hud', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false })
  await expect(root(page)).toHaveAttribute('data-theme', 'saas')
  expect(await stored(page), 'the default is not written into storage').toBeNull()
  expect(await themeParam(page), 'nor into the URL').toBeNull()
  // Counter-check of the reader: the stand's own keys are in this same storage.
  expect(await stored(page, 'geo.sim.v2.apiBase')).toBe('/api/v1')
  const offered = await switcher(page).locator('option').evaluateAll((els) => els.map((o) => (o as HTMLOptionElement).value))
  expect(offered).toEqual(['saas', 'hud'])
})

test('039 B3: a stored shadcn and ?theme=library both land on saas - storage rewritten once, URL replaced without a reload', async ({ page }) => {
  let loads = 0
  page.on('load', () => { loads += 1 })
  await boot(page, { snapshot: ringSnapshot(), clock: false, query: '&theme=library', beforeLoad: seedStored('shadcn') })
  await expect(root(page)).toHaveAttribute('data-theme', 'saas')
  expect(await stored(page), 'the stored shadcn is rewritten to saas').toBe('saas')
  expect(await themeParam(page), 'the removed theme of the URL is replaced').toBe('saas')
  expect(new URLSearchParams(await page.evaluate(() => window.location.search)).get('mode'), 'other parameters are kept').toBe('real')
  expect(loads, 'no reload: one load event, the first one').toBe(1)
})

test('039 B3: ?theme=hud beats a stored saas; back/forward follow the URL, then the stored choice, and re-resolve the palette; a switch writes both', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false, query: '&theme=hud', beforeLoad: seedStored('saas') })
  await expect(root(page)).toHaveAttribute('data-theme', 'hud')
  expect(await stored(page), 'a URL does not write the stored choice').toBe('saas')

  // A second entry without the parameter (pushState fires no popstate: the theme stays), then back and forward.
  await page.evaluate(() => {
    const u = new URL(window.location.href)
    u.searchParams.delete('theme')
    history.pushState(history.state, '', u.toString())
  })
  await expect(root(page)).toHaveAttribute('data-theme', 'hud')

  // The palette is resolved on mount and on a theme change only: a token changed now shows up only if the popstate
  // switch below resolves it again.
  const swatch = () => page.locator('[data-testid="legend"] [data-legend-role="neutral"] [data-legend-swatch]').evaluate((el) => getComputedStyle(el).backgroundColor)
  await waitLoopAsleep(page)
  const neutralBefore = await swatch()
  await page.addStyleTag({ content: '.root { --ds-data-neutral: #00ff00 !important; }' })
  expect(await swatch(), 'counter-check: nothing follows the token by itself').toBe(neutralBefore)

  await page.evaluate(() => history.back())
  await expect.poll(() => themeParam(page)).toBe('hud')
  await expect(root(page), 'back to the entry with ?theme=hud').toHaveAttribute('data-theme', 'hud')
  const draws = (await loopCounters(page)).draws
  await page.evaluate(() => history.forward())
  await expect(root(page), 'forward to the entry without the parameter: the stored choice').toHaveAttribute('data-theme', 'saas')
  await expect.poll(async () => (await loopCounters(page)).draws - draws, { message: 'redrawn after the popstate switch', timeout: 15_000 }).toBeGreaterThan(0)
  await expect.poll(swatch, { message: 'the legend reads the palette resolved again after the popstate switch' }).toBe('rgb(0, 255, 0)')
  expect(await stored(page), 'back/forward write nothing').toBe('saas')

  await switcher(page).selectOption('hud')
  await expect(root(page)).toHaveAttribute('data-theme', 'hud')
  expect([await stored(page), await themeParam(page)], 'a switch to hud: stored and in the URL').toEqual(['hud', 'hud'])
  await switcher(page).selectOption('saas')
  await expect(root(page)).toHaveAttribute('data-theme', 'saas')
  expect([await stored(page), await themeParam(page)], 'a switch to the default: stored, and the parameter dropped').toEqual(['saas', null])
})
