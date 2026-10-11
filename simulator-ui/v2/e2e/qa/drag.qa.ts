/**
 * 038 S0 - R5 (F-038-1, the interaction part): a node dragged with the mouse under a zoomed and panned camera moves
 * by the mouse delta divided by the zoom, and is pinned. Zoom and pan are set the way a user sets them (wheel, drag
 * of the empty canvas), so the expected world delta is computed from the camera the product reports.
 *
 * S1 adds the two properties the first test does not hold (adversarial pass 2026-10-11, mutations M9c2 and M9e):
 *  - the dropped node is fixed IN THE PHYSICS (`fx`/`fy`), not only listed in the pinned-position map, and keeps its
 *    place through a relayout. Mutation M9c2: the drag never calls `pinNodeLive`.
 *  - the click the browser fires at the end of a drag is not taken as a click. "Opens no card" could not see that (a
 *    card opens on a double click); what the suppression protects is an open Interact flow, where a click on a node
 *    is a CHOICE of a participant. Mutation M9e: the suppression after a drag is removed.
 * The delta at three zooms is a unit test (`useDragToPinInteraction.test.ts`).
 * Not checked: the canvas picture against the DOM drag preview (the node is hidden on the canvas while dragged, there
 * are never two pictures to compare); touch; a drag that ends outside the window.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, camera, frames, layoutNodes, ringSnapshot, screenNodes, type Pt } from './stand.js'

type PhysNode = { id: string; x: number; y: number; fx: number | null; fy: number | null }
const physNodes = (page: Page) => page.evaluate(() => (window as unknown as { __geoSim: { layoutNodes: PhysNode[] } }).__geoSim.layoutNodes)
const physNode = async (page: Page, id: string) => (await physNodes(page)).find((n) => n.id === id)!

/** Nodes whose centre, and the point `delta` away from it, are on the bare canvas: nothing of the HUD or a window is above. */
async function draggableNodes(page: Page, delta: Pt): Promise<Array<[string, Pt]>> {
  const all = [...(await screenNodes(page)).entries()]
  const onCanvas = await page.evaluate((pts) => pts.map(([x, y]) => document.elementFromPoint(x!, y!)?.matches('canvas.canvas:not(.canvas-fx)') ?? false),
    all.flatMap(([, p]) => [[p.x, p.y], [p.x + delta.x, p.y + delta.y]]))
  return all.filter((_, i) => onCanvas[2 * i] && onCanvas[2 * i + 1])
}

const VIEW = { x0: 140, x1: 1140, y0: 150, y1: 560 }

/** A point of the canvas far from every node: a press there pans the camera instead of picking a node. */
async function emptyPoint(page: Page): Promise<Pt> {
  const nodes = [...(await screenNodes(page)).values()]
  let best: Pt = { x: 0, y: 0 }
  let bestD = -1
  for (let x = VIEW.x0; x <= VIEW.x1; x += 20) {
    for (let y = VIEW.y0; y <= VIEW.y1; y += 20) {
      const d = Math.min(...nodes.map((n) => Math.hypot(n.x - x, n.y - y)))
      if (d > bestD) { bestD = d; best = { x, y } }
    }
  }
  expect(bestD, 'there is empty canvas to press on').toBeGreaterThan(60)
  return best
}

async function mouseDrag(page: Page, from: Pt, by: Pt, steps = 8) {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  for (let i = 1; i <= steps; i += 1) {
    await page.mouse.move(from.x + (by.x * i) / steps, from.y + (by.y * i) / steps)
    await frames(page, 1)
  }
  await page.mouse.up()
  await frames(page, 4)
}

const windows = (page: Page) => page.locator('.ws-shell').count()
const pinned = (page: Page) => page.evaluate(() => (window as unknown as { __geoSim: { pinnedNodeIds: string[] } }).__geoSim.pinnedNodeIds)

test('R5 - a mouse drag moves the node by delta / zoom under a zoomed and panned camera, pins it, and opens no card', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const cam0 = await camera(page)

  // Zoom in with the wheel over empty canvas, then pan by dragging it.
  const empty = await emptyPoint(page)
  await page.mouse.move(empty.x, empty.y)
  await page.mouse.wheel(0, -300)
  await frames(page, 30)
  await mouseDrag(page, await emptyPoint(page), { x: 70, y: 40 })
  const cam = await camera(page)
  expect(cam.zoom, 'the wheel zoomed the camera in').toBeGreaterThan(cam0.zoom * 1.2)
  expect(Math.hypot(cam.panX - cam0.panX, cam.panY - cam0.panY), 'the camera is panned').toBeGreaterThan(30)

  // A node that is on screen now and stays on screen after the drag.
  const delta = { x: 120, y: -60 }
  const onScreen = [...(await screenNodes(page)).entries()].find(([, p]) =>
    p.x > VIEW.x0 && p.x + delta.x < VIEW.x1 && p.y + delta.y > VIEW.y0 && p.y < VIEW.y1)
  expect(onScreen, 'a node is on screen with room to be dragged').toBeTruthy()
  const [id, at] = onScreen!
  const before = (await layoutNodes(page)).find((n) => n.id === id)!
  const windowsBefore = await windows(page)
  expect(await pinned(page)).not.toContain(id)

  await mouseDrag(page, at, delta)

  const after = (await layoutNodes(page)).find((n) => n.id === id)!
  const camAfter = await camera(page)
  const moved = { x: after.x - before.x, y: after.y - before.y }
  const expected = { x: delta.x / cam.zoom, y: delta.y / cam.zoom }
  const row = { node: id, camera: cam, mouseDelta: delta, worldDelta: moved, expected, pinned: await pinned(page), windows: [windowsBefore, await windows(page)] }
  console.log('[038 R5]', JSON.stringify(row))
  test.info().annotations.push({ type: '038-R5', description: JSON.stringify(row) })

  expect(camAfter, 'dragging a node does not move the camera').toEqual(cam)
  expect(Math.abs(moved.x - expected.x), 'world dx = mouse dx / zoom, within 0.5').toBeLessThanOrEqual(0.5)
  expect(Math.abs(moved.y - expected.y), 'world dy = mouse dy / zoom, within 0.5').toBeLessThanOrEqual(0.5)
  expect(row.pinned, 'the dragged node is pinned').toContain(id)
  expect(row.windows[1], 'the release after a drag opens no node card').toBe(windowsBefore)

  // Counter-check of "moved": a press and release without movement moves nothing and pins nothing more.
  const other = [...(await screenNodes(page)).entries()].find(([k, p]) => k !== id && p.x > VIEW.x0 && p.x < VIEW.x1 && p.y > VIEW.y0 && p.y < VIEW.y1)
  expect(other, 'a second node is on screen for the counter-check').toBeTruthy()
  const b = (await layoutNodes(page)).find((n) => n.id === other![0])!
  await mouseDrag(page, other![1], { x: 0, y: 0 }, 1)
  const a = (await layoutNodes(page)).find((n) => n.id === other![0])!
  console.log('[038 R5] click without movement', JSON.stringify({ node: other![0], moved: { x: a.x - b.x, y: a.y - b.y }, pinned: await pinned(page), windows: await windows(page) }))
  expect({ x: a.x - b.x, y: a.y - b.y }, 'a click without movement moves nothing').toEqual({ x: 0, y: 0 })
  expect(await pinned(page), 'a click does not pin').not.toContain(other![0])
})

