/**
 * 043 S2 (one screen, autostart, pause on a manual action) - the owner's "before / after" frames (spec 043, Verification
 * plan, point 5: S2 merges after the owner has looked). Debug-only; writes pictures and asserts only what makes a picture
 * mean what its name says (the theme on screen). Same machinery as the 043 S1 frames (config
 * `playwright.p039-frames.config.ts`, `P039_FRAMES_DIR`, `P039_FRAMES_PHASE`); run it alone with `--grep "043 S2"`.
 *
 * The SAME file takes "before" (on the base) and "after" (on the slice): it was committed before any product change.
 * The backend is the stand's, with a run that has a state (`mockLiveRun`: a create answers a RUNNING run, `/pause` and
 * `/resume` move it); a payment of the run is a frame the script writes, as on the whole stand. Storage is clean
 * (a first visit), 1366x768, DPR 1, no GPU, the 100-node scene, both themes (`?theme=`):
 *   <theme>-1-open             `/` - the address a person opens, nothing in storage; a payment frame is written if the
 *                              page listens to a run (on the base `/` is the offline snapshot and listens to nothing);
 *   <theme>-2-open-real        `/?mode=real` - the old Auto-Run address, nothing in storage;
 *   <theme>-3-manual-action    `/?mode=real&ui=interact` (the old Interact address), then a click on "Send Payment";
 *   <theme>-4-flow-resumed     the same page after a click on "Поток" when the product has it (else the same frame).
 * The last test lays `before` and `after` side by side into `compare/`.
 *
 * LIMITS: the backend is the stand's mock, not a seeded server; the layout is the product's force layout; the frames
 * are of the stand's browser; nothing here judges.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import {
  OPEN_ROUTE, fixtureSnapshot100, installRafCounter, installSseShim, mockBackend, mockLiveRun, sseFrame, txUpdated, waitLoopAsleep,
} from '../stand.js'

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''
const THEMES = ['saas', 'hud'] as const

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('043 frames: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && PHASE !== 'after') throw new Error('043 frames: P039_FRAMES_PHASE must be "before" or "after"')
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

/** A first visit at `path`: clean storage, the stand's backend with a live run, the scene on screen (or the 20 s are up). */
async function openFirstVisit(page: Page, path: string, theme: string): Promise<void> {
  await page.setViewportSize({ width: 1366, height: 768 })
  await mockBackend(page, fixtureSnapshot100())
  await mockLiveRun(page)
  await installSseShim(page)
  await installRafCounter(page)
  await page.addInitScript(() => { try { localStorage.clear() } catch { /* ignore */ } })
  await page.goto(`${path}${path.includes('?') ? '&' : '?'}theme=${theme}`)
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.root'), 'the theme of the frame is the theme on screen').toHaveAttribute('data-theme', theme)
  await waitLoopAsleep(page)
}

/** Writes one payment along a route of the scene when the page listens to a run; returns whether it did. */
async function payIfListening(page: Page): Promise<boolean> {
  const listening = await page.evaluate(() => ((window as unknown as { __qaSse?: { state: { controllers: unknown[] } } }).__qaSse?.state.controllers.length ?? 0) > 0)
  if (!listening) return false
  const ids = await page.evaluate(() => ((window as unknown as { __geoSim?: { layoutNodes?: Array<{ id: string }> } }).__geoSim?.layoutNodes ?? []).map((n) => n.id))
  const route = ids.length >= OPEN_ROUTE.length ? ids.slice(0, 3) : [...OPEN_ROUTE].slice(0, 3)
  const { frame } = sseFrame(txUpdated(route, '10.00', { ttl_ms: 4000 }))
  await page.evaluate((f) => (window as unknown as { __qaSse: { push: (t: string) => Promise<number> } }).__qaSse.push(f), frame)
  await expect.poll(() => page.evaluate(() => (window as unknown as { __geoSim?: { fxState?: { sparks?: unknown[] } } }).__geoSim?.fxState?.sparks?.length ?? 0), { timeout: 5_000 }).toBeGreaterThan(0)
  return true
}

for (const theme of THEMES) {
  test(`043 S2 frames [${theme}] - open, open real, a manual action, the flow back`, async ({ page }) => {
    const dir = outDir()

    await openFirstVisit(page, '/', theme)
    await payIfListening(page)
    await page.screenshot({ path: join(dir, `${theme}-1-open.png`) })

    await page.goto('about:blank')
    await openFirstVisit(page, '/?mode=real', theme)
    await page.screenshot({ path: join(dir, `${theme}-2-open-real.png`) })

    await page.goto('about:blank')
    await openFirstVisit(page, '/?mode=real&ui=interact', theme)
    const send = page.locator('[data-testid="actionbar-payment"]')
    await expect(send, 'the action bar is on screen').toBeVisible({ timeout: 20_000 })
    await send.click()
    await expect(page.locator('[data-testid="manual-payment-panel"]')).toBeVisible()
    await expect(page.locator('[data-testid="network-state"]'), 'the state the frame is named for').toContainText(/на паузе|живёт/)
    await page.screenshot({ path: join(dir, `${theme}-3-manual-action.png`) })

    const flow = page.locator('[data-testid="flow-toggle"]')
    if ((await flow.count()) > 0) {
      await flow.click()
      await expect(page.locator('[data-testid="network-state"]')).toContainText('живёт')
    }
    await page.screenshot({ path: join(dir, `${theme}-4-flow-resumed.png`) })
  })
}

test('043 S2 frames - before | after, side by side (only when both phases exist)', async ({ page }) => {
  if (!isAbsolute(DIR)) throw new Error('043 frames: P039_FRAMES_DIR must be an absolute directory')
  const before = join(DIR, 'before')
  const after = join(DIR, 'after')
  test.skip(!existsSync(before) || !existsSync(after), 'one of the phases has no frames yet')
  const out = join(DIR, 'compare')
  mkdirSync(out, { recursive: true })
  const names = readdirSync(before).filter((n) => n.endsWith('.png') && existsSync(join(after, n)))
  expect(names.length, 'frames present in both phases').toBeGreaterThan(0)
  const uri = (p: string) => `data:image/png;base64,${readFileSync(p).toString('base64')}`
  for (const name of names) {
    await page.setContent(`<body style="margin:0;background:#222;font:14px sans-serif;color:#eee">
      <div id="c" style="display:inline-flex;gap:8px;padding:8px;align-items:flex-start">
        <figure style="margin:0"><figcaption>before: ${name}</figcaption><img src="${uri(join(before, name))}"></figure>
        <figure style="margin:0"><figcaption>after: ${name}</figcaption><img src="${uri(join(after, name))}"></figure>
      </div></body>`)
    await page.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))
    const size = await page.locator('#c').evaluate((el) => ({ width: Math.ceil(el.getBoundingClientRect().width), height: Math.ceil(el.getBoundingClientRect().height) }))
    await page.setViewportSize({ width: Math.max(320, size.width), height: Math.max(200, size.height) })
    await page.locator('#c').screenshot({ path: join(out, name) })
  }
})
