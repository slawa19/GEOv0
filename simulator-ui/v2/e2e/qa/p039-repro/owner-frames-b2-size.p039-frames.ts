/**
 * 039 slice B, PR 2, fix-delta of 2026-10-16 - the frames the owner chooses the SIZE of the direction marker by.
 * Debug-only (`playwright.p039-frames.config.ts`): writes pictures, asserts only what makes a picture mean its name.
 *
 *   P039_FRAMES_DIR    absolute directory of the review (`<repo>/.local-run/owner-review/2026-10-12-039-b-2`)
 *   P039_FRAMES_PHASE  `before`    - the product without markers (run on a tree whose `src` is the base);
 *                      `size-min`  - the product as shipped: the measured minimum, length 7 x width 5.6;
 *                      `size-step` - one step larger on the measured curve: length 8 x width 6.4 (+1 px length,
 *                                    +0.8 px width - the aspect 0.8 is kept), by a TEST-ONLY override of the served
 *                                    module (`overrideArrowLength`); the product has no switch for it.
 * A last test lays `before | <variant>` side by side into `compare/<variant>/` for every variant present.
 *
 * Frames, each in `saas` and `hud`, 1366 x 768, DPR 1, no GPU:
 *   scene100-zoom1, scene100-zoom06    the 100-node fixture at application zoom 1 and 0.6;
 *   small                              a one-way line with debt, a mutual pair, a line with no debt, a hub as the
 *                                      debtor - zoom 1, the whole frame;
 *   small-<pair>-zoom3x                each pair cut out and magnified three times with hard pixels.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { overrideArrowLength, zoomTo } from '../directionProbe.js'
import { boot, fixtureSnapshot100, screenNodes, waitLoopAsleep, type QaSnapshot } from '../stand.js'

const DIR = process.env.P039_FRAMES_DIR ?? ''
const PHASE = process.env.P039_FRAMES_PHASE ?? ''
const VARIANTS = ['size-min', 'size-step'] as const
const STEP_LENGTH_PX = 8
const THEMES = ['saas', 'hud'] as const

function outDir(): string {
  if (!isAbsolute(DIR)) throw new Error('039 frames: P039_FRAMES_DIR must be an absolute directory')
  if (PHASE !== 'before' && !(VARIANTS as readonly string[]).includes(PHASE)) throw new Error(`039 frames: P039_FRAMES_PHASE must be before, ${VARIANTS.join(' or ')}`)
  const d = join(DIR, PHASE)
  mkdirSync(d, { recursive: true })
  return d
}

const person = (id: string, name: string, netSign: number, type = 'person') => ({
  id, name, type, status: 'active', links_count: 1, net_balance_atoms: '0', net_sign: netSign, net_balance: null,
  viz_color_key: 'person', viz_shape_key: 'circle', viz_size: { w: 24, h: 24 }, viz_badge_key: null,
})
/** `link_alpha_key` of the backend for an active line: what a line gets for its debt. */
const alphaKeyOf = (used: number) => (used / 100 >= 0.75 ? 'hi' : used / 100 >= 0.4 ? 'active' : used / 100 >= 0.15 ? 'muted' : 'bg')
const trust = (source: string, target: string, used: number) => ({
  source, target, trust_limit: '100', used: String(used), available: String(100 - used), status: 'active',
  viz_color_key: 'default', viz_width_key: 'mid', viz_alpha_key: alphaKeyOf(used),
})

/** Pairs: [creditor (source), debtor (target)]. Every node has one neighbour. */
const PAIRS = { oneWayDebt: ['a', 'b'], mutual: ['c', 'd'], noDebt: ['e', 'f'], hubDebtor: ['g', 'hub'] } as const

function smallSnapshot(): QaSnapshot {
  return {
    equivalent: 'UAH', generated_at: '2026-02-01T00:00:00.000Z',
    palette: { default: { color: '#64748b', label: 'Default' } }, limits: { max_particles: 120 },
    nodes: [person('a', 'A', 1), person('b', 'B', -1), person('c', 'C', 1), person('d', 'D', -1), person('e', 'E', 0), person('f', 'F', 0), person('g', 'G', 1), person('hub', 'Hub', -1, 'hub')],
    links: [trust('a', 'b', 80), trust('c', 'd', 50), trust('d', 'c', 5), trust('e', 'f', 0), trust('g', 'hub', 80)],
  }
}

async function bootFor(page: Page, snapshot: QaSnapshot, theme: string, zoom: number): Promise<void> {
  await boot(page, {
    snapshot, clock: false, query: `&theme=${theme}`,
    beforeLoad: PHASE === 'size-step' ? (p) => overrideArrowLength(p, STEP_LENGTH_PX) : undefined,
  })
  await expect(page.locator('.root'), 'the theme of the frame is the theme on screen').toHaveAttribute('data-theme', theme)
  await waitLoopAsleep(page)
  if (zoom !== 1) await zoomTo(page, zoom, 'real')
  await page.mouse.move(5, 5)
  await waitLoopAsleep(page)
}

