/**
 * 046 wave 3 (T4604): the two inspectors - the participant card and the line window - in ONE language, at 1366x768, in
 * both product themes and both languages, on the mocked backend of the 037 specs (`helpers/p037Mock.ts`: Alice holds the
 * line Bob -> Alice; with `aliceLines` eight more lines Alice -> x01..x08).
 *
 *   (а) the card and the line window say nothing in the other language - visible text and the `aria-label` / `title` /
 *       `placeholder` of every element inside the two shells, and the shells' own names - only data (names, ids, the
 *       equivalent code, the key `Esc`); the rule is structural, as wave 1's (`p046-w1-full.spec.ts`);
 *   (б) Russian fits: the card's head and statistics stay one line each, the line window stays 320 wide with its figures
 *       on one line and its buttons on one row (045 S3's measures, `p045-s3-inspectors.spec.ts`, now in Russian too);
 *       nothing inside either window is wider than the window;
 *   (в) 048 item 13 (б): the line window says "A trusts B up to the limit - that is how much B may owe A" in the names of
 *       its title, in the language of the page;
 *   (г) a switch of the language on the open page re-renders both open windows (no reload, no reopen).
 *
 * `P046_FRAMES=1` writes the owner's frames (`.local-run/owner-review/2026-10-19-046-w3/`, or `P046_FRAMES_DIR`), taken as
 * soon as the window is open; the default run writes none.
 * What this does NOT see: the wording (the parity guard and a human); the history and the notification after a switch (the
 * mount test `inspectors.p046w3.test.ts` holds those on the real `useInteractMode`).
 */
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Locator, type Page } from '@playwright/test'

import { mockApp, ready, settleShell } from './helpers/p037Mock.js'
import { t } from '../src/i18n/t'

const THEMES = ['saas', 'hud'] as const
const LANGS = ['en', 'ru'] as const
type Theme = (typeof THEMES)[number]
type Lang = (typeof LANGS)[number]

const CARD = '.ws-shell[data-win-type="node-card"]'
const EDGE = '.ws-shell[data-win-type="edge-detail"]'
const FRAMES = process.env.P046_FRAMES === '1'
const FRAMES_DIR = process.env.P046_FRAMES_DIR ?? fileURLToPath(new URL('../../../.local-run/owner-review/2026-10-19-046-w3/', import.meta.url))

/** Data in either language: the mock's names and ids, the equivalent code, the key `Esc`. */
const DATA_WORDS = new Set(['Alice', 'Bob', 'Carol', 'alice', 'bob', 'carol', 'Extra', 'x', 'UAH', 'Esc'])

async function open(page: Page, theme: Theme, lang: Lang, o: { aliceLines?: number } = {}): Promise<void> {
  await mockApp(page, { paymentRealBodies: [], extraTargets: o.aliceLines ?? 0, aliceLines: o.aliceLines })
  await page.setViewportSize({ width: 1366, height: 768 })
  await page.goto(`/?mode=real&ui=interact&e2eReal=1&theme=${theme}&lang=${lang}`)
  await ready(page, true)
  await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
  await expect(page.locator('html')).toHaveAttribute('lang', lang)
  await page.waitForFunction(() => Boolean((window as unknown as { __geoSim?: unknown }).__geoSim))
}

async function openCard(page: Page): Promise<Locator> {
  await page.evaluate(() => (window as unknown as { __geoSim: { openNodeCard: (o: unknown) => void } }).__geoSim
    .openNodeCard({ nodeId: 'alice', anchor: { x: 500, y: 300 } }))
  const card = page.locator(CARD)
  await expect(card.locator('.ds-ov-node-card')).toBeVisible()
  await expect(card.locator('.nco-trustline-row').first()).toBeVisible()
  await settleShell(page, 'node-card-stats')
  return card
}

