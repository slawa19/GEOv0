/**
 * 039 slice D (T3940) - the total debt "was -> now" in the bottom row, through the product's own SSE reader, event
 * pipeline, state and bar (spec 039, target 6, amendment P-5). Grown from the reproducer R7 of T3901
 * (`p039-repro/r07-debt-before-after.p039-repro.ts`, removed with this file: every assertion it made is made here, its
 * last one changed on purpose - see below).
 *
 * The scene separates the candidates for "was":
 *   - settled ............................................ 48.00 (cycle a>b>c>a of 10 each, and d>e of 18)
 *   - a payment raises d>e to 25: was 48.00, now 55.00
 *   - written IN THE SAME CALL, a clearing takes 10 off each line of the cycle: was 55.00, now 25.00. Wrong: 48.00 (the
 *     total before the payment) and 35.00 (now + `cleared_amount`, forbidden by P-5). The bar is NOT behind the data
 *     here (an in-place patch updates it at once; its debounce is for a replaced snapshot), so this case does not tell
 *     "the total before the patches" from "the last displayed value" - the unit test with a held clock does
 *     (`src/composables/debtChange.p039d.test.ts`).
 *   - a status frame keeps the pair; a clearing that changes nothing drops it (no false "was");
 *   - the next debt-changing event states ITS pair: a payment 25 -> 27 gives was 25.00, now 27.00. (R7 asserted that
 *     no "was" is shown after the next event; slice D shows "was -> now" after a payment as well as after a clearing
 *     - brief of T3940 - so the clearing's 55.00 is gone and the payment's own pair stands.)
 *
 * Both languages (`?lang=ru`, `?lang=en`): the words are the dictionary's (`systemBalance.debtBefore`), the figures
 * and the equivalent the same. The "was" sits above the bar, out of its flow: the bottom bar keeps its place and size
 * with and without it (inline, it wrapped the main row to two lines in Russian at 1366x768, 42 -> 80 px; the third
 * test was red on that form), and the "was" is whole on screen, just above the total.
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

const WORDS = { ru: { was: 'было' }, en: { was: 'was' } } as const

/** The bottom bar's box, rounded: equal with and without a "was". */
const barBox = (page: Page) => page.locator('.ds-ov-bottom').first().evaluate((el) => {
  const r = el.getBoundingClientRect()
  return { top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width) }
})

/** Where the "was" is: whole in the viewport, above the total's panel, drawn (a width). */
const wasPlaced = (page: Page) => page.evaluate(() => {
  const was = document.querySelector('[data-testid="total-debt-change"]')!.getBoundingClientRect()
  const panel = document.querySelector('[data-testid="system-balance"] [data-testid="sbb-total-debt"]')!.getBoundingClientRect()
  return { inViewport: was.left >= 0 && was.right <= innerWidth && was.top >= 0, abovePanel: was.bottom <= panel.top + 0.5, drawn: was.width > 20 }
})

