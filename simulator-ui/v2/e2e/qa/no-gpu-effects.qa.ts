/**
 * 038 S3 - the census of expensive CSS effects without a GPU, a discrete budget (`budgets.ts`, `CSS_EFFECTS_NO_GPU`).
 *
 * The no-GPU policy turns off `backdrop-filter` and CSS `filter` (on software compositing a filtered element is
 * rendered again on every frame that damages it). A budget per effect and per element class would be closed one
 * finding at a time; this test instead lists EVERY element of the page with a computed `filter` or `backdrop-filter`,
 * in the states the stand can put on screen, and holds the list - expected empty. On `page.clock`, no wall clock.
 *
 * States: the named load; `Labels = Neighbors` with the hub selected (28 node labels); the payment panel; a node
 * card; an edge tooltip; a node being dragged. NOT opened here, so nothing is claimed about them: the success and
 * error toasts, the trust line and clearing panels, HUD dropdowns, the metrics panel, the history overlay, the loading
 * and error messages, the dev perf overlay (the window matrix of slice S2 owns the recipes).
 *
 * MEMBERSHIP (2026-10-12, 039 slice A, R14): `*.qa.ts`, the counted project `qa-stand`, in the required gate; it was
 * `no-gpu-effects.qaperf.ts`. On a production build (run by `qa-perf` only) the census is skipped: no dev hook.
 *
 * It does not see: `box-shadow`, `text-shadow`, `opacity`, `mix-blend-mode`, masks, canvas `shadowBlur` (a separate
 * budget), pseudo-elements, an effect that appears only on hover or focus, and what any of it costs in time.
 */
import { expect, test, type Page } from '@playwright/test'

import { BUDGETS, CSS_EFFECTS_NO_GPU, type Budget } from './budgets.js'
import { buildPerfLoad } from './perf-load.js'
import { boot, fixtureSnapshot100, pausedClockTime, pretendGpu, screenNodes, selectHub, type QaSnapshot } from './stand.js'

const devHook = process.env.QA_STAND_SERVER !== 'preview'
const snapshot = fixtureSnapshot100()
const load = buildPerfLoad(snapshot)

function hold(actual: number, b: Budget) {
  expect(actual, `budget ${b.name} (${b.what}) is ${b.value}, recorded ${b.since}. If the product changed on purpose, move the constant in e2e/qa/budgets.ts with a dated line and the reason.`).toBe(b.value)
}

/** Every element with a computed `filter` or `backdrop-filter`, as "tag.classes [effect]" with a count. CSS transitions are finished first. */
const census = (page: Page): Promise<string[]> => page.evaluate(() => {
  for (const a of document.getAnimations()) if (a instanceof CSSTransition) a.finish()
  const by = new Map<string, number>()
  for (const el of document.querySelectorAll('*')) {
    const cs = getComputedStyle(el)
    const fx = [cs.filter !== 'none' ? 'filter' : '', cs.backdropFilter !== 'none' ? 'backdrop-filter' : ''].filter(Boolean)
    if (fx.length === 0) continue
    const cls = [...el.classList].filter((c) => !/^(is-|data-v-)/.test(c)).sort().join('.')
    const key = `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ''} [${fx.join(', ')}]`
    by.set(key, (by.get(key) ?? 0) + 1)
  }
  return [...by].sort().map(([k, n]) => `${n} x ${k}`)
})

const push = (page: Page, frames: string[]) =>
  page.evaluate((fs) => { for (const f of fs) (window as unknown as { __qaSse: { push: (t: string) => void } }).__qaSse.push(f) }, frames)

async function bootNeighbors(page: Page, snap: QaSnapshot, gpu: boolean) {
  await boot(page, {
    snapshot: snap, clock: true, devHook,
    beforeLoad: async (p) => {
      if (gpu) await pretendGpu(p)
      await p.addInitScript(() => { try { localStorage.setItem('geo.sim.labelsLod', 'neighbors') } catch { /* ignore */ } })
    },
  })
  if (!devHook) await page.clock.pauseAt(pausedClockTime((await page.evaluate(() => Date.now())) + 1000))
}

const nodeLabels = (page: Page) => page.evaluate(() => {
  const all = [...document.querySelectorAll('.node-label-inner')]
  return {
    labels: all.length, withFilter: all.filter((el) => getComputedStyle(el).filter !== 'none').length,
    withTextShadow: all.filter((el) => getComputedStyle(el).textShadow !== 'none').length,
  }
})

