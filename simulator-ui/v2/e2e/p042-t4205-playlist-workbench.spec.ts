/**
 * 042 T4205 - the offline effects workbench: the recorded event playlists of the offline snapshot
 * (`public/simulator-fixtures/v1/<EQ>/events/`) are played through the same event path as the server's SSE events, and
 * the effect is DRAWN, with no server.
 *
 * The page is opened as a person opens it (`?mode=fixtures`, the "Источник" choice "Снимок без сервера"); the
 * workbench is reached under "Подробнее". Every request to `/api/` is answered with an abort and counted: the test
 * holds that none is made (no server, no hidden fallback).
 *
 * What is read: the FX state of the dev hook (`window.__geoSim.fxState`: sparks of a payment, edge pulses of a clearing)
 * and the PIXELS of the FX canvas along the segments of those sparks and pulses, away from the nodes (the method of
 * `e2e/live/autostart.live.ts`: a painted point there is the beam or the pulse, not a node burst). The FX canvas is
 * cleared every frame and the graph lives on the base canvas.
 *
 * Test mode is on (the default config: physics off, deterministic layout), and `navigator.webdriver` is cleared for this
 * file: the product reads it as "screenshot stand" and then spawns no FX at all (`render/fxRenderer/spawn.ts`).
 *
 * The amount labels are read too (2026-10-18, 039 slice C): the recordings carried no `amount` / `cleared_amount` until
 * that day, so the workbench never showed one; the amounts now come from the recorded patches (rule:
 * `usePlaylistWorkbench.test.ts`, spec 039 Changelog). Until then this header said "the amount labels (the recorded
 * payments carry no amount)" under what the test does not see.
 *
 * WHAT IT DOES NOT SEE: the timing of the effects against their CSS (the QA stand's business), where on screen a label
 * stands, a production build (no dev hook), the equivalents other than UAH (the offline screen shows UAH only while
 * `VITE_DEMO_FIXTURES=1`).
 */
import { expect, test, type Page } from '@playwright/test'

test.use({ launchOptions: { args: ['--disable-blink-features=AutomationControlled'] } })

/** Screen pixels around a node left out of the sample: the outer reach of a node burst's glow (`renderFrame.ts`). */
const NODE_CLEAR_PX = 45

type Seg = 'sparks' | 'edgePulses'

/** Painted points (alpha >= 24) on the segments of the live sparks or edge pulses, away from every node on them. */
const fxPointsLit = (page: Page, kind: Seg) =>
  page.evaluate(([clear, which]) => {
    type G = {
      fxState: { sparks: Array<{ source: string; target: string }>; edgePulses: Array<{ from: string; to: string }> }
      layoutNodes: Array<{ id: string; x: number; y: number }>
      camera: { panX: number; panY: number; zoom: number }
    }
    const g = (window as unknown as { __geoSim?: G }).__geoSim
    const c = document.querySelector<HTMLCanvasElement>('canvas.canvas-fx')
    const ctx = c?.getContext('2d')
    const segs = !g ? [] : which === 'sparks'
      ? g.fxState.sparks.map((s) => [s.source, s.target] as const)
      : g.fxState.edgePulses.map((p) => [p.from, p.to] as const)
    if (!g || !c || !ctx || c.width === 0 || segs.length === 0) return { lit: 0, live: segs.length, sampled: 0 }
    const k = c.width / Math.max(1, c.clientWidth)
    const img = ctx.getImageData(0, 0, c.width, c.height)
    const alpha = (x: number, y: number) => {
      const px = Math.round(x * k)
      const py = Math.round(y * k)
      return px < 0 || py < 0 || px >= c.width || py >= c.height ? 0 : img.data[(py * c.width + px) * 4 + 3]!
    }
    const cam = g.camera
    const at = new Map(g.layoutNodes.map((n) => [n.id, { x: n.x * cam.zoom + cam.panX, y: n.y * cam.zoom + cam.panY }]))
    let lit = 0
    let sampled = 0
    for (const [s, t] of segs) {
      const a = at.get(s)
      const b = at.get(t)
      if (!a || !b) continue
      const len = Math.hypot(b.x - a.x, b.y - a.y)
      for (let d = clear; d <= len - clear; d += 2) {
        const p = { x: a.x + ((b.x - a.x) * d) / len, y: a.y + ((b.y - a.y) * d) / len }
        sampled += 1
        let best = 0
        for (let dy = -2; dy <= 2; dy += 1) for (let dx = -2; dx <= 2; dx += 1) best = Math.max(best, alpha(p.x + dx, p.y + dy))
        if (best >= 24) lit += 1
      }
    }
    return { lit, live: segs.length, sampled }
  }, [NODE_CLEAR_PX, kind] as const)

/** Texts of the amount labels on screen (DOM over the canvas). */
const labelTexts = (page: Page) => page.locator('.floating-label').allTextContents().then((t) => t.map((x) => x.replace(/\s+/g, ' ').trim()))

/** The label texts seen so far: a label lives its CSS fade, a poll may come after the first one is gone. */
const seenLabels = async (page: Page, seen: Set<string>) => {
  for (const t of await labelTexts(page)) seen.add(t)
  return [...seen]
}

