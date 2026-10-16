/**
 * 039 slice B, PR 2 - the direction marker along the paths of the REAL page that the counted stand does not walk.
 * DEBUG-ONLY (this config is not a gate and is not counted): a record of an adversarial pass (AGENTS.md section 16),
 * kept so that it can be run again. The counted checks are `e2e/qa/direction.qa.ts`.
 *
 * Method: the markers a pass of the base canvas draws are recorded and compared with the model of
 * `e2e/qa/directionProbe.ts` (tip 2 px before the rim of the TARGET of each line that has 14 px of room) - for the
 * lines the scene must have AFTER each step. Paths:
 *   - a topology event that adds the reverse line (a one-way line becomes a mutual pair) and one that removes a line;
 *   - an empty topology event (the scene is fetched again in full);
 *   - a participant selected (focus overlay), a clearing highlight (the marker again, in the highlight colour);
 *   - a node being dragged (`focus` LOD: only its lines) and dropped (all lines again, at the new place);
 *   - zoom in with the wheel (the triangle keeps its size on screen);
 *   - the other theme (`saas`);
 *   - a device pixel ratio of 2: drawing calls against the model, and the pixel probe at one debtor and one creditor end.
 * LIMITS: the model shares the product's rule of where a line meets a node; a marker painted over later is not seen
 * here (pixels are the stand's); the demo (fixtures) pipeline is not walked by this file - `e2e/scenes.spec.ts` shows it.
 */
import { expect, test, type Page } from '@playwright/test'

import { compareWithModel, lastPass, markerSeen, modelOf, probeEnd, recordMarkers, SHIPPED_ARROW, type Marker } from '../directionProbe.js'
import { boot, camera, frames, pushEvent, ringSnapshot, screenNodes, waitLoopAsleep, type QaSnapshot } from '../stand.js'

/** The colour of a marker (`--ds-data-direction`, since 2026-10-16; before that the line colour #64748b). */
const LINE = '#b4bfcf'
type Line = { source: string; target: string }

/** a -> b, c <-> d, e -> f: every node has one neighbour. */
function scene(): QaSnapshot {
  const s = ringSnapshot()
  const proto = s.links[0]!
  const line = (source: string, target: string) => ({ ...proto, source, target })
  return { ...s, links: [line('a', 'b'), line('c', 'd'), line('d', 'c'), line('e', 'f')] }
}

const note = (id: string, row: unknown) => {
  console.log(`[039 B2 paths] ${id}`, JSON.stringify(row))
  test.info().annotations.push({ type: `039-B2-paths-${id}`, description: JSON.stringify(row) })
}

/** The markers of the last pass in the colour of a line, against the model of `lines`; markers of any other colour apart. */
async function check(page: Page, s: QaSnapshot, lines: Line[], what: string) {
  const pass = await lastPass(page)
  const own = pass.markers.filter((m) => m.style === LINE)
  const other = pass.markers.filter((m) => m.style !== LINE)
  const r = compareWithModel(own, await modelOf(page, { nodes: s.nodes, links: lines as QaSnapshot['links'] }))
  note(what, { lines: lines.map((l) => `${l.source}>${l.target}`).join(' '), drawn: r.drawn, withRoom: r.withRoom, missing: r.linesWithRoomWithoutTheirMarker, extra: r.markersThatAreNoLinesTargetEnd, otherColour: other.length })
  expect(r.linesWithRoomWithoutTheirMarker, `${what}: every line with room has its marker at its target`).toBe(0)
  expect(r.markersThatAreNoLinesTargetEnd, `${what}: no other marker in the line colour`).toBe(0)
  expect(r.drawn, `${what}: precondition - the scene has room for every marker`).toBe(lines.length)
  return { own, other }
}

const wake = async (page: Page) => { await page.mouse.move(8, 8); await page.mouse.move(12, 12); await frames(page, 3) }
/** A structural change is laid out again: step the fake clock until the layout the product reports has stopped moving, then draw. */
async function settle(page: Page) {
  let prev = ''
  let same = 0
  for (let i = 0; i < 200 && same < 5; i += 1) {
    await frames(page, 6)
    const cur = JSON.stringify((await screenNodes(page)).size) + JSON.stringify([...(await screenNodes(page)).values()].map((p) => [Math.round(p.x * 10), Math.round(p.y * 10)]))
    same = cur === prev ? same + 1 : 0
    prev = cur
  }
  expect(same, 'the layout settled').toBeGreaterThanOrEqual(5)
  await wake(page)
}

