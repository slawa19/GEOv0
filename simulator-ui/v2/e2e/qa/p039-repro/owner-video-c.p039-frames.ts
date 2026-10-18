/**
 * 039 slice C - the owner's short video (before / after), wall clock: the recorded payments of the offline workbench
 * played for 12 s, then the recorded clearing for 5 s, quality med, theme saas, 1366x768. NOT a test of anything.
 * Same config and variables as `owner-frames-c.p039-frames.ts`; writes `<dir>/<phase>/video-saas-med.webm`.
 */
import { copyFileSync, mkdirSync } from 'node:fs'
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
  await page.waitForTimeout(12_000) // a film, not a check: real time on purpose
  await page.locator('[data-testid="details-toggle"]').click()
  await page.locator('[data-testid="playlist-workbench"] button.ds-btn--secondary').click() // "Стоп"
  await choosePlaylist(page, 'clearing')
  await page.locator('[data-testid="playlist-workbench"] button.ds-btn--secondary').click() // "Проиграть"
  await closeDetails(page)
  await page.waitForTimeout(5_000)
  const video = page.video()
  await page.close()
  if (video) copyFileSync(await video.path(), join(outDir(), 'video-saas-med.webm'))
})
