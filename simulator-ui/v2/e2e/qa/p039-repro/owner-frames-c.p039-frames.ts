/**
 * 039 slice C (motion) - the owner's "before / after" frames and a short video, taken on the offline effects workbench
 * (042 T4205: "Подробнее" -> "Источник" -> "Снимок без сервера", the recorded payments and the recorded clearing of UAH,
 * no server). NOT a gate and NOT a test of anything: it writes pictures, and asserts only what makes a picture mean what
 * its name says. Debug-only config `playwright.p039-frames.config.ts` (1366x768, DPR 1, Chromium without a GPU, Vite
 * dev server out of test mode). The SAME file takes "before" (on the base) and "after" (on the slice).
 *
 *   P039_FRAMES_DIR=<abs dir> P039_FRAMES_PHASE=before|after PW_E2E_PORT=<free port> \
 *     npx playwright test --config=e2e/qa/p039-repro/playwright.p039-frames.config.ts "owner-frames-c"
 *
 * Frames (on the paused clock, so "before" and "after" are taken at the same moments), per theme (`saas`, `hud`) and
 * quality (`low` - the machine without a GPU; `med` - the product's default where there is one):
 *   <theme>-<quality>-1-scene            the scene at rest (the node glow is part of slice C: only active nodes glow)
 *   <theme>-<quality>-2-beam-leg1        the 3-line payment of the recording, in the middle of its first line
 *   <theme>-<quality>-3-beam-leg2        ... in the middle of its second line (an intermediary was just passed)
 *   <theme>-<quality>-4-beam-arrived     ... just after the beam reached the receiver (the fade-out)
 *   <theme>-<quality>-5-clearing-loop    the recorded clearing 600 ms after it came (the spark going round, after; before: the pulses)
 *   <theme>-<quality>-6-clearing-after   ... 1800 ms after it came (the lines pulse)
 *   <theme>-<quality>-7-amount-at-receiver-marked  600 ms after the 3-line payment arrived: its amount at the
 *                                        receiver, every amount label on screen outlined in red (the outline is
 *                                        the frame's, not the product's)
 *   <theme>-<quality>-8-amount-at-clearing-marked  2000 ms after the clearing came: its amount, outlined the same way
 * The video is `owner-video-c.p039-frames.ts` (a file of its own: `video` is a worker option).
 *
 * LIMITS: the frames are of a software-only browser; a GPU machine composites differently. Nothing here judges.
 * Until 2026-10-18 the recordings had no `amount` / `cleared_amount` and no amount label appeared on any frame; they
 * have them now (spec 039 Changelog), and a label's CSS fade is set to its JS age before every shot (`syncCssToClock`).
 *
 * 2026-10-18, 039 T3935: the recordings are re-recorded from a live run and each is played on its OWN start snapshot
 * (the workbench shows it when a playback begins and returns to the offline snapshot when it ends), so the scene of
 * these frames is the recording's graph, not `UAH/snapshot.json`. The receiver of the 3-line payment and the amounts
 * are read from the recording (they were literals of the old one: `PID_U0036_`, `+33.30`, `121.00`); a Step that
 * begins a playback loads that snapshot first (a real fetch, not on the page clock), so the script waits for the
 * workbench's position before it moves the clock.
 */
import { mkdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page } from '@playwright/test'

import { syncCssToClock } from '../stand.js'
import { FRAME_MS, choosePlaylist, openWorkbench } from './workbench.js'