test('039 B2 paths - topology events, refresh, selection, clearing highlight, drag and zoom: the markers are those of the lines on screen, each at its target', async ({ page }) => {
  const s = scene()
  await boot(page, { snapshot: s, clock: true, beforeLoad: recordMarkers })
  let lines: Line[] = s.links.map((l) => ({ source: l.source, target: l.target }))
  await wake(page)
  await check(page, s, lines, 'at load')

  // A later event makes a -> b a MUTUAL pair: the new line b -> a has its marker at a.
  await pushEvent(page, { type: 'topology.changed', payload: { added_nodes: [], removed_nodes: [], added_edges: [{ from_pid: 'b', to_pid: 'a', equivalent_code: 'UAH', limit: '50' }], removed_edges: [] } })
  await settle(page)
  lines = [...lines, { source: 'b', target: 'a' }]
  await check(page, s, lines, 'reverse line added by an event')

  // A line removed by an event takes its marker with it.
  await pushEvent(page, { type: 'topology.changed', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [{ from_pid: 'e', to_pid: 'f', equivalent_code: 'UAH' }] } })
  await settle(page)
  lines = lines.filter((l) => !(l.source === 'e' && l.target === 'f'))
  await check(page, s, lines, 'line removed by an event')

  // A participant selected: its lines are stroked again, the markers stay one per line.
  const at = async (id: string) => (await screenNodes(page)).get(id)!
  const c = await at('c')
  await page.mouse.click(c.x, c.y)
  await frames(page, 6)
  const selected = await check(page, s, lines, 'participant c selected')
  expect(selected.other, 'a selection draws no marker of another colour').toHaveLength(0)
  await page.keyboard.press('Escape')
  await frames(page, 6)

  // A clearing lights c <-> d: each of the two lines gets its marker AGAIN in the highlight colour, at the same tip.
  await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-paths', cleared_cycles: 1, cleared_amount: '5.00', cycle_edges: [{ from: 'c', to: 'd' }, { from: 'd', to: 'c' }] })
  await frames(page, 12)
  const lit = await check(page, s, lines, 'clearing highlight of c <-> d')
  expect(lit.other, 'two highlight markers, one per lit line').toHaveLength(2)
  const tipOf = (m: Marker) => m.pts[0]!.map((v) => Math.round(v * 100) / 100).join(',')
  const litTips = lit.other.map(tipOf).sort()
  const cd = await modelOf(page, { nodes: s.nodes, links: [{ source: 'c', target: 'd' }, { source: 'd', target: 'c' }] as QaSnapshot['links'] })
  expect(litTips, 'the highlight markers are at the targets of c -> d and d -> c').toEqual(cd.lines.map((l) => [l.tip.x, l.tip.y].map((v) => Math.round(v * 100) / 100).join(',')).sort())
  await frames(page, 400) // the highlight is over
  await wake(page)
  expect((await check(page, s, lines, 'after the highlight')).other, 'no highlight marker is left').toHaveLength(0)

  // A node being dragged: `focus` LOD - only the lines of the SELECTED node (a press selects the node it drags) are
  // drawn, with their markers; dropped: all of them again, at the new place.
  const pinned = () => page.evaluate(() => (window as unknown as { __geoSim: { pinnedNodeIds: string[] } }).__geoSim.pinnedNodeIds)
  const onCanvas = (x: number, y: number) => page.evaluate(([px, py]) => document.elementFromPoint(px!, py!)?.matches('canvas.canvas:not(.canvas-fx)') ?? false, [x, y])
  let dragged = ''
  for (const id of ['d', 'c', 'a', 'b']) {
    const p = await at(id)
    if ((await onCanvas(p.x, p.y)) && (await onCanvas(p.x + 60, p.y + 36))) { dragged = id; break }
  }
  expect(dragged, 'a node with bare canvas under it and under the drop point').not.toBe('')
  const d = await at(dragged)
  await page.mouse.move(d.x, d.y)
  await page.mouse.down()
  for (let i = 1; i <= 6; i += 1) { await page.mouse.move(d.x + 10 * i, d.y + 6 * i); await frames(page, 1) }
  await frames(page, 2)
  const mid = await lastPass(page)
  const ofDragged = lines.filter((l) => l.source === dragged || l.target === dragged)
  const during = compareWithModel(mid.markers.filter((m) => m.style === LINE), await modelOf(page, { nodes: s.nodes, links: ofDragged as QaSnapshot['links'] }))
  note('while a node is dragged', { dragged, itsLines: ofDragged.length, drawn: during.drawn, missing: during.linesWithRoomWithoutTheirMarker, extra: during.markersThatAreNoLinesTargetEnd })
  await page.mouse.up()
  await frames(page, 30)
  expect(await pinned(), 'the drag happened: the node is pinned').toContain(dragged)
  expect(during.linesWithRoomWithoutTheirMarker, 'while dragged: every line of the dragged node keeps its marker, at the place the node is dragged to').toBe(0)
  expect(during.markersThatAreNoLinesTargetEnd, 'while dragged (focus LOD): no marker of a line that is not drawn').toBe(0)
  await settle(page)
  await check(page, s, lines, 'after the drop')

  // Zoom in with the wheel: every marker still at its target, of the shipped size on screen.
  const zoom0 = (await camera(page)).zoom
  await page.mouse.move(640, 400)
  await page.mouse.wheel(0, -400)
  await frames(page, 30)
  const zoomed = await check(page, s, lines, 'zoomed in')
  const zoom = (await camera(page)).zoom
  expect(zoom, 'the wheel zoomed in').toBeGreaterThan(zoom0 * 1.2)
  const size = zoomed.own.map((m) => {
    const [tip, l, r] = m.pts as [number[], number[], number[]]
    const mid = [(l[0]! + r[0]!) / 2, (l[1]! + r[1]!) / 2]
    return { length: Math.hypot(tip[0]! - mid[0]!, tip[1]! - mid[1]!) * zoom, width: Math.hypot(l[0]! - r[0]!, l[1]! - r[1]!) * zoom }
  })
  note('size on screen at the new zoom', { zoom, length: size.map((x) => Math.round(x.length * 1e4) / 1e4), width: size.map((x) => Math.round(x.width * 1e4) / 1e4) })
  for (const x of size) { expect(x.length).toBeCloseTo(SHIPPED_ARROW.lengthPx, 4); expect(x.width).toBeCloseTo(SHIPPED_ARROW.widthPx, 4) }

  // The scene fetched again in full (an empty topology event asks for a refresh): the mock answers with the ORIGINAL snapshot.
  await pushEvent(page, { type: 'topology.changed', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] } })
  // While physics settles after the reload the product draws in `focus` LOD (only the lines of the selected node, and
  // their markers) - an existing rule of the render loop; the full set is back when the layout is still.
  await settle(page)
  await check(page, s, s.links.map((l) => ({ source: l.source, target: l.target })), 'after a full refresh of the scene')
})