async function openWorkbench(page: Page) {
  const apiCalls: string[] = []
  // The backend's prefix only: a glob `**/api/**` would also catch the dev server's own modules (`/src/api/*.ts`).
  await page.route((url) => url.pathname.startsWith('/api/'), (route) => {
    apiCalls.push(route.request().url())
    return route.abort()
  })
  await page.goto('/?mode=fixtures')
  await page.waitForSelector('[data-ready="1"]', { timeout: 20_000 })
  await page.locator('[data-testid="details-toggle"]').click()
  const select = page.getByRole('combobox', { name: 'Плейлист' })
  await expect(select).toBeVisible()
  return { apiCalls, select }
}

test.describe('042 T4205: the offline effects workbench plays the recorded playlists', () => {
  test('a recorded payment playlist, played offline, draws its beam on the FX canvas', async ({ page }) => {
    const { apiCalls, select } = await openWorkbench(page)
    await select.selectOption({ label: 'Платежи (20)' })
    await page.getByRole('button', { name: 'Проиграть', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Стоп', exact: true })).toBeVisible()

    let last = { lit: 0, live: 0, sampled: 0 }
    await expect
      .poll(async () => {
        last = await fxPointsLit(page, 'sparks')
        return last.lit
      }, { timeout: 8_000, intervals: [50] })
      .toBeGreaterThan(0)
    expect(last.live, 'a spark of the played payment is in the FX state').toBeGreaterThan(0)
    // The first recorded payment, U0003 -> U0017, 38.93: the sender's label at once, the receiver's when the beam arrives.
    const seen = new Set<string>()
    await expect.poll(() => seenLabels(page, seen), { timeout: 8_000, intervals: [50] }).toContain('-38.93 UAH')
    await expect.poll(() => seenLabels(page, seen), { timeout: 8_000, intervals: [50] }).toContain('+38.93 UAH')

    await page.getByRole('button', { name: 'Стоп', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Проиграть', exact: true })).toBeVisible()
    expect(apiCalls, 'the workbench makes no request to a server').toEqual([])
  })

  test('a recorded clearing playlist, played offline, draws its cycle pulses on the FX canvas', async ({ page }) => {
    const { apiCalls, select } = await openWorkbench(page)
    await select.selectOption({ label: 'Клиринг (1)' })
    await page.getByRole('button', { name: 'Проиграть', exact: true }).click()

    let last = { lit: 0, live: 0, sampled: 0 }
    await expect
      .poll(async () => {
        last = await fxPointsLit(page, 'edgePulses')
        return last.lit
      }, { timeout: 8_000, intervals: [50] })
      .toBeGreaterThan(0)
    // The recorded clearing names 5 cycles of 3 lines: every line of it pulses.
    expect(last.live).toBe(15)
    // Then its amount, 121.00 (the sum over the five cycles, `cleared_amount`), as the clearing's label.
    await expect.poll(() => labelTexts(page), { timeout: 8_000, intervals: [50] }).toContain('−121.00 UAH')
    expect(apiCalls, 'the workbench makes no request to a server').toEqual([])
  })

  test('Stop during the recorded three-line payment: its later legs are never drawn (fix round, Codex P2)', async ({ page }) => {
    const { select } = await openWorkbench(page)
    await select.selectOption({ label: 'Платежи (20)' })
    const sparkKeys = () =>
      page.evaluate(() => ((window as unknown as { __geoSim?: { fxState: { sparks: Array<{ source: string; target: string }> } } })
        .__geoSim?.fxState.sparks ?? []).map((s) => `${s.source}>${s.target}`))
    await page.getByRole('button', { name: 'Проиграть', exact: true }).click()
    // The second recorded event is the route U0001 -> U0002 -> U0006 -> U0036: wait for its first leg, then stop.
    await expect.poll(sparkKeys, { timeout: 5_000, intervals: [20] }).toContain('PID_U0001_9e3779b1>PID_U0002_3c6ef362')
    await page.getByRole('button', { name: 'Стоп', exact: true }).click()
    const seen = new Set<string>()
    const until = Date.now() + 2_000
    while (Date.now() < until) {
      for (const k of await sparkKeys()) seen.add(k)
      await page.waitForTimeout(30)
    }
    expect([...seen].filter((k) => k !== 'PID_U0001_9e3779b1>PID_U0002_3c6ef362'), 'legs drawn after Stop').toEqual([])
  })

  test('"Шаг" plays one recorded event at a time and says which', async ({ page }) => {
    const { select } = await openWorkbench(page)
    await select.selectOption({ label: 'Платежи (20)' })
    const step = page.getByRole('button', { name: 'Шаг', exact: true })
    await step.click()
    await expect(page.getByLabel('Позиция плейлиста')).toHaveText('1 / 20')
    await expect.poll(async () => (await fxPointsLit(page, 'sparks')).live, { timeout: 4_000, intervals: [50] }).toBeGreaterThan(0)
    await step.click()
    await expect(page.getByLabel('Позиция плейлиста')).toHaveText('2 / 20')
  })
})
