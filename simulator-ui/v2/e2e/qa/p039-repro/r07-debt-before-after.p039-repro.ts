/**
 * 039 T3901 - R7 (total debt "was -> now" after a clearing).
 *
 * Corrected form (P-2, P-5) and the review of 2026-10-12: the two totals are asserted as SEMANTIC FIELDS, in order and
 * with the equivalent - "before 55.00 UAH", then "after 25.00 UAH" - not as substrings of a bar (a bar saying
 * "25 -> 55" would have passed). The clearing is really applied (its `edge_patch` reaches the snapshot through the
 * product's own SSE reader and pipeline). Totals are taken from the lines, immediately before and after the patches of
 * the accepted event: never as `after + cleared_amount`, and never from the value the bar happened to DISPLAY.
 *
 * The scene separates the three candidates for "before":
 *   - settled and displayed at the start ................ 48.00  (cycle a>b>c>a of 10 each, and d>e of 18)
 *   - a payment raises d>e to 25 IMMEDIATELY before the clearing: the authoritative total is 55.00, while the bar,
 *     which is debounced (`useSystemBalance.ts`, 100 ms), may still display 48.00
 *   - the clearing takes 10 off each of the three lines: after = 25.00; the event says `cleared_amount: "10.00"`,
 *     so the forbidden derivation gives 35.00.
 * Right: before 55.00, after 25.00. Wrong: 48.00 (the displayed value), 35.00 (after + cleared_amount).
 *
 * The names of the two fields are in `contract.ts` (`DEBT.before`, `DEBT.after`, inside `DEBT.bar`): the one place
 * slice D - or slice E, when variant A moves the pair into the bottom bar - renames them.
 *
 * SLICE D must turn this test green; plain failing test, no `test.fail`.
 *
 * LIMITS. (1) NOT ACHIEVED: telling "the displayed value" from "the authoritative total". The two frames are written
 * in one call, yet on the base the bar displays 48.00, then 55.00, then 25.00 (three runs of three, 2026-10-12): the
 * bar is not behind the data when the clearing arrives, so an implementation that wrongly captured the DISPLAYED total
 * would say 55.00 here as well. What the bar displayed is recorded (`displayedTotals`,
 * `discriminatesTheDisplayedValue`), not asserted. The case does separate both from the stale 48.00 and from
 * 25 + 10 = 35.00. The displayed-versus-authoritative case belongs to a composable test of slice D, where the
 * debounce timer can be held by a fake clock; it cannot be written before the capture exists.
 * (2) On the base the first red assertion is the finding; the hold and reset steps after it prove nothing
 * until it is green. (3) Replay, an incomplete patch and a change of equivalent (P-5) are not walked.
 */
import { expect, test, type Page } from '@playwright/test'

import { EQ, RUN_ID, SCENARIO_ID, boot, pushEvent, ringSnapshot, type QaSnapshot } from '../stand.js'
import { DEBT } from './contract.js'

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

const bar = (page: Page) => page.locator(DEBT.bar).first()
const barText = async (page: Page) => ((await bar(page).textContent()) ?? '').replace(/\s+/g, ' ').trim()

/** The pair as the page states it: the two fields, their texts, and whether "before" comes first in the document. */
async function pair(page: Page) {
  return page.evaluate(({ bar, before, after }) => {
    const root = document.querySelector(bar)
    const b = root?.querySelector(before) ?? null
    const a = root?.querySelector(after) ?? null
    const text = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    const shown = (el: Element | null) => !!el && (el as HTMLElement).getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
    return {
      before: b ? text(b) : null, after: a ? text(a) : null, beforeShown: shown(b), afterShown: shown(a),
      beforeComesFirst: !!a && !!b && !!(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING),
    }
  }, DEBT)
}