test('039 B2 paths - the other theme (saas): the same markers at the same targets', async ({ page }) => {
  const s = scene()
  await boot(page, { snapshot: s, clock: true, beforeLoad: recordMarkers, query: '&theme=saas' })
  await expect(page.locator('.root')).toHaveAttribute('data-theme', 'saas')
  await wake(page)
  await check(page, s, s.links.map((l) => ({ source: l.source, target: l.target })), 'theme saas')
})

test.describe('device pixel ratio 2', () => {
  test.use({ deviceScaleFactor: 2 })
  test('039 B2 paths - DPR 2: the markers are at their targets in CSS pixels, and the pixels show one at the debtor end and none at the creditor end', async ({ page }) => {
    const s = scene()
    await boot(page, { snapshot: s, clock: false, beforeLoad: recordMarkers })
    await waitLoopAsleep(page)
    expect(await page.evaluate(() => window.devicePixelRatio)).toBe(2)
    await check(page, s, s.links.map((l) => ({ source: l.source, target: l.target })), 'DPR 2')
    const n = await screenNodes(page)
    const solid: [number, number, number] = [180, 191, 207]
    const debtor = await probeEnd(page, n.get('b')!, n.get('a')!, 12, null, solid)
    const creditor = await probeEnd(page, n.get('a')!, n.get('b')!, 12, null, solid)
    note('DPR 2 pixels', { debtor, creditor })
    expect(markerSeen(debtor, 12), 'a marker at the debtor end b').toBe(true)
    expect(debtor.solid).toBeGreaterThan(0)
    expect(markerSeen(creditor, 12), 'none at the creditor end a').toBe(false)
    expect(creditor.solid).toBe(0)
  })
})
