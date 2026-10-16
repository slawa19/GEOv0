/**
 * 039 slice B, PR 2 - the direction marker of a trust line in a real page (decisions 4 and 9 of the slice's design).
 *
 * MONEY SEMANTICS (AGENTS.md section 8; `docs/ru/02-protocol-spec.md:505-512`). A trust line `source -> target` is
 * creditor -> debtor; its arrowhead stands at the TARGET (the potential debtor) and shows TRUST - it is there when
 * nothing is owed. A payment travels sender -> receiver, AGAINST the line under which the obligation arises: the
 * payer `b` of the line `a -> b` is the debtor, and its beam leaves the node the arrowhead points at.
 *
 * Three tests, all counted in the gate (they hold a class-1 risk of the slice's design: the direction turned round):
 *  1. PIXELS, small scene (`directionProbe.ts`): marker at the debtor end of a one-way line and none at its creditor
 *     end; at both ends of a mutual pair; at the debtor end of a line with NO debt. The marker is the colour of the
 *     legend's arrow, and the legend says `Trust → potential debtor`.
 *  2. THREE PICTURES: trust without debt, existing debt, a moving payment - told apart by the marker, the strength of
 *     the line body and paint on the FX canvas. The beam runs debtor > creditor over a line whose marker points at the
 *     debtor: both are on screen, and the marker is neither repainted nor moved by the effect.
 *  3. THE 100-NODE SCENE: every marker the product draws in a pass is at the TARGET end of a line of the snapshot
 *     (positions compared with a model written here from the layout the product reports), every line with room has
 *     one, and the number of lines WITHOUT one is the recorded number. Zooming out removes markers exactly where the
 *     model says the room is gone - the count is not blind to a disappearance.
 *
 * LIMITS. (1) The pixel probe needs nodes with one line each and counts paint, not shape (shape and order are
 * `src/render/baseGraph.direction.test.ts`). (2) The model of test 3 repeats the product's rule of where a line meets
 * a node (circle: the larger half-size; organisation: the rectangle) - an error common to both is not seen; the rule
 * itself is `linkGeometry.test.ts`. (3) Quality `low`, no GPU, DPR 1, theme `hud`; the base canvas is byte-identical
 * between the two themes (`data-colours.qa.ts`), a DPR of 2 is not run. (4) Nothing here judges whether the picture
 * reads well: that is the owner's look at the frames.
 */
import { expect, test, type Page } from '@playwright/test'

import { compareWithModel, lastPass, legibility, markerSeen, minLineLengthPx, modelOf, probeEnd, recordMarkers, SHIPPED_ARROW, zoomTo, type End } from './directionProbe.js'
import { boot, camera, fixtureSnapshot100, frames, pushEvent, ringSnapshot, sampleCanvas, screenNodes, txUpdated, waitLoopAsleep, type Pt, type QaSnapshot } from './stand.js'

/** `viz_size` of the small scene is 24 x 24: a circle of this radius in SCREEN px at any zoom. */
const R = 12
/**
 * A marker is at least this many samples (0.5 px grid) more than a bare stretch of its line. Decision 4's triangle is
 * 10 px2 and 4 of its 5 px of length are in the window: about 38 samples, of which the line body covers some.
 * MEASURED 2026-10-12 on this scene (the `[039 B2 direction pixels]` line of a run): the four ends with a marker give
 * 33, 39, 45 and 62 more than their bare stretch, the two creditor ends 0 and 0, the synthetic marker of decision 4
 * painted at those creditor ends 40 and 50. The threshold is about a third of the smallest marker; it is not derived
 * for every angle of a line (BACKLOG 039-3 (d)) - the synthetic control of each run is what holds it.
 */
const MIN_EXTRA = 12

/** `link_alpha_key` of `app/core/simulator/viz_rules.py` for an active line: the alpha the backend gives a line by its debt. */
function alphaKeyOf(used: number, limit: number): string {
  const r = Math.abs(used) / limit
  return r >= 0.75 ? 'hi' : r >= 0.4 ? 'active' : r >= 0.15 ? 'muted' : 'bg'
}

/**
 * a -> b: one-way, b owes 80 of 100.   c <-> d: mutual, d owes c 50 and c owes d 5.   e -> f: one-way, NOTHING owed.
 * Every node has one neighbour (the probe needs it); the keys of a line are what the backend gives it for its debt.
 */
