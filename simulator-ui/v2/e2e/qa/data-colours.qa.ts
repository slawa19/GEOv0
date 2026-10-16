/**
 * 039 slice B, PR 1 - the colour language in a real page: the data tokens, the legend, the canvas and the node card.
 * Real pipeline over the mocked backend, real clock (nothing here is about time), Chromium without a GPU.
 *
 * What each test holds:
 *  1. the five data tokens resolve to ONE value under every theme - switched live through the switcher, and set as
 *     the attribute for every theme id the design system has tokens for;
 *  2. the legend says what the scene contains and nothing else, in the fixed order, and each colour swatch has the
 *     resolved colour of its token; a scene without a hub has no Hub entry;
 *  3. a live theme switch redraws the scene BY ITSELF and the base canvas is byte-identical between `saas` and `hud`;
 *     names are text of the theme and follow it;
 *  4. the canvas, the legend and the node card read ONE resolved palette: a token changed under the running app moves
 *     all three together after the next resolution - and moves nothing that is not of that role.
 *
 * LIMITS. (a) Test 3 compares the BASE canvas, not the page: DOM and FX differ between themes by design. Equal
 * pixels alone would prove nothing if the product had not redrawn - the draw counter is asserted first, and test 4
 * is the counter-check that the same reader of pixels does see a colour change. (b) Test 4 changes a token with an
 * injected style rule: it shows that the consumers share a source, not that the stylesheet's values are good ones
 * (contrast is measured in `visual-language.md`; axe runs in `surfaces.qa.ts`). (c) Pixels are read in boxes round
 * node centres; the sign glyph is not judged pixel by pixel (its drawing is `src/render/nodePainter.test.ts`).
 * (d) The empty scene (no legend) is held at the component level (`SimulatorAppRoot.interact.test.ts`), not here.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, loopCounters, ringSnapshot, screenNodes, waitLoopAsleep, type QaSnapshot } from './stand.js'

const DATA_TOKENS = ['--ds-data-debtor', '--ds-data-creditor', '--ds-data-neutral', '--ds-data-frozen', '--ds-data-hub'] as const
/** Every theme id the design system has TOKENS for: `library` and `shadcn` are frozen (out of the app, tokens kept). */
const ALL_THEME_IDS = ['hud', 'saas', 'library', 'shadcn'] as const
const BASE = 'canvas.canvas:not(.canvas-fx)'

function node(id: string, name: string, o: { type?: string; status?: string; net_sign: -1 | 0 | 1 | null; key: string }) {
  const business = o.type === 'business'
  return {
    id, name, type: o.type ?? 'person', status: o.status ?? 'active', links_count: 2,
    net_balance_atoms: o.net_sign === null ? null : o.net_sign === 0 ? '0' : '3000', net_sign: o.net_sign, net_balance: null,
    // The colour key is what the backend gives such a node today; it must not matter.
    viz_color_key: o.key, viz_shape_key: business ? 'rounded-rect' : 'circle', viz_size: business ? { w: 26, h: 22 } : { w: 20, h: 20 },
    viz_badge_key: null,
  }
}
const link = (source: string, target: string, used = '0') => ({
  source, target, trust_limit: '100', used, available: String(100 - Number(used)), status: 'active',
  viz_color_key: 'default', viz_width_key: 'mid', viz_alpha_key: 'hi',
})

/** Every feature the legend can name, once or twice. */
function rolesSnapshot(): QaSnapshot {
  return {
    equivalent: 'UAH', generated_at: '2026-02-01T00:00:00.000Z',
    palette: { default: { color: '#64748b', label: 'Default' } }, limits: { max_particles: 120 },
    nodes: [
      node('cr', 'Creditor', { net_sign: 1, key: 'person' }),
      node('db', 'Debtor', { net_sign: -1, key: 'debt-4' }),
      node('dbB', 'Debtor shop', { type: 'business', net_sign: -1, key: 'debt-8' }),
      node('ev', 'Even', { net_sign: 0, key: 'person' }),
      node('un', 'Unknown', { net_sign: null, key: 'person' }),
      node('fz', 'Frozen creditor', { status: 'frozen', net_sign: 1, key: 'suspended' }),
      node('hub', 'Hub', { type: 'hub', net_sign: 0, key: 'person' }),
    ],
    links: [link('cr', 'db', '30'), link('hub', 'cr'), link('hub', 'ev'), link('hub', 'fz'), link('hub', 'un'), link('hub', 'dbB'), link('ev', 'dbB')],
  }
}

const switcher = (page: Page) => page.getByRole('combobox', { name: 'UI theme' })

async function switchTheme(page: Page, theme: string): Promise<void> {
  await switcher(page).selectOption(theme)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
}

