/**
 * 043 S3 (external review Codex §15, P2 on `46094c4d`): the service row of the top bar with the longest inputs the
 * product shows there - the scenario `greenfield-village-100-realistic-v2` (its name equals its id, so the option reads
 * "<name> (<id>)", ~69 characters) and a stopped run whose stop summary is cut at 64 characters - stays readable: no
 * control of the row overlaps another, none leaves the viewport. At 1280x720 and 1366x768, in both themes, with the
 * Admin menu present (`menu-admin` holds where its menu opens).
 *
 * 046 wave 1 (2026-10-18): the row holds the language switch right after the theme, and runs in both languages
 * (`?lang=en`, `?lang=ru`). By the owner's narrowing of that day only the SWITCH (its label "Язык") and the stop reason
 * change with the language; the other texts of the row stay English literals until their review. The stop summary is
 * now the stop reason in words (`runStop.*`): the free-text reason below is outside the closed set, so it reads as the
 * general text with the value, cut at 64 characters as before.
 *
 * LIMITS: the controls compared are the leaves of the row (selects, buttons, inputs, badges, labels, the stop summary,
 * the Admin summary), not their containers; a control clipped by `overflow` inside its own box (an ellipsis) is not an
 * overlap. Chromium without a GPU, the stand's mock backend.
 */
import { expect, test, type Page, type Route } from '@playwright/test'

import { t } from '../../src/i18n/t'
import { boot, DETAILS_TOGGLE, RUN_ID, ringSnapshot, SCENARIO_ID } from './stand.js'
import { THEMES } from './surfaceMatrix.js'

const LONG_SCENARIO = 'greenfield-village-100-realistic-v2'
const LONG_REASON = 'operator stopped the run after the clearing refused a cycle of seven participants twice'

const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

async function longInputs(page: Page): Promise<void> {
  const stopped = {
    api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'stopped',
    sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, stop_source: 'operator', stop_reason: LONG_REASON,
  }
  await page.route(/\/simulator\/scenarios$/i, (r) => json(r, {
    api_version: 'simulator-api/1',
    // The stand's scenario id is the one stored for the returning user; its name is the longest one the product ships.
    items: [{ api_version: 'simulator-api/1', scenario_id: SCENARIO_ID, name: LONG_SCENARIO, participants_count: 6, trustlines_count: 6, equivalents: ['UAH'] },
      { api_version: 'simulator-api/1', scenario_id: LONG_SCENARIO, name: LONG_SCENARIO, participants_count: 6, trustlines_count: 6, equivalents: ['UAH'] }],
  }))
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, stopped))
  await page.route(/\/simulator\/runs$/i, (r) => (r.request().method() === 'POST' ? json(r, { run_id: RUN_ID }) : r.fallback()))
  await page.route(`**/simulator/runs/${RUN_ID}/artifacts`, (r) => json(r, { items: [] }))
  await page.route('**/simulator/admin/runs', (r) => json(r, { items: [] }))
}

type Box = { name: string; x: number; y: number; w: number; h: number }

const leaves = (page: Page) => page.evaluate(() => {
  const row = document.querySelector('[data-testid="top-details-row"]')
  if (!row) return [] as Array<{ name: string; x: number; y: number; w: number; h: number }>
  const sel = 'select, button, input, summary, .ds-badge, .ds-label, [data-testid="stop-reason"] .ds-value'
  return [...row.querySelectorAll<HTMLElement>(sel)]
    .filter((el) => !el.closest('.ds-ov-dropdown')) // the content of an open menu is a surface over the row, not a control of it
    .filter((el) => !el.parentElement?.closest(sel)) // a badge inside a summary is part of it
    .map((el) => {
      const r = el.getBoundingClientRect()
      const name = `${el.tagName.toLowerCase()}${el.getAttribute('aria-label') ? `[${el.getAttribute('aria-label')}]` : ''} "${(el.textContent ?? '').trim().slice(0, 24)}"`
      return { name, x: r.x, y: r.y, w: r.width, h: r.height }
    })
    .filter((b) => b.w > 0 && b.h > 0)
})

