/**
 * Programme 046, wave 1 in full (T4602, after 048 S1 and S2) - the first screen in ONE language, in the browser at
 * 1366x768, both product themes, both languages, a visitor (a participant JWT) and the administrator (the dev build's
 * admin token), on the mocked backend of the 037 specs (`helpers/p037Mock.ts`):
 *
 *   1. everything the page says - the browser tab's title, visible text and the `aria-label` / `title` / `placeholder` of every element, the
 *      first row, both rows of "Подробнее", the network figures, the bottom bar, the legend, "Find on graph" (open) and
 *      the onboarding hint - holds no word of the other language: under `ru` no Latin word but data, under `en` no
 *      Cyrillic. The rule is structural (AGENTS.md §9); data is read from the mock (participant names, the scenario's
 *      name and id), plus the codes the parity guard allows (`GEO`, `Esc`, the equivalent) and the language names;
 *   2. the same after a switch of the language on the open page (no reload);
 *   3. 048 item 13 (a): the onboarding names the three actions in the words of the protocol, and each action's hint says
 *      what it results in;
 *   4. the 048 S2 hand-over (P2-2): a visitor whose run the operator stopped, or whose run ended with an error, reads on the
 *      first view what to do - reload the page - and no text sends them to a Start they have not got;
 *   5. the Russian first screen fits: no control of the first row or of "Подробнее" overlaps another or leaves the window.
 *
 * `P046_FRAMES=1` writes the owner's frames (`.local-run/owner-review/2026-10-19-046-w1/`); the default run writes none.
 * What this does NOT see: the developer's `?debug=1` controls (not translated - spec 046), the inspector windows (wave 3),
 * the real server (the live suite `e2e/live/autostart.live.ts` runs as a visitor against it), phone viewports.
 */
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, test, type Page, type Route } from '@playwright/test'

import { UI_LOCALE_ENDONYMS } from '../src/i18n/locale'
import { t } from '../src/i18n/t'
import { mockApp, PARTICIPANTS, ready, RUN_ID, SCENARIO_ID } from './helpers/p037Mock.js'

const THEMES = ['saas', 'hud'] as const
const LANGS = ['en', 'ru'] as const
type Theme = (typeof THEMES)[number]
type Lang = (typeof LANGS)[number]

const FRAMES = process.env.P046_FRAMES === '1'
const FRAMES_DIR = fileURLToPath(new URL('../../../.local-run/owner-review/2026-10-19-046-w1/', import.meta.url))
const DETAILS = '[data-testid="details-toggle"]'
const JWT = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJwIn0.c2lnbmF0dXJl'
const SCENARIO_NAME = 'Greenfield-village-100' // the mock's catalogue (`helpers/p037Mock.ts`)

/** Data, the same in both languages: words of the participants' names, of the scenario's name and id; the language names. */
const DATA_WORDS = new Set([
  ...[...PARTICIPANTS.map((p) => p.name), SCENARIO_NAME, SCENARIO_ID].flatMap((s) => s.split(/[^A-Za-z]+/)).filter(Boolean),
  ...Object.values(UI_LOCALE_ENDONYMS),
])
/** Under `ru`, also the words the parity guard allows (`GEO`, `Esc`) and the code of the equivalent. */
const RU_ALLOWED = new Set([...DATA_WORDS, 'GEO', 'Esc', 'UAH'])