function directionScene(): QaSnapshot {
  const s = ringSnapshot()
  const proto = s.links[0]!
  const line = (source: string, target: string, used: number) =>
    ({ ...proto, source, target, used: used.toFixed(2), available: (100 - used).toFixed(2), viz_alpha_key: alphaKeyOf(used, 100) })
  return { ...s, links: [line('a', 'b', 80), line('c', 'd', 50), line('d', 'c', 5), line('e', 'f', 0)] }
}

const rgbOf = (css: string): [number, number, number] => {
  const m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(css)
  if (!m) throw new Error(`not an rgb colour: "${css}"`)
  return [Number(m[1]), Number(m[2]), Number(m[3])]
}

/** The legend's arrow: its words and the colour a browser computes for its head. */
async function legendArrow(page: Page): Promise<{ text: string; rgb: [number, number, number] }> {
  const entry = page.locator('[data-testid="legend"] [data-legend-direction]')
  await expect(entry, 'the legend has the direction entry when the scene has trust lines').toBeVisible()
  return { text: (await entry.locator('.ds-legend__text').textContent()) ?? '', rgb: rgbOf(await entry.locator('polygon').evaluate((el) => getComputedStyle(el).fill)) }
}

const said = (e: End) => `stands out at the end: ${e.standsOut} samples, on a bare stretch of the same line: ${e.bare}, of the legend's colour: ${e.solid}`

test('039 B2: pixels - a marker at the debtor end of a one-way line and none at its creditor end, at both ends of a mutual pair, and on a line with no debt; in the colour of the legend arrow', async ({ page }) => {
  await boot(page, { snapshot: directionScene(), clock: false })
  await waitLoopAsleep(page)
  const legend = await legendArrow(page)
  expect(legend.text, 'the legend names trust and the end the arrow stands at').toBe('Trust → potential debtor')
  // The arrow of the legend comes before its words and points right, at them: its tip is its rightmost point.
  const arrow = await page.locator('[data-legend-direction] polygon').evaluate((el) => {
    const pts = (el.getAttribute('points') ?? '').trim().split(/\s+/).map((p) => p.split(',').map(Number))
    const box = el.getBoundingClientRect()
    const text = el.closest('[data-legend-direction]')!.querySelector('.ds-legend__text')!.getBoundingClientRect()
    const tipX = Math.max(...pts.map((p) => p[0]!))
    return { tips: pts.filter((p) => p[0] === tipX).length, headRight: box.right, textLeft: text.left }
  })
  expect(arrow.tips, 'one tip, at the right end of the arrow').toBe(1)
  expect(arrow.headRight, 'the head is on the side of the words').toBeLessThanOrEqual(arrow.textLeft)

  const n = await screenNodes(page)
  const at = (id: string) => n.get(id)!
  const len = (p: string, q: string) => Math.hypot(at(q).x - at(p).x, at(q).y - at(p).y)
  expect(Math.min(len('a', 'b'), len('c', 'd'), len('e', 'f')), 'the lines are long enough for an end window and a bare stretch from each end').toBeGreaterThan(minLineLengthPx(R))

  const probe = (end: string, other: string, synthetic: Parameters<typeof probeEnd>[4] = null) => probeEnd(page, at(end), at(other), R, synthetic, legend.rgb)
  const ends = {
    oneWayDebtor_b: await probe('b', 'a'), oneWayCreditor_a: await probe('a', 'b'),
    mutual_c: await probe('c', 'd'), mutual_d: await probe('d', 'c'),
    noDebtDebtor_f: await probe('f', 'e'), noDebtCreditor_e: await probe('e', 'f'),
  }
  // POSITIVE CONTROL of "none at the creditor end": the triangle of decision 4 (tip 2 px off the rim, 5 long, 4 wide -
  // SMALLER than the shipped marker since 2026-10-16, so a stricter control) painted at the two creditor ends on a COPY
  // of the frame, in the legend's colour, is seen there by the same reading.
  const css = `rgb(${legend.rgb.join(',')})`
  const d4 = (end: string, other: string) => ({ at: at(end), to: at(other), tipGapPx: 2, lengthPx: 5, halfWidthPx: 2, fill: css })
  const synthetic = { atCreditor_a: await probe('a', 'b', d4('a', 'b')), atCreditor_e: await probe('e', 'f', d4('e', 'f')) }
  const row = { zoom: (await camera(page)).zoom, legend, minExtra: MIN_EXTRA, ends, synthetic }
  console.log('[039 B2 direction pixels]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-B2-direction-pixels', description: JSON.stringify(row) })

  // The noise of the method enters `markerSeen` (three times over), so a noisy end cannot be "seen" by accident; that it
  // cannot be "not seen" by noise either is the positive control below, painted at the very ends claimed to be bare.
  for (const [name, e] of Object.entries(ends)) expect(e.noise, `counter-check: the control windows at ${name} hold nearly the same paint`).toBeLessThan(MIN_EXTRA)
  for (const name of ['oneWayDebtor_b', 'oneWayCreditor_a', 'mutual_c', 'mutual_d'] as const) expect(ends[name].bare, `counter-check: the line body is seen on its bare stretch near ${name}`).toBeGreaterThan(0)
  for (const [name, e] of Object.entries(synthetic)) {
    expect(markerSeen(e, MIN_EXTRA), `POSITIVE CONTROL: the marker of decision 4 painted at ${name} on a copy of the frame is seen there (${said(e)})`).toBe(true)
    expect(e.solid, `POSITIVE CONTROL: and its colour is found (${name})`).toBeGreaterThan(0)
  }

  // The debtor ends: more paint than the line has, and of the legend's colour.
  for (const name of ['oneWayDebtor_b', 'mutual_c', 'mutual_d', 'noDebtDebtor_f'] as const) {
    expect.soft(markerSeen(ends[name], MIN_EXTRA), `a marker at ${name} (${said(ends[name])})`).toBe(true)
    expect.soft(ends[name].solid, `the marker at ${name} is the colour of the legend's arrow (${said(ends[name])})`).toBeGreaterThan(0)
  }
  // The creditor ends of the two one-way lines: the line and nothing else.
  for (const name of ['oneWayCreditor_a', 'noDebtCreditor_e'] as const) {
    expect.soft(markerSeen(ends[name], MIN_EXTRA), `NO marker at ${name} (${said(ends[name])})`).toBe(false)
    expect.soft(ends[name].solid, `nothing of the marker's colour at ${name}`).toBe(0)
  }
  // That the triangle is THE SAME with and without debt is held on the list of drawing calls
  // (`src/render/baseGraph.direction.test.ts`): here lines at different angles rasterise to different counts
  // (measured 15 to 29 samples of the marker's colour), so the counts are logged and not compared.
})

