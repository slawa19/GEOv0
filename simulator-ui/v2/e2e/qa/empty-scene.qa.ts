/**
 * 038 S1 - "an empty snapshot is an empty screen" (handed over from 034 S8, BACKLOG 034-6 (a)).
 *
 * When the load of ANOTHER source fails, the scene model is cleared (`snapshot = null`, useSceneState.ts). These tests
 * look at what the user then sees: the pixels of both canvases and the composed page, with the render loop awake and
 * with the render loop in deep sleep, and whether a payment event that arrives afterwards still puts an amount label
 * on the screen. The other source here is the preview of another scenario, chosen in the top bar after the run on
 * screen has reported `stopped`; its preview answers 500. The SSE stream of the run stays open, as in the product.
 *
 * Not seen here: a failed load of another EQUIVALENT or of another RUN (the same branch of `applySceneLoad`, not
 * driven: the stand's server shows no equivalent selector); the time between the failure and the next animation
 * frame; themes other than the default.
 */
import { expect, test, type Page } from '@playwright/test'

import { NAMED_OCCLUDERS } from './oracle.js'
import { EQ, RUN_ID, SCENARIO_ID, boot, frames, fxCounts, loopCounters, pushEvent, ringSnapshot, screenNodes, txUpdated, type Pt } from './stand.js'

const OTHER_SCENARIO = 'p038-qa-unloadable'
/** The colour `useRenderLoop` fills the base canvas with before it draws the graph. */
const BASE_FILL = [2, 6, 23] as const

/** `other`: the preview of the other scenario answers 500. `all`: every scene read answers 500. */
type Fail = { other: boolean; all: boolean }

/** The stand's backend with a second scenario; its scene (or every scene) cannot be loaded while `fail` says so. */
async function bootTwoScenarios(page: Page, fail: Fail) {
  const item = (scenario_id: string, name: string) => ({ api_version: 'simulator-api/1', scenario_id, name, participants_count: 6, trustlines_count: 6, equivalents: [EQ] })
  await boot(page, {
    snapshot: ringSnapshot(),
    clock: true,
    ui: 'auto', // the Auto-Run segment: the scenario selector lives there
    beforeLoad: async (p) => {
      await p.route(/\/simulator\/scenarios$/i, (r) => r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ api_version: 'simulator-api/1', items: [item(SCENARIO_ID, 'QA stand'), item(OTHER_SCENARIO, 'QA stand, unloadable')] }),
      }))
      await p.route(/\/graph\/(snapshot|preview)/i, (r) => {
        const other = new URL(r.request().url()).pathname.includes(`/scenarios/${OTHER_SCENARIO}/`)
        if (fail.all || (other && fail.other)) {
          return r.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ code: 'QA_STAND_NO_SCENE', message: 'qa stand: this scene cannot be loaded' }) })
        }
        // Loadable: other participants, so that choosing it is a full reload of the scene and not an increment.
        if (other) return r.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(otherRing()) })
        return r.fallback()
      })
    },
  })
}

/** The ring with other participant ids. */
function otherRing() {
  const s = ringSnapshot()
  const rename = (id: string) => `x${id}`
  return { ...s, nodes: s.nodes.map((n) => ({ ...n, id: rename(n.id), name: `X${String(n.name)}` })), links: s.links.map((l) => ({ ...l, source: rename(l.source), target: rename(l.target) })) }
}

type Pixels = { base: { painted: number; graph: number }; fx: { painted: number } }

/** `painted`: pixels with any alpha. `graph`: pixels of the base canvas that are not its background fill. */
async function canvasPixels(page: Page): Promise<Pixels> {
  return page.evaluate((fill) => {
    const read = (sel: string) => {
      const c = document.querySelector<HTMLCanvasElement>(sel)
      if (!c) throw new Error(`no canvas ${sel}`)
      const d = c.getContext('2d')!.getImageData(0, 0, c.width, c.height).data
      let painted = 0
      let graph = 0
      for (let i = 0; i < d.length; i += 4) {
        if (d[i + 3]! === 0) continue
        painted += 1
        if (Math.abs(d[i]! - fill[0]) + Math.abs(d[i + 1]! - fill[1]) + Math.abs(d[i + 2]! - fill[2]) > 24) graph += 1
      }
      return { painted, graph }
    }
    return { base: read('canvas.canvas:not(.canvas-fx)'), fx: { painted: read('canvas.canvas-fx').painted } }
  }, BASE_FILL)
}

