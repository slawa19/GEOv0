/**
 * Programme 046, wave 1 (T4602), narrowed by the owner's word 2026-10-18 - the five things of the wave, in the browser,
 * at 1366x768 (the stand's TopBar test also takes 1280x720), in both product themes and both languages, on the mocked
 * backend of the 037 specs (`helpers/p037Mock.ts`):
 *
 *   1. the language switch behind "Подробнее", right after the theme: choosing a language rewrites `<html lang>` and
 *      the page's texts without a reload, stores the choice, and the stored choice is what the next load shows;
 *   2. BACKLOG 043-3: under a live run the scenario select is disabled and says why (title, accessible description - no
 *      drawn line); the service row with the switch has no control over another or off the screen;
 *   3. the 409 of the global limit of runs: the error line in the interface language, with the server's number of
 *      places, and "Try again", which starts the run;
 *   4. a stopped run says why, in the interface language (a known reason, and an unknown one with its value);
 *   5. the shell of an action window is named in the interface language (wave 2 finding 9).
 *   6. (044 stage 2 merged) a trust line create the server refuses with 409 `CONFLICT` and `details.reason`
 *      `participant_suspended` - the body `test_p044_s2_trustline_create_conflict_kinds.py` pins - reads as that reason.
 *
 * `P046_FRAMES=1` also writes the owner's frames (`.local-run/owner-review/2026-10-18-046-w1/`); the default run writes none.
 *
 * What this does NOT see: the other texts of the top bar (English literals by the owner's decision: under review), the
 * real server's answers (the mock answers as `run_lifecycle.py` does), phone viewports.
 */
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page, type Route } from '@playwright/test'

import { t, tn } from '../src/i18n/t'
import { mockApp, ready, RUN_ID, SCENARIO_ID } from './helpers/p037Mock.js'

const THEMES = ['saas', 'hud'] as const
const LANGS = ['en', 'ru'] as const
type Theme = (typeof THEMES)[number]
type Lang = (typeof LANGS)[number]

const FRAMES = process.env.P046_FRAMES === '1'
const FRAMES_DIR = fileURLToPath(new URL('../../../.local-run/owner-review/2026-10-18-046-w1/', import.meta.url))
const DETAILS = '[data-testid="details-toggle"]'

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const runBody = (state: string, extra: Record<string, unknown> = {}) => ({
  api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state,
  sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, ...extra,
})