test('039 B2: trust without debt, existing debt and a moving payment are three pictures; the beam runs debtor > creditor over a line whose marker points at the debtor, and does not repaint or move it', async ({ page }) => {
  await boot(page, { snapshot: directionScene(), clock: true })
  const legend = await legendArrow(page)
  const n = await screenNodes(page)
  const at = (id: string) => n.get(id)!
  const probe = (end: string, other: string) => probeEnd(page, at(end), at(other), R, null, legend.rgb)

  // Points of the line a - b by thirds, from the rim of b (the payer) to the rim of a: where the beam is.
  const along = (from: Pt, to: Pt, t0: number, t1: number): Pt[] => {
    const l = Math.hypot(to.x - from.x, to.y - from.y)
    const u = { x: (to.x - from.x) / l, y: (to.y - from.y) / l }
    return Array.from({ length: 30 }, (_, i) => { const d = R + (l - 2 * R) * (t0 + ((t1 - t0) * i) / 29); return { x: from.x + u.x * d, y: from.y + u.y * d } })
  }
  const bands = { nearPayer_b: along(at('b'), at('a'), 0, 1 / 3), middle: along(at('b'), at('a'), 1 / 3, 2 / 3), nearPayee_a: along(at('b'), at('a'), 2 / 3, 1), otherLine_ef: along(at('f'), at('e'), 0, 1) }
  const fx = async () => { const s = await sampleCanvas(page, bands); return { b: s.nearPayer_b!.lit, mid: s.middle!.lit, a: s.nearPayee_a!.lit, ef: s.otherLine_ef!.lit } }
  /** The base canvas round the debtor end of a -> b, byte for byte: 40 x 40 px centred on the tip of the marker. */
  const markerBytes = () => page.evaluate(({ b, a, r }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const l = Math.hypot(a.x - b.x, a.y - b.y)
    const tip = { x: b.x + ((a.x - b.x) / l) * (r + 2), y: b.y + ((a.y - b.y) / l) * (r + 2) }
    const d = c.getContext('2d')!.getImageData(Math.round((tip.x - box.x) * k) - 20, Math.round((tip.y - box.y) * k) - 20, 40, 40).data
    let h = 0
    for (let i = 0; i < d.length; i += 1) h = (h * 31 + d[i]!) | 0
    return h
  }, { b: at('b'), a: at('a'), r: R })

  // --- two pictures at rest ----------------------------------------------------------------------------------------
  const rest = { debt_b: await probe('b', 'a'), debtCreditor_a: await probe('a', 'b'), noDebt_f: await probe('f', 'e'), fx: await fx(), bytes: await markerBytes() }
  const picture = (end: End, fxLit: number) => ({ marker: markerSeen(end, MIN_EXTRA) && end.solid > 0, bodyStrong: end.bare > 0, fx: fxLit > 0 })
  const trustWithoutDebt = picture(rest.noDebt_f, rest.fx.ef)
  const existingDebt = picture(rest.debt_b, rest.fx.b + rest.fx.mid + rest.fx.a)
  expect(rest.noDebt_f.strength, 'the body of the line without debt IS drawn (faint: alpha key `bg`)').toBeGreaterThan(0)
  expect(rest.debt_b.strength, `the body of the line with debt is several times stronger than that of the line without (${rest.debt_b.strength} against ${rest.noDebt_f.strength})`).toBeGreaterThan(3 * rest.noDebt_f.strength)

  // --- the payment: b pays a -----------------------------------------------------------------------------------------
  await pushEvent(page, txUpdated(['b', 'a'], '10.00', { amount_flyout: false }))
  const flight: Array<{ frame: number; fx: Awaited<ReturnType<typeof fx>>; markerAt_b: boolean; markerAt_a: boolean; solid_b: number; solid_a: number; sameBytes: boolean }> = []
  for (let frame = 3; frame <= 75; frame += 3) {
    await frames(page, 3)
    const [b, a] = [await probe('b', 'a'), await probe('a', 'b')]
    flight.push({ frame, fx: await fx(), markerAt_b: markerSeen(b, MIN_EXTRA), markerAt_a: markerSeen(a, MIN_EXTRA), solid_b: b.solid, solid_a: a.solid, sameBytes: (await markerBytes()) === rest.bytes })
  }
  const lit = flight.filter((f) => f.fx.b + f.fx.mid + f.fx.a > 0)
  const row = { rest: { ...rest, trustWithoutDebt, existingDebt }, flight }
  console.log('[039 B2 three pictures]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-B2-three-pictures', description: JSON.stringify(row) })

  expect(lit.length, 'the payment is seen on the FX canvas along the line a - b').toBeGreaterThan(3)
  const movingPayment = { marker: lit.every((f) => f.markerAt_b && f.solid_b > 0), bodyStrong: existingDebt.bodyStrong, fx: true }
  expect({ trustWithoutDebt, existingDebt, movingPayment }, 'three different pictures').toEqual({
    trustWithoutDebt: { marker: true, bodyStrong: false, fx: false },
    existingDebt: { marker: true, bodyStrong: true, fx: false },
    movingPayment: { marker: true, bodyStrong: true, fx: true },
  })
  expect(flight.every((f) => f.fx.ef === 0), 'counter-check of the FX reading: nothing is painted along the line e - f, which carries no payment').toBe(true)

  // The beam leaves the payer b - the node the arrowhead points at - and arrives at the payee a: against the arrow.
  const first = lit[0]!
  const last = lit[lit.length - 1]!
  expect(first.fx.b, `the beam starts at the payer b, the DEBTOR end of a -> b (first lit frame ${first.frame}: ${JSON.stringify(first.fx)})`).toBeGreaterThan(0)
  expect(first.fx.a, 'and is not yet at the payee').toBe(0)
  expect(last.fx.a, `and ends at the payee a, the creditor (last lit frame ${last.frame}: ${JSON.stringify(last.fx)})`).toBeGreaterThan(0)
  expect(last.fx.b, 'having left the payer').toBe(0)

  // The marker under the beam: where it was, what it was - and nothing appears at the creditor end.
  for (const f of flight) {
    expect.soft(f.markerAt_b, `frame ${f.frame}: the marker is still at the debtor end b`).toBe(true)
    expect.soft(f.solid_b, `frame ${f.frame}: with the same amount of its colour`).toBe(rest.debt_b.solid)
    expect.soft(f.markerAt_a, `frame ${f.frame}: no marker has appeared at the creditor end a`).toBe(false)
    expect.soft(f.solid_a, `frame ${f.frame}: nothing of the marker's colour at a`).toBe(0)
    expect.soft(f.sameBytes, `frame ${f.frame}: the base canvas round the marker is byte-identical to the frame before the payment - the effect is on its own canvas`).toBe(true)
  }
})

