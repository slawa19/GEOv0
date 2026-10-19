/**
 * 039 slice D (T3940) - the last clearing's total debt "was -> now" in the bottom row, through the product's own SSE
 * reader, event pipeline, state and bar (spec 039 :160, target 6, amendment P-5; advisor's decision 2026-10-19, option
 * (a): only a clearing states the pair - "the clearing took this much off" - and payments neither create, replace nor
 * drop it). Grown from the reproducer R7 of T3901 (`p039-repro/r07-debt-before-after.p039-repro.ts`, removed with
 * this file). Until 2026-10-19 (§15 Codex) the first form of this file did NOT make every assertion of R7: R7 also
 * required "now" to be visible, and `pair()` checked only "was"; restored (`afterShown`).
 *
 * The scene separates the candidates for "was":
 *   - settled ............................................ 48.00 (cycle a>b>c>a of 10 each, and d>e of 18)
 *   - a payment raises d>e to 25: the bar says 55.00 and there is NO pair (no clearing yet);
 *   - a clearing takes 10 off each line of the cycle: "clearing: was 55.00 -> now 25.00". Wrong: 48.00 (the total
 *     before the payment) and 35.00 (now + `cleared_amount`, forbidden by P-5). The bar is not behind the data here
 *     (an in-place patch updates it at once; its debounce is for a replaced snapshot), so this case does not tell "the
 *     total before the patches" from "the last displayed value" - the unit test with a held clock does
 *     (`src/composables/debtChange.p039d.test.ts`);
 *   - a status frame keeps the pair; a payment after the clearing (25 -> 27) keeps the clearing's pair 55.00 -> 25.00
 *     while the bar says 27.00; a clearing that changes nothing drops the pair (no false "was").
 *
 * Both languages (`?lang=ru`, `?lang=en`): the words are the dictionary's (`systemBalance.clearingChange`), the figures
 * and the equivalent the same. The pair sits above the bar, out of its flow: the bottom bar keeps its place and size
 * with and without it (inline, the first build wrapped the main row to two lines in Russian at 1366x768, 42 -> 80 px;
 * the third test was red on that form), and the pair is whole on screen, just above the total.
 *
 * NOT seen here (unit tier, `src/composables/debtChange.p039d.test.ts`): the debounce of the bar held by a fake clock,
 * a switch of equivalent, a reload, an unknown debt, exact sums beyond a double. Not seen at all: a real backend.
 */
import { expect, test, type Page } from '@playwright/test'

import { EQ, RUN_ID, SCENARIO_ID, boot, fixtureSnapshot100, pushEvent, ringSnapshot, sseFrame, type QaSnapshot } from './stand.js'
import { DEBT } from './p039-repro/contract.js'

const USED: Record<string, string> = { 'a>b': '10.00', 'b>c': '10.00', 'c>a': '10.00', 'd>e': '18.00' }

function sceneWithDebts(): QaSnapshot {
  const s = ringSnapshot()
  const pairs: Array<[string, string]> = [['a', 'b'], ['b', 'c'], ['c', 'a'], ['d', 'e'], ['e', 'f'], ['f', 'd']]
  const links = pairs.map(([source, target]) => {
    const used = USED[`${source}>${target}`] ?? '0.00'
    return { ...s.links[0]!, source, target, used, available: (100 - Number(used)).toFixed(2), trust_limit: '100.00' }
  })
  return { ...s, links }
}

/** The bar's figure the rest of the app reads (`.ds-value`): the current total with its unit. */
const shownTotal = (page: Page) => page.locator(`${DEBT.bar} [data-testid="sbb-total-debt"] .ds-value`).first().textContent()

