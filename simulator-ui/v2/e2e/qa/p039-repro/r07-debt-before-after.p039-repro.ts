/**
 * 039 T3901 - R7 (total debt "was -> now" after a clearing).
 *
 * Corrected form (P-2, P-5): known UNEQUAL totals, a clearing that is really applied (its `edge_patch` reaches the
 * snapshot through the product's own SSE reader and pipeline), the pair held until the next DEBT-CHANGING event and
 * reset by it. Totals are taken from the lines, never as `now + cleared_amount`.
 *
 * The scene is built so that the forbidden derivation gives a different number: a cycle of three lines is cleared by
 * 10 each, so the total falls by 30 (48 -> 18) while the event says `cleared_amount: "10.00"` (18 + 10 = 28).
 *
 * LIMITS. (1) The numbers are looked for in the system balance bar (`[aria-label="System balance"]`) only; a
 * "was -> now" shown elsewhere (variant A moves it into the bottom bar) needs the selector moved with it. (2) Two
 * numbers in one bar are judged, not their order, wording or arrow. (3) On the base the first red assertion is the
 * finding; the hold and reset steps after it are then trivially true or false and prove nothing until it is green.
 * (4) Replay, an incomplete patch and a change of equivalent (P-5) are not walked.
 *
 * SLICE D must turn this test green; plain failing test, no `test.fail`.
 */
import { expect, test, type Page } from '@playwright/test'

import { EQ, RUN_ID, SCENARIO_ID, boot, pushEvent, ringSnapshot, type QaSnapshot } from '../stand.js'

const USED: Record<string, string> = { 'a>b': '10.00', 'b>c': '10.00', 'c>a': '10.00', 'd>e': '18.00' }

/** Six participants; a debt cycle a -> b -> c -> a of 10 each and one more debt of 18: total 48.00. */
function sceneWithDebts(): QaSnapshot {
  const s = ringSnapshot()
  const pairs: Array<[string, string]> = [['a', 'b'], ['b', 'c'], ['c', 'a'], ['d', 'e'], ['e', 'f'], ['f', 'd']]
  const links = pairs.map(([source, target]) => {
    const used = USED[`${source}>${target}`] ?? '0.00'
    return { ...s.links[0]!, source, target, used, available: (100 - Number(used)).toFixed(2), trust_limit: '100.00' }
  })
  return { ...s, links }
}

const bar = (page: Page) => page.locator('[aria-label="System balance"]').first()
const barText = async (page: Page) => ((await bar(page).textContent()) ?? '').replace(/\s+/g, ' ').trim()

test('039 R7 - after a clearing the system balance shows the total debt before AND after it, until the next debt-changing event (today: only the new total)', async ({ page }) => {
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
  await expect.poll(() => barText(page), { message: 'counter-check: before the clearing the bar shows the known total, 48.00' }).toContain('48.00')
  const before = await barText(page)

  await pushEvent(page, {
    type: 'clearing.done', plan_id: 'plan-p039-r7', cleared_cycles: 1, cleared_amount: '10.00',
    cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
    edge_patch: [['a', 'b'], ['b', 'c'], ['c', 'a']].map(([source, target]) => ({ source, target, used: '0.00', available: '100.00' })),
  })
  await expect.poll(() => barText(page), { message: 'counter-check: the clearing was APPLIED - the bar shows the new total, 18.00' }).toContain('18.00')
  const after = await barText(page)
  console.log('[039 R7]', JSON.stringify({ before, after }))
  test.info().annotations.push({ type: '039-R7', description: JSON.stringify({ before, after }) })

  expect.soft(after, 'the bar still names the total before the clearing (48.00) next to the new one (18.00)').toContain('48.00')
  expect.soft(after, 'the forbidden derivation "now + cleared_amount" (28.00) is not what is shown').not.toContain('28.00')

  // Held through traffic that does not change a debt: a status frame of the run.
  await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'paused', sim_time_ms: 1000, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
  const held = await barText(page)
  expect.soft(held, 'a status frame does not take the pair away').toContain('48.00')

  // Reset by the next event that changes a debt: a payment that raises d -> e from 18 to 20.
  await pushEvent(page, {
    type: 'tx.updated', from: 'e', to: 'd', amount: '2.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
    edge_patch: [{ source: 'd', target: 'e', used: '20.00', available: '80.00' }],
  })
  await expect.poll(() => barText(page), { message: 'counter-check: the payment was applied - the bar shows 20.00' }).toContain('20.00')
  const next = await barText(page)
  console.log('[039 R7 next]', JSON.stringify({ held, next }))
  expect.soft(next, 'after the next debt-changing event the pair of the clearing is gone').not.toContain('48.00')
})