/** A token as the browser resolves it to a colour (`rgb(...)`), through a probe inside the themed root. */
const resolved = (page: Page, token: string) =>
  page.evaluate((token) => {
    const probe = document.createElement('span')
    probe.style.backgroundColor = `var(${token})`
    document.querySelector('.root')!.appendChild(probe)
    const c = getComputedStyle(probe).backgroundColor
    probe.remove()
    return c
  }, token)

/** Keep the pixels of the base canvas in the page under `key`; answer how many differ from those kept under `against`. */
const keepCanvas = (page: Page, key: string, against?: string) =>
  page.evaluate(({ key, against, selector }) => {
    const c = document.querySelector<HTMLCanvasElement>(selector)!
    const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data
    const store = ((window as unknown as { __p039px?: Record<string, Uint8ClampedArray> }).__p039px ??= {})
    // The reference is read BEFORE this frame is stored, and a frame is never compared with the key it is stored under.
    if (against === key) throw new Error(`039: the canvas kept as "${key}" would be compared with itself`)
    const ref = against ? (store[against] ?? null) : null
    if (against && !ref) throw new Error(`039: no canvas was kept as "${against}"`)
    store[key] = d
    let painted = 0
    for (let i = 3; i < d.length; i += 4) if (d[i]! > 0) painted += 1
    let differing = -1
    if (ref && ref.length === d.length) {
      differing = 0
      for (let i = 0; i < d.length; i++) if (d[i] !== ref[i]) { differing += 1; i |= 3 }
    }
    return { pixels: d.length / 4, painted, differing }
  }, { key, against, selector: BASE })

/** Pixels that differ between two kept canvases inside a box of `half` CSS px round a point (DPR 1). */
const differingRound = (page: Page, a: string, b: string, at: { x: number; y: number }, half: number) =>
  page.evaluate(({ a, b, at, half, selector }) => {
    const c = document.querySelector<HTMLCanvasElement>(selector)!
    const store = (window as unknown as { __p039px: Record<string, Uint8ClampedArray> }).__p039px
    const [pa, pb] = [store[a]!, store[b]!]
    let n = 0
    for (let y = Math.round(at.y - half); y <= Math.round(at.y + half); y++) {
      for (let x = Math.round(at.x - half); x <= Math.round(at.x + half); x++) {
        const i = (y * c.width + x) * 4
        if (pa[i] !== pb[i] || pa[i + 1] !== pb[i + 1] || pa[i + 2] !== pb[i + 2] || pa[i + 3] !== pb[i + 3]) n += 1
      }
    }
    return n
  }, { a, b, at, half, selector: BASE })