// ---------------------------------------------------------------------------------------------------------------------
// The 100-node scene
// ---------------------------------------------------------------------------------------------------------------------

/**
 * Lines of the 100-node fixture that have NO marker at the layout and zoom the stand loads it with (1280 x 720, zoom
 * 1): the free line between the two rims is under the bound (`ARROW.minFreeGapPx`). A record (AGENTS.md section 6): more = markers are
 * disappearing - find it, do not move the number; it cannot be fewer.
 * 2026-10-12: 0 of 348 - first record (039 slice B, PR 2). Derived from the scene's measured gaps, not chosen: the
 * shortest free line is 23.2 px, 9.2 px above the bound; the 5th percentile is 74.7 px, the median 153.6 px. So on
 * the ordinary scene EVERY trust line shows its direction, and one missing marker fails this test.
 * 2026-10-16: still 0 with the measured size (length 7, bound 2 x (2 + 7) = 18 px): the shortest free line is 5.2 px
 * above the new bound.
 */
const LINES_WITHOUT_A_MARKER_100 = 0

test('039 B2: the 100-node scene - every marker is at the target (debtor) end of a line, every line with room has one, the lines without are the recorded few; zooming out removes exactly those that lose their room', async ({ page }) => {
  const snapshot = fixtureSnapshot100()
  await boot(page, { snapshot, clock: true, beforeLoad: recordMarkers })
  const legend = await legendArrow(page)
  // A pass with the settled layout: a pointer move wakes the loop.
  await page.mouse.move(640, 300)
  await frames(page, 3)
  const p0 = await lastPass(page)
  expect(p0.passes, 'the recorder saw passes of the base canvas').toBeGreaterThan(0)
  const atLoad = compareWithModel(p0.markers, await modelOf(page, snapshot))
  const styles = [...new Set(p0.markers.map((m) => m.style))]

  // Zoom out with the wheel: nodes keep their size on screen, the lines between them get shorter.
  const zoom0 = (await camera(page)).zoom
  for (let i = 0; i < 6; i += 1) { await page.mouse.wheel(0, 300); await frames(page, 6) }
  await frames(page, 30)
  const p1 = await lastPass(page)
  const zoomedOut = compareWithModel(p1.markers, await modelOf(page, snapshot))
  const zoom1 = (await camera(page)).zoom

  const row = { atLoad, zoom0, zoomedOut, zoom1, styles, legend: legend.rgb, shareWithoutMarkerPct: Math.round((atLoad.withoutRoom / atLoad.lines) * 1000) / 10 }
  console.log('[039 B2 100 nodes]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-B2-100-nodes', description: JSON.stringify(row) })

  expect(atLoad.lines, 'the fixture has 348 trust lines').toBe(348)
  expect(atLoad.linesWithRoomWithoutTheirMarker, 'every line with room has its marker at its TARGET end').toBe(0)
  expect(atLoad.markersThatAreNoLinesTargetEnd, 'and there is no other marker: none at a source end, none for a line without room, none twice').toBe(0)
  expect(atLoad.drawn, 'drawn = lines with room').toBe(atLoad.withRoom)
  expect(atLoad.withoutRoom, `lines without a marker (free line under the bound of ${SHIPPED_ARROW.minFreeGapPx} px: ${JSON.stringify(atLoad.belowBound)}) - the recorded number, two-sided`).toBe(LINES_WITHOUT_A_MARKER_100)
  // The colour on the canvas is the colour of the legend's arrow, for every marker of the scene.
  expect(styles, 'every marker has one fill style').toHaveLength(1)
  expect(styles[0]!.startsWith('#') ? [1, 3, 5].map((i) => parseInt(styles[0]!.slice(i, i + 2), 16)) : rgbOf(styles[0]!), 'and it is the colour the legend draws its arrow with').toEqual(legend.rgb)

  // ANTI-VACUUM: the count follows the room. After zooming out more lines are short, and exactly their markers go.
  expect(zoom1, 'the wheel zoomed out').toBeLessThan(zoom0 * 0.8)
  expect(zoomedOut.withoutRoom, 'zoomed out, more lines have no room').toBeGreaterThan(atLoad.withoutRoom + 5)
  expect(zoomedOut.linesWithRoomWithoutTheirMarker, 'zoomed out: every line that still has room keeps its marker').toBe(0)
  expect(zoomedOut.markersThatAreNoLinesTargetEnd, 'zoomed out: no marker is left for a line without room').toBe(0)
  expect(zoomedOut.drawn).toBe(zoomedOut.withRoom)
})

