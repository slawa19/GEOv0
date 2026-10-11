/**
 * 038 S1 (A4, adversarial pass on fdd890c5) - what ends a beam in flight and what does not.
 *
 * The legs of a route are spawned on timers. A REFRESH of the scene that is on screen (an empty `topology.changed`,
 * the Refresh button, recovery after a broken stream) cut the beam after its current edge, while the receiver's
 * amount label - a critical timer - still came: a number arriving with no beam. The legs are now timers of the
 * label's class. The other direction is held too: a change of scene in the middle of a flight leaves no leg of the
 * old payment on the new scene.
 *
 * Not driven in a browser: a run stopped or restarted in the middle of a flight (the Stop button needs a backend that
 * answers it); that direction is a unit test (`useRealTxFx.test.ts`, `resetRateLimiter`).
 */
import { expect, test, type Page } from '@playwright/test'

import { pickRingRoute } from './oracle.js'
import {
  OTHER_SCENARIO, RUN_ID, SCENARIO_ID, bootTwoScenarios, frames, fxCounts, pushEvent, sampleCanvas, txUpdated, type SceneFail,
} from './stand.js'

const RING = ['a', 'b', 'c', 'd', 'e', 'f']

async function aliveSparks(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const now = performance.now()
    return (window as unknown as { __geoSim: { fxState: { sparks: Array<{ source: string; target: string; startedAtMs: number; ttlMs: number }> } } })
      .__geoSim.fxState.sparks.filter((s) => now - s.startedAtMs < s.ttlMs).map((s) => `${s.source}>${s.target}`)
  })
}

const receiverLabel = (page: Page) => page.evaluate(() => [...document.querySelectorAll('.floating-label-inner')].some((el) => (el.textContent ?? '').trim().startsWith('+')))

test('a snapshot refresh of the scene on screen in the middle of a flight does not cut the beam: 3 edges of 3, and the label at 1200 ms', async ({ page }) => {
  const fail: SceneFail = { other: false, all: false }
  await bootTwoScenarios(page, fail)
  const g = await pickRingRoute(page, RING, 3)
  await pushEvent(page, txUpdated(g.ids, '10.00'))
  const seen: string[] = []
  let labelAtFrame: number | null = null
  let readsAtRefresh = -1
  for (let f = 1; f <= 80; f += 1) {
    if (f === 13) { // inside the first leg (400 ms = 25 frames)
      readsAtRefresh = fail.reads ?? 0
      await pushEvent(page, { type: 'topology.changed', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] } })
    }
    await frames(page, 1)
    for (const s of await aliveSparks(page)) if (seen[seen.length - 1] !== s) seen.push(s)
    if (labelAtFrame === null && (await receiverLabel(page))) labelAtFrame = f
  }
  const row = { seen, labelAtFrame, sceneReadsDuringFlight: (fail.reads ?? 0) - readsAtRefresh }
  console.log('[038 refresh/same-scene]', JSON.stringify(row))
  expect(row.sceneReadsDuringFlight, 'counter-check: the scene WAS read again during the flight').toBeGreaterThan(0)
  expect(seen, 'every edge of the route is flown').toEqual(g.edges.map((e) => e.name))
  expect(labelAtFrame, 'and the receiver label comes when the beam arrives').toBeGreaterThanOrEqual(75)
  expect(labelAtFrame).toBeLessThanOrEqual(77)
})

test('a change of scene in the middle of a flight ends the beam: no leg, no burst and no label of the old payment on the new scene', async ({ page }) => {
  await bootTwoScenarios(page, { other: false, all: false })
  const g = await pickRingRoute(page, RING, 3)
  await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'stopped', sim_time_ms: 0, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
  await frames(page, 2)
  await pushEvent(page, txUpdated(g.ids, '10.00'))
  await frames(page, 13)
  expect(await aliveSparks(page), 'counter-check: the beam is on its first edge when the scene changes').toEqual([g.edges[0]!.name])

  await page.getByRole('combobox', { name: 'Scenario' }).selectOption(OTHER_SCENARIO)
  await expect.poll(() => page.evaluate(() => (window as unknown as { __geoSim: { layoutNodes: Array<{ id: string }> } }).__geoSim.layoutNodes.some((n) => n.id.startsWith('x'))), { message: 'the other scenario is on screen', timeout: 15_000 }).toBe(true)

  let maxSparksInState = 0
  let maxBursts = 0
  let maxLabels = 0
  let maxPainted = 0
  for (let f = 0; f < 90; f += 1) { // past the whole route time of the old payment
    await frames(page, 1)
    const c = await fxCounts(page)
    maxSparksInState = Math.max(maxSparksInState, c.sparks)
    maxBursts = Math.max(maxBursts, c.nodeBursts)
    maxLabels = Math.max(maxLabels, c.labels)
    maxPainted = Math.max(maxPainted, (await sampleCanvas(page, {})).__canvas.lit)
  }
  const row = { maxSparksInState, maxBursts, maxLabels, maxPainted }
  console.log('[038 refresh/other-scene]', JSON.stringify(row))
  expect(row, 'nothing of the old payment reaches the new scene').toEqual({ maxSparksInState: 0, maxBursts: 0, maxLabels: 0, maxPainted: 0 })
})
