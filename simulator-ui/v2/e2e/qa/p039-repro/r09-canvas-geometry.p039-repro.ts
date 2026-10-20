/**
 * 039 T3901 - R9 (the canvas is the main thing on screen).
 *
 * Corrected form (P-2, P-7): the numerator is the canvas area that is actually UNOBSCURED - the canvas element itself
 * is positioned over the whole root (`App.css`), so its rectangle "passes" whatever lies over it; windows and toasts
 * must EXIST before their intersections are judged.
 *
 * Two kinds of result, kept apart:
 *  - MEASUREMENT: the unobscured share at 1366x768 and 1440x900, navigator closed and open. The spec's 85 % is a design
 *    budget; it is not asserted here. The rows the HUD is made of are reported with their heights, so that a threshold
 *    can be derived from what two bars really need.
 *  - REPRODUCER: no window and no toast lies over a HUD panel or over another window. Red only if an overlap is measured.
 *    Which nodes lie under a toast is reported, not asserted: the spec's rule names HUD rows and windows.
 *
 * "Unobscured" = the element under the point is a canvas (`document.elementFromPoint` on a 4 px grid). LIMITS: an
 * element with `pointer-events: none` over the canvas (labels, the FX canvas) does not count as covering it; a
 * translucent panel counts as covering it fully; hit-testing is not visibility. Theme `hud`, the ring of six, Interact.
 * Long names, wrapped rows and growing content (P-7) are NOT walked: the ring's names are one letter.
 *
 * SLICE E must turn the four window tests green; plain failing tests, no `test.fail`. The rest is measurement or already green.
 */
import { expect, test, type Page } from '@playwright/test'

import { RUN_ID, EQ, boot, openDetails, ringSnapshot } from '../stand.js'
import { MATRIX, VIEWPORT_MAIN, type Viewport } from '../surfaceMatrix.js'

type R = { name: string; x: number; y: number; w: number; h: number }

const HUD_PANELS = ['.ds-ov-top .ds-panel', '.ds-ov-bottom:not(.sar-interact-history-overlay) .ds-panel', '.graph-navigator', '.action-bar'] as const
const WINDOWS = '.ws-shell'
// 048 S3: the error toast is gone (a window's failure is its own block). 049 S2: the success toast is gone (the history
// line and the window keep the result).
const TOASTS = '.sar-interact-history-overlay'