/** The line Bob -> Alice through "Find on graph" (as `p045-s3-inspectors.spec.ts`). */
async function openLine(page: Page): Promise<Locator> {
  await page.locator('[data-testid="details-toggle"]').click()
  const nav = page.getByTestId('graph-navigator')
  await nav.getByTestId('graph-navigator-summary').click()
  await nav.getByTestId('graph-navigator-open-edge').click()
  const edge = page.locator(EDGE)
  await expect(edge.locator('[data-testid="edge-detail-popup"]')).toBeVisible()
  await page.locator('[data-testid="details-toggle"]').click()
  await page.evaluate(() => { (document.querySelector('.root') as HTMLElement).scrollTop = 0 })
  await settleShell(page, 'edge-detail-popup')
  return edge
}

/** Everything a window says: its text nodes, and the names, titles and placeholders of the shell and its elements. */
function said(root: Locator): Promise<string[]> {
  return root.evaluate((el) => {
    const out: string[] = []
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue && n.nodeValue.trim()) out.push(n.nodeValue)
    for (const e of [el, ...Array.from(el.querySelectorAll('*'))]) {
      for (const a of ['aria-label', 'title', 'placeholder', 'aria-valuetext']) {
        const v = e.getAttribute(a)
        if (v) out.push(v)
      }
    }
    return out
  })
}

async function foreignWords(root: Locator, lang: Lang): Promise<string[]> {
  const words = (await said(root)).flatMap((s) => (lang === 'ru' ? s.match(/[A-Za-z_]+/g) : s.match(/[Ѐ-ӿ]+/g)) ?? [])
  return [...new Set(words.filter((w) => !DATA_WORDS.has(w) && !/^x\d+$/.test(w)))]
}

/** Elements inside `root` that stick out of it horizontally (the Russian is 20-35 % longer). */
function overflowing(root: Locator): Promise<string[]> {
  return root.evaluate((el) => {
    const b = el.getBoundingClientRect()
    return Array.from(el.querySelectorAll<HTMLElement>('*'))
      .filter((n) => { const r = n.getBoundingClientRect(); return r.width > 0 && (r.left < b.left - 0.5 || r.right > b.right + 0.5) })
      .filter((n) => !n.closest('.nco-trustline-row__avail-desc'))
      .map((n) => `${n.tagName.toLowerCase()}.${n.className}:${(n.textContent ?? '').trim().slice(0, 24)}`)
  })
}

const centreY = async (l: Locator) => { const b = (await l.boundingBox())!; return b.y + b.height / 2 }
const spread = (xs: number[]) => Math.max(...xs) - Math.min(...xs)

async function frame(page: Page, name: string): Promise<void> {
  if (!FRAMES) return
  mkdirSync(FRAMES_DIR, { recursive: true })
  // §15 Codex P3: joined, not concatenated - a `P046_FRAMES_DIR` without a trailing separator wrote beside the directory.
  await page.screenshot({ path: join(FRAMES_DIR, `${name}.png`) })
}

