/**
 * 039 slice B, PR 2, fix-delta of 2026-10-16 (external review, finding 2) - the SIZE of the direction marker,
 * MEASURED. DEBUG-ONLY (this config is not a gate and is not counted): a sweep that the product's constant
 * `ARROW_LENGTH_PX` (`src/render/linkArrowhead.ts`) is derived from. The counted stand holds only that the shipped
 * size passes (`e2e/qa/direction.qa.ts`).
 *
 * For each length L = 3 .. 10 CSS px (width 0.8 L, the aspect of decision 4; the size is overridden in the page by
 * `overrideArrowLength` - a test-only rewrite of the served module, no product switch), both themes (`saas`, `hud`):
 *   - the 100-node fixture at application zoom 0.6: the share of markers that are DISTINGUISHABLE as an arrowhead on
 *     their own line (`legibility` in `e2e/qa/directionProbe.ts`: contrast against a bare stretch of the same line, a
 *     base at least twice as wide as the widest stroke of a line, a taper - the three conditions are written there);
 *   - a line with NO debt (alpha key `bg`, the faintest body) in a small scene at zoom 0.6: distinguishable or not.
 * The MINIMUM is the smallest L at which, in both themes, at least `MIN_SCENE_SHARE` of the measured markers of the
 * 100-node scene and the marker of the line without debt are distinguishable. Nothing is asserted about which L that is:
 * the curve is printed (`[039 marker size]`) and the derived minimum with it.
 *
 * Why `MIN_SCENE_SHARE` = 0.95: in a dense scene a few markers lie under a third node or a crossing line, which the
 * metric counts as not distinguishable whatever the size; the curve shows where the share stops growing.
 *
 * LIMITS: quality `low`, no GPU, DPR 1, 1280 x 720; the metric is contrast against the line, not shape recognition.
 */
import { expect, test, type Browser, type Page } from '@playwright/test'

import { arrowOfLength, lastPass, legibility, modelOf, overrideArrowLength, recordMarkers, zoomTo } from '../directionProbe.js'
import { boot, camera, fixtureSnapshot100, frames, ringSnapshot, type QaSnapshot } from '../stand.js'

const LENGTHS = process.env.P039_SIZE_ONLY ? [Number(process.env.P039_SIZE_ONLY)] : [3, 4, 5, 6, 7, 8, 9, 10]
/** `--ds-data-direction` (#b4bfcf): the colour of the marker's own pixels. */
const MARKER_RGB: [number, number, number] = [180, 191, 207]
const THEMES = ['saas', 'hud'] as const
const APP_ZOOM = 0.6
const MIN_SCENE_SHARE = 0.95
const med = (v: number[]) => [...v].sort((p, q) => p - q)[Math.floor(v.length / 2)]

/** a -> b with debt, e -> f with NO debt (alpha key `bg`); one neighbour each. */
function smallScene(): QaSnapshot {
  const s = ringSnapshot()
  const proto = s.links[0]!
  return { ...s, nodes: s.nodes.filter((n) => ['a', 'b', 'e', 'f'].includes(n.id)), links: [{ ...proto, source: 'a', target: 'b', used: '80.00', viz_alpha_key: 'hi' }, { ...proto, source: 'e', target: 'f', used: '0.00', available: '100.00', viz_alpha_key: 'bg' }] }
}

/** One measurement in a FRESH page of its own (init scripts, routes and the fake clock of a page accumulate over boots). */
async function measure(browser: Browser, snapshot: QaSnapshot, L: number, theme: string, only?: (key: string) => boolean, zoom = APP_ZOOM) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1, baseURL: test.info().project.use.baseURL })
  const page = await context.newPage()
  try {
    return await measureIn(page, snapshot, L, theme, only, zoom)
  } finally {
    await context.close()
  }
}