/** The pair as the page states it: the two fields, the sentence they are in, and whether "was" comes first. */
async function pair(page: Page) {
  return page.evaluate(({ bar, before, after }) => {
    const root = document.querySelector(bar)
    const b = root?.querySelector(before) ?? null
    const a = root?.querySelector(after) ?? null
    const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    // Seen by a person: a box with an area, and neither it nor an ancestor hidden, undisplayed or fully transparent by
    // computed style (§15 Codex 2026-10-19: the first form of this file checked only "was"; R7 checked both fields).
    const shown = (el: Element | null) => {
      if (!el) return false
      const r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0 || getComputedStyle(el).visibility === 'hidden') return false
      for (let n: Element | null = el; n; n = n.parentElement) {
        const cs = getComputedStyle(n)
        if (cs.display === 'none' || Number(cs.opacity) === 0) return false
      }
      return true
    }
    return {
      before: b ? text(b) : null, after: a ? text(a) : null, beforeShown: shown(b), afterShown: shown(a),
      sentence: text(root?.querySelector('[data-testid="total-debt-change"]')),
      beforeComesFirst: !!a && !!b && !!(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING),
    }
  }, DEBT)
}

const SENTENCE = {
  ru: (before: string, after: string) => `клиринг: было ${before} → стало ${after} ${EQ}`,
  en: (before: string, after: string) => `clearing: was ${before} → became ${after} ${EQ}`,
} as const

/** The bottom bar's box, rounded: equal with and without the pair. */
const barBox = (page: Page) => page.locator('.ds-ov-bottom').first().evaluate((el) => {
  const r = el.getBoundingClientRect()
  return { top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width) }
})

/** Where the pair is: whole in the viewport, above the total's panel, drawn (a width). */
const wasPlaced = (page: Page) => page.evaluate(() => {
  const was = document.querySelector('[data-testid="total-debt-change"]')!.getBoundingClientRect()
  const panel = document.querySelector('[data-testid="system-balance"] [data-testid="sbb-total-debt"]')!.getBoundingClientRect()
  return { inViewport: was.left >= 0 && was.right <= innerWidth && was.top >= 0, abovePanel: was.bottom <= panel.top + 0.5, drawn: was.width > 20 }
})