test('budget: without a GPU no element of the page carries a CSS filter or a backdrop-filter, in any state the stand can open', async ({ page }) => {
  test.skip(!devHook, 'the node card, the edge tooltip and the drag are opened through window.__geoSim (dev server only): on a production build this census is NOT taken')
  await bootNeighbors(page, snapshot, false)
  const root = await page.evaluate(() => ({ gpu: document.querySelector<HTMLElement>('.root')!.dataset.gpu, quality: document.querySelector<HTMLElement>('.root')!.dataset.quality }))
  expect(root, 'precondition: the no-GPU profile').toEqual({ gpu: '0', quality: 'low' })

  const seen: Record<string, string[]> = {}
  const present: Record<string, number> = {}
  const step = async (state: string, selector: string) => {
    await page.clock.runFor(320)
    present[state] = await page.locator(selector).count()
    expect(present[state], `state "${state}": ${selector} is on screen - a census of a state that did not open says nothing`).toBeGreaterThan(0)
    seen[state] = await census(page)
  }

  await push(page, load.frames(8, 6, 'census')) // five payments with labels and a clearing
  await step('the named load', '.floating-label-inner')

  expect(await selectHub(page), 'the hub is selected: 28 node labels (the cap of useLabelNodes.ts)').toBe(28)
  await step('Labels = Neighbors, hub selected', '.node-label-inner')
  const labels = await nodeLabels(page)

  type GeoSim = { openNodeCard: (o: unknown) => void; showEdgeTooltip: (e: unknown) => void }
  const at = [...(await screenNodes(page))][0]!
  await page.evaluate(({ id, anchor }) => (window as unknown as { __geoSim: GeoSim }).__geoSim.openNodeCard({ nodeId: id, anchor }), { id: at[0], anchor: at[1] })
  await step('node card', '.ws-shell')
  await page.keyboard.press('Escape')

  await page.evaluate((p) => (window as unknown as { __geoSim: GeoSim }).__geoSim.showEdgeTooltip({
    key: 'x', fromId: 'a', toId: 'b', amountText: '10.00 UAH', screenX: p.x, screenY: p.y + 40, trustLimit: '100', used: '10', available: '90', edgeStatus: 'active',
  }), at[1])
  await step('edge tooltip', '.ds-ov-tooltip')

  await page.locator('[data-testid="actionbar-payment"]').click()
  await step('payment panel', '.ws-shell')
  await page.keyboard.press('Escape')
  await page.clock.runFor(320)

  // A node under the pointer, held and moved: the DOM drag preview is shown until the button is released.
  const free = await page.evaluate(() => {
    const g = (window as unknown as { __geoSim: { layoutNodes: Array<{ x: number; y: number }>; camera: { panX: number; panY: number; zoom: number } } }).__geoSim
    for (const n of g.layoutNodes) {
      const x = n.x * g.camera.zoom + g.camera.panX
      const y = n.y * g.camera.zoom + g.camera.panY
      const el = document.elementFromPoint(x, y)
      if (el instanceof HTMLCanvasElement && !el.classList.contains('canvas-fx') && y > 320 && y < 640 && x > 80 && x < 1200) return { x, y }
    }
    return null
  })
  expect(free, 'a node clear of the HUD to drag').not.toBeNull()
  await page.mouse.move(free!.x, free!.y)
  await page.mouse.down()
  await page.mouse.move(free!.x + 40, free!.y + 30, { steps: 6 })
  await page.clock.runFor(64)
  const dragShown = await page.evaluate(() => {
    const d = document.querySelector<HTMLElement>('.drag-preview')
    return !!d && getComputedStyle(d).display !== 'none' && d.getBoundingClientRect().width > 0
  })
  seen['node being dragged'] = await census(page)
  await page.mouse.up()

  console.log('[038 budget] css effects without a GPU', JSON.stringify({ present, dragShown, labels, seen }))
  expect(dragShown, 'state "node being dragged": the drag preview is on screen').toBe(true)
  expect(seen, 'elements with a computed CSS filter or backdrop-filter without a GPU, by state. Expected is the recorded list (CSS_EFFECTS_NO_GPU in e2e/qa/budgets.ts): '
    + 'a new entry is an effect that software compositing pays for on every frame - gate it by data-gpu=0 as the others are, or record it there with a dated line and its measured cost')
    .toEqual(CSS_EFFECTS_NO_GPU)
  hold(labels.withFilter, BUDGETS.NODE_LABELS_WITH_CSS_FILTER_NO_GPU)
  expect(labels.withTextShadow, 'the text-shadow of node labels is not what the gate removes').toBe(labels.labels)
})

// The other side: the gate is "no GPU". With a GPU believed present node labels keep their drop-shadow.
test('counter-check: with a GPU believed present the node labels of a selected hub keep their drop-shadow filter', async ({ page }) => {
  await bootNeighbors(page, snapshot, true)
  expect(await page.evaluate(() => document.querySelector<HTMLElement>('.root')!.dataset.gpu)).toBe('1')
  expect(await selectHub(page)).toBe(28)
  const withGpu = await nodeLabels(page)
  await page.evaluate(() => document.querySelector<HTMLElement>('.root')!.setAttribute('data-quality', 'low'))
  const atLow = await nodeLabels(page)
  await page.evaluate(() => document.querySelector<HTMLElement>('.root')!.setAttribute('data-gpu', '0'))
  const noGpu = await nodeLabels(page)
  console.log('[038 budget] node labels', JSON.stringify({ withGpu, atLow, noGpu }))
  expect(withGpu, 'every node label carries the filter with a GPU').toEqual({ labels: 28, withFilter: 28, withTextShadow: 28 })
  expect(atLow.withFilter, 'data-quality=low alone does not remove it').toBe(28)
  expect(noGpu, 'data-gpu=0 removes the filter and only the filter').toEqual({ labels: 28, withFilter: 0, withTextShadow: 28 })
})