// ---------------------------------------------------------------------------------------------------------------------
// The shipped size is legible (039 slice B, PR 2, fix-delta of 2026-10-16; external review finding 2)
// ---------------------------------------------------------------------------------------------------------------------

/** At least this many markers of the 100-node scene must be measured at zoom 0.6, or the share proves nothing. */
const MIN_MEASURED_AT_06 = 3

test('039 B2: the shipped marker is DISTINGUISHABLE as an arrowhead at application zoom 0.6 - on the 100-node scene and on a line with no debt, in both themes', async ({ browser }) => {
  // The criterion is `legibility` of `directionProbe.ts` (contrast against a bare stretch of the same line, a base at
  // least twice as wide as a line at rest, a taper); the size it holds was DERIVED with it by the debug sweep
  // `p039-repro/marker-size.p039-repro.ts` - this test holds that the product still ships a size that passes.
  const rows: Record<string, unknown> = {}
  for (const theme of ['hud', 'saas'] as const) {
    for (const [name, snapshot, only] of [['scene100', fixtureSnapshot100(), undefined], ['noDebt', directionScene(), (k: string) => k === 'e>f']] as const) {
      // A fresh page for every boot: init scripts, routes and the fake clock of a page accumulate over boots.
      const context = await browser.newContext({ baseURL: test.info().project.use.baseURL, viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 })
      const pg = await context.newPage()
      await boot(pg, { snapshot, clock: true, query: `&theme=${theme}` })
      const rgb = (await legendArrow(pg)).rgb
      await zoomTo(pg, 0.6)
      await pg.mouse.move(5, 5)
      await frames(pg, 3)
      const l = await legibility(pg, await modelOf(pg, snapshot, SHIPPED_ARROW), rgb, only)
      rows[`${theme} ${name}`] = { measured: l.measured, hiddenUnderANode: l.hidden, notMeasured: l.notMeasured, legible: l.legible, share: l.share, failedBy: l.failedBy, baseWidths: l.baseWidths, tipWidths: l.tipWidths }
      if (name === 'scene100') {
        expect(l.measured, `${theme}: enough markers of the 100-node scene are measured at zoom 0.6 (the rest lie under a node, by design)`).toBeGreaterThanOrEqual(MIN_MEASURED_AT_06)
        expect(l.share, `${theme}: every measured marker of the 100-node scene is distinguishable (${JSON.stringify(l.failedBy)})`).toBeGreaterThanOrEqual(0.95)
      } else {
        expect(l.measured, `${theme}: the marker of the line with no debt is measured`).toBe(1)
        expect(l.legible, `${theme}: the marker of the line with NO debt is distinguishable (${JSON.stringify(l.failedBy)}; base ${l.baseWidths[0]} px, tip ${l.tipWidths[0]} px)`).toBe(1)
      }
      await context.close()
    }
  }
  console.log('[039 B2 legible at 0.6]', JSON.stringify({ arrow: SHIPPED_ARROW, rows }))
  test.info().annotations.push({ type: '039-B2-legible', description: JSON.stringify({ arrow: SHIPPED_ARROW, rows }) })
})

