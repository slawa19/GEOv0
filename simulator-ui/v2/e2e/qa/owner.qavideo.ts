/**
 * 038 S1 - what the owner looks at: three short videos in real time and the frames of the empty screen.
 * Nothing here is a verdict on the product; the assertions only make sure the video shows what its title says.
 * Wall clock only: `page.clock` is never installed, CSS animations of labels run by themselves (`cssClock: false`).
 * Waiting is by condition (a label is gone, a spark is gone) or by animation frames of the page, never by a timeout.
 */
import { expect, test, type Page } from '@playwright/test'

import { pickRingRoute } from './oracle.js'
import { RING, RUN_ID, SCENARIO_ID, EQ, boot, fxCounts, pushEvent, ringSnapshot, screenNodes, txUpdated } from './stand.js'

/** Let the page render `n` frames in real time. */
async function realFrames(page: Page, n: number): Promise<void> {
  await page.evaluate((count) => new Promise<void>((resolve) => {
    let left = count
    const tick = () => { left -= 1; if (left <= 0) resolve(); else requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
  }), n)
}

/** Everything a payment or a clearing put on the screen is gone again. */
async function sceneQuiet(page: Page): Promise<void> {
  await expect.poll(async () => { const c = await fxCounts(page); return c.sparks + c.edgePulses + c.nodeBursts + c.labels }, { timeout: 20_000, intervals: [100] }).toBe(0)
}

async function setQuality(page: Page, value: 'low' | 'med' | 'high'): Promise<void> {
  await page.getByRole('combobox', { name: 'Quality' }).selectOption(value)
  await expect.poll(() => page.evaluate(() => document.querySelector<HTMLElement>('.root')?.dataset.quality)).toBe(value)
}

test('video 1 - the beam of a payment over a route of 3 edges (twice at the quality a no-GPU browser gets, twice at high)', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false, cssClock: false })
  const g = await pickRingRoute(page, RING, 3)
  test.info().annotations.push({ type: 'route', description: g.ids.join(' > ') })
  await realFrames(page, 45)
  for (const quality of ['low', 'high'] as const) {
    await setQuality(page, quality)
    for (let i = 0; i < 2; i += 1) {
      await pushEvent(page, txUpdated(g.ids, '10.00'))
      await expect.poll(async () => (await fxCounts(page)).sparks, { intervals: [20] }).toBeGreaterThan(0)
      await sceneQuiet(page)
      await realFrames(page, 30)
    }
  }
})

test('video 2 - the amount label of a payment and the amount label of a clearing fade out (F-038-2: the question for the owner)', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false, cssClock: false })
  await realFrames(page, 45)
  for (let i = 0; i < 2; i += 1) {
    // Payment: "-10.00" at the sender at once, "+10.00" at the receiver when the beam arrives; each lives 1900 ms.
    await pushEvent(page, txUpdated(['b', 'c'], '10.00'))
    await expect.poll(async () => (await fxCounts(page)).labels, { intervals: [20] }).toBeGreaterThan(0)
    await sceneQuiet(page)
    await realFrames(page, 30)
    // Clearing: the amount over the cycle lives 3800 ms.
    await pushEvent(page, {
      type: 'clearing.done', plan_id: `plan-p038-video-${i}`, cleared_cycles: 1, cleared_amount: '7.00',
      cycle_edges: [{ from: 'b', to: 'c' }, { from: 'c', to: 'd' }, { from: 'd', to: 'e' }],
    })
    await expect.poll(async () => (await fxCounts(page)).labels, { intervals: [20] }).toBeGreaterThan(0)
    await sceneQuiet(page)
    await realFrames(page, 30)
  }
})

test('video 3 - a node is dragged with the mouse and stays where it is dropped', async ({ page }) => {
  await boot(page, { snapshot: ringSnapshot(), clock: false, cssClock: false })
  await realFrames(page, 45)
  const pinned = () => page.evaluate(() => (window as unknown as { __geoSim: { pinnedNodeIds: string[] } }).__geoSim.pinnedNodeIds)
  const drags: Array<[string, { x: number; y: number }]> = [['e', { x: 150, y: 90 }], ['c', { x: -170, y: -60 }], ['e', { x: -120, y: -150 }]]
  for (const [id, by] of drags) {
    const at = (await screenNodes(page)).get(id)!
    await page.mouse.move(at.x - 60, at.y - 40)
    await realFrames(page, 10)
    await page.mouse.move(at.x, at.y, { steps: 8 })
    await realFrames(page, 10)
    await page.mouse.down()
    const steps = 45
    for (let i = 1; i <= steps; i += 1) {
      await page.mouse.move(at.x + (by.x * i) / steps, at.y + (by.y * i) / steps)
      await realFrames(page, 1)
    }
    await realFrames(page, 15)
    await page.mouse.up()
    await realFrames(page, 40)
    expect(await pinned()).toContain(id)
  }
})

test('frames - the empty screen with the error after a scene that cannot be loaded (and the screen before it)', async ({ page }, info) => {
  const OTHER = 'p038-qa-unloadable'
  const item = (scenario_id: string, name: string) => ({ api_version: 'simulator-api/1', scenario_id, name, participants_count: 6, trustlines_count: 6, equivalents: [EQ] })
  await boot(page, {
    snapshot: ringSnapshot(), clock: false, cssClock: false, ui: 'auto',
    beforeLoad: async (p) => {
      await p.route(/\/simulator\/scenarios$/i, (r) => r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ api_version: 'simulator-api/1', items: [item(SCENARIO_ID, 'QA stand'), item(OTHER, 'QA stand, unloadable')] }),
      }))
      await p.route(new RegExp(`/scenarios/${OTHER}/graph/preview`, 'i'), (r) => r.fulfill({
        status: 500, contentType: 'application/json', body: JSON.stringify({ code: 'QA_STAND_NO_SCENE', message: 'qa stand: this scene cannot be loaded' }),
      }))
    },
  })
  await realFrames(page, 45)
  const before = info.outputPath('empty-screen-1-before.png')
  await page.screenshot({ path: before })
  await info.attach('1 - the scene before the failed load', { path: before, contentType: 'image/png' })

  await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'stopped', sim_time_ms: 0, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
  await realFrames(page, 20)
  await page.getByRole('combobox', { name: 'Scenario' }).selectOption(OTHER)
  await expect(page.locator('.ds-ov-inset[role="alert"]')).toBeVisible()
  await expect.poll(() => page.evaluate(() => (window as unknown as { __geoSim: { hasSnapshot: boolean } }).__geoSim.hasSnapshot)).toBe(false)
  await realFrames(page, 30)
  const after = info.outputPath('empty-screen-2-after-failed-load.png')
  await page.screenshot({ path: after })
  await info.attach('2 - the empty screen with the error', { path: after, contentType: 'image/png' })

  // A payment event that still arrives over the open stream of the stopped run: no amount label appears.
  await pushEvent(page, txUpdated(['a', 'b'], '10.00'))
  await realFrames(page, 100)
  const later = info.outputPath('empty-screen-3-after-a-late-payment-event.png')
  await page.screenshot({ path: later })
  await info.attach('3 - the same screen after a late payment event', { path: later, contentType: 'image/png' })
  expect((await fxCounts(page)).labels).toBe(0)
})
