/**
 * 049 S1 (T4910), decision 2 - reproducer (б) of the Verification plan: the run's error line and the capacity-stall
 * plaque stand in the same horizontal grid as the controls of the top bar - their outer left and right edges are the
 * CONTROL LINE, the content edges of the first row (`.tb-main-row`, a HudBar: its box minus its own padding), not the
 * outer edges of the top stack (until S1 both were direct children of `.ds-ov-top-stack`, outside any HudBar, and so
 * wider than the controls by the HudBar's padding on each side: owner frame 17, 2026-10-20).
 *
 * The line is read from the page by the RULE (a HudBar's content edge), never as a px literal (spec 049, Verification
 * plan item 4). Counter-check of the rule: the top and the bottom bars give the same line (one line for every edge group,
 * decision 1).
 *
 * Both alerts are shown by one run status the product reads from its own SSE pipeline: a run the server ended with an
 * error (`state: error`, `last_error`) whose payments stalled (`consec_all_rejected_ticks` 3). Both languages (one boot
 * each), both themes (the product's own switcher), "Подробнее" off and on (its service row sits between the first row
 * and the alerts), at 1366x768 and 1024x640. Geometry is measured once the layout is settled (`settledBoxes`: the same
 * boxes on three reads in a row).
 *
 * NOT seen here: the wording of the lines (spec 049 S2 owns it), a real backend, phone widths (spec 049 Non-goals).
 */
import { expect, test, type Page } from '@playwright/test'

import { t } from '../../src/i18n/t'
import { DETAILS_TOGGLE, RUN_ID, SCENARIO_ID, boot, fixtureSnapshot100, pushEvent, renderedProblems, ringSnapshot } from './stand.js'
import { VIEWPORT_MAIN, VIEWPORT_RESIZED } from './surfaceMatrix.js'

const ALERTS = ['top-error', 'capacity-stall'] as const

/** The control lines of both bars (content edges of their HudBar rows) and the boxes of the two alerts, rounded to 0.5 px. */
const boxes = (page: Page) => page.evaluate((alerts) => {
  const half = (v: number) => Math.round(v * 2) / 2
  const contentEdges = (el: Element) => {
    const r = el.getBoundingClientRect()
    const cs = getComputedStyle(el)
    return { left: half(r.left + parseFloat(cs.paddingLeft)), right: half(r.right - parseFloat(cs.paddingRight)) }
  }
  const out: Record<string, { left: number; right: number } | null> = {
    topLine: contentEdges(document.querySelector('.ds-ov-top .tb-main-row')!),
    bottomLine: contentEdges(document.querySelector('.ds-ov-bottom .bb-main-row')!),
  }
  for (const id of alerts) {
    const el = document.querySelector(`[data-testid="${id}"]`)
    const r = el?.getBoundingClientRect()
    out[id] = r && r.width > 0 ? { left: half(r.left), right: half(r.right) } : null
  }
  return out
}, ALERTS as unknown as string[])

/** A condition, not a sleep: the same boxes on three reads in a row (a resize and a theme switch reflow the bars). */
async function settledBoxes(page: Page): Promise<Awaited<ReturnType<typeof boxes>>> {
  let prev = ''
  let same = 0
  let last: Awaited<ReturnType<typeof boxes>> | null = null
  await expect.poll(async () => {
    last = await boxes(page)
    const key = JSON.stringify(last)
    same = key === prev ? same + 1 : 0
    prev = key
    return same >= 2
  }, { message: 'the bars and the alerts stop moving', intervals: [50, 100, 100, 150] }).toBe(true)
  return last!
}