test('039 B1: the five data tokens resolve to ONE value under every theme - switched live, and by attribute for every theme id', async ({ page }) => {
  await boot(page, { snapshot: rolesSnapshot(), clock: false })
  const read = () => page.evaluate((names) => {
    const el = document.querySelector('.root')!
    return Object.fromEntries(names.map((n) => [n, getComputedStyle(el).getPropertyValue(n).trim()]))
  }, [...DATA_TOKENS])
  const offered = await switcher(page).locator('option').evaluateAll((els) => els.map((o) => (o as HTMLOptionElement).value))
  expect(offered.length, 'counter-check: the switcher offers themes to walk').toBeGreaterThan(1)
  expect(offered, 'the two themes that remain are offered, and only they (039 B3)').toEqual(['saas', 'hud'])
  const seen: Array<Record<string, string>> = []
  for (const theme of offered) {
    await switchTheme(page, theme)
    seen.push(await read())
  }
  for (const theme of ALL_THEME_IDS) {
    await page.evaluate((theme) => document.querySelector('.root')!.setAttribute('data-theme', theme), theme)
    seen.push(await read())
  }
  // Counter-check of the reader and of the walk: a token the themes DO override is read as different values.
  await page.evaluate(() => document.querySelector('.root')!.setAttribute('data-theme', 'saas'))
  const bgSaas = await page.evaluate(() => getComputedStyle(document.querySelector('.root')!).getPropertyValue('--ds-page-bg').trim())
  await page.evaluate(() => document.querySelector('.root')!.setAttribute('data-theme', 'hud'))
  const bgHud = await page.evaluate(() => getComputedStyle(document.querySelector('.root')!).getPropertyValue('--ds-page-bg').trim())
  expect(bgSaas, 'counter-check: a themed token differs between the themes').not.toBe(bgHud)

  for (const token of DATA_TOKENS) {
    const values = seen.map((row) => row[token]!)
    expect(values.every((v) => /^#[0-9a-f]{6}$/i.test(v)), `${token} is declared as #rrggbb under every theme: ${JSON.stringify(values)}`).toBe(true)
    expect(new Set(values).size, `${token} has ONE value across the themes`).toBe(1)
  }
  expect(new Set(DATA_TOKENS.map((t) => seen[0]![t])).size, 'the five tokens are five different colours').toBe(DATA_TOKENS.length)
})

test('039 B1: the legend names what the scene contains, in the fixed order, each swatch in the resolved colour of its token; no Hub entry without a hub', async ({ page }) => {
  await boot(page, { snapshot: rolesSnapshot(), clock: false })
  const legend = page.locator('[data-testid="legend"]')
  await expect(legend).toBeVisible()
  await expect(legend.locator('[data-testid="legend-title"]')).toHaveText('Overall balance · UAH')
  await expect(legend, 'the explanation in plain words is the accessible description').toHaveAccessibleDescription('Net position: what others owe minus what this participant owes.')
  const rows = await legend.locator('[data-legend-role]').evaluateAll((els) => els.map((el) => {
    const s = el.querySelector<HTMLElement>('[data-legend-swatch]')!
    const cs = getComputedStyle(s)
    return { id: el.getAttribute('data-legend-role'), sign: s.textContent, text: el.querySelector('.ds-legend__text')!.textContent, bg: cs.backgroundColor, border: cs.borderTopColor, dashed: cs.borderTopStyle === 'dashed', visible: el.getBoundingClientRect().width > 0 }
  }))
  expect(rows.map((r) => `${r.sign}|${r.text}`)).toEqual(['−|Owes more', '+|Is owed more', '0|Even overall', '?|Balance unknown', '|Frozen', '|Hub', '|Person', '|Organisation'])
  expect(rows.every((r) => r.visible), 'every entry is on screen').toBe(true)
  const by = Object.fromEntries(rows.map((r) => [r.id, r]))
  expect(by.debtor!.bg).toBe(await resolved(page, '--ds-data-debtor'))
  expect(by.creditor!.bg).toBe(await resolved(page, '--ds-data-creditor'))
  expect(by.neutral!.bg).toBe(await resolved(page, '--ds-data-neutral'))
  expect(by.unknown!.bg, 'unknown is filled like neutral; its sign tells it apart').toBe(await resolved(page, '--ds-data-neutral'))
  expect(by.frozen!.bg).toBe(await resolved(page, '--ds-data-frozen'))
  expect(by.frozen!.dashed, 'a freeze is a dashed outline in the legend too').toBe(true)
  expect(by.hub!.border, 'the hub is a ring in the hub colour').toBe(await resolved(page, '--ds-data-hub'))
  expect(by.hub!.bg, 'and is not a fill').toBe('rgba(0, 0, 0, 0)')
  expect(new Set([by.debtor!.bg, by.creditor!.bg, by.neutral!.bg, by.frozen!.bg]).size, 'four different fills').toBe(4)

  // The legend is a row of the bottom bar, above its controls, and the published height of the bar includes it.
  const geo = await page.evaluate(() => {
    const bar = [...document.querySelectorAll<HTMLElement>('.ds-ov-bottom')].find((el) => !el.classList.contains('sar-interact-history-overlay'))!
    const l = document.querySelector<HTMLElement>('[data-testid="legend"]')!.getBoundingClientRect()
    const controls = bar.querySelector<HTMLElement>('.hud-bar')!.getBoundingClientRect()
    const published = getComputedStyle(document.querySelector('.root')!).getPropertyValue('--ds-hud-bottom-stack-height').trim()
    return { inside: bar.contains(document.querySelector('[data-testid="legend"]')), legendBottom: l.bottom, controlsTop: controls.top, barHeight: Math.round(bar.getBoundingClientRect().height), published }
  })
  expect(geo.inside).toBe(true)
  expect(geo.legendBottom, 'the legend is above the controls, not over them').toBeLessThanOrEqual(geo.controlsTop)
  expect(geo.published, 'the bottom-bar measurer publishes the height WITH the legend row').toBe(`${geo.barHeight}px`)

  // A scene of six even persons: two entries, and nothing the scene does not contain.
  await page.goto('about:blank')
  await boot(page, { snapshot: ringSnapshot(), clock: false })
  await expect(page.locator('[data-testid="legend"]')).toBeVisible()
  expect(await page.locator('[data-testid="legend"] [data-legend-role]').evaluateAll((els) => els.map((el) => el.getAttribute('data-legend-role')))).toEqual(['neutral', 'person'])
})

test('039 B1: a live theme switch redraws the scene by itself and the base canvas is byte-identical between saas and hud; names are text of the theme', async ({ page }) => {
  await boot(page, { snapshot: rolesSnapshot(), clock: false, query: '&theme=saas' })
  await expect(page.locator('.root')).toHaveAttribute('data-theme', 'saas')
  // A selected node: a name label is on screen, and the selection glow (the node's own colour) is part of the frame.
  const hub = (await screenNodes(page)).get('hub')!
  await page.mouse.click(hub.x, hub.y)
  await expect(page.locator('.node-label-inner').first()).toBeVisible()
  await waitLoopAsleep(page)
  // The reference is kept under a key of its OWN ('first'): a later frame stored as 'saas' would overwrite a
  // reference kept under that name before it is read, and the return to saas would be compared with itself
  // (external review of 2026-10-12).
  const first = await keepCanvas(page, 'first')
  expect(first.painted, 'counter-check: the scene is painted').toBeGreaterThan(500)
  const labelColour = () => page.locator('.node-label-inner').first().evaluate((el) => getComputedStyle(el).color)
  const nameSaas = await labelColour()
  expect(nameSaas, 'a name is the text colour of the theme').toBe(await resolved(page, '--ds-text-1'))
  expect(await page.locator('.node-label-inner').first().getAttribute('style'), 'and carries no inline colour').toBeNull()

  for (const [to, from] of [['hud', 'saas'], ['saas', 'hud']] as const) {
    const before = (await loopCounters(page)).draws
    await switchTheme(page, to)
    // The proof that the pixels compared below are NEW: the product drew a pass after the switch, unprompted.
    await expect.poll(async () => (await loopCounters(page)).draws - before, { message: `the scene is redrawn after the switch ${from} -> ${to}`, timeout: 15_000 }).toBeGreaterThan(0)
    await waitLoopAsleep(page)
    const now = await keepCanvas(page, to, 'first')
    expect(now.painted).toBeGreaterThan(500)
    expect(now.differing, `base canvas under ${to} against the first saas frame: differing pixels`).toBe(0)
  }
  await switchTheme(page, 'hud')
  const nameHud = await labelColour()
  expect(nameHud, 'the name follows the theme').toBe(await resolved(page, '--ds-text-1'))
  expect(nameHud, 'counter-check: the two themes have different text colours').not.toBe(nameSaas)
})

test('039 B1: canvas, legend and node card read one resolved palette - a token changed under the running app moves all three, and only that role', async ({ page }) => {
  await boot(page, { snapshot: rolesSnapshot(), clock: false, query: '&theme=saas' })
  await waitLoopAsleep(page)
  const at = await screenNodes(page)
  const swatch = (role: string) => page.locator(`[data-testid="legend"] [data-legend-role="${role}"] [data-legend-swatch]`).evaluate((el) => getComputedStyle(el).backgroundColor)
  const debtorBefore = await swatch('debtor')
  const creditorBefore = await swatch('creditor')
  await keepCanvas(page, 'before')

  // The token changes; nothing may follow until the app resolves its colours again (it reads tokens on mount and on a theme change only).
  await page.addStyleTag({ content: '.root { --ds-data-debtor: #00ff00 !important; }' })
  expect(await resolved(page, '--ds-data-debtor'), 'counter-check: the token really changed in the page').toBe('rgb(0, 255, 0)')
  const draws = (await loopCounters(page)).draws
  await switchTheme(page, 'hud')
  await expect.poll(async () => (await loopCounters(page)).draws - draws, { message: 'redrawn after the switch', timeout: 15_000 }).toBeGreaterThan(0)
  await waitLoopAsleep(page)
  await keepCanvas(page, 'after')

  expect(await swatch('debtor'), 'legend: the debtor swatch has the new colour').toBe('rgb(0, 255, 0)')
  expect(await swatch('creditor'), 'legend: the creditor swatch did not move').toBe(creditorBefore)
  expect(debtorBefore).not.toBe('rgb(0, 255, 0)')
  for (const id of ['db', 'dbB']) expect(await differingRound(page, 'before', 'after', at.get(id)!, 9), `canvas: the net debtor ${id} is repainted`).toBeGreaterThan(40)
  for (const id of ['cr', 'ev', 'un', 'fz']) expect(await differingRound(page, 'before', 'after', at.get(id)!, 9), `canvas: ${id} is not a debtor and is not repainted`).toBe(0)

  // The card of a debtor: its swatch and its balance take the same value.
  const db = at.get('db')!
  await page.mouse.dblclick(db.x, db.y)
  const card = page.locator('.ws-shell[data-win-type="node-card"]')
  await expect(card).toBeVisible()
  await expect(card.locator('[data-testid="node-card-role"]')).toHaveText(/Owes more/)
  expect(await card.locator('[data-testid="node-card-role-swatch"]').evaluate((el) => getComputedStyle(el).backgroundColor)).toBe('rgb(0, 255, 0)')
  expect(await card.locator('.ds-ov-node-card__balance').evaluate((el) => getComputedStyle(el).color)).toBe('rgb(0, 255, 0)')
})