/**
 * The composed page at the centres of the nodes: how many of them differ from an empty spot of the scene. Centres under
 * named DOM (HUD, the error message) are not judged; the number judged is returned so that "0 differ" cannot mean
 * "nothing was looked at".
 */
async function nodesOnPage(page: Page, nodes: Pt[], empty: Pt): Promise<{ judged: number; visible: number }> {
  const shot = (await page.screenshot()).toString('base64')
  return page.evaluate(async ({ shot, nodes, empty, occ }) => {
    const bmp = await createImageBitmap(await (await fetch(`data:image/png;base64,${shot}`)).blob())
    const c = new OffscreenCanvas(bmp.width, bmp.height)
    const ctx = c.getContext('2d')!
    ctx.drawImage(bmp, 0, 0)
    const at = (p: { x: number; y: number }) => ctx.getImageData(Math.round(p.x), Math.round(p.y), 1, 1).data
    const rects = occ.flatMap((s) => [...document.querySelectorAll(s)].map((el) => el.getBoundingClientRect())).filter((r) => r.width > 0 && r.height > 0)
    const e = at(empty)
    let judged = 0
    let visible = 0
    for (const n of nodes) {
      if (rects.some((r) => n.x >= r.x - 10 && n.x <= r.right + 10 && n.y >= r.y - 10 && n.y <= r.bottom + 10)) continue
      judged += 1
      const d = at(n)
      if (Math.abs(d[0]! - e[0]!) + Math.abs(d[1]! - e[1]!) + Math.abs(d[2]! - e[2]!) > 24) visible += 1
    }
    return { judged, visible }
  }, { shot, nodes, empty, occ: [...NAMED_OCCLUDERS, '.ds-ov-message'] })
}

/** A spot of the scene far from every node and outside the HUD. */
function emptySpot(nodes: Pt[]): Pt {
  let best: Pt = { x: 0, y: 0 }
  let bestD = -1
  for (let x = 620; x <= 1200; x += 20) {
    for (let y = 260; y <= 600; y += 20) {
      const d = Math.min(...nodes.map((n) => Math.hypot(n.x - x, n.y - y)))
      if (d > bestD) { bestD = d; best = { x, y } }
    }
  }
  expect(bestD, 'there is an empty spot of the scene to compare with').toBeGreaterThan(60)
  return best
}

const hasSnapshot = (page: Page) => page.evaluate(() => (window as unknown as { __geoSim: { hasSnapshot: boolean } }).__geoSim.hasSnapshot)
const errorShown = (page: Page) => page.locator('.ds-ov-inset[role="alert"]').count()

/**
 * The run on screen reports `stopped` over its own stream, then the user chooses the other scenario in the top bar.
 * Its preview fails, which is a failed load of another source. Steps the clock until the scene model is cleared.
 */
async function switchToUnloadableScenario(page: Page): Promise<number> {
  await runReportsStopped(page)
  return chooseUnloadableScenario(page)
}

async function runReportsStopped(page: Page): Promise<void> {
  await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'stopped', sim_time_ms: 0, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
  await frames(page, 2)
}

async function chooseUnloadableScenario(page: Page): Promise<number> {
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption(OTHER_SCENARIO)
  for (let f = 1; f <= 240; f += 1) {
    if (!(await hasSnapshot(page)) && (await errorShown(page)) > 0) return f
    await frames(page, 1)
  }
  throw new Error('the scene model was not cleared within 240 frames of the failed load')
}

async function sceneBefore(page: Page) {
  const nodes = [...(await screenNodes(page)).values()]
  const empty = emptySpot(nodes)
  const px = await canvasPixels(page)
  const onPage = await nodesOnPage(page, nodes, empty)
  // Counter-check: before the failure the graph IS on both the canvas and the page, or "empty after" proves nothing.
  expect(px.base.graph, 'counter-check: the base canvas holds the graph before the failure').toBeGreaterThan(500)
  expect(onPage.judged, 'counter-check: at least three node centres are open to judge on the page').toBeGreaterThanOrEqual(3)
  expect(onPage.visible, 'counter-check: every judged node is on the page before the failure').toBe(onPage.judged)
  return { nodes, empty, px, onPage }
}

