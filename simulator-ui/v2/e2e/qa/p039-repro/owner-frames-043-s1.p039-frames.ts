/**
 * 043 S1 (layout and the "Подробнее" toggle) - the owner's "before / after" frames (spec 043, Verification plan,
 * point 5). Debug-only; writes pictures, asserts only what makes a picture mean what its name says (theme on screen,
 * scene loaded, the toggle state named in the file). Same machinery as the 039 frames (config
 * `playwright.p039-frames.config.ts`, `P039_FRAMES_DIR`, `P039_FRAMES_PHASE`); run it alone with `--grep "043 S1"`.
 *
 * The SAME file takes "before" (on the base) and "after" (on the slice): it was committed before any product change.
 * On the base there is no toggle, so a `*-details` frame of "before" is the same screen as its `*-default` frame - the
 * base shows every service control all the time; that is the point of the comparison.
 *
 * States, 1366x768, DPR 1, no GPU, the 100-node scene, both themes (`?theme=`), clean storage (the stand clears it):
 *   <theme>-auto-default       `?mode=real` without `?ui=`: what the first visit of the real demo shows;
 *   <theme>-interact-default   `?ui=interact`: the screen with the three protocol actions;
 *   <theme>-<ui>-details       the same after a click on "Подробнее" (when the product has the toggle), with
 *                              "Inspect graph" opened when it is on screen;
 *   <theme>-interact-tooltip   a trust line's tooltip, shown through the dev hook (`__geoSim.showEdgeTooltip`).
 * The last test lays `before` and `after` side by side into `compare/`.
 *
 * LIMITS: the layout is the product's (force layout); the frames are of the stand's browser; nothing here judges.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { boot, fixtureSnapshot100, screenNodes, waitLoopAsleep, type QaSnapshot } from '../stand.js'

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''
const THEMES = ['saas', 'hud'] as const
const UIS = ['auto', 'interact'] as const

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('043 frames: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && PHASE !== 'after') throw new Error('043 frames: P039_FRAMES_PHASE must be "before" or "after"')
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

async function bootFrame(page: Page, snapshot: QaSnapshot, ui: 'auto' | 'interact', theme: string): Promise<void> {
  await page.setViewportSize({ width: 1366, height: 768 })
  await boot(page, { snapshot, clock: false, ui, query: `&theme=${theme}` })
  await waitLoopAsleep(page)
  await expect(page.locator('.root'), 'the theme of the frame is the theme on screen').toHaveAttribute('data-theme', theme)
}

/** "Подробнее", when the product has it; returns whether it was there. */
async function openDetails(page: Page): Promise<boolean> {
  const toggle = page.locator('[data-testid="details-toggle"]')
  if ((await toggle.count()) === 0) return false
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  return true
}

for (const theme of THEMES) {
  for (const ui of UIS) {
    test(`043 S1 frames [${theme}] ${ui} - default and with every service control`, async ({ page }) => {
      const dir = outDir()
      await bootFrame(page, fixtureSnapshot100(), ui, theme)
      await page.screenshot({ path: join(dir, `${theme}-${ui}-default.png`) })

      await openDetails(page)
      const nav = page.locator('.graph-navigator > details')
      if ((await nav.count()) > 0 && !(await nav.evaluate((el) => (el as HTMLDetailsElement).open))) await nav.locator('summary').click()
      await waitLoopAsleep(page)
      await page.screenshot({ path: join(dir, `${theme}-${ui}-details.png`) })
    })
  }

  test(`043 S1 frames [${theme}] interact - the tooltip of a trust line`, async ({ page }) => {
    const dir = outDir()
    const snapshot = fixtureSnapshot100()
    await bootFrame(page, snapshot, 'interact', theme)
    const nodes = await screenNodes(page)
    const link = snapshot.links.find((l) => nodes.has(String(l.source)) && nodes.has(String(l.target)))!
    const a = nodes.get(String(link.source))!
    const b = nodes.get(String(link.target))!
    await page.evaluate(({ link, x, y }) => {
      const hook = (window as unknown as { __geoSim: { showEdgeTooltip: (e: Record<string, unknown>) => void } }).__geoSim
      hook.showEdgeTooltip({
        key: `${link.source}→${link.target}`, fromId: link.source, toId: link.target, amountText: '',
        screenX: x, screenY: y, trustLimit: link.trust_limit ?? null, used: link.used ?? null, available: link.available ?? null,
        edgeStatus: link.status ?? null,
      })
    }, { link: link as unknown as Record<string, string>, x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 })
    const tip = page.locator('.ds-ov-tooltip')
    await expect(tip, 'the tooltip is on screen').toBeVisible()
    await waitLoopAsleep(page)
    const box = (await tip.boundingBox())!
    await page.screenshot({ path: join(dir, `${theme}-interact-tooltip.png`), clip: { x: Math.max(0, box.x - 80), y: Math.max(0, box.y - 60), width: Math.min(1366, box.width + 160), height: box.height + 120 } })
  })
}

test('043 S1 frames - before | after, side by side (only when both phases exist)', async ({ page }) => {
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