const json = (route: Route, body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
const runBody = (extra: Record<string, unknown> = {}) => ({
  api_version: 'simulator-api/1', run_id: RUN_ID, scenario_id: SCENARIO_ID, mode: 'real', state: 'running',
  sim_time_ms: 0, intensity_percent: 30, ops_sec: 0, queue_depth: 0, ...extra,
})

type Opened = { setRun: (extra: Record<string, unknown>) => void; endStreams: () => Promise<void> }

/** The mocked app with a running run whose event stream stays open until `endStreams()`; the onboarding hint is shown. */
async function open(page: Page, o: { theme: Theme; lang: Lang | null; visitor: boolean }): Promise<Opened> {
  await mockApp(page, { paymentRealBodies: [] })
  if (o.visitor) await page.addInitScript((tk) => { try { localStorage.setItem('geo.sim.v2.accessToken', tk) } catch { /* ignore */ } }, JWT)
  let run = runBody()
  const held: Route[] = []
  await page.route(`**/simulator/runs/${RUN_ID}`, (r) => json(r, run))
  await page.route(/\/simulator\/runs\/[^/]+\/events/i, (r) => { held.push(r) })
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&e2eReal=1&theme=${o.theme}${o.lang ? `&lang=${o.lang}` : ''}`)
  await ready(page, true)
  if (o.lang) await expect(page.locator('html')).toHaveAttribute('lang', o.lang)
  return {
    setRun: (extra) => { run = runBody(extra) },
    endStreams: async () => {
      for (const r of held.splice(0)) {
        await r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' }, body: ':bye\n\n' }).catch(() => undefined)
      }
    },
  }
}

/** Open "Подробнее" and "Find on graph", so that every first-screen surface is on the page. */
async function openEverything(page: Page): Promise<void> {
  await page.locator(DETAILS).click()
  await expect(page.locator(DETAILS)).toHaveAttribute('aria-expanded', 'true')
  await page.getByTestId('graph-navigator-summary').click()
  await expect(page.locator('#graph-navigator-node')).toBeVisible()
}

/** Everything the page says: its text nodes one by one, and the names, titles and placeholders of its elements. */
function said(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    // The browser tab's title is said too (046 closing review, finding 1): it lives in <head>, outside the body walk.
    const out: string[] = [document.title]
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const parent = n.parentElement
      if (parent && (parent.closest('script, style'))) continue
      if (n.nodeValue && n.nodeValue.trim()) out.push(n.nodeValue)
    }
    for (const el of Array.from(document.body.querySelectorAll('*'))) {
      for (const a of ['aria-label', 'title', 'placeholder']) {
        const v = el.getAttribute(a)
        if (v) out.push(v)
      }
    }
    return out
  })
}

/** The words of the other language than `lang` on the page (not data). */
async function foreignWords(page: Page, lang: Lang): Promise<string[]> {
  const words = (await said(page)).flatMap((s) => (lang === 'ru' ? s.match(/[A-Za-z_]+/g) : s.match(/[Ѐ-ӿ]+/g)) ?? [])
  return [...new Set(words.filter((w) => !(lang === 'ru' ? RU_ALLOWED : DATA_WORDS).has(w)))]
}

async function frame(page: Page, name: string): Promise<void> {
  if (!FRAMES) return
  mkdirSync(FRAMES_DIR, { recursive: true })
  await page.screenshot({ path: `${FRAMES_DIR}${name}.png` })
}

type Box = { name: string; x: number; y: number; w: number; h: number }

/** The leaves of the first row and of both rows of "Подробнее" (as `top-bar-long.qa.ts` reads them). */
const firstScreenBoxes = (page: Page) => page.evaluate(() => {
  const roots = ['.tb-main-row', '[data-testid="top-details-row"]', '[data-testid="bottom-details-row"]', '.bb-main-row']
  const sel = 'select, button, input, summary, .ds-badge, .ds-label, .ds-value, [data-testid="stop-reason"] .ds-value'
  const out: { name: string; x: number; y: number; w: number; h: number }[] = []
  for (const root of roots) {
    const r = document.querySelector(root)
    if (!r) continue
    for (const el of r.querySelectorAll<HTMLElement>(sel)) {
      if (el.parentElement?.closest(sel)) continue
      if (el.closest('.graph-navigator details[open] .graph-navigator__controls')) continue // the open panel floats by design
      const b = el.getBoundingClientRect()
      if (b.width > 0 && b.height > 0) out.push({ name: `${root} ${el.tagName.toLowerCase()} "${(el.textContent ?? '').trim().slice(0, 24)}"`, x: b.x, y: b.y, w: b.width, h: b.height })
    }
  }
  return out
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
  for (const lang of LANGS) {
    for (const visitor of [true, false]) {
      const who = visitor ? 'visitor' : 'admin'
      test(`P046W1F (1) [${theme}] [${lang}] [${who}] the first screen says no word of the other language`, async ({ page }) => {
        await open(page, { theme, lang, visitor })
        await expect(page.getByTestId('onboarding-hint'), 'the onboarding hint is on the page').toBeVisible()
        await openEverything(page)
        // Counter-check: what the rule judges is there - the administrator's row only with the token (048 S2).
        await expect(page.getByTestId('own-run-row')).toHaveCount(visitor ? 0 : 1)
        for (const id of ['network-state', 'flow-toggle', 'action-bar', 'top-details-row', 'system-metrics', 'bottom-details-row', 'legend', 'graph-navigator']) {
          await expect(page.getByTestId(id), id).toBeVisible()
        }
        expect(await foreignWords(page, lang)).toEqual([])
        if (lang === 'ru') expect(problems(await firstScreenBoxes(page), 1366, 768), 'the Russian first screen fits').toEqual([])
        await page.getByTestId('graph-navigator-summary').click() // close the navigator for the frame
        await frame(page, `${theme}-${lang}-${who}-details`)
      })
    }
  }

  test(`P046W1F (2) [${theme}] a switch en -> ru on the open page: the whole first screen follows, no reload`, async ({ page }) => {
    await open(page, { theme, lang: null, visitor: false })
    await expect(page.locator('html'), 'Chromium en-US: English').toHaveAttribute('lang', 'en')
    await openEverything(page)
    expect(await foreignWords(page, 'en')).toEqual([])
    await page.evaluate(() => { (window as unknown as { __p046Same?: number }).__p046Same = 1 })
    await page.getByTestId('language-select').selectOption('ru')
    await expect(page.locator('html')).toHaveAttribute('lang', 'ru')
    await expect(page.getByTestId('details-toggle')).toContainText(t('topBar.details', undefined, 'ru'))
    expect(await foreignWords(page, 'ru')).toEqual([])
    expect(await page.evaluate(() => (window as unknown as { __p046Same?: number }).__p046Same), 'the same document').toBe(1)
  })

  test(`P046W1F (3) [${theme}] 048 item 13 (a): the onboarding names the three actions in protocol words; the hints say the result`, async ({ page }) => {
    for (const lang of LANGS) {
      await open(page, { theme, lang, visitor: true })
      const hint = page.getByTestId('onboarding-hint')
      await expect(hint.getByTestId('onboarding-protocol')).toHaveText(t('onboarding.protocol', undefined, lang))
      await expect(hint).toContainText(t('onboarding.explore', undefined, lang))
      // Two sentences, one space between them (the frame of the first run read "по кругу.Выберите").
      expect((await hint.locator('.sar-onboarding-hint__text').innerText()).replace(/\s+/g, ' ').trim())
        .toBe(`${t('onboarding.protocol', undefined, lang)} ${t('onboarding.explore', undefined, lang)}`)
      for (const [id, key] of [['actionbar-payment', 'actions.paymentHint'], ['actionbar-trustline', 'actions.trustlineHint'], ['actionbar-clearing', 'actions.clearingHint']] as const) {
        await expect(page.getByTestId(id), `${lang} ${id}`).toHaveAttribute('title', t(key, undefined, lang))
      }
      await frame(page, `${theme}-${lang}-onboarding`)
    }
  })

  test(`P046W1F (4) [${theme}] a visitor whose run was stopped (stop-all, an API stop without a reason) or ended with an error reads "reload the page"`, async ({ page }) => {
    for (const lang of LANGS) {
      for (const [ending, extra, key] of [
        ['operator', { state: 'stopped', stop_source: 'admin', stop_reason: 'admin_stop_all' }, 'runLine.stopped'],
        // The runbook's one-run stop through the API sends no reason (internal adversarial pass, 2026-10-19).
        ['api-no-reason', { state: 'stopped', stop_source: null, stop_reason: null }, 'runLine.stopped'],
        ['error', { state: 'error', last_error: { code: 'REAL_MODE_TICK_FAILED', message: 'tick failed', at: '2026-10-19T00:00:00Z' } }, 'runLine.errorStopped'],
      ] as const) {
        const app = await open(page, { theme, lang, visitor: true })
        await expect(page.getByTestId('network-state')).toHaveAttribute('data-run-state', 'running')
        app.setRun(extra)
        await app.endStreams()
        await expect(page.getByTestId('network-state')).toHaveAttribute('data-run-state', ending === 'error' ? 'error' : 'stopped')
        // On the first view ("Подробнее" closed): the line says what to do.
        await expect(page.locator(DETAILS)).toHaveAttribute('aria-expanded', 'false')
        await expect(page.getByTestId('top-error'), `${lang} ${ending}`).toContainText(t(key, { code: 'REAL_MODE_TICK_FAILED' }, lang))
        // 049 S2 (decision 7): the code is not drawn in brackets - it is the title (and the console).
        await expect(page.getByTestId('top-error'), `${lang} ${ending}`).not.toContainText('REAL_MODE_TICK_FAILED')
        for (const id of ['actionbar-payment', 'actionbar-trustline', 'actionbar-clearing']) {
          await expect(page.getByTestId(id)).toBeDisabled()
          await expect(page.getByTestId(id), 'the reason names the reload').toHaveAttribute('title', t('actions.noRun', undefined, lang))
        }
        // No text of the first screen promises a Start the visitor has not got.
        const texts = (await said(page)).join(' \n ')
        expect(texts, `${lang} ${ending}: no Start / Старт promised`).not.toMatch(/\bStart\b|Старт/)
        await frame(page, `${theme}-${lang}-visitor-${ending}`)
      }
    }
  })
}
