/**
 * 039 slice B, PR 3 (themes and the stand) - the frames the owner looks at before any baseline of `e2e/scenes.spec.ts`
 * is touched (spec 039, "Решения по проекту среза B", point 8). Debug-only; writes pictures, asserts only what makes
 * a picture mean what its name says (theme on screen, scene loaded, card open). Same machinery as
 * `owner-frames-b1.p039-frames.ts` (config `playwright.p039-frames.config.ts`, `P039_FRAMES_DIR`, `P039_FRAMES_PHASE`);
 * run it alone with `--grep "039 B3"`.
 *
 * The SAME file takes "before" (on the base) and "after" (on the slice): it was committed before any product change.
 *
 * States, 1366x768, DPR 1, no GPU:
 *   first-load-*   the first visit with no stored preference and no `?theme=`: whatever the product picks by default
 *                  (the theme on screen is written into the file name: `first-load-<theme>-...`);
 *   <theme>-*      `?theme=<theme>` for `saas` and `hud` (the themes that remain) and for the current default if it is
 *                  a third one: the 100-node scene, the node card (cut to the card), the bottom bar (cut to it), and the
 *                  theme switcher shown open (a native `<select>` popup is drawn outside the page, so "open" is the
 *                  list of its options laid out beside it - see `switcherSheet`).
 * The last test lays `before` and `after` side by side into `compare/` (the B1 file's compare test does the same over
 * the same directory; either works).
 *
 * LIMITS: the layout is the product's (force layout); the frames are of the stand's browser; nothing here judges.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { boot, fixtureSnapshot100, openDetails, screenNodes, waitLoopAsleep, type QaSnapshot } from '../stand.js'

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''
const THEMES = ['saas', 'hud'] as const

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('039 frames: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && PHASE !== 'after') throw new Error('039 frames: P039_FRAMES_PHASE must be "before" or "after"')
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

async function bootQuery(page: Page, snapshot: QaSnapshot, query: string): Promise<string> {
  await boot(page, { snapshot, clock: false, query })
  await waitLoopAsleep(page)
  const theme = await page.locator('.root').getAttribute('data-theme')
  expect(theme, 'a theme is on screen').toBeTruthy()
  return theme!
}

/** The switcher, and beside it the list of what it offers with the chosen one marked: a native popup is not in a screenshot. */
async function switcherSheet(page: Page, path: string): Promise<void> {
  await openDetails(page) // 043 S1: the switcher is behind "Подробнее"
  const select = page.getByRole('combobox', { name: 'UI theme' })
  await expect(select, 'the theme switcher is on screen').toBeVisible()
  const options = await select.evaluate((el) => {
    const s = el as HTMLSelectElement
    return [...s.options].map((o) => ({ value: o.value, text: o.text, selected: o.selected }))
  })
  await page.evaluate((options) => {
    const s = document.querySelector('select[aria-label="UI theme"]') as HTMLSelectElement
    const r = s.getBoundingClientRect()
    const list = document.createElement('div')
    list.id = 'p039-b3-switcher-list'
    list.style.cssText = `position:fixed;left:${Math.round(r.left)}px;top:${Math.round(r.bottom + 2)}px;z-index:2147483647;`
      + 'background:#fff;color:#000;font:13px sans-serif;border:1px solid #888;padding:2px 0;min-width:' + Math.round(r.width) + 'px'
    for (const o of options) {
      const row = document.createElement('div')
      row.textContent = `${o.selected ? '> ' : '  '}${o.text}  [${o.value}]`
      row.style.cssText = `padding:2px 8px;white-space:pre;${o.selected ? 'background:#1e6fd9;color:#fff' : ''}`
      list.appendChild(row)
    }
    document.body.appendChild(list)
  }, options)
  const box = await page.locator('#p039-b3-switcher-list').boundingBox()
  const sel = await select.boundingBox()
  const x = Math.max(0, Math.min(box!.x, sel!.x) - 12)
  const y = Math.max(0, sel!.y - 12)
  await page.screenshot({ path, clip: { x, y, width: Math.max(box!.width, sel!.width) + 24, height: box!.y + box!.height - y + 12 } })
  await page.evaluate(() => document.getElementById('p039-b3-switcher-list')?.remove())
}