test('039 R7 - after a clearing the system balance states the total debt BEFORE it and AFTER it, in that order and with the equivalent, until the next debt-changing event (today: only the new total)', async ({ page }) => {
  const snapshot = sceneWithDebts()
  await boot(page, {
    snapshot, clock: false,
    // The stand's own answer to trustlines-list says used 0.00 for every line; here the lines carry debts.
    beforeLoad: (p) => p.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ items: snapshot.links.map((l) => ({
        from_pid: l.source, from_name: l.source, to_pid: l.target, to_name: l.target, equivalent: EQ,
        limit: '100.00', used: l.used, reverse_used: '0.00', available: l.available, status: 'active',
      })) }),
    })),
  })
  await expect(bar(page), 'counter-check: the system balance bar is on screen').toBeVisible()
  await expect.poll(() => barText(page), { message: 'counter-check: the settled bar shows the known total, 48.00' }).toContain('48.00')

  // A debt change IMMEDIATELY before the clearing: d -> e goes from 18 to 25, the authoritative total from 48 to 55.
  // The two frames are written in ONE call into the stream, so that the clearing follows the payment within the
  // debounce of the bar (100 ms). What the bar displays from here on is recorded in the page: every total it shows.
  const frame = (seq: number, evt: Record<string, unknown> & { type: string }) => {
    const id = `evt_${RUN_ID}_${String(seq).padStart(6, '0')}`
    return `id: ${id}\nevent: ${evt.type}\ndata: ${JSON.stringify({ event_id: id, ts: '2026-02-01T00:00:01.000Z', equivalent: EQ, ...evt })}\n\n`
  }
  const payment = frame(900001, {
    type: 'tx.updated', from: 'e', to: 'd', amount: '7.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
    edge_patch: [{ source: 'd', target: 'e', used: '25.00', available: '75.00' }],
  })
  const clearing = frame(900002, {
    type: 'clearing.done', plan_id: 'plan-p039-r7', cleared_cycles: 1, cleared_amount: '10.00',
    cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
    edge_patch: [['a', 'b'], ['b', 'c'], ['c', 'a']].map(([source, target]) => ({ source, target, used: '0.00', available: '100.00' })),
  })
  const takenBy = await page.evaluate(async ({ sel, frames }) => {
    const el = document.querySelector(sel)!
    const seen: string[] = []
    ;(window as unknown as { __p039Bar: string[] }).__p039Bar = seen
    const read = () => { const t = /Total Debt\s*([\d.]+)/.exec((el.textContent ?? '').replace(/\s+/g, ' '))?.[1]; if (t && seen[seen.length - 1] !== t) seen.push(t) }
    read()
    new MutationObserver(read).observe(el, { childList: true, subtree: true, characterData: true })
    const sse = (window as unknown as { __qaSse: { push: (t: string) => Promise<number> } }).__qaSse
    return Promise.all(frames.map((f) => sse.push(f)))
  }, { sel: DEBT.bar, frames: [payment, clearing] })
  expect(takenBy, 'counter-check: the product took both frames (one connection each)').toEqual([1, 1])
  await expect.poll(() => barText(page), { message: 'counter-check: both events were APPLIED - the bar shows the new total, 25.00' }).toContain('25.00')
  const after = await barText(page)
  const stated = await pair(page)
  const displayedTotals = await page.evaluate(() => (window as unknown as { __p039Bar: string[] }).__p039Bar)
  // The bar never displayed 55.00: an implementation that takes "before" from the displayed value would say 48.00.
  const row = { displayedTotals, discriminatesTheDisplayedValue: !displayedTotals.includes('55.00'), barAfter: after, stated }
  console.log('[039 R7]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-R7', description: JSON.stringify(row) })

  expect.soft({ before: stated.beforeShown, after: stated.afterShown }, `the bar states the two totals as its own fields (${DEBT.before}, ${DEBT.after}), both visible`).toEqual({ before: true, after: true })
  expect.soft(stated.before ?? '', 'BEFORE is the authoritative total just before the patches of the clearing: 55.00 (not the displayed 48.00, not 25 + 10 = 35.00)').toMatch(/(^|[^\d.])55\.00(?![\d])/)
  expect.soft(stated.after ?? '', 'AFTER is the total once the patches are applied: 25.00').toMatch(/(^|[^\d.])25\.00(?![\d])/)
  expect.soft(`${stated.before ?? ''} | ${stated.after ?? ''}`, `the pair carries its equivalent (${EQ})`).toContain(EQ)
  expect.soft(stated.beforeComesFirst, 'BEFORE comes before AFTER in the document').toBe(true)

  // Held through traffic that does not change a debt: a status frame of the run.
  await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'paused', sim_time_ms: 1000, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
  const held = await pair(page)
  expect.soft({ before: held.before, after: held.after }, 'a status frame does not take the pair away or change it').toEqual({ before: stated.before, after: stated.after })
  expect.soft(held.beforeShown, 'and the "before" field is still shown').toBe(true)

  // Reset by the next event that changes a debt: a payment that raises d -> e from 25 to 27.
  await pushEvent(page, {
    type: 'tx.updated', from: 'e', to: 'd', amount: '2.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
    edge_patch: [{ source: 'd', target: 'e', used: '27.00', available: '73.00' }],
  })
  await expect.poll(() => barText(page), { message: 'counter-check: the payment was applied - the bar shows 27.00' }).toContain('27.00')
  const next = await pair(page)
  console.log('[039 R7 next]', JSON.stringify({ held, next, bar: await barText(page) }))
  expect.soft(next.beforeShown, 'after the next debt-changing event the "before" of the clearing is no longer shown').toBe(false)
})