for (const lang of ['ru', 'en'] as const) {
  test(`049 S1 (б) [${lang}] [saas|hud, details off|on, 1366x768|1024x640] - the run error line and the stall plaque stand on the control line`, async ({ page }) => {
    await page.setViewportSize(VIEWPORT_MAIN)
    await boot(page, { snapshot: ringSnapshot(), clock: false, query: `&lang=${lang}&theme=saas` })
    await pushEvent(page, {
      type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'error', sim_time_ms: 1000, intensity_percent: 0,
      ops_sec: 0, queue_depth: 0, consec_all_rejected_ticks: 3, last_error: { code: 'REAL_MODE_TICK_FAILED', message: 'tick failed', at: '2026-02-01T00:00:01.000Z' },
    })
    for (const id of ALERTS) await expect(page.getByTestId(id), `counter-check: ${id} is shown`).toBeVisible()

    const toggle = page.locator(DETAILS_TOGGLE)
    const setDetails = async (on: boolean) => {
      if (((await toggle.getAttribute('aria-expanded')) === 'true') !== on) await toggle.click()
      await expect(toggle).toHaveAttribute('aria-expanded', on ? 'true' : 'false')
    }
    const seen: string[] = []
    for (const theme of ['saas', 'hud'] as const) {
      if (theme !== 'saas') {
        await setDetails(true)
        await page.getByTestId('theme-select').selectOption(theme)
      }
      await expect(page.locator('.root'), `counter-check: the theme is ${theme}`).toHaveAttribute('data-theme', theme)
      for (const details of [false, true]) {
        await setDetails(details)
        if (details) await expect(page.getByTestId('top-details-row'), 'counter-check: the service row is between the first row and the alerts').toBeVisible()
        for (const vp of [VIEWPORT_MAIN, VIEWPORT_RESIZED]) {
          await page.setViewportSize(vp)
          const where = `[${lang}, ${theme}, details ${details ? 'on' : 'off'}, ${vp.width}x${vp.height}]`
          const b = await settledBoxes(page)
          // §15 Codex P3: the alerts are SEEN, not boxes alone (opacity, visibility, area, nothing over their centre).
          expect(await renderedProblems(page, ALERTS.map((id) => `[data-testid="${id}"]`)), `${where} both alerts are drawn and seen`).toEqual([])
          // Anti-vacuum: the line is inside the viewport and not the viewport's edge (a HudBar with its padding).
          expect(b.topLine!.left, `${where} counter-check: the control line is inset from the edge`).toBeGreaterThan(1)
          expect(b.topLine!.right, `${where} counter-check: the control line is inset from the edge`).toBeLessThan(vp.width - 1)
          // One line for every edge group (decision 1): the top and the bottom bar give the same line.
          expect(Math.abs(b.topLine!.left - b.bottomLine!.left), `${where} one control line, left: ${JSON.stringify(b)}`).toBeLessThanOrEqual(1)
          expect(Math.abs(b.topLine!.right - b.bottomLine!.right), `${where} one control line, right: ${JSON.stringify(b)}`).toBeLessThanOrEqual(1)
          // Owner's review of the final frames (2026-10-20, frame 07): in hud a node of the graph showed through the stall
          // plaque over its words; in saas both alerts painted alpha 0.05 (BACKLOG 049-9, frames 07/08 saas). The alerts stand
          // over the canvas: in both themes the colour they paint first is opaque (alpha 1).
          const backs = await page.evaluate((alerts) => Object.fromEntries(alerts.map((id) => {
            const c = getComputedStyle(document.querySelector(`[data-testid="${id}"]`)!).backgroundColor
            // `rgb(r, g, b)`, `rgba(r, g, b, a)` or a modern form with `/ a` (a `color-mix` serializes as `color(srgb ... / a)`).
            const slash = c.match(/\/\s*([\d.]+)\s*\)$/)
            const commas = c.match(/^rgba\([^)]*,\s*([\d.]+)\s*\)$/)
            // A form not read here is not taken for opaque: its alpha is NaN, and the assertion below fails on it.
            const opaque = /^rgb\([^)]*\)$/.test(c)
            return [id, { colour: c, alpha: slash ? Number(slash[1]) : commas ? Number(commas[1]) : opaque ? 1 : Number.NaN }]
          })), ALERTS as unknown as string[])
          for (const id of ALERTS) expect(backs[id]!.alpha, `${where} ${id}: its background is opaque over the canvas: ${JSON.stringify(backs)}`).toBe(1)
          for (const id of ALERTS) {
            const a = b[id]
            expect(a, `${where} ${id} is drawn`).not.toBeNull()
            expect(Math.abs(a!.left - b.topLine!.left), `${where} ${id}: its left edge is the control line: ${JSON.stringify(b)}`).toBeLessThanOrEqual(1)
            expect(Math.abs(a!.right - b.topLine!.right), `${where} ${id}: its right edge is the control line: ${JSON.stringify(b)}`).toBeLessThanOrEqual(1)
          }
          seen.push(where)
        }
      }
      await page.setViewportSize(VIEWPORT_MAIN)
    }
    expect(seen, 'counter-check: every combination was measured').toHaveLength(8)
  })
}