test('R5 - the dropped node is fixed in the physics (fx/fy) and keeps its place through a relayout (M9c2)', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  const delta = { x: 90, y: -45 }
  const candidates = await draggableNodes(page, delta)
  expect(candidates.length, 'a node with room to be dragged on the bare canvas').toBeGreaterThan(0)
  const [id, at] = candidates[0]!
  const before = await physNode(page, id)
  expect({ fx: before.fx, fy: before.fy }, 'counter-check: before the drag the physics is free to move the node').toEqual({ fx: null, fy: null })

  await mouseDrag(page, at, delta)
  const dropped = await physNode(page, id)
  expect(Math.hypot(dropped.x - before.x, dropped.y - before.y), 'the node was dragged').toBeGreaterThan(50)
  expect({ fx: dropped.fx, fy: dropped.fy }, 'the physics holds the node where it was dropped (fx/fy = its position)').toEqual({ fx: dropped.x, fy: dropped.y })

  // A relayout as a user causes it: the browser window gets smaller. The layout arrays and the physics are rebuilt.
  const othersBefore = (await physNodes(page)).filter((n) => n.id !== id)
  await page.setViewportSize({ width: 1100, height: 640 })
  await frames(page, 300)
  const after = await physNode(page, id)
  const othersAfter = (await physNodes(page)).filter((n) => n.id !== id)
  const othersMoved = Math.max(...othersBefore.map((b) => { const a = othersAfter.find((n) => n.id === b.id)!; return Math.hypot(a.x - b.x, a.y - b.y) }))
  const row = { node: id, before, dropped, after, othersMovedMaxPx: Number(othersMoved.toFixed(1)) }
  console.log('[038 R5 pin]', JSON.stringify(row))
  test.info().annotations.push({ type: '038-R5-pin', description: JSON.stringify(row) })
  expect(othersMoved, 'counter-check: the relayout did happen (free nodes moved)').toBeGreaterThan(5)
  expect(Math.hypot(after.x - dropped.x, after.y - dropped.y), 'the dropped node kept its world position through the relayout, within 0.5').toBeLessThanOrEqual(0.5)
  expect({ fx: after.fx, fy: after.fy }, 'and is still fixed in the rebuilt physics').toEqual({ fx: after.x, fy: after.y })
})

test('R5 - the click that ends a drag is not a choice: an open payment flow does not take the dragged node as sender (M9e)', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: true })
  await page.locator('[data-testid="actionbar-payment"]').click()
  await frames(page, 30)
  const panel = page.locator('[data-testid="manual-payment-panel"]')
  await expect(panel).toBeVisible()
  const sender = () => panel.locator('select').first().inputValue()
  expect(await sender(), 'the flow waits for a sender').toBe('')

  const delta = { x: 70, y: 40 }
  const candidates = await draggableNodes(page, delta)
  expect(candidates.length, 'two nodes on the bare canvas: one to drag, one to click').toBeGreaterThanOrEqual(2)
  const [dragId, dragAt] = candidates[0]!
  const [clickId, clickAt] = candidates[1]!

  await mouseDrag(page, dragAt, delta)
  await frames(page, 30) // past the 300 ms window in which a late click would still be suppressed
  const afterDrag = { sender: await sender(), pinned: await pinned(page) }
  expect(afterDrag.pinned, 'counter-check: the gesture was a drag').toContain(dragId)
  expect(afterDrag.sender, 'the drag did not choose a sender').toBe('')

  // Counter-check: a press and release WITHOUT movement on a node is a choice, so the reading above is alive.
  await mouseDrag(page, clickAt, { x: 0, y: 0 }, 1)
  await frames(page, 30)
  const afterClick = await sender()
  console.log('[038 R5 click]', JSON.stringify({ dragged: dragId, afterDrag, clicked: clickId, afterClick }))
  expect(afterClick, 'a plain click on a node chooses it as the sender').toBe(clickId)
})