for (const lang of ['ru', 'en'] as const) {
  test(`039 D [${lang}] - the total debt states "was -> now" of the last clearing; payments do not touch it`, async ({ page }) => {
    const snapshot = sceneWithDebts()
    await boot(page, {
      snapshot, clock: false, query: `&lang=${lang}`,
      // The stand's own answer to trustlines-list says used 0.00 for every line; here the lines carry debts.
      beforeLoad: (p) => p.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) => r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ items: snapshot.links.map((l) => ({
          from_pid: l.source, from_name: l.source, to_pid: l.target, to_name: l.target, equivalent: EQ,
          limit: '100.00', used: l.used, reverse_used: '0.00', available: l.available, status: 'active',
        })) }),
      })),
    })
    await expect(page.locator(DEBT.bar).first(), 'counter-check: the system balance bar is on screen').toBeVisible()
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the settled bar shows the known total' }).toBe(`48.00 ${EQ}`)
    expect((await pair(page)).beforeShown, 'settled: no pair').toBe(false)
    const settledBar = await barBox(page)

    // A payment with no clearing before it: the total moves, no pair.
    await pushEvent(page, {
      type: 'tx.updated', from: 'e', to: 'd', amount: '7.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
      edge_patch: [{ source: 'd', target: 'e', used: '25.00', available: '75.00' }],
    })
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the payment was applied' }).toBe(`55.00 ${EQ}`)
    expect((await pair(page)).beforeShown, 'a payment states no pair (advisor 2026-10-19, option (a))').toBe(false)

    // The clearing.
    const taken = await page.evaluate((frame) => {
      const sse = (window as unknown as { __qaSse: { push: (t: string) => Promise<number> } }).__qaSse
      return sse.push(frame)
    }, sseFrame({
      type: 'clearing.done', plan_id: 'plan-p039-d', cleared_cycles: 1, cleared_amount: '10.00',
      cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
      edge_patch: [['a', 'b'], ['b', 'c'], ['c', 'a']].map(([source, target]) => ({ source, target, used: '0.00', available: '100.00' })),
    }).frame)
    expect(taken, 'counter-check: the product took the frame').toBe(1)
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the clearing was APPLIED' }).toBe(`25.00 ${EQ}`)

    const stated = await pair(page)
    expect(stated, 'was 55.00 (just before the clearing\'s patches; not 48.00, not 25 + 10 = 35.00) -> now 25.00, both seen, with the equivalent')
      .toEqual({ before: '55.00', after: '25.00', beforeShown: true, afterShown: true, sentence: SENTENCE[lang]('55.00', '25.00'), beforeComesFirst: true })
    expect(await barBox(page), 'the bottom bar keeps its place and size with the pair').toEqual(settledBar)
    expect(await wasPlaced(page), 'the pair is whole on screen, just above the total').toEqual({ inViewport: true, abovePanel: true, drawn: true })

    // Held through traffic that does not change a debt: a status frame of the run.
    await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'paused', sim_time_ms: 1000, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
    expect(await pair(page), 'a status frame keeps the pair').toEqual(stated)

    // A payment after the clearing: the bar moves, the pair stays the clearing's.
    await pushEvent(page, {
      type: 'tx.updated', from: 'e', to: 'd', amount: '2.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
      edge_patch: [{ source: 'd', target: 'e', used: '27.00', available: '73.00' }],
    })
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the payment was applied' }).toBe(`27.00 ${EQ}`)
    expect(await pair(page), 'a payment after the clearing keeps the clearing\'s pair 55.00 -> 25.00').toEqual(stated)

    // A clearing that changes nothing: no false "was".
    await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-p039-d-0', cleared_cycles: 0, cleared_amount: '0.00', cycle_edges: [], edge_patch: [] })
    await expect.poll(async () => (await pair(page)).beforeShown, { message: 'a clearing that changes nothing drops the pair' }).toBe(false)
    expect((await pair(page)).afterShown, 'and its "now" with it').toBe(false)
    expect(await shownTotal(page)).toBe(`27.00 ${EQ}`)
  })
}

// The widest first view the demo shows: the 100-node scene (its full legend), Russian, 1366x768. Here the inline form
// of the first build of slice D (e10ac818: "Общий долг было X → стало Y" inside the row) wrapped the main row of the
// bottom bar to two lines, 42 -> 80 px: this test was red on it (`the bottom bar keeps its place and size`).
test('039 D [ru] [1366x768, 100 nodes] - the pair does not move or resize the bottom bar', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  const snapshot = fixtureSnapshot100()
  // The line with the largest debt: a clearing of it (the scene's total is about 88 thousand: a realistic magnitude).
  const l = snapshot.links.reduce((m, x) => (Number(x.used ?? 0) > Number(m.used ?? 0) ? x : m))
  expect(Number(l.used ?? 0), 'counter-check: the scene has a line with a debt to clear').toBeGreaterThan(0)
  await boot(page, { snapshot, clock: false, query: '&lang=ru' })
  const settled = await barBox(page)
  expect((await pair(page)).beforeShown, 'counter-check: settled, no pair').toBe(false)
  await pushEvent(page, {
    type: 'clearing.done', plan_id: 'plan-p039-d-100', cleared_cycles: 1, cleared_amount: String(l.used),
    cycle_edges: [{ from: String(l.source), to: String(l.target) }],
    edge_patch: [{ source: l.source, target: l.target, used: '0.00', available: String(l.trust_limit ?? '0') }],
  })
  await expect.poll(async () => (await pair(page)).beforeShown, { message: 'counter-check: the clearing states its pair' }).toBe(true)
  expect(await barBox(page), 'the bottom bar keeps its place and size with the pair').toEqual(settled)
  expect(await wasPlaced(page), 'the pair is whole on screen, just above the total').toEqual({ inViewport: true, abovePanel: true, drawn: true })
})