test('empty snapshot, loop awake: after a failed load of another scenario both canvases are blank and no node is on the page', async ({ page }) => {
  await bootTwoScenarios(page, { other: true, all: false })
  const before = await sceneBefore(page)
  const c0 = await loopCounters(page)
  const steps = await switchToUnloadableScenario(page)
  const c1 = await loopCounters(page)
  await frames(page, 5)
  const c2 = await loopCounters(page)
  const after = { px: await canvasPixels(page), onPage: await nodesOnPage(page, before.nodes, before.empty) }
  console.log('[038 empty/awake]', JSON.stringify({ steps, before: before.px, after, rafWhileLoading: c1.calls - c0.calls, rafAfter: c2.calls - c1.calls }))
  expect(c2.calls - c1.calls, 'precondition: the render loop is awake (it still requests frames after the failure)').toBeGreaterThan(0)
  expect.soft(after.px.base.painted, 'no painted pixel is left on the base canvas').toBe(0)
  expect.soft(after.px.fx.painted, 'no painted pixel is left on the FX canvas').toBe(0)
  expect.soft(after.onPage.judged, 'counter-check: the same node centres are judged on the page').toBeGreaterThanOrEqual(3)
  expect.soft(after.onPage.visible, 'no node of the old graph is on the composed page').toBe(0)
})

test('empty snapshot, loop in deep sleep: the canvases are blanked although not one frame runs after the failure', async ({ page }) => {
  await bootTwoScenarios(page, { other: true, all: false })
  // The run stops first (its event wakes the loop), then nothing happens long enough for the loop to fall asleep.
  await runReportsStopped(page)
  await page.clock.runFor(3600)
  const rested = await loopCounters(page)
  await page.clock.runFor(2000)
  const asleep = await loopCounters(page)
  expect({ draws: asleep.draws - rested.draws, raf: asleep.calls - rested.calls }, 'precondition: the render loop is in deep sleep before the failure').toEqual({ draws: 0, raf: 0 })
  const before = await sceneBefore(page)

  // The clock is NOT stepped from here on: no animation frame and no timer can run, whoever asks for one. The failed
  // load itself needs neither (a fetch and promises). Whatever blanks the canvases now is not a frame of the loop.
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption(OTHER_SCENARIO)
  await expect.poll(async () => !(await hasSnapshot(page)) && (await errorShown(page)) > 0, { message: 'the failed load clears the scene model while the clock stands', timeout: 15_000 }).toBe(true)
  const c1 = await loopCounters(page)
  const after = { px: await canvasPixels(page), onPage: await nodesOnPage(page, before.nodes, before.empty) }
  const row = { before: before.px, after, framesSinceAsleep: c1.frames - asleep.frames, clearsOfBaseCanvasSinceAsleep: c1.draws - asleep.draws, rafRequestedSinceAsleep: c1.calls - asleep.calls }
  console.log('[038 empty/asleep]', JSON.stringify(row))
  test.info().annotations.push({ type: '038-empty-asleep', description: JSON.stringify(row) })
  expect(row.framesSinceAsleep, 'precondition: not one animation frame ran between the sleep and the reading').toBe(0)
  expect.soft(after.px.base.painted, 'no painted pixel is left on the base canvas').toBe(0)
  expect.soft(after.px.fx.painted, 'no painted pixel is left on the FX canvas').toBe(0)
  expect.soft(after.onPage.judged, 'counter-check: the same node centres are judged on the page').toBeGreaterThanOrEqual(3)
  expect.soft(after.onPage.visible, 'no node of the old graph is on the composed page').toBe(0)
})