async function open(page: Page, theme: Theme, lang: Lang | null, before?: () => Promise<void>): Promise<void> {
  await mockApp(page, { paymentRealBodies: [] })
  if (before) await before()
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}${lang ? `&lang=${lang}` : ''}`)
  await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
  if (lang) await expect(page.locator('html')).toHaveAttribute('lang', lang)
}

async function frame(page: Page, name: string): Promise<void> {
  if (!FRAMES) return
  mkdirSync(FRAMES_DIR, { recursive: true })
  await page.screenshot({ path: `${FRAMES_DIR}${name}.png` })
}

type Box = { name: string; x: number; y: number; w: number; h: number }

/** The leaves of the service row (as `top-bar-long.qa.ts` reads them), the switch and the 043-3 line among them. */
const serviceRow = (page: Page) => page.evaluate(() => {
  const row = document.querySelector('[data-testid="top-details-row"]')
  if (!row) return [] as Box[]
  const sel = 'select, button, input, summary, .ds-badge, .ds-label, [data-testid="stop-reason"] .ds-value'
  return [...row.querySelectorAll<HTMLElement>(sel)]
    .filter((el) => !el.closest('.ds-ov-dropdown'))
    .filter((el) => !el.parentElement?.closest(sel))
    .map((el) => {
      const r = el.getBoundingClientRect()
      return { name: `${el.tagName.toLowerCase()} "${(el.textContent ?? '').trim().slice(0, 24)}"`, x: r.x, y: r.y, w: r.width, h: r.height }
    })
    .filter((b) => b.w > 0 && b.h > 0)
})

function problems(boxes: Box[], vw: number, vh: number): string[] {
  const out: string[] = []
  for (const b of boxes) if (b.x < -0.5 || b.y < -0.5 || b.x + b.w > vw + 0.5 || b.y + b.h > vh + 0.5) out.push(`outside: ${b.name}`)
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]!, b = boxes[j]!
      const ix = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)
      const iy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y)
      if (ix > 0.5 && iy > 0.5) out.push(`overlap: ${a.name} / ${b.name}`)
    }
  }
  return out
}

for (const theme of THEMES) {
  test(`P046W1 (1) [${theme}] the language switch: in the service row after the theme; no reload; stored; the stored choice is the next load`, async ({ page }) => {
    await open(page, theme, null)
    await expect(page.locator('html'), 'Chromium en-US, nothing stored: English').toHaveAttribute('lang', 'en')
    await expect(page.locator('[data-testid="language-switch"]'), 'not on the first view').toHaveCount(0)
    await page.locator(DETAILS).click()
    const sw = page.locator('.tb-mode-row > .tb-theme-row + [data-testid="language-switch"]')
    await expect(sw, 'right after the theme row').toBeVisible()
    await frame(page, `${theme}-1-switch-en`)
    await page.evaluate(() => { (window as unknown as { __p046SameDocument?: number }).__p046SameDocument = 1 })
    const href = page.url()

    await sw.locator('select').selectOption('ru')
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru')
    await expect(sw.locator('.ds-label')).toHaveText('Язык')
    await page.locator('[data-testid="actionbar-payment"]').click()
    await expect(page.locator('.ws-shell[aria-label]').first(), 'a window opened after the switch speaks the new language').toHaveAttribute('aria-label', 'Окно платежа')
    expect(await page.evaluate(() => (window as unknown as { __p046SameDocument?: number }).__p046SameDocument), 'the same document: no reload').toBe(1)
    expect(page.url(), 'no navigation, no ?lang= written').toBe(href)
    const stored = await page.evaluate(() => localStorage.getItem('geo.sim.v2.lang'))
    expect(stored).toBe('ru')
    await frame(page, `${theme}-1-switch-ru`)

    // The mock's init script clears the storage on every load (`mockApp`); the page's own stored value is put back
    // before the app starts, so the next load reads what the switch wrote.
    await page.addInitScript((v) => { try { localStorage.setItem('geo.sim.v2.lang', v) } catch { /* ignore */ } }, stored!)
    await page.reload()
    await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
    await expect(page.locator('html'), 'the stored choice wins over the browser').toHaveAttribute('lang', 'ru')
    // A link wins over the stored choice and is not written into the storage.
    await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}&lang=en`)
    await expect(page.locator('html')).toHaveAttribute('lang', 'en')
    expect(await page.evaluate(() => localStorage.getItem('geo.sim.v2.lang'))).toBe('ru')
  })

  for (const lang of LANGS) {
    test(`P046W1 (2) [${theme}] [${lang}] 043-3: under a live run the scenario is locked and says why; the service row holds`, async ({ page }) => {
      await open(page, theme, lang)
      await page.locator(DETAILS).click()
      const select = page.locator('[data-testid="scenario-select"]')
      await expect(select, 'the mock run is paused - live').toBeDisabled()
      const line = page.locator('[data-testid="scenario-locked"]')
      await expect(line).toHaveText(t('topBar.scenarioLocked', undefined, lang))
      await expect(select).toHaveAttribute('aria-describedby', 'tb-scenario-locked')
      await expect(select).toHaveAttribute('title', new RegExp(`${t('topBar.scenarioLocked', undefined, lang).replace(/[.]/g, '\\.')}$`))
      const box = await line.boundingBox()
      expect(box && box.width <= 1 && box.height <= 1, 'the reason is not drawn as a line of its own (the row does not grow)').toBe(true)
      await expect(page.locator('[data-testid="language-switch"]')).toBeVisible()
      for (const vp of [{ width: 1366, height: 768 }, { width: 1280, height: 720 }]) {
        await page.setViewportSize(vp)
        await page.waitForTimeout(100)
        const boxes = await serviceRow(page)
        console.log(`P046W1 service-row ${theme} ${lang} ${vp.width}x${vp.height} ${JSON.stringify(boxes.map((b) => [b.name, Math.round(b.x), Math.round(b.y), Math.round(b.w)]))}`)
        expect(boxes.length, 'anti-vacuum: the row has its controls').toBeGreaterThan(8)
        expect(boxes.some((b) => b.name.startsWith('select "English') || b.name.startsWith('select "Русский') || b.name.includes('English')), 'the switch is measured').toBe(true)
        expect(problems(boxes, vp.width, vp.height), `${vp.width}x${vp.height}`).toEqual([])
      }
      await page.setViewportSize({ width: 1366, height: 768 })
      await frame(page, `${theme}-${lang}-2-scenario-locked`)
    })

    test(`P046W1 (3) [${theme}] [${lang}] the 409 of the limit of runs: the line names the server's number and "Try again" starts the run`, async ({ page }) => {
      let creates = 0
      await open(page, theme, lang, async () => {
        // No stored run: the opening starts one, and the server's limit refuses the first create.
        await page.addInitScript(() => { try { localStorage.removeItem('geo.sim.v2.runId') } catch { /* ignore */ } })
        await page.route(/\/simulator\/runs$/i, (r) => {
          if (r.request().method() !== 'POST') return r.fallback()
          creates += 1
          return creates === 1
            ? json(r, { error: { code: 'E008', message: 'Global active runs limit reached', details: { conflict_kind: 'global_active_limit', max_active_runs: 10, active_runs: 10 } } }, 409)
            : json(r, { run_id: RUN_ID })
        })
      })
      const line = page.locator('[data-testid="top-error"]')
      await expect(line).toContainText(tn('runStart.limitAll', 10, undefined, lang))
      const retry = line.locator('[data-testid="start-retry"]')
      await expect(retry).toHaveText(t('topBar.retry', undefined, lang))
      await expect(line.locator('.ds-label')).toHaveText(t('topBar.errorLabel', undefined, lang))
      expect(creates).toBe(1)
      await frame(page, `${theme}-${lang}-3-limit-retry`)
      // §15 Codex P2: the refusal on screen follows a language switch - its text and title - and keeps its "Try again".
      const other: Lang = lang === 'en' ? 'ru' : 'en'
      const body = line.locator('.ds-value')
      await page.locator(DETAILS).click()
      for (const l of [other, lang]) {
        await page.locator('[data-testid="language-select"]').selectOption(l)
        await expect(page.locator('html')).toHaveAttribute('lang', l)
        await expect(body).toHaveText(tn('runStart.limitAll', 10, undefined, l))
        await expect(body).toHaveAttribute('title', tn('runStart.limitAll', 10, undefined, l))
        await expect(retry).toHaveText(t('topBar.retry', undefined, l))
      }
      await page.locator(DETAILS).click()
      await retry.click()
      await expect(line, 'a place freed: the run starts, the line is gone').toHaveCount(0)
      expect(creates).toBe(2)
      await expect(page.locator('[data-testid="actionbar-payment"]')).toBeVisible()
    })

    test(`P046W1 (4) [${theme}] [${lang}] a stopped run says why: a known reason, and an unknown one with its value`, async ({ page }) => {
      let reason = 'idle_no_subscribers'
      await open(page, theme, lang, async () => {
        await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, runBody('stopped', { stop_source: 'server', stop_reason: reason })))
        await page.route('**/simulator/runs/*/start', (r) => r.fallback())
      })
      await page.locator(DETAILS).click()
      const value = page.locator('[data-testid="stop-reason"] .ds-value')
      await expect(value).toHaveText(t('runStop.idle_no_subscribers', undefined, lang))
      await expect(page.locator('[data-testid="stop-reason"] .ds-label')).toHaveText(t('topBar.stopLabel', undefined, lang))
      await expect(page.locator('[data-testid="stop-reason"]')).toHaveAttribute('title', 'server: idle_no_subscribers')
      await frame(page, `${theme}-${lang}-4-stop-reason`)
      reason = 'maintenance_window'
      await page.reload()
      await expect(page.locator('[data-ready="1"]')).toBeVisible({ timeout: 20_000 })
      await page.locator(DETAILS).click()
      await expect(value).toHaveText(t('runStop.unknown', { code: 'maintenance_window' }, lang))
    })

    test(`P046W1 (6) [${theme}] [${lang}] a trust line create refused for a suspended participant says so`, async ({ page }) => {
      let creates = 0
      await open(page, theme, lang, async () => {
        await page.route(`**/simulator/runs/${RUN_ID}/actions/trustline-create`, (r) => {
          creates += 1
          return json(r, { code: 'CONFLICT', message: 'Participant carol is not active', details: { reason: 'participant_suspended', participants: ['carol'] } }, 409)
        })
      })
      await ready(page, true)
      await page.locator('[data-testid="actionbar-trustline"]').click()
      await page.locator('#tl-from__trigger').click()
      await page.locator('#tl-from__surface [role="option"][data-option-value="alice"]').click()
      await page.locator('#tl-to__trigger').click()
      await page.locator('#tl-to__surface [role="option"][data-option-value="carol"]').click()
      await page.locator('#tl-limit').fill('20')
      await page.locator('[data-testid="trustline-create"]').click()
      await expect(page.locator('[data-testid="trustline-error"]')).toHaveText(t('actionReason.participant_suspended', undefined, lang))
      expect(creates).toBe(1)
    })

    test(`P046W1 (5) [${theme}] [${lang}] the window shell is named in the interface language`, async ({ page }) => {
      await open(page, theme, lang)
      await ready(page, true)
      const cases: Array<[string, string]> = [['actionbar-payment', 'payment.panelAria'], ['actionbar-trustline', 'trustline.panelAria'], ['actionbar-clearing', 'clearing.panelAria']]
      for (const [button, key] of cases) {
        await page.locator(`[data-testid="${button}"]`).click()
        const shell = page.locator('.ws-shell[aria-label]')
        await expect(shell).toHaveCount(1)
        await expect(shell).toHaveAttribute('aria-label', t(key as 'payment.panelAria', undefined, lang))
        await page.keyboard.press('Escape')
        await page.keyboard.press('Escape')
        await expect(shell).toHaveCount(0)
      }
    })
  }
}