for (const theme of THEMES) {
  for (const lang of LANGS) {
    test.describe(`046 W3 [${theme}] [${lang}] inspectors at 1366x768`, () => {
      test('(а)(б) the participant card: one language, the head and the statistics one line each, nothing sticks out', async ({ page }, info) => {
        await open(page, theme, lang, { aliceLines: 2 })
        const card = await openCard(page)
        await frame(page, `${theme}-${lang}-1-card`)
        await expect(card.locator('.nco-trustline-row'), 'counter-check: the rows of both groups are there').toHaveCount(3)
        await expect(card.getByTestId('node-card-group-out')).toHaveText(t('card.trustsGroup', { count: 2 }, lang))
        await expect(card.getByTestId('node-card-group-in')).toHaveText(t('card.trustedByGroup', { count: 1 }, lang))
        expect(await foreignWords(card, lang)).toEqual([])
        const head = [await centreY(card.getByTestId('node-card-name')), await centreY(card.getByTestId('node-card-role'))]
        const stats = await card.locator('[data-testid="node-card-stats"] > span').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return r.top + r.height / 2 }))
        info.annotations.push({ type: 'P046W3-card', description: JSON.stringify({ head, stats }) })
        expect(stats.length, 'counter-check: labels and values').toBe(4)
        expect(spread(head), 'name and role one line').toBeLessThan(4)
        expect(spread(stats), 'the statistics one line').toBeLessThan(4)
        expect(await overflowing(card)).toEqual([])
      })

      test('(а)(б)(в) the line window: one language, 320 wide, the figures one line, the buttons one row, the explaining line', async ({ page }, info) => {
        await open(page, theme, lang)
        const edge = await openLine(page)
        await frame(page, `${theme}-${lang}-2-line-window`)
        expect(await foreignWords(edge, lang)).toEqual([])
        await expect(edge.getByTestId('edge-trust-explanation')).toHaveText(t('edgeWindow.trustExplanation', { from: 'Bob', to: 'Alice' }, lang))
        const w = (await edge.boundingBox())!.width
        const figures = await edge.locator('[data-testid="edge-stats"]').evaluate((el) => {
          const tops: number[] = []
          const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
          for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            if (!(n.textContent ?? '').trim()) continue
            const range = document.createRange()
            range.selectNodeContents(n)
            for (const r of Array.from(range.getClientRects())) tops.push(r.top + r.height / 2)
          }
          return tops
        })
        const buttons = await edge.locator('.popup__actions button').evaluateAll((bs) => bs.map((b) => { const r = b.getBoundingClientRect(); return { t: (b.textContent ?? '').trim(), c: r.top + r.height / 2, w: Math.round(r.width) } }))
        const row = await edge.locator('.popup__actions').evaluate((el) => ({ w: el.clientWidth, gap: getComputedStyle(el).columnGap }))
        info.annotations.push({ type: 'P046W3-edge', description: JSON.stringify({ w, row, figures, buttons }) })
        expect(w, 'the window is the preferred width of its shell').toBeLessThanOrEqual(320.5)
        expect(figures.length, 'counter-check: the figures are found').toBeGreaterThanOrEqual(6)
        expect(spread(figures), 'the figures one line').toBeLessThan(4)
        expect(buttons.length, 'counter-check: Pay, Change limit, Close line').toBe(3)
        expect(spread(buttons.map((b) => b.c)), `every button on one row: ${JSON.stringify(buttons)}`).toBeLessThan(4)
        expect(await overflowing(edge)).toEqual([])
      })
    })
  }
}

/** The language switch behind "Details" (wave 0); "Details" is closed again so the windows are measured as they stand. */
async function switchLanguage(page: Page, lang: Lang): Promise<void> {
  await page.locator('[data-testid="details-toggle"]').click()
  await page.getByTestId('language-select').selectOption(lang)
  await expect(page.locator('html')).toHaveAttribute('lang', lang)
  await page.locator('[data-testid="details-toggle"]').click()
}

test('(г) a switch of the language re-renders the open card and the open line window - no reload, no reopen', async ({ page }) => {
  await open(page, 'saas', 'en')
  const card = await openCard(page)
  const pay = card.getByTestId('node-card-send-payment')
  await expect(pay).toHaveText(t('card.pay', {}, 'en'))
  const before = await pay.elementHandle()
  await switchLanguage(page, 'ru')
  await expect(pay).toHaveText(t('card.pay', {}, 'ru'))
  expect(await pay.evaluate((el, h) => el === h, before), 'the same element: re-rendered, not reopened').toBe(true)
  expect(await foreignWords(card, 'ru')).toEqual([])
  await page.keyboard.press('Escape')
  await expect(card).toBeHidden()

  const edge = await openLine(page)
  const change = edge.getByTestId('edge-change-limit')
  await expect(change).toHaveText(t('edgeWindow.changeLimit', {}, 'ru'))
  const was = await change.elementHandle()
  await switchLanguage(page, 'en')
  await expect(change).toHaveText(t('edgeWindow.changeLimit', {}, 'en'))
  expect(await change.evaluate((el, h) => el === h, was), 'the same element: re-rendered, not reopened').toBe(true)
  await expect(edge.getByTestId('edge-trust-explanation')).toHaveText(t('edgeWindow.trustExplanation', { from: 'Bob', to: 'Alice' }, 'en'))
  expect(await foreignWords(edge, 'en')).toEqual([])
})