const EVENTS = fileURLToPath(new URL('../../../public/simulator-fixtures/v1/UAH/events/', import.meta.url))
const TX = JSON.parse(readFileSync(`${EVENTS}demo-tx.json`, 'utf8')) as Array<{ to: string; amount: string; edges: unknown[] }>
const CLEARING = JSON.parse(readFileSync(`${EVENTS}demo-clearing.json`, 'utf8')) as Array<{ cleared_amount: string }>
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('039 C frames: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && PHASE !== 'after') throw new Error('039 C frames: P039_FRAMES_PHASE must be "before" or "after"')
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

async function closeDetails(page: Page) {
  await page.locator('[data-testid="details-toggle"]').click()
  await expect(page.locator('[data-testid="playlist-workbench"]')).toBeHidden()
}

const shot = async (page: Page, name: string) => {
  await syncCssToClock(page)
  await page.screenshot({ path: join(outDir(), `${name}.png`) })
}
/** The same shot with every amount label outlined, to show where the amount stands; the outline is removed after. */
const markedShot = async (page: Page, name: string, expectText: RegExp) => {
  const texts = (await syncCssToClock(page)).filter((l) => l.opacity > 0.05).map((l) => l.text)
  expect(texts.some((t) => expectText.test(t)), `${name}: a visible amount label ${expectText} (seen: ${JSON.stringify(texts)})`).toBe(true)
  const style = await page.addStyleTag({ content: '.floating-label-inner { outline: 2px solid #e5484d !important; outline-offset: 3px; }' })
  await shot(page, name)
  await style.evaluate((el) => el.remove())
}
/** "Шаг", then wait until the workbench says the event `position` was delivered (its scene may load first). */
const step = async (page: Page, position: string) => {
  await page.locator('[data-testid="playlist-workbench"] button.ds-btn--ghost').click()
  await expect(page.locator('[data-testid="playlist-workbench"] .ds-mono')).toHaveText(position)
}

for (const theme of ['saas', 'hud'] as const) {
  for (const quality of ['low', 'med'] as const) {
    test(`039 C frames - ${theme}, quality ${quality}: scene, a 3-line payment (two legs and the arrival), the clearing`, async ({ page }) => {
      test.setTimeout(300_000)
      await openWorkbench(page, { theme, quality })
      const p = `${theme}-${quality}`
      // The recorded payments: 1st has one line, 2nd the 3-line route. Step the first and let it end.
      expect(TX[1]!.edges, 'the second recorded payment has three lines').toHaveLength(3)
      await step(page, `1 / ${TX.length}`)
      await page.clock.runFor(4000)
      await closeDetails(page)
      await page.clock.runFor(FRAME_MS * 2)
      await shot(page, `${p}-1-scene`)
      // The second recorded payment, the 3-line route. Open details only to press "Шаг", close again.
      await page.locator('[data-testid="details-toggle"]').click()
      await step(page, `2 / ${TX.length}`)
      await closeDetails(page)
      const legs = async () => page.evaluate(() => (window as unknown as { __geoSim: { fxState: { sparks: Array<{ target: string; startedAtMs: number; ttlMs: number; moveMs?: number }> } } })
        .__geoSim.fxState.sparks.map((s) => ({ target: s.target, startedAtMs: s.startedAtMs, moveMs: s.moveMs ?? s.ttlMs })))
      const first = (await legs())[0]!
      await page.clock.runFor(Math.round(first.moveMs / 2))
      await shot(page, `${p}-2-beam-leg1`)
      await page.clock.runFor(Math.round(first.moveMs / 2) + 16)
      const second = (await legs()).find((l) => l.startedAtMs > first.startedAtMs)
      if (second) {
        await page.clock.runFor(Math.round(second.moveMs / 2))
        await shot(page, `${p}-3-beam-leg2`)
        await page.clock.runFor(Math.round(second.moveMs / 2) + 16)
      }
      // Wait for the last leg (the one into the receiver) and step to just after its arrival. Corrected
      // 2026-10-18: this waited for three sparks alive at once, which the slice's beam (one leg at a time) never has,
      // so the wait ran out and the "arrived" frame of `after-no-amounts/` and earlier was taken about 2.5 s late.
      let last: { startedAtMs: number; moveMs: number } | undefined
      for (let i = 0; i < 400 && !last; i += 1) {
        last = (await legs()).find((l) => l.target === TX[1]!.to)
        if (!last) await page.clock.runFor(16)
      }
      expect(last, 'the last leg of the 3-line payment was seen').toBeTruthy()
      if (last) {
        const now = await page.evaluate(() => performance.now())
        const arrival = last.startedAtMs + last.moveMs
        if (arrival + 120 > now) await page.clock.runFor(Math.round(arrival + 120 - now))
      }
      await shot(page, `${p}-4-beam-arrived`)
      await page.clock.runFor(600)
      await markedShot(page, `${p}-7-amount-at-receiver-marked`, new RegExp(`^\\+\\s?${escapeRe(TX[1]!.amount)}`))
      await page.clock.runFor(3400)
      // The clearing.
      await page.locator('[data-testid="details-toggle"]').click()
      await choosePlaylist(page, 'clearing')
      await step(page, '1 / 1')
      await closeDetails(page)
      await page.clock.runFor(600)
      await shot(page, `${p}-5-clearing-loop`)
      await page.clock.runFor(1200)
      await shot(page, `${p}-6-clearing-after`)
      await page.clock.runFor(200)
      await markedShot(page, `${p}-8-amount-at-clearing-marked`, new RegExp(escapeRe(CLEARING[0]!.cleared_amount)))
    })
  }
}