/**
 * BACKLOG 049-10 (advisor 2026-10-21, owner frames 05/06 of 2026-10-20): the line «Сначала отмените текущее действие
 * (Esc)» that an open action shows hung OUT of every row - absolutely under the three actions, centred - over the graph:
 * on this scene a node lay under its words at 1366x768. It now stands INSIDE the control-line grid without adding a band
 * over the scene: within the first row of the top bar (the HudBar whose content edges are the control line), between
 * its left and right content edges, over no node of the graph and over no other surface, and the top stack is as tall
 * with it as without it (so no row is pushed onto the scene and the window under the stack does not move).
 *
 * Not the full-width row of the error line and the stall plaque (spec 049, decision 2), which the brief named: measured
 * on this scene, such a row - under the first row, or under the service rows with "Подробнее" on - lay over 1 node at
 * 1366x768 and over 12-18 with "Подробнее" on; the scene is framed under the first row only.
 *
 * The hint is shown by the product's own gesture (a click on "Отправить платёж" locks the other actions), on the
 * 100-node demo snapshot, in both languages (one boot; the second language through the product's switch, once without
 * and once with the action open), both themes, "Подробнее" off and on, at 1366x768 and 1024x640 (where the words wrap
 * inside the row). Each combination is measured first with no action open, then with the hint. A node is "under" the hint when its centre lies inside the
 * hint's box grown by the node's half-size on screen (its `viz_size`, the larger side) - the rule of the bottom-right
 * column (`debt-before-after.qa.ts`). Counter-checks: no hint before the gesture, in every combination; Escape (the way
 * it names) removes it and frees the actions; the same rule finds nodes in a box of the hint's size at the median node
 * (the check is not blind); the graph is on screen; the first row's controls are read.
 *
 * NOT seen here: a live scene's positions, phone widths (spec 049 Non-goals), the busy line (the same element in the
 * same place, shown while a request is in flight; the stand does not hold a request open here).
 */
const HINT = '[data-testid="actionbar-locked-hint"]'

/** The first row and its control line, the hint, the neighbours it overlaps, the nodes under it, the stack's bottom. */
const hintGeometry = (page: Page, radiusById: Record<string, number>) => page.evaluate(({ hintSel, radiusById }) => {
  const half = (v: number) => Math.round(v * 2) / 2
  const box = (r: { left: number; top: number; right: number; bottom: number }) => ({ left: half(r.left), top: half(r.top), right: half(r.right), bottom: half(r.bottom) })
  const row = document.querySelector('.ds-ov-top .tb-main-row')!
  const rr = row.getBoundingClientRect()
  const cs = getComputedStyle(row)
  const topLine = { left: half(rr.left + parseFloat(cs.paddingLeft)), right: half(rr.right - parseFloat(cs.paddingRight)) }
  const firstRow = box(rr)
  const stackBottom = half(document.querySelector('.ds-ov-top-stack')!.getBoundingClientRect().bottom)
  // The controls of the first row (the identity, the actions, "Подробнее"): the hint moves none of them (043 S2, B2).
  const rowControls = [...row.querySelectorAll('button, .ds-panel')].filter((n) => n !== row).map((n) => box(n.getBoundingClientRect()))
  const el = document.querySelector(hintSel)
  const hr = el?.getBoundingClientRect()
  const hint = hr && hr.width > 0 ? box(hr) : null
  // The neighbours: every control and panel of the HUD that does not hold the hint (the first row's HudBar holds it),
  // every window, the onboarding hint (it hangs under the stack).
  const others = [...document.querySelectorAll('button, select, input, .ds-panel, .ds-alert, .ws-shell, [data-testid="onboarding-hint"]')]
    .filter((n) => !el || (n !== el && !n.contains(el)))
    .map((n) => ({ name: `${n.tagName.toLowerCase()}${n.getAttribute('data-testid') ? `[${n.getAttribute('data-testid')}]` : ''}.${String(n.className).split(' ')[0]}`, r: n.getBoundingClientRect() }))
    .filter((n) => n.r.width > 0 && n.r.height > 0)
  const overlaps = hr
    ? others.filter((n) => n.r.left < hr.right - 0.5 && n.r.right > hr.left + 0.5 && n.r.top < hr.bottom - 0.5 && n.r.bottom > hr.top + 0.5).map((n) => `${n.name}@${JSON.stringify(box(n.r))}`)
    : []
  const sim = (window as unknown as { __geoSim?: { layoutNodes?: Array<{ id: string; x: number; y: number }>; camera: { panX: number; panY: number; zoom: number } } }).__geoSim!
  const canvas = document.querySelector('canvas')!.getBoundingClientRect()
  const cam = sim.camera
  const nodes = (sim.layoutNodes ?? []).map((n) => ({ id: n.id, x: canvas.left + n.x * cam.zoom + cam.panX, y: canvas.top + n.y * cam.zoom + cam.panY, radius: (radiusById[n.id] ?? 12) * cam.zoom }))
  const under = (r: { left: number; top: number; right: number; bottom: number }) => nodes
    .filter((n) => n.x > r.left - n.radius && n.x < r.right + n.radius && n.y > r.top - n.radius && n.y < r.bottom + n.radius)
    .map((n) => `${n.id}@${Math.round(n.x)},${Math.round(n.y)}`)
  const onScreen = nodes.filter((n) => n.x >= 0 && n.x <= innerWidth && n.y >= 0 && n.y <= innerHeight).length
  // Counter-check of the rule: a box of the hint's size at the median node (the graph is there).
  const xs = nodes.map((n) => n.x).sort((a, b) => a - b)
  const ys = nodes.map((n) => n.y).sort((a, b) => a - b)
  const cx = xs[Math.floor(xs.length / 2)] ?? 0
  const cy = ys[Math.floor(ys.length / 2)] ?? 0
  const w = hr ? hr.width : 0
  const h = hr ? hr.height : 0
  const centreUnder = under({ left: cx - w / 2, top: cy - h / 2, right: cx + w / 2, bottom: cy + h / 2 }).length
  // The camera is part of the settle key: the node checks are read on a framed scene, not during a re-frame.
  const camera = { panX: half(cam.panX), panY: half(cam.panY), zoom: Math.round(cam.zoom * 1000) / 1000 }
  return { topLine, firstRow, stackBottom, rowControls, hint, overlaps, under: hint ? under(hint) : [], nodes: nodes.length, onScreen, centreUnder, camera }
}, { hintSel: HINT, radiusById })

