/**
 * 039 slice C - the offline effects workbench (042 T4205) as a measuring and filming bench: helpers shared by
 * `c-motion.p039-repro.ts` (measurements) and `owner-frames-c.p039-frames.ts` (the owner's frames and video). Not a
 * test file. Debug-only, like the configs that run them.
 *
 * The workbench is found by its `data-testid` and its select's options by their count "(20)" / "(1)", not by the
 * Russian labels of the buttons, which programme 046 may translate; "Шаг" is the ghost button of the row.
 */
import { expect, type Page } from '@playwright/test'

export const ALPHA_MIN = 12
export const CORRIDOR_PX = 5
export const NODE_CLEAR_PX = 46
export const FRAME_MS = 16

async function waitStill(page: Page) {
  let prev = ''
  let same = 0
  await expect.poll(async () => {
    const cur = await page.evaluate(() => JSON.stringify(((window as unknown as { __geoSim?: { layoutNodes?: Array<{ id: string; x: number; y: number }> } }).__geoSim?.layoutNodes ?? []).map((n) => [n.id, Math.round(n.x * 10), Math.round(n.y * 10)])))
    same = cur === prev && cur !== '[]' ? same + 1 : 0
    prev = cur
    return same
  }, { timeout: 60_000, intervals: [150] }).toBeGreaterThanOrEqual(3)
}

export async function openWorkbench(page: Page, o: { theme: string; quality: string; playlist?: 'payments' | 'clearing'; clock?: boolean }) {
  if (o.clock !== false) await page.clock.install({ time: new Date('2026-02-01T00:00:00Z') })
  await page.addInitScript(() => { try { localStorage.clear() } catch { /* ignore */ } })
  await page.goto(`/?mode=fixtures&theme=${o.theme}`)
  await page.waitForSelector('[data-ready="1"]', { timeout: 60_000 })
  await page.waitForFunction(() => '__geoSim' in window)
  expect(await page.evaluate(() => (window as unknown as { __geoSim: { isTestMode: boolean } }).__geoSim.isTestMode), 'not test mode').toBe(false)
  expect(await page.evaluate(() => navigator.webdriver), 'not webdriver').toBe(false)
  expect(await page.locator('.root').getAttribute('data-gpu'), 'no GPU').toBe('0')
  await waitStill(page)
  await page.locator('[data-testid="details-toggle"]').click()
  await page.getByRole('combobox', { name: 'Quality' }).selectOption(o.quality)
  const wb = page.locator('[data-testid="playlist-workbench"]')
  await expect(wb).toBeVisible()
  await choosePlaylist(page, o.playlist ?? 'payments')
  if (o.clock !== false) {
    const now = await page.evaluate(() => Date.now())
    await page.clock.pauseAt(now + 1000 - (now % FRAME_MS) + 1000)
    await page.clock.runFor(FRAME_MS * 4)
  }
  expect(await page.locator('.root').getAttribute('data-quality')).toBe(o.quality)
  expect(await page.locator('.root').getAttribute('data-theme')).toBe(o.theme)
  return { step: wb.locator('button.ds-btn--ghost') }
}

/** One frame: for every spark of the FX state, the lit pixels of the FX canvas in its edge's corridor away from its nodes. */
export const probeSparks = (page: Page) => page.evaluate(([alphaMin, corridor, nodeClear]) => {
  type G = {
    fxState: { sparks: Array<{ source: string; target: string; startedAtMs: number; ttlMs: number; moveMs?: number }> }
    layoutNodes: Array<{ id: string; x: number; y: number }>
    camera: { panX: number; panY: number; zoom: number }
  }
  const g = (window as unknown as { __geoSim: G }).__geoSim
  const c = document.querySelector<HTMLCanvasElement>('canvas.canvas-fx')!
  const ctx = c.getContext('2d')!
  const k = c.width / Math.max(1, c.clientWidth)
  const box = c.getBoundingClientRect()
  const now = performance.now()
  const cam = g.camera
  const at = new Map(g.layoutNodes.map((n) => [n.id, { x: n.x * cam.zoom + cam.panX, y: n.y * cam.zoom + cam.panY }]))
  return g.fxState.sparks.map((s) => {
    const a = at.get(s.source)
    const b = at.get(s.target)
    const alive = now - s.startedAtMs < s.ttlMs && now >= s.startedAtMs
    if (!a || !b) return { edge: `${s.source}>${s.target}`, ttlMs: s.ttlMs, moveMs: s.moveMs ?? s.ttlMs, startedAtMs: s.startedAtMs, alive, px: 0, lenPx: 0 }
    const x0 = Math.max(0, Math.floor((Math.min(a.x, b.x) - corridor - box.x) * k))
    const y0 = Math.max(0, Math.floor((Math.min(a.y, b.y) - corridor - box.y) * k))
    const x1 = Math.min(c.width, Math.ceil((Math.max(a.x, b.x) + corridor - box.x) * k))
    const y1 = Math.min(c.height, Math.ceil((Math.max(a.y, b.y) + corridor - box.y) * k))
    let px = 0
    if (x1 > x0 && y1 > y0) {
      const img = ctx.getImageData(x0, y0, x1 - x0, y1 - y0)
      const dx = b.x - a.x
      const dy = b.y - a.y
      const l2 = dx * dx + dy * dy
      for (let yy = 0; yy < img.height; yy += 1) {
        for (let xx = 0; xx < img.width; xx += 1) {
          if (img.data[(yy * img.width + xx) * 4 + 3]! < alphaMin) continue
          const x = box.x + (x0 + xx + 0.5) / k
          const y = box.y + (y0 + yy + 0.5) / k
          if (Math.hypot(x - a.x, y - a.y) <= nodeClear || Math.hypot(x - b.x, y - b.y) <= nodeClear) continue
          const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / l2))
          if (Math.hypot(x - (a.x + t * dx), y - (a.y + t * dy)) <= corridor) px += 1
        }
      }
    }
    return { edge: `${s.source}>${s.target}`, ttlMs: s.ttlMs, moveMs: s.moveMs ?? s.ttlMs, startedAtMs: s.startedAtMs, alive, px, lenPx: Math.hypot(b.x - a.x, b.y - a.y) }
  })
}, [ALPHA_MIN, CORRIDOR_PX, NODE_CLEAR_PX] as const)


/** Choose the recorded payments (20) or the recorded clearing (1) in the workbench's select. */
export async function choosePlaylist(page: Page, which: 'payments' | 'clearing') {
  const sel = page.locator('[data-testid="playlist-workbench"] select')
  const re = which === 'payments' ? /\(20\)/ : /\(1\)/
  const value = await sel.locator('option').evaluateAll((os, src) => (os as HTMLOptionElement[]).find((x) => new RegExp(src).test(x.textContent ?? ''))?.value ?? '', re.source)
  expect(value, `the ${which} playlist is offered`).not.toBe('')
  await sel.selectOption(value)
}