test("039 B2: a HUB as the debtor - its ring does not cut the marker: where the ring crosses the axis, the pixel is the marker's colour, not the ring's", async ({ page }) => {
  // External review 2026-10-16, finding 1: the ring of a hub is a 1.4 px stroke centred 3.5 px outside the outline;
  // the marker runs from 2 px outside it to one length farther. The ring used to be painted over the marker's neck.
  // MEASURED 2026-10-16 on the axis at the centre line of the ring: with the marker painted last the pixel is the
  // marker's colour (6 colour units off it); with the old order (markers before the nodes - the mutant of this test)
  // it is the ring's (2 units off the ring, 208 off the marker). Elsewhere along the narrow neck the pixels are blends
  // either way at DPR 1 (the app keeps its canvas at DPR 1 on quality low), so they are logged, not judged.
  const s = ringSnapshot()
  const proto = s.links[0]!
  const hub = { ...s.nodes.find((n) => n.id === 'b')!, id: 'hub', name: 'Hub', type: 'hub' }
  const snapshot: QaSnapshot = { ...s, nodes: [s.nodes.find((n) => n.id === 'a')!, hub], links: [{ ...proto, source: 'a', target: 'hub', used: '80.00', viz_alpha_key: 'hi' }] }
  await boot(page, { snapshot, clock: false })
  await waitLoopAsleep(page)
  const marker = (await legendArrow(page)).rgb
  const ringHex = await page.evaluate(() => getComputedStyle(document.querySelector('.root')!).getPropertyValue('--ds-data-hub').trim())
  const ring = [1, 3, 5].map((i) => parseInt(ringHex.slice(i, i + 2), 16))
  const n = await screenNodes(page)
  const RING_CENTRE_PX = 3.5
  const along = await page.evaluate(({ at, to, r0, from, until }) => {
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas:not(.canvas-fx)')!
    const k = c.width / Math.max(1, c.clientWidth)
    const box = c.getBoundingClientRect()
    const img = c.getContext('2d')!.getImageData(0, 0, c.width, c.height)
    const l = Math.hypot(to.x - at.x, to.y - at.y)
    const u = { x: (to.x - at.x) / l, y: (to.y - at.y) / l }
    const out: Array<{ d: number; rgb: number[] }> = []
    for (let d = from; d <= until; d += 0.5) {
      const x = Math.round((at.x + u.x * (r0 + d) - box.x) * k)
      const y = Math.round((at.y + u.y * (r0 + d) - box.y) * k)
      const i = (y * c.width + x) * 4
      out.push({ d, rgb: [img.data[i]!, img.data[i + 1]!, img.data[i + 2]!] })
    }
    return out
  }, { at: n.get('hub')!, to: n.get('a')!, r0: R, from: SHIPPED_ARROW.tipGapPx + 0.5, until: SHIPPED_ARROW.tipGapPx + SHIPPED_ARROW.lengthPx - 1 })
  const diff = (v: number[], w: number[]) => Math.abs(v[0]! - w[0]!) + Math.abs(v[1]! - w[1]!) + Math.abs(v[2]! - w[2]!)
  const row = { marker, ring, along: along.map((p) => [p.d, diff(p.rgb, marker), diff(p.rgb, ring)]) }
  console.log('[039 B2 hub debtor]', JSON.stringify(row))
  test.info().annotations.push({ type: '039-B2-hub-debtor', description: JSON.stringify(row) })
  expect(diff(marker, ring), 'counter-check: the marker and the ring are told apart by colour').toBeGreaterThan(100)
  const atRing = along.find((p) => p.d === RING_CENTRE_PX)
  expect(atRing, 'counter-check: the centre line of the ring lies inside the marker, and is sampled').toBeTruthy()
  expect(diff(atRing!.rgb, marker), `at the centre line of the hub's ring, on the axis: the marker's colour (rgb ${atRing!.rgb.join(',')})`).toBeLessThanOrEqual(24)
  expect(diff(atRing!.rgb, ring), "and not the ring's").toBeGreaterThan(100)
})