async function sceneCardBottom(page: Page, dir: string, prefix: string): Promise<void> {
  await page.screenshot({ path: join(dir, `${prefix}-scene100.png`) })
  const bottom = await page.locator('.ds-ov-bottom').first().boundingBox()
  expect(bottom, 'the bottom bar is on screen').not.toBeNull()
  await page.screenshot({ path: join(dir, `${prefix}-bottom.png`), clip: bottom! })
  await switcherSheet(page, join(dir, `${prefix}-switcher-open.png`))
}

test('039 B3 frames - the first visit: no stored preference, no ?theme=', async ({ page }) => {
  const dir = outDir()
  const theme = await bootQuery(page, fixtureSnapshot100(), '')
  await sceneCardBottom(page, dir, `first-load-${theme}`)
})

for (const theme of THEMES) {
  test(`039 B3 frames [${theme}] - the 100-node scene, the bottom bar, the switcher`, async ({ page }) => {
    const dir = outDir()
    const shown = await bootQuery(page, fixtureSnapshot100(), `&theme=${theme}`)
    expect(shown, 'the theme of the frame is the theme on screen').toBe(theme)
    await sceneCardBottom(page, dir, theme)
  })

  test(`039 B3 frames [${theme}] - the node card`, async ({ page }) => {
    const dir = outDir()
    const snapshot = fixtureSnapshot100()
    const shown = await bootQuery(page, snapshot, `&theme=${theme}`)
    expect(shown, 'the theme of the frame is the theme on screen').toBe(theme)
    // The first node of the versioned fixture: the same participant in every phase and theme.
    const at = (await screenNodes(page)).get(String(snapshot.nodes[0]!.id))!
    await page.mouse.dblclick(at.x, at.y)
    const card = page.locator('.ws-shell[data-win-type="node-card"]')
    await expect(card, 'the card is open').toBeVisible()
    await waitLoopAsleep(page)
    await card.screenshot({ path: join(dir, `${theme}-node-card.png`) })
  })
}

test('039 B3 frames - before | after, side by side (only when both phases exist)', async ({ page }) => {
  if (!isAbsolute(DIR)) throw new Error('039 frames: P039_FRAMES_DIR must be an absolute directory')
  const before = join(DIR, 'before')
  const after = join(DIR, 'after')
  test.skip(!existsSync(before) || !existsSync(after), 'one of the phases has no frames yet')
  const out = join(DIR, 'compare')
  mkdirSync(out, { recursive: true })
  // The first visit changes its name with the default theme: pair it by its role, not by its name.
  const firstOf = (d: string, suffix: string) => readdirSync(d).find((n) => n.startsWith('first-load-') && n.endsWith(suffix))
  const pairs: Array<[string, string, string]> = readdirSync(before).filter((n) => n.endsWith('.png') && existsSync(join(after, n))).map((n) => [n, n, n])
  for (const suffix of ['-scene100.png', '-bottom.png', '-switcher-open.png']) {
    const a = firstOf(before, suffix)
    const b = firstOf(after, suffix)
    if (a && b && a !== b) pairs.push([a, b, `first-load${suffix}`])
  }
  expect(pairs.length, 'frames present in both phases').toBeGreaterThan(0)
  const uri = (p: string) => `data:image/png;base64,${readFileSync(p).toString('base64')}`
  for (const [a, b, name] of pairs) {
    await page.setContent(`<body style="margin:0;background:#222;font:14px sans-serif;color:#eee">
      <div id="c" style="display:inline-flex;gap:8px;padding:8px;align-items:flex-start">
        <figure style="margin:0"><figcaption>before: ${a}</figcaption><img src="${uri(join(before, a))}"></figure>
        <figure style="margin:0"><figcaption>after: ${b}</figcaption><img src="${uri(join(after, b))}"></figure>
      </div></body>`)
    await page.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))
    const size = await page.locator('#c').evaluate((el) => ({ width: Math.ceil(el.getBoundingClientRect().width), height: Math.ceil(el.getBoundingClientRect().height) }))
    await page.setViewportSize({ width: Math.max(320, size.width), height: Math.max(200, size.height) })
    await page.locator('#c').screenshot({ path: join(out, name) })
  }
})
