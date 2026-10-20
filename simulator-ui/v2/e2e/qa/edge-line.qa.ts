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

import { DETAILS_TOGGLE, RUN_ID, SCENARIO_ID, boot, pushEvent, renderedProblems, ringSnapshot } from './stand.js'
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
          // plaque over its words. The alerts stand over the canvas: in hud the colour they paint first is opaque (alpha 1).
          const backs = await page.evaluate((alerts) => Object.fromEntries(alerts.map((id) => {
            const c = getComputedStyle(document.querySelector(`[data-testid="${id}"]`)!).backgroundColor
            // `rgb(r, g, b)`, `rgba(r, g, b, a)` or a modern form with `/ a` (a `color-mix` serializes as `color(srgb ... / a)`).
            const slash = c.match(/\/\s*([\d.]+)\s*\)$/)
            const commas = c.match(/^rgba\([^)]*,\s*([\d.]+)\s*\)$/)
            return [id, { colour: c, alpha: slash ? Number(slash[1]) : commas ? Number(commas[1]) : 1 }]
          })), ALERTS as unknown as string[])
          if (theme === 'hud') {
            for (const id of ALERTS) expect(backs[id]!.alpha, `${where} ${id}: its background is opaque over the canvas: ${JSON.stringify(backs)}`).toBe(1)
          } else if (details && vp === VIEWPORT_MAIN) {
            console.log(`[049 alert backgrounds] ${where} ${JSON.stringify(backs)}`)
          }
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
