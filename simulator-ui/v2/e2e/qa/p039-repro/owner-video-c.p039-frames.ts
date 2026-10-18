/**
 * 039 slice C - the owner's short video (before / after), wall clock: the recorded payments of the offline workbench
 * played until ten legs were drawn, then the recorded clearing until its pulses faded, quality med, theme saas, 1366x768. NOT a test of anything.
 * Same config and variables as `owner-frames-c.p039-frames.ts`; writes `<dir>/<phase>/video-saas-med.webm`.
 */
import { mkdirSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { choosePlaylist, openWorkbench } from './workbench.js'

test.use({ video: { mode: 'on', size: { width: 1366, height: 768 } } })

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('039 C video: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && PHASE !== 'after') throw new Error('039 C video: P039_FRAMES_PHASE must be "before" or "after"')
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

async function closeDetails(page: Page) {
  await page.locator('[data-testid="details-toggle"]').click()
  await expect(page.locator('[data-testid="playlist-workbench"]')).toBeHidden()
}

test('039 C video - payments for 12 s, then the clearing, quality med, saas (wall clock)', async ({ page }) => {
  test.setTimeout(180_000)
  await openWorkbench(page, { theme: 'saas', quality: 'med', clock: false })
  await page.locator('[data-testid="playlist-workbench"] button.ds-btn--secondary').click() // "Проиграть"
  await closeDetails(page)
  // A film in real time: until ten legs of the recorded payments have been drawn (a condition, not a sleep; the stand
  // forbids waits by time). The legs are counted by an observer of the FX state on the page's own frames.
  await page.evaluate(() => {
    const seen = new Set<string>()
    const w = window as unknown as { __geoSim: { fxState: { sparks: Array<{ key: string }> } }; __p039cLegs: () => number }
    w.__p039cLegs = () => seen.size
    const tick = () => { for (const s of w.__geoSim.fxState.sparks) seen.add(s.key); requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
  })
  await expect.poll(() => page.evaluate(() => (window as unknown as { __p039cLegs: () => number }).__p039cLegs()), { timeout: 90_000, intervals: [250] }).toBeGreaterThanOrEqual(10)
  await page.locator('[data-testid="details-toggle"]').click()
  await page.locator('[data-testid="playlist-workbench"] button.ds-btn--secondary').click() // "Стоп"
  await choosePlaylist(page, 'clearing')
  await page.locator('[data-testid="playlist-workbench"] button.ds-btn--secondary').click() // "Проиграть"
  await closeDetails(page)
  // ... and the clearing: until its lines have pulsed and faded (no edge pulse left in the FX state).
  const pulses = () => page.evaluate(() => (window as unknown as { __geoSim: { fxState: { edgePulses: unknown[] } } }).__geoSim.fxState.edgePulses.length)
  await expect.poll(pulses, { timeout: 20_000, intervals: [200] }).toBeGreaterThan(0)
  await expect.poll(pulses, { timeout: 20_000, intervals: [200] }).toBe(0)
  const video = page.video()
  await page.close()
  // `saveAs` waits until the recording is finished; a copy of `path()` right after close took a partial file.
  if (video) await video.saveAs(join(outDir(), 'video-saas-med.webm'))
})