function problems(boxes: Box[], vw: number, vh: number): string[] {
  const out: string[] = []
  const eps = 0.5
  for (const b of boxes) {
    if (b.x < -eps || b.y < -eps || b.x + b.w > vw + eps || b.y + b.h > vh + eps) out.push(`outside the viewport: ${b.name} [${Math.round(b.x)},${Math.round(b.y)} ${Math.round(b.w)}x${Math.round(b.h)}]`)
  }
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]!, b = boxes[j]!
      const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
      const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
      if (ix > eps && iy > eps) out.push(`overlap ${Math.round(ix)}x${Math.round(iy)}: ${a.name} [${Math.round(a.x)}..${Math.round(a.x + a.w)}] / ${b.name} [${Math.round(b.x)}..${Math.round(b.x + b.w)}]`)
    }
  }
  return out
}

const LANGS = ['en', 'ru'] as const
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

for (const lang of LANGS) {
for (const vp of [{ width: 1280, height: 720 }, { width: 1366, height: 768 }]) {
  for (const theme of THEMES) {
    // The English titles are the 043 S3 ones, unchanged; the Russian run carries its language in the title.
    test(`043 S3 [${theme}]${lang === 'ru' ? ' [ru]' : ''} ${vp.width}x${vp.height}: the service row with the longest scenario and stop summary - no control overlaps another or leaves the screen`, async ({ page }, info) => {
      await page.setViewportSize(vp)
      await boot(page, { snapshot: ringSnapshot(), clock: false, sseShim: false, awaitLayout: false, query: `&theme=${theme}&lang=${lang}`, beforeLoad: longInputs })
      await expect(page.locator('html')).toHaveAttribute('lang', lang)
      await page.locator(DETAILS_TOGGLE).click()
      const scenario = page.locator('select[aria-label="Scenario"]')
      // Anti-vacuum: the long inputs are on screen - the option and the cut stop summary - and the Admin summary is there.
      await expect(scenario.locator('option:checked')).toHaveText(`${LONG_SCENARIO} (${SCENARIO_ID})`)
      const generalText = t('runStop.unknown', { code: LONG_REASON }, lang)
      await expect(page.locator('[data-testid="stop-reason"] .ds-value')).toHaveText(new RegExp(`^${escapeRe(generalText.slice(0, 40))}.{10,}`))
      await expect(page.locator('summary[aria-label="Admin"]')).toBeVisible()
      // 046 wave 1: the language switch is a control of this row, measured with the rest.
      await expect(page.locator('[data-testid="language-switch"] .ds-label')).toHaveText(t('locale.switcherLabel', undefined, lang))
      const boxes = await leaves(page)
      info.annotations.push({ type: 'boxes', description: JSON.stringify(boxes.map((b) => ({ ...b, x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.w), h: Math.round(b.h) }))) })
      console.log(`[043 S3 top-bar-long] ${theme} ${lang} ${vp.width}x${vp.height} ${JSON.stringify(boxes.map((b) => [b.name, Math.round(b.x), Math.round(b.y), Math.round(b.w), Math.round(b.h)]))}`)
      expect(boxes.length, 'anti-vacuum: the row has its controls').toBeGreaterThan(8)
      expect(boxes.some((b) => b.name.startsWith('select[Interface language]') || b.name.startsWith('select[Язык интерфейса]')), 'anti-vacuum: the switch is among the measured controls').toBe(true)
      expect.soft(problems(boxes, vp.width, vp.height), 'controls of the service row overlap or leave the viewport').toEqual([])
      // Nothing of the scenario's name is lost when the select is narrower than it: the full option text is its title.
      await expect.soft(scenario).toHaveAttribute('title', `${LONG_SCENARIO} (${SCENARIO_ID})`)
      await page.screenshot({ path: info.outputPath(`top-bar-long-${theme}-${lang}-${vp.width}x${vp.height}.png`) })
    })
  }
}
}
