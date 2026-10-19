/**
 * 039 slice D ("was -> now" of the total debt) - the owner's frames. Debug-only; writes pictures and asserts only what
 * makes a picture mean what its name says (theme, language, the total the bar shows). Same machinery as the other
 * frames (config `playwright.p039-frames.config.ts`, `P039_FRAMES_DIR`, `P039_FRAMES_PHASE`); run it alone with
 * `--grep "039 D frames"`. The same file takes "before" (on the base) and "after" (on the slice).
 *
 * The stand's backend and its small scene with debts (the scene of `e2e/qa/debt-before-after.qa.ts`: a cycle a>b>c>a
 * of 10 each and d>e of 18 - total 48.00 UAH), 1366x768, no GPU, both themes (`?theme=`), both languages (`?lang=`):
 *   <theme>-<lang>-1-settled        the scene as it opens: "Общий долг 48.00 UAH";
 *   <theme>-<lang>-2-after-payment  a payment raised d>e to 25: total 55.00 (slice D: nothing above it - only a
 *                                   clearing states the pair, advisor's decision 2026-10-19, option (a));
 *   <theme>-<lang>-3-after-clearing a clearing took 10 off each line of the cycle: total 25.00 (slice D: "клиринг: было
 *                                   55.00 → стало 25.00 UAH" above it);
 *   <theme>-<lang>-4-bar            the bottom row alone, after the clearing;
 *   <theme>-<lang>-5-payment-after-clearing  a payment raised d>e to 27: total 27.00, the clearing's pair unchanged.
 * The last test lays `before` and `after` side by side into `compare/`.
 *
 * LIMITS: the backend is the stand's mock; events are frames the script writes; nothing here judges.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { EQ, RUN_ID, boot, pushEvent, ringSnapshot, waitLoopAsleep, type QaSnapshot } from '../stand.js'

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''
const THEMES = ['saas', 'hud'] as const
const LANGS = ['ru', 'en'] as const
const USED: Record<string, string> = { 'a>b': '10.00', 'b>c': '10.00', 'c>a': '10.00', 'd>e': '18.00' }

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('039 D frames: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && PHASE !== 'after') throw new Error('039 D frames: P039_FRAMES_PHASE must be "before" or "after"')
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

function sceneWithDebts(): QaSnapshot {
  const s = ringSnapshot()
  const pairs: Array<[string, string]> = [['a', 'b'], ['b', 'c'], ['c', 'a'], ['d', 'e'], ['e', 'f'], ['f', 'd']]
  const links = pairs.map(([source, target]) => {
    const used = USED[`${source}>${target}`] ?? '0.00'
    return { ...s.links[0]!, source, target, used, available: (100 - Number(used)).toFixed(2), trust_limit: '100.00' }
  })
  return { ...s, links }
}

const total = (page: Page) => page.locator('[data-testid="system-balance"] [data-testid="sbb-total-debt"] .ds-value').first()

for (const theme of THEMES) {
  for (const lang of LANGS) {
    test(`039 D frames [${theme}] [${lang}] - settled, after a payment, after a clearing`, async ({ page }) => {
      const dir = outDir()
      const snapshot = sceneWithDebts()
      await boot(page, {
        snapshot, clock: false, query: `&theme=${theme}&lang=${lang}`,
        beforeLoad: (p) => p.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) => r.fulfill({
          status: 200, contentType: 'application/json',
          body: JSON.stringify({ items: snapshot.links.map((l) => ({
            from_pid: l.source, from_name: l.source, to_pid: l.target, to_name: l.target, equivalent: EQ,
            limit: '100.00', used: l.used, reverse_used: '0.00', available: l.available, status: 'active',
          })) }),
        })),
      })
      await expect(page.locator('.root'), 'the theme of the frame').toHaveAttribute('data-theme', theme)
      await expect(page.locator('html'), 'the language of the frame').toHaveAttribute('lang', lang)
      await expect(total(page)).toHaveText(`48.00 ${EQ}`)
      await waitLoopAsleep(page)
      const barBox = () => page.locator('.ds-ov-bottom').first().evaluate((el) => {
        const r = el.getBoundingClientRect()
        return { top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width) }
      })
      const settledBar = await barBox()
      await page.screenshot({ path: join(dir, `${theme}-${lang}-1-settled.png`) })

      await pushEvent(page, {
        type: 'tx.updated', from: 'e', to: 'd', amount: '7.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
        edge_patch: [{ source: 'd', target: 'e', used: '25.00', available: '75.00' }],
      })
      await expect(total(page)).toHaveText(`55.00 ${EQ}`)
      await waitLoopAsleep(page)
      await page.screenshot({ path: join(dir, `${theme}-${lang}-2-after-payment.png`) })

      await pushEvent(page, {
        type: 'clearing.done', plan_id: 'plan-p039-d-frames', cleared_cycles: 1, cleared_amount: '10.00',
        cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
        edge_patch: [['a', 'b'], ['b', 'c'], ['c', 'a']].map(([source, target]) => ({ source, target, used: '0.00', available: '100.00' })),
      })
      await expect(total(page)).toHaveText(`25.00 ${EQ}`)
      await waitLoopAsleep(page)
      await page.screenshot({ path: join(dir, `${theme}-${lang}-3-after-clearing.png`) })
      // What makes the frame mean what the README says: the bar did not move (slice D only; the base has no "was").
      if (PHASE === 'after') expect(await barBox(), 'the bottom bar keeps its place and size with the pair').toEqual(settledBar)
      const bar = page.locator('.ds-ov-bottom').first()
      const box = await bar.boundingBox()
      expect(box, 'the bottom row is on screen').not.toBeNull()
      await page.screenshot({ path: join(dir, `${theme}-${lang}-4-bar.png`), clip: box!, scale: 'device' })

      await pushEvent(page, {
        type: 'tx.updated', from: 'e', to: 'd', amount: '2.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
        edge_patch: [{ source: 'd', target: 'e', used: '27.00', available: '73.00' }],
      })
      await expect(total(page)).toHaveText(`27.00 ${EQ}`)
      await waitLoopAsleep(page)
      await page.screenshot({ path: join(dir, `${theme}-${lang}-5-payment-after-clearing.png`) })
    })
  }
}

test('039 D frames - before | after, side by side (only when both phases exist)', async ({ page }) => {
  if (!isAbsolute(DIR)) throw new Error('039 D frames: P039_FRAMES_DIR must be an absolute directory')
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
      <div id="c" style="display:inline-flex;flex-direction:column;gap:8px;padding:8px;align-items:flex-start">
        <figure style="margin:0"><figcaption>до среза D: ${name}</figcaption><img src="${uri(join(before, name))}"></figure>
        <figure style="margin:0"><figcaption>после среза D: ${name}</figcaption><img src="${uri(join(after, name))}"></figure>
      </div></body>`)
    await page.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))
    const size = await page.locator('#c').evaluate((el) => ({ width: Math.ceil(el.getBoundingClientRect().width), height: Math.ceil(el.getBoundingClientRect().height) }))
    await page.setViewportSize({ width: Math.max(320, size.width), height: Math.max(200, size.height) })
    await page.locator('#c').screenshot({ path: join(out, name) })
  }
})
