/**
 * 039 slice B, PR 1, fix-delta of the external review (2026-10-12, class 1): the DRAG PREVIEW kept the money
 * semantics of the moment the drag began.
 *
 * While a node is dragged the canvas does not draw it (`render/baseGraph.ts`, `hiddenNodeId`); a DOM element stands
 * for it. Its colour, sign and freeze styling were written once, when the drag started. So:
 *  (a) a patch accepted DURING the drag (a payment in Auto-Run) changed the node's `net_sign`, and the only picture
 *      of that node on screen went on showing the old role until the button was released;
 *  (b) a scene replaced DURING the drag left the preview of a node of the OLD scene on the new one - with the
 *      legend already titled by the new snapshot.
 *
 * Both through the product's own paths, in a browser: a real pointer drag, a real SSE frame through parser,
 * normalizer, pipeline and patch applier; a real scenario change through the selector and the scene loader.
 * Debug-only (this config is not counted); the counted checks of the same behaviour are Vitest:
 * `src/composables/useAppDragToPinAndPreview.p039.test.ts`.
 *
 * LIMITS. The scene replacement exercised here is the scenario change (a full reload). The pending EQUIVALENT switch
 * of the review's scenario takes the same branch of the scene loader (`useSceneState.ts` `clearScene`: another
 * equivalent is never an increment) and is held at that level by the Vitest file, with the real scene loader - the
 * stand's mocked backend offers one equivalent. Pixels of the preview are not judged.
 */
import { expect, test, type Page } from '@playwright/test'

import { OTHER_SCENARIO, RUN_ID, SCENARIO_ID, boot, bootTwoScenarios, frames, pushEvent, ringSnapshot, screenNodes, txUpdated } from '../stand.js'

const preview = (page: Page) => page.locator('.drag-preview')
const shown = (page: Page) => preview(page).evaluate((el) => ({ display: getComputedStyle(el).display, sign: el.textContent ?? '', bg: (el as HTMLElement).style.background, border: (el as HTMLElement).style.borderStyle }))
const pinned = (page: Page) => page.evaluate(() => [...(window as unknown as { __geoSim: { pinnedNodeIds: string[] } }).__geoSim.pinnedNodeIds])

/** A node whose centre the canvas owns (not under a bar of the HUD): the first such of the scene. */
async function freeNode(page: Page): Promise<string> {
  for (const [id, at] of await screenNodes(page)) {
    const onCanvas = await page.evaluate(({ x, y }) => { const el = document.elementFromPoint(x, y); return el instanceof HTMLCanvasElement && !el.classList.contains('canvas-fx') }, at)
    if (onCanvas) return id
  }
  throw new Error('039: no node of the scene is free of the HUD')
}

/** Press on a node and move far enough for the drag to begin; the button stays DOWN. */
async function beginDrag(page: Page, id: string, step: () => Promise<void>): Promise<void> {
  const at = (await screenNodes(page)).get(id)!
  await page.mouse.move(at.x, at.y)
  await page.mouse.down()
  for (let i = 1; i <= 4; i += 1) {
    await page.mouse.move(at.x + i * 6, at.y + i * 4)
    await step()
  }
}

test('039 B1 review, class 1 (a) - a patch accepted during a drag: the preview of the dragged node follows its new sign, and an explicit null shows "?"', async ({ page }) => {
  const s = ringSnapshot()
  for (const n of s.nodes) Object.assign(n, { net_sign: 1, net_balance_atoms: '1000' }) // every node is a net creditor
  await boot(page, { snapshot: s, clock: false })
  const id = await freeNode(page)
  const peer = s.links.find((l) => l.source === id)!.target
  await beginDrag(page, id, async () => {})
  await expect.poll(async () => (await shown(page)).display, { message: 'counter-check: the drag began and its preview is on screen' }).not.toBe('none')
  const before = await shown(page)
  expect(before.sign, 'counter-check: the creditor is dragged with its sign').toBe('+')

  // A payment of the dragged node lands while the button is down; its patch makes the node a net debtor.
  await pushEvent(page, txUpdated([id, peer], '10.00', { node_patch: [{ id, net_sign: -1, net_balance_atoms: '500' }] }))
  await expect.poll(async () => (await shown(page)).sign, { message: 'the preview shows the sign the snapshot now has for the node', timeout: 5000 }).toBe('−')
  expect((await shown(page)).bg, 'and its colour changed with the role').not.toBe(before.bg)

  await pushEvent(page, txUpdated([id, peer], '1.00', { node_patch: [{ id, net_sign: null }] }))
  await expect.poll(async () => (await shown(page)).sign, { message: 'an explicit null is UNKNOWN on the preview too', timeout: 5000 }).toBe('?')

  await page.mouse.up()
  expect(await pinned(page), 'counter-check: a drag that was not cancelled still pins its node').toEqual([id])
})

test('039 B1 review, class 1 (b) - a scene replaced during a drag: the preview of the old node is gone, and releasing the button pins nothing of the old scene', async ({ page }) => {
  await bootTwoScenarios(page, { other: false, all: false })
  // The run on screen reports `stopped` over its own stream: only then does the selector change the scene (as in `empty-scene.qa.ts`).
  await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'stopped', sim_time_ms: 0, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
  await frames(page, 2)
  await beginDrag(page, await freeNode(page), () => frames(page, 1))
  expect((await shown(page)).display, 'counter-check: the drag began and its preview is on screen').not.toBe('none')

  await page.getByTestId('scenario-select').selectOption(OTHER_SCENARIO)
  await expect.poll(() => page.evaluate(() => (window as unknown as { __geoSim: { layoutNodes: Array<{ id: string }> } }).__geoSim.layoutNodes.some((n) => n.id.startsWith('x'))), { message: 'the other scenario is loaded', timeout: 15_000 }).toBe(true)
  await frames(page, 4)
  expect((await shown(page)).display, 'the preview of a node of the replaced scene is not shown over the new one').toBe('none')

  // The button is still down. Moving and releasing it must not bring the preview back, nor pin the old node.
  const vp = page.viewportSize()!
  await page.mouse.move(vp.width / 2, vp.height / 2)
  await frames(page, 2)
  expect((await shown(page)).display, 'a move after the cancel does not show it again').toBe('none')
  await page.mouse.up()
  await frames(page, 4)
  expect(await pinned(page), 'no pin is written for a node of the old scene').toEqual([])
})