for (const lang of ['ru', 'en'] as const) {
  test(`039 D [${lang}] - the total debt states "was -> now" after a payment and after a clearing, held until the next debt-changing event`, async ({ page }) => {
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
    expect((await pair(page)).beforeShown, 'settled: no "was"').toBe(false)

    const settledBar = await barBox(page)

    // A payment, then a clearing: both frames in ONE call.
    const payment = sseFrame({
      type: 'tx.updated', from: 'e', to: 'd', amount: '7.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
      edge_patch: [{ source: 'd', target: 'e', used: '25.00', available: '75.00' }],
    }).frame
    const clearing = sseFrame({
      type: 'clearing.done', plan_id: 'plan-p039-d', cleared_cycles: 1, cleared_amount: '10.00',
      cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
      edge_patch: [['a', 'b'], ['b', 'c'], ['c', 'a']].map(([source, target]) => ({ source, target, used: '0.00', available: '100.00' })),
    }).frame
    const taken = await page.evaluate((frames) => {
      const sse = (window as unknown as { __qaSse: { push: (t: string) => Promise<number> } }).__qaSse
      return Promise.all(frames.map((f) => sse.push(f)))
    }, [payment, clearing])
    expect(taken, 'counter-check: the product took both frames (one connection each)').toEqual([1, 1])
    await expect.poll(() => shownTotal(page), { message: 'counter-check: both events were APPLIED' }).toBe(`25.00 ${EQ}`)

    const w = WORDS[lang]
    const stated = await pair(page)
    expect(stated, 'was 55.00 (just before the clearing\'s patches; not 48.00, not 25 + 10 = 35.00), now 25.00, with the equivalent')
      .toEqual({ before: '55.00', after: `25.00 ${EQ}`, beforeShown: true, afterShown: true, sentence: `${w.was} 55.00 ${EQ}`, beforeComesFirst: true })
    expect(await barBox(page), 'the bottom bar keeps its place and size with a "was"').toEqual(settledBar)
    expect(await wasPlaced(page), 'the "was" is whole on screen, just above the total').toEqual({ inViewport: true, abovePanel: true, drawn: true })

    // Held through traffic that does not change a debt: a status frame of the run.
    await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'paused', sim_time_ms: 1000, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
    expect(await pair(page), 'a status frame keeps the pair').toEqual(stated)

    // A clearing that changes nothing: no false "was".
    await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-p039-d-0', cleared_cycles: 0, cleared_amount: '0.00', cycle_edges: [], edge_patch: [] })
    await expect.poll(async () => (await pair(page)).beforeShown, { message: 'a clearing that changes nothing drops the pair' }).toBe(false)
    expect(await shownTotal(page)).toBe(`25.00 ${EQ}`)

    // The next debt-changing event states its own pair.
    await pushEvent(page, {
      type: 'tx.updated', from: 'e', to: 'd', amount: '2.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
      edge_patch: [{ source: 'd', target: 'e', used: '27.00', available: '73.00' }],
    })
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the payment was applied' }).toBe(`27.00 ${EQ}`)
    const next = await pair(page)
    expect({ before: next.before, after: next.after, beforeShown: next.beforeShown }, 'the payment\'s pair: was 25.00, now 27.00')
      .toEqual({ before: '25.00', after: `27.00 ${EQ}`, beforeShown: true })
  })
}

// The widest first view the demo shows: the 100-node scene (its full legend), Russian, 1366x768. Here the inline form
// of the first build of slice D (e10ac818: "Общий долг было X → стало Y" inside the row) wrapped the main row of the
// bottom bar to two lines, 42 -> 80 px: this test was red on it (`the bottom bar keeps its place and size`).
test('039 D [ru] [1366x768, 100 nodes] - a "was" does not move or resize the bottom bar', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 768 })
  const snapshot = fixtureSnapshot100()
  const l = snapshot.links[0]!
  await boot(page, { snapshot, clock: false, query: '&lang=ru' })
  const settled = await barBox(page)
  expect((await pair(page)).beforeShown, 'counter-check: settled, no "was"').toBe(false)
  // A payment that moves this line's debt by 7.00 (the scene's total is about 88 thousand: a realistic magnitude).
  const used = (Number(l.used ?? 0) + 7).toFixed(2)
  await pushEvent(page, {
    type: 'tx.updated', from: String(l.target), to: String(l.source), amount: '7.00', amount_flyout: false, ttl_ms: 1200,
    edges: [{ from: String(l.target), to: String(l.source) }], edge_patch: [{ source: l.source, target: l.target, used, available: '1.00' }],
  })
  await expect.poll(async () => (await pair(page)).beforeShown, { message: 'counter-check: the payment states its "was"' }).toBe(true)
  expect(await barBox(page), 'the bottom bar keeps its place and size with a "was"').toEqual(settled)
  expect(await wasPlaced(page), 'the "was" is whole on screen, just above the total').toEqual({ inViewport: true, abovePanel: true, drawn: true })
})