async function magnify(page: Page, from: string, to: string, k: number): Promise<void> {
  const zoom = await page.context().newPage()
  await zoom.setContent(`<body style="margin:0;background:#000"><img id="i" style="display:block;image-rendering:pixelated" src="data:image/png;base64,${readFileSync(from).toString('base64')}"></body>`)
  await zoom.waitForFunction(() => { const i = document.images[0]!; return i.complete && i.naturalWidth > 0 })
  const size = await zoom.locator('#i').evaluate((el, k) => { const i = el as HTMLImageElement; i.width = i.naturalWidth * k; i.height = i.naturalHeight * k; return { width: i.width, height: i.height } }, k)
  await zoom.setViewportSize(size)
  await zoom.locator('#i').screenshot({ path: to })
  await zoom.close()
}

/** The debtor end of a pair: a 70 x 70 px square round the debtor's rim towards the creditor (both ends for the mutual pair). */
async function endCut(page: Page, dir: string, name: string, [creditor, debtor]: readonly [string, string]): Promise<void> {
  const n = await screenNodes(page)
  const [c, d] = [n.get(creditor)!, n.get(debtor)!]
  const l = Math.hypot(c.x - d.x, c.y - d.y)
  for (const [label, at, other] of [['debtor-end', d, c], ['creditor-end', c, d]] as const) {
    const u = { x: (other.x - at.x) / l, y: (other.y - at.y) / l }
    const cx = at.x + u.x * 20
    const cy = at.y + u.y * 20
    const clip = { x: Math.round(cx - 35), y: Math.round(cy - 35), width: 70, height: 70 }
    const file = join(dir, `${name}-${label}.png`)
    await page.screenshot({ path: file, clip })
    await magnify(page, file, join(dir, `${name}-${label}-zoom3x.png`), 3)
  }
}

for (const theme of THEMES) {
  for (const zoom of [1, 0.6]) {
    test(`039 B2 size frames [${theme}] - the 100-node scene at application zoom ${zoom}`, async ({ page }) => {
      const dir = outDir()
      await bootFor(page, fixtureSnapshot100(), theme, zoom)
      await page.screenshot({ path: join(dir, `${theme}-scene100-zoom${zoom === 1 ? '1' : '06'}.png`) })
    })
  }

  test(`039 B2 size frames [${theme}] - small scenes at zoom 1: one-way, mutual, no debt, hub as the debtor; their ends magnified`, async ({ page }) => {
    const dir = outDir()
    await bootFor(page, smallSnapshot(), theme, 1)
    await page.screenshot({ path: join(dir, `${theme}-small.png`) })
    for (const [name, pair] of Object.entries(PAIRS)) await endCut(page, dir, `${theme}-small-${name}`, pair)
  })
}

test('039 B2 size frames - before | variant, side by side (for every variant that has frames)', async ({ page }) => {
  if (!isAbsolute(DIR)) throw new Error('039 frames: P039_FRAMES_DIR must be an absolute directory')
  const before = join(DIR, 'before')
  const present = VARIANTS.filter((v) => existsSync(join(DIR, v)))
  test.skip(!existsSync(before) || present.length === 0, 'no pair of phases yet')
  const uri = (p: string) => `data:image/png;base64,${readFileSync(p).toString('base64')}`
  for (const v of present) {
    const out = join(DIR, 'compare', v)
    mkdirSync(out, { recursive: true })
    const names = readdirSync(before).filter((n) => n.endsWith('.png') && existsSync(join(DIR, v, n)))
    expect(names.length, `frames present in before and ${v}`).toBeGreaterThan(0)
    for (const name of names) {
      await page.setContent(`<body style="margin:0;background:#222;font:14px sans-serif;color:#eee">
        <div id="c" style="display:inline-flex;gap:8px;padding:8px;align-items:flex-start">
          <figure style="margin:0"><figcaption>before</figcaption><img src="${uri(join(before, name))}"></figure>
          <figure style="margin:0"><figcaption>${v}</figcaption><img src="${uri(join(DIR, v, name))}"></figure>
        </div></body>`)
      await page.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))
      const size = await page.locator('#c').evaluate((el) => ({ width: Math.ceil(el.getBoundingClientRect().width), height: Math.ceil(el.getBoundingClientRect().height) }))
      await page.setViewportSize({ width: Math.max(320, size.width), height: Math.max(200, size.height) })
      await page.locator('#c').screenshot({ path: join(out, name) })
    }
  }
})
