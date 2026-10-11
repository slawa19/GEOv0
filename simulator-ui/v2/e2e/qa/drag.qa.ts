/**
 * 038 S0 - R5 (F-038-1, the interaction part): a node dragged with the mouse under a zoomed and panned camera moves
 * by the mouse delta divided by the zoom, and is pinned. Zoom and pan are set the way a user sets them (wheel, drag
 * of the empty canvas), so the expected world delta is computed from the camera the product reports.
 * Not checked here (S1): position after a relayout, and the canvas picture against the DOM drag preview.
 * Known to pass on a broken product (adversarial pass 2026-10-11): "pinned" is read from the pinned-position map, so a
 * drag that never calls `pinNodeLive` (fx/fy not set on the physics node) passes; and "opens no card" is true today
 * whether or not the click after a drag is suppressed. Both mutations must turn red in S1 before R5 is relied on.
 */
import { expect, test, type Page } from '@playwright/test'

import { boot, camera, frames, layoutNodes, ringSnapshot, screenNodes, type Pt } from './stand.js'

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