async function measureIn(page: Page, snapshot: QaSnapshot, L: number, theme: string, only: ((key: string) => boolean) | undefined, appZoom: number) {
  await boot(page, { snapshot, clock: true, query: `&theme=${theme}`, beforeLoad: async (p) => { await overrideArrowLength(p, L); await recordMarkers(p) } })
  await page.mouse.move(640, 300)
  await frames(page, 3)
  const before = await lastPass(page)
  const zoom = await zoomTo(page, appZoom)
  await page.mouse.move(645, 305)
  await frames(page, 3)
  // The override is in effect: a drawn marker is L long on screen.
  const pass = await lastPass(page)
  if (process.env.P039_SIZE_DEBUG) console.log('[039 marker size debug]', JSON.stringify({ L, theme, before: { passes: before.passes, markers: before.markers.length }, after: { passes: pass.passes, markers: pass.markers.length }, zoom }))
  expect(pass.markers.length, 'markers are drawn').toBeGreaterThan(0)
  const [tip, l, r] = pass.markers[0]!.pts as [number[], number[], number[]]
  const len = Math.hypot(tip[0]! - (l[0]! + r[0]!) / 2, tip[1]! - (l[1]! + r[1]!) / 2) * zoom
  expect(len, `the page draws the overridden length ${L}`).toBeCloseTo(L, 3)
  const model = await modelOf(page, snapshot, arrowOfLength(L))
  if (process.env.P039_SIZE_DEBUG) {
    const cam = await camera(page)
    const drawnTips = pass.markers.slice(0, 3).map((m) => [m.pts[0]![0]! * cam.zoom + cam.panX, m.pts[0]![1]! * cam.zoom + cam.panY].map((v) => Math.round(v * 10) / 10))
    const modelTips = model.lines.filter((l) => l.freePx >= model.arrow.minFreeGapPx).slice(0, 3).map((l) => [l.tip.x * cam.zoom + cam.panX, l.tip.y * cam.zoom + cam.panY].map((v) => Math.round(v * 10) / 10))
    console.log('[039 marker size debug] tips', JSON.stringify({ drawnTips, modelTips, styles: [...new Set(pass.markers.map((m) => m.style))] }))
    await page.screenshot({ path: test.info().outputPath(`debug-${L}-${theme}-${snapshot.nodes.length}.png`) })
  }
  return legibility(page, model, MARKER_RGB, only)
}

test('039 marker size - the curve: distinguishable markers by length at application zoom 0.6, both themes, the 100-node scene and a line without debt', async ({ browser }) => {
  test.setTimeout(1_800_000)
  const rows: Array<Record<string, unknown>> = []
  for (const L of LENGTHS) {
    for (const theme of THEMES) {
      const scene = await measure(browser, fixtureSnapshot100(), L, theme)
      // Informative, not part of the criterion: the same scene at application zoom 1, where far fewer ends lie under a node.
      const sceneAt1 = await measure(browser, fixtureSnapshot100(), L, theme, undefined, 1)
      const noDebt = await measure(browser, smallScene(), L, theme, (k) => k === 'e>f')
      const withDebt = await measure(browser, smallScene(), L, theme, (k) => k === 'a>b')
      const row = {
        L, W: Math.round(L * 0.8 * 10) / 10, theme,
        scene: { measured: scene.measured, notMeasured: scene.notMeasured, hiddenUnderANode: scene.hidden, legible: scene.legible, share: Math.round(scene.share * 1000) / 1000, failedBy: scene.failedBy, median: { frac: med(scene.fracs), baseWidth: med(scene.baseWidths), tipWidth: med(scene.tipWidths) } },
        sceneAtZoom1: { measured: sceneAt1.measured, hiddenUnderANode: sceneAt1.hidden, legible: sceneAt1.legible, share: Math.round(sceneAt1.share * 1000) / 1000, failedBy: sceneAt1.failedBy },
        noDebt: { frac: noDebt.fracs[0], baseWidth: noDebt.baseWidths[0], tipWidth: noDebt.tipWidths[0], legible: noDebt.legible === 1 },
        withDebt: { frac: withDebt.fracs[0], baseWidth: withDebt.baseWidths[0], tipWidth: withDebt.tipWidths[0], legible: withDebt.legible === 1 },
      }
      console.log('[039 marker size]', JSON.stringify(row))
      rows.push(row)
    }
  }
  const ok = (L: number) => rows.filter((r) => r.L === L).every((r) => (r.scene as { share: number }).share >= MIN_SCENE_SHARE && (r.noDebt as { legible: boolean }).legible)
  const minimum = LENGTHS.find(ok) ?? null
  const summary = { appZoom: APP_ZOOM, minSceneShare: MIN_SCENE_SHARE, minimum, passes: LENGTHS.map((L) => [L, ok(L)]) }
  console.log('[039 marker size] minimum', JSON.stringify(summary))
  test.info().annotations.push({ type: '039-marker-size', description: JSON.stringify({ summary, rows }) })
  expect(rows.length, 'every length was measured in both themes').toBe(LENGTHS.length * THEMES.length)
})