test('empty snapshot: a payment event that arrives after the scene was cleared puts no amount label on the screen', async ({ page }) => {
  await bootTwoScenarios(page, { other: true, all: false })
  // Counter-check: on a loaded scene the same event does put labels on the screen.
  await pushEvent(page, txUpdated(['a', 'b'], '10.00'))
  await frames(page, 4)
  expect((await fxCounts(page)).labels, 'counter-check: on a loaded scene a payment gives an amount label').toBeGreaterThan(0)
  await frames(page, 200) // every label of that payment has expired (1900 ms after the receiver's at 1200 ms)
  expect((await fxCounts(page)).labels).toBe(0)

  await switchToUnloadableScenario(page)
  await pushEvent(page, txUpdated(['a', 'b'], '10.00'))
  let maxLabels = 0
  for (let f = 0; f < 90; f += 1) { // past the receiver's label at 1200 ms
    await frames(page, 1)
    maxLabels = Math.max(maxLabels, (await fxCounts(page)).labels)
  }
  console.log('[038 empty/labels]', JSON.stringify({ maxLabels, hasSnapshot: await hasSnapshot(page) }))
  expect(await hasSnapshot(page), 'the scene is still cleared').toBe(false)
  expect(maxLabels, 'no amount label is on the screen while there is no scene').toBe(0)
})

test('counter-check: a failed load of the SAME source keeps the graph on the canvas and on the page', async ({ page }) => {
  const fail: Fail = { other: true, all: false }
  await bootTwoScenarios(page, fail)
  const before = await sceneBefore(page)
  // The run is live and its scene is on screen. Every scene read now fails, and an event asks for a refresh of the
  // scene that is shown: the same source.
  fail.all = true
  await pushEvent(page, { type: 'topology.changed', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] } })
  let errors = 0
  for (let f = 0; f < 240 && errors === 0; f += 1) { await frames(page, 1); errors = await errorShown(page) }
  await frames(page, 5)
  const after = { px: await canvasPixels(page), onPage: await nodesOnPage(page, before.nodes, before.empty), hasSnapshot: await hasSnapshot(page), errors }
  console.log('[038 empty/same-source]', JSON.stringify({ before: before.px, after }))
  expect(after.errors, 'the failed refresh is reported').toBeGreaterThan(0)
  expect(after.hasSnapshot, 'the scene model is kept').toBe(true)
  expect(after.px.base.graph, 'the graph is still on the base canvas').toBeGreaterThan(500)
  expect(after.onPage.judged, 'counter-check: node centres are open to judge').toBeGreaterThanOrEqual(3)
  expect(after.onPage.visible, 'and every judged node is still on the page').toBe(after.onPage.judged)
})

test('counter-check: a full reload that SUCCEEDS never leaves the base canvas blank on any frame (no flash of an empty screen)', async ({ page }) => {
  // `applySceneLoad` sets `snapshot = null` and the new snapshot in the same tick when the scene changes. The blanking
  // must not fire on that: a user would see the screen flash empty on every scenario change.
  await bootTwoScenarios(page, { other: false, all: false })
  const before = await canvasPixels(page)
  expect(before.base.graph).toBeGreaterThan(500)
  await runReportsStopped(page)
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption(OTHER_SCENARIO)
  // While the clock stands the new scene is in the model but no frame has drawn it: the old picture must still be there.
  await expect.poll(() => page.evaluate(() => (window as unknown as { __geoSim: { layoutNodes: Array<{ id: string }> } }).__geoSim.layoutNodes.some((n) => n.id.startsWith('x'))), { message: 'the other scenario is loaded', timeout: 15_000 }).toBe(true)
  const standing = await canvasPixels(page)
  let minPainted = standing.base.painted
  for (let f = 0; f < 90; f += 1) {
    await frames(page, 1)
    minPainted = Math.min(minPainted, (await canvasPixels(page)).base.painted)
  }
  const after = await canvasPixels(page)
  console.log('[038 empty/no-flash]', JSON.stringify({ before: before.base, standing: standing.base, minPaintedOver90Frames: minPainted, after: after.base, errors: await errorShown(page) }))
  expect(await errorShown(page), 'the reload succeeded').toBe(0)
  expect(standing.base.painted, 'the picture is not blanked between the load and the next frame').toBe(before.base.painted)
  expect(minPainted, 'and the base canvas is fully painted on every frame of the reload').toBe(before.base.painted)
  expect(after.base.graph, 'the new graph is drawn').toBeGreaterThan(500)
})