test('049-10 [ru, en] [100 nodes, saas|hud, details off|on, 1366x768|1024x640] - the "cancel the current action" hint stands inside the control-line grid, over no node and no other surface, and adds no band', async ({ page }) => {
  await page.setViewportSize(VIEWPORT_MAIN)
  const snapshot = fixtureSnapshot100()
  const radii = Object.fromEntries(snapshot.nodes.map((n) => {
    const v = (n as { viz_size?: { w?: number; h?: number } }).viz_size
    return [String(n.id), Math.max(Number(v?.w ?? 24), Number(v?.h ?? 24)) / 2]
  }))
  // One boot (the 100-node scene settles in about 3.5 s): the second language through the product's own switch.
  await boot(page, { snapshot, clock: false, query: '&lang=ru&theme=saas' })
  const hint = page.locator(HINT)
  const panel = page.locator('[data-testid="manual-payment-panel"]')

  const toggle = page.locator(DETAILS_TOGGLE)
  const setDetails = async (on: boolean) => {
    if (((await toggle.getAttribute('aria-expanded')) === 'true') !== on) await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', on ? 'true' : 'false')
  }
  const setTheme = async (theme: 'saas' | 'hud') => {
    if ((await page.locator('.root').getAttribute('data-theme')) !== theme) {
      await setDetails(true)
      await page.getByTestId('theme-select').selectOption(theme)
    }
    await expect(page.locator('.root'), `counter-check: the theme is ${theme}`).toHaveAttribute('data-theme', theme)
  }
  /** A condition, not a sleep: the same geometry on three reads in a row (the bars reflow, a resize re-frames the scene). */
  const settled = async () => {
    let prev = ''
    let same = 0
    let last: Awaited<ReturnType<typeof hintGeometry>> | null = null
    await expect.poll(async () => {
      last = await hintGeometry(page, radii)
      const key = JSON.stringify(last)
      same = key === prev ? same + 1 : 0
      prev = key
      return same >= 2
    }, { message: 'the hint, the bars and the camera stop moving', intervals: [50, 100, 100, 150] }).toBe(true)
    return last!
  }
  type Combo = { theme: 'saas' | 'hud'; details: boolean; vp: typeof VIEWPORT_MAIN }
  const combos: Combo[] = []
  // Grouped by viewport: a resize re-frames the scene, and the settle waits for the camera (about 0.5-1 s each time).
  for (const vp of [VIEWPORT_MAIN, VIEWPORT_RESIZED]) for (const theme of ['saas', 'hud'] as const) for (const details of [false, true]) combos.push({ theme, details, vp })
  const name = (lang: string, c: Combo) => `[${lang}, ${c.theme}, details ${c.details ? 'on' : 'off'}, ${c.vp.width}x${c.vp.height}]`
  const enter = async (c: Combo) => {
    await setTheme(c.theme)
    await setDetails(c.details)
    await page.setViewportSize(c.vp)
  }

  /** With no action open: the first row and the stack as they are without the hint, per combination. */
  const before = new Map<string, Awaited<ReturnType<typeof hintGeometry>>>()
  const measureBefore = async (lang: string, order: Combo[]) => {
    for (const c of order) {
      await enter(c)
      await expect(hint, `${name(lang, c)} counter-check: no hint while no action is open`).toHaveCount(0)
      before.set(name(lang, c), await settled())
    }
  }
  const seen: string[] = []
  /** With the payment open: the hint in every combination, against the same combination without it. */
  const measureHint = async (lang: 'ru' | 'en', order: Combo[]) => {
    for (const c of order) {
      await enter(c)
      const where = name(lang, c)
      await expect(panel, `${where} counter-check: the payment window is open`).toBeVisible()
      await expect(hint, `${where} counter-check: the open payment locks the other actions and says so`).toBeVisible()
      await expect(hint, `${where} the hint keeps its words`).toHaveText(t('card.flowOpenReason', undefined, lang))
      const g = await settled()
      const b = before.get(where)!
      expect(b, `${where} counter-check: measured without the hint`).toBeDefined()
      expect(await renderedProblems(page, [HINT]), `${where} the hint is drawn and seen`).toEqual([])
      expect(g.hint, `${where} the hint is drawn`).not.toBeNull()
      // Inside the control-line grid: between the content edges of the first row, inside its band.
      expect(g.hint!.left, `${where} the hint is inside the control line, left: ${JSON.stringify(g)}`).toBeGreaterThanOrEqual(g.topLine.left - 1)
      expect(g.hint!.right, `${where} the hint is inside the control line, right: ${JSON.stringify(g)}`).toBeLessThanOrEqual(g.topLine.right + 1)
      expect(g.hint!.top, `${where} the hint is inside the first row, top: ${JSON.stringify(g)}`).toBeGreaterThanOrEqual(g.firstRow.top - 0.5)
      expect(g.hint!.bottom, `${where} the hint is inside the first row, bottom: ${JSON.stringify(g)}`).toBeLessThanOrEqual(g.firstRow.bottom + 0.5)
      // It adds no band and moves nothing: the first row and the stack are as tall, and its controls where they were, as
      // with no action open.
      expect({ firstRow: g.firstRow, stackBottom: g.stackBottom }, `${where} the hint adds no height to the top stack: before ${JSON.stringify(b)}`).toEqual({ firstRow: b.firstRow, stackBottom: b.stackBottom })
      expect(g.rowControls, `${where} the hint moves no control of the first row: before ${JSON.stringify(b.rowControls)}`).toEqual(b.rowControls)
      expect(g.rowControls.length, `${where} counter-check: the first row's controls are read`).toBeGreaterThanOrEqual(6)
      expect(g.overlaps, `${where} the hint overlaps no other surface: ${JSON.stringify(g)}`).toEqual([])
      expect(g.onScreen, `${where} counter-check: the graph is on screen: ${JSON.stringify(g)}`).toBeGreaterThanOrEqual(50)
      expect(g.centreUnder, `${where} counter-check: the rule finds the nodes at the median node: ${JSON.stringify(g)}`).toBeGreaterThan(0)
      expect(g.under, `${where} no node of the graph under the hint: ${JSON.stringify(g)}`).toEqual([])
      seen.push(where)
    }
  }

  const setLang = async (lang: 'ru' | 'en') => {
    await setDetails(true)
    await page.getByTestId('language-select').selectOption(lang)
    await expect(page.locator('html'), `counter-check: the interface is in ${lang}`).toHaveAttribute('lang', lang)
  }
  await measureBefore('ru', combos)
  await setLang('en')
  await measureBefore('en', [...combos].reverse())
  await page.getByTestId('actionbar-payment').click()
  await measureHint('en', combos)
  // The language switched with the action open: the same element, now in Russian.
  await setLang('ru')
  await measureHint('ru', [...combos].reverse())
  expect(seen, 'counter-check: every combination was measured with the hint').toHaveLength(16)
  // Escape - the way the hint names - ends the action: the hint goes, the window closes, the actions are free.
  await page.keyboard.press('Escape')
  await expect(hint, 'Escape removes the hint').toHaveCount(0)
  await expect(panel, 'and the payment window').toHaveCount(0)
  await expect(page.getByTestId('actionbar-trustline'), 'and frees the actions').toBeEnabled()
})