const note = (id: string, row: unknown) => {
  console.log(`[039 ${id}]`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-${id}`, description: JSON.stringify(row) })
}

async function rects(page: Page, selectors: readonly string[]): Promise<R[]> {
  return page.evaluate((selectors) => {
    const out: Array<{ name: string; x: number; y: number; w: number; h: number }> = []
    for (const s of selectors) {
      for (const el of document.querySelectorAll<HTMLElement>(s)) {
        const r = el.getBoundingClientRect()
        const cs = getComputedStyle(el)
        if (r.width <= 0 || r.height <= 0 || cs.visibility === 'hidden' || cs.display === 'none') continue
        out.push({ name: `${s} <${el.tagName.toLowerCase()}.${[...el.classList].slice(0, 2).join('.')}>`, x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) })
      }
    }
    return out
  }, [...selectors])
}

const overlap = (a: R, b: R) => Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y))

/** Share of the viewport where the element under the point is a canvas, and who covers the rest. */
async function unobscured(page: Page, vp: Viewport) {
  return page.evaluate(({ w, h }) => {
    const step = 4
    let total = 0
    let free = 0
    const by: Record<string, number> = {}
    for (let y = step / 2; y < h; y += step) {
      for (let x = step / 2; x < w; x += step) {
        total += 1
        const el = document.elementFromPoint(x, y)
        if (el instanceof HTMLCanvasElement) { free += 1; continue }
        const owner = el?.closest('.ws-shell, .graph-navigator, .action-bar, .ds-ov-bottom, .ds-ov-top-stack > *, .ds-ov-top, [class*="toast"]')
        const name = owner ? `${owner.tagName.toLowerCase()}.${[...owner.classList].slice(0, 2).join('.')}` : (el?.tagName.toLowerCase() ?? 'nothing')
        by[name] = (by[name] ?? 0) + 1
      }
    }
    const canvas = document.querySelector('canvas.canvas')!.getBoundingClientRect()
    const pct = (n: number) => Math.round((n / total) * 1000) / 10
    return {
      unobscuredPct: pct(free), coveredBy: Object.fromEntries(Object.entries(by).map(([k, v]) => [k, pct(v)])),
      // What a check of the canvas RECTANGLE would say - the vacuous numerator P-2 warns about.
      canvasRectPct: Math.round(((canvas.width * canvas.height) / (w * h)) * 1000) / 10,
    }
  }, { w: vp.width, h: vp.height })
}

const entry = (id: string) => {
  const e = MATRIX.find((m) => m.id === id)
  if (!e) throw new Error(`039 R9: the stand's matrix has no entry "${id}"`)
  return e
}

for (const vp of [VIEWPORT_MAIN, { width: 1440, height: 900 }] as Viewport[]) {
  test(`039 R9 measurement - unobscured canvas at ${vp.width}x${vp.height}: navigator closed and open, and the rows of the HUD`, async ({ page }) => {
    await page.setViewportSize(vp)
    await boot(page, { snapshot: ringSnapshot(), clock: false })
    const closed = await unobscured(page, vp)
    const rows = await rects(page, ['.ds-ov-top .ds-ov-top-stack > *', '.ds-ov-bottom:not(.sar-interact-history-overlay)', '.action-bar', '.graph-navigator'])
    await openDetails(page) // 043 S1: the navigator is behind "Подробнее" ("closed" above is the first view without it)
    await page.locator('.graph-navigator summary').click()
    await expect(page.locator('#graph-navigator-node')).toBeVisible()
    const open = await unobscured(page, vp)
    const navigatorOpen = (await rects(page, ['.graph-navigator']))[0]
    note(`R9 canvas ${vp.width}x${vp.height}`, { closed, open, hudRows: rows, navigatorOpen, specBudgetPct: 85, pxLeftForBothBarsAt85: Math.round(vp.height * 0.15) })
    expect(closed.unobscuredPct, 'counter-check: something covers the canvas and not everything').toBeGreaterThan(0)
    expect(closed.unobscuredPct).toBeLessThan(100)
    expect(closed.canvasRectPct, 'counter-check of the vacuous form: the canvas RECTANGLE is the whole viewport whatever covers it').toBe(100)
    expect(open.unobscuredPct, 'counter-check: the open navigator covers more than the closed one').toBeLessThan(closed.unobscuredPct)
  })
}

/** No window is entering or leaving (`<Transition name="ws">`), and the set of windows stopped changing. */
async function settledWindows(page: Page): Promise<R[]> {
  let prev = ''
  let same = 0
  await expect.poll(async () => {
    const moving = await page.locator('.ws-shell.ws-enter-active, .ws-shell.ws-leave-active, .ws-shell.ws-enter-from, .ws-shell.ws-leave-to').count()
    const cur = JSON.stringify(await rects(page, [WINDOWS]))
    same = moving === 0 && cur === prev ? same + 1 : 0
    prev = cur
    return same
  }, { timeout: 15_000, intervals: [100] }).toBeGreaterThanOrEqual(3)
  return rects(page, [WINDOWS])
}

/**
 * Pairs of windows a user can summon one after the other. Which pairs really STAY on screen together is a result of
 * the run, not an assumption: the window manager replaces some windows by others, and a pair that leaves one window
 * is reported as such ("windowsSettled": 1) - then only that window is judged against the HUD.
 */
// Not a pair: a trust line popup, then "Send Payment" - the action bar is disabled while the popup's flow is open
// ("Cancel current action first (press ESC)", observed 2026-10-12), so a user cannot summon that second window.
const PAIRS: Array<[string, string]> = [['node-card', 'payment-panel'], ['payment-panel', 'node-card'], ['node-card', 'edge-popup'], ['edge-popup', 'node-card']]

for (const [first, second] of PAIRS) {
  test(`039 R9 - windows at 1366x768 lie over no HUD panel and not over each other: ${first}, then ${second}`, async ({ page }) => {
    await page.setViewportSize(VIEWPORT_MAIN)
    await boot(page, { snapshot: ringSnapshot(), clock: false })
    await entry(first).open(page)
    await expect(page.locator(entry(first).root), `the first window exists (${first})`).toBeVisible()
    const afterFirst = await settledWindows(page)
    await entry(second).open(page)
    await expect(page.locator(entry(second).root), `the second window exists (${second})`).toBeVisible()
    const windows = await settledWindows(page)
    const hud = await rects(page, HUD_PANELS)
    const overHud = windows.flatMap((w) => hud.filter((p) => overlap(w, p) > 0).map((p) => ({ window: w.name, hud: p.name, px2: overlap(w, p) })))
    const overEachOther = windows.length === 2 ? overlap(windows[0]!, windows[1]!) : null
    const covered = await unobscured(page, VIEWPORT_MAIN)
    note(`R9 windows ${first}+${second}`, { afterFirst, windowsSettled: windows.length, windows, hudPanels: hud.length, overHud, overEachOtherPx2: overEachOther, unobscuredPct: covered.unobscuredPct })
    expect(hud.length, 'counter-check: the HUD panels are found').toBeGreaterThan(0)
    expect(windows.length, 'counter-check: at least the second window is on screen and still').toBeGreaterThan(0)
    expect.soft(overHud, 'windows lying over a HUD panel').toEqual([])
    if (overEachOther !== null) expect.soft(overEachOther, 'area in which the two windows overlap, px2').toBe(0)
  })
}

test('039 R9 - the history line of an action (049 S2: no toast any more) at 1366x768 lie over no HUD panel and over no window (already green; nodes under them are measured)', async ({ page }) => {
  await page.setViewportSize(VIEWPORT_MAIN)
  await boot(page, {
    snapshot: ringSnapshot(), clock: false,
    beforeLoad: (p) => p.route(`**/simulator/runs/${RUN_ID}/actions/clearing-real`, (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        ok: true, equivalent: EQ, cleared_cycles: 1, total_cleared_amount: '7.00', client_action_id: null,
        cycles: [{ cleared_amount: '7.00', edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }] }],
      }),
    })),
  })
  await page.locator('[data-testid="actionbar-clearing"]').click()
  await page.locator('[data-testid="clearing-confirm"]').click()
  await expect(page.locator('.sar-interact-history-overlay'), 'the history line exists before anything is judged').toBeVisible()
  const toasts = await rects(page, TOASTS.split(', '))
  const hud = await rects(page, HUD_PANELS)
  const windows = await rects(page, [WINDOWS])
  const overHud = toasts.flatMap((t) => hud.filter((p) => overlap(t, p) > 0).map((p) => ({ toast: t.name, hud: p.name, px2: overlap(t, p) })))
  const overWindows = toasts.flatMap((t) => windows.filter((w) => overlap(t, w) > 0).map((w) => ({ toast: t.name, window: w.name, px2: overlap(t, w) })))
  const nodesUnder = await page.evaluate((sel) => {
    const g = (window as unknown as { __geoSim: { layoutNodes: Array<{ id: string; x: number; y: number }>; camera: { panX: number; panY: number; zoom: number } } }).__geoSim
    const boxes = [...document.querySelectorAll<HTMLElement>(sel)].map((el) => el.getBoundingClientRect())
    return g.layoutNodes
      .map((n) => ({ id: n.id, x: n.x * g.camera.zoom + g.camera.panX, y: n.y * g.camera.zoom + g.camera.panY }))
      .filter((n) => boxes.some((b) => n.x >= b.left && n.x <= b.right && n.y >= b.top && n.y <= b.bottom)).map((n) => n.id)
  }, TOASTS)
  note('R9 toast', { toasts, windowsOpen: windows.length, overHud, overWindows, nodesUnder })
  expect(toasts.length, 'counter-check: the toast is measured').toBeGreaterThan(0)
  expect.soft(overHud, 'toasts lying over a HUD panel').toEqual([])
  expect.soft(overWindows, 'toasts lying over a window').toEqual([])
  // `nodesUnder` is a MEASUREMENT: the spec's rule is about HUD rows and windows, not about nodes, and which node (if
  // any) lies under a toast is a matter of the layout.
})
