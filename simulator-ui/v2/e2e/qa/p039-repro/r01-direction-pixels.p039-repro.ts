/**
 * 039 T3901 - R1 (direction of a trust line), PIXEL tier on a small scene. Geometry: `src/p039-repro/r01-direction-geometry...`.
 *
 * Corrected form (P-1, P-2): the arrowhead is at the DEBTOR (the target of `source -> target`); a one-way line and a
 * mutual pair are judged separately (in a mutual pair both ends are a debtor's); the probe does not take node paint.
 *
 * SLICE B, PR 2 (2026-10-12) drew the marker: this test is GREEN since then. It stays here as the reproducer in its
 * own (debug-only) config; the counted checks of the same property are `e2e/qa/direction.qa.ts`.
 *
 * THE PROBE moved to `e2e/qa/directionProbe.ts` (one probe for this file and the stand) and was re-read against
 * decision 4 of the slice's design before this test was relied on:
 *  - its window was 26 px along the line and 9 px to either side, and "a marker" was six points more than a bare
 *    stretch - both taken from the synthetic triangles this file painted (7 x 5 and 10 x 10 px). The marker of
 *    decision 4 is 5 x 4 px, 2 px off the rim: in that window the line outnumbered it, and a CORRECT marker was within
 *    the noise of the threshold. The window is now the size of the marker (5 px along, 5 px to either side, sampled
 *    every half pixel) and the threshold is the caller's, held by a synthetic marker of decision 4;
 *  - the synthetic controls painted a marker at the debtor end of the bare frame; the product now paints one there
 *    itself, so the control is painted at the CREDITOR end of a copy of the frame: "no marker at the creditor end" is
 *    shown not to be blindness of the probe.
 * BACKLOG 039-3 (d) is NOT closed by this: the thresholds are held at the angles of this one layout and its zoom.
 *
 * LIMITS. Nodes with ONE line each are needed (the control directions must be empty) - not a probe for the 100-node
 * scene. A marker fainter than `STANDS_OUT` is not seen. Colour and shape are not judged here. Quality `low` (the
 * stand's browser), theme `hud`, the zoom the layout gives.
 */
import { expect, test } from '@playwright/test'

import { markerSeen, minLineLengthPx, probeEnd, type End } from '../directionProbe.js'
import { boot, camera, ringSnapshot, screenNodes, waitLoopAsleep, type Pt, type QaSnapshot } from '../stand.js'

/** `viz_size` of the stand's nodes is 24 x 24: a circle of this radius in SCREEN px, at any zoom (`render/linkGeometry.ts`). */
const NODE_RADIUS_SCREEN_PX = 12
/** Samples (0.5 px grid) a marker adds over a bare stretch of its line; the same number as `direction.qa.ts`, see there. */
const MIN_EXTRA = 12

/** One one-way line a -> b (a is the creditor, b the debtor) and one mutual pair c <-> d. Every node has one neighbour. */
function directionScene(): QaSnapshot {
  const s = ringSnapshot()
  const proto = s.links[0]!
  const link = (source: string, target: string, used: string) => ({ ...proto, source, target, used, available: (100 - Number(used)).toFixed(2) })
  return { ...s, nodes: s.nodes.filter((n) => ['a', 'b', 'c', 'd'].includes(n.id)), links: [link('a', 'b', '10.00'), link('c', 'd', '10.00'), link('d', 'c', '5.00')] }
}

test('039 R1 - pixels: a marker at the debtor end of a one-way line and at both ends of a mutual pair, none at a creditor-only end', async ({ page }) => {
  await boot(page, { snapshot: directionScene(), clock: false })
  await waitLoopAsleep(page)
  const n = await screenNodes(page)
  const [a, b, c, d] = ['a', 'b', 'c', 'd'].map((id) => n.get(id)!) as [Pt, Pt, Pt, Pt]
  const lengths = { ab: Math.hypot(b.x - a.x, b.y - a.y), cd: Math.hypot(d.x - c.x, d.y - c.y) }
  expect(Math.min(lengths.ab, lengths.cd), 'the lines are long enough for an end window and a bare stretch from each end').toBeGreaterThan(minLineLengthPx(NODE_RADIUS_SCREEN_PX))

  const probe = (at: Pt, to: Pt, synthetic: Parameters<typeof probeEnd>[4] = null) => probeEnd(page, at, to, NODE_RADIUS_SCREEN_PX, synthetic)
  const ends = { oneWayDebtorEnd: await probe(b, a), oneWayCreditorEnd: await probe(a, b), mutualFirstEnd: await probe(c, d), mutualSecondEnd: await probe(d, c) }
  // The marker of decision 4 (tip 2 px off the rim, 5 long, 4 wide) in the colour of a trust line, on a COPY of the frame.
  const d4 = { tipGapPx: 2, lengthPx: 5, halfWidthPx: 2, fill: 'rgb(100, 116, 139)' }
  const synthetic = { decision4AtCreditorEnd: await probe(a, b, { at: a, to: b, ...d4 }) }
  const row = { zoom: (await camera(page)).zoom, nodeRadiusScreenPx: NODE_RADIUS_SCREEN_PX, lengths, minExtra: MIN_EXTRA, ends, synthetic }
  console.log('[039 R1 pixels]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-R1-pixels', description: JSON.stringify(row) })

  // Counter-checks of the probe itself.
  for (const [name, e] of Object.entries(ends)) {
    expect(e.bare, `counter-check: the line is seen on its bare stretch near ${name} (samples that stand out from the canvas beside it)`).toBeGreaterThan(0)
    // The noise enters `markerSeen` three times over; that an end is not "bare" by noise is the positive control below.
    expect(e.noise, `counter-check: the control windows at ${name} hold nearly the same paint`).toBeLessThan(MIN_EXTRA)
  }
  expect(markerSeen(synthetic.decision4AtCreditorEnd, MIN_EXTRA), 'POSITIVE CONTROL: the marker of decision 4 painted at the creditor end of a copy of the frame is seen there').toBe(true)

  const said = (e: End) => `stands out at the end: ${e.standsOut} samples, on a bare stretch of the same line: ${e.bare}`
  expect.soft(markerSeen(ends.oneWayDebtorEnd, MIN_EXTRA), `a marker at the debtor end b of a -> b (${said(ends.oneWayDebtorEnd)})`).toBe(true)
  expect.soft(markerSeen(ends.mutualFirstEnd, MIN_EXTRA), `a marker at c of the mutual pair (${said(ends.mutualFirstEnd)})`).toBe(true)
  expect.soft(markerSeen(ends.mutualSecondEnd, MIN_EXTRA), `a marker at d of the mutual pair (${said(ends.mutualSecondEnd)})`).toBe(true)
  expect.soft(markerSeen(ends.oneWayCreditorEnd, MIN_EXTRA), `NO marker at the creditor end a of the one-way line a -> b (${said(ends.oneWayCreditorEnd)})`).toBe(false)
})
