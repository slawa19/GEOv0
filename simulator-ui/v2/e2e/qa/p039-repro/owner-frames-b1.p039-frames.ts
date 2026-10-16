/**
 * 039 slice B, PR 1 (palette, roles, signs, legend, node card) - the frames the owner looks at BEFORE the baselines of
 * `e2e/scenes.spec.ts` are regenerated (spec 039, "Решения по проекту среза B", point 8). Debug-only; writes pictures,
 * asserts only what makes a picture mean what its name says (theme on screen, scene loaded, card open).
 *
 * The SAME file takes "before" (on the base) and "after" (on the slice): it was committed before any product change.
 *
 *   P039_FRAMES_DIR   absolute directory of the review, e.g. <repo>/.local-run/owner-review/2026-10-12-039-b-1
 *   P039_FRAMES_PHASE `before` or `after` - the sub-directory the frames go to
 *
 * States, each in both themes that remain (`saas`, `hud`), 1366x768, DPR 1, no GPU:
 *   scene100        the versioned 100-node fixture through the real pipeline (mocked backend);
 *   roles           a small scene: creditor / debtor / even / unknown (`net_sign: null`) / frozen, EACH as a person
 *                   (circle) and as an organisation (rounded square), and a participant of type `hub`; a one-way
 *                   line, a mutual pair;
 *   roles-nodes     the same frame cut to the nodes; `roles-nodes-zoom3x` is that cut magnified three times with hard
 *                   pixels, so that the sign inside a node can be read;
 *   roles-labels    the same scene, Labels: Neighbors, the hub selected - name labels on screen;
 *   node-card       the card of the net debtor, cut to the card;
 *   bottom          the bottom bar (after the slice: with the legend row), cut to it.
 * The last test lays `before` and `after` side by side into `compare/` when both exist.
 *
 * LIMITS: the layout is the product's (force layout) - positions are equal between phases only while the layout code
 * is; the frames are of the stand's browser, not of a user's GPU machine; nothing here judges the picture.
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

type NodeOver = { type?: string; status?: string; net_sign: -1 | 0 | 1 | null; atoms: string | null; key: string; shape?: string }

function node(id: string, name: string, o: NodeOver) {
  const business = o.type === 'business'
  return {
    id, name, type: o.type ?? 'person', status: o.status ?? 'active', links_count: 2,
    net_balance_atoms: o.atoms, net_sign: o.net_sign, net_balance: null,
    viz_color_key: o.key, viz_shape_key: o.shape ?? (business ? 'rounded-rect' : 'circle'), viz_size: business ? { w: 26, h: 22 } : { w: 18, h: 18 },
    viz_badge_key: null,
  }
}

const link = (source: string, target: string, used: string) => ({
  source, target, trust_limit: '100', used, available: String(100 - Number(used)), status: 'active',
  viz_color_key: 'default', viz_width_key: 'mid', viz_alpha_key: 'hi',
})

/**
 * Five states of the net position, each as a PERSON (circle) and as an ORGANISATION (rounded square), plus a hub:
 * the shape by kind of participant is not changed by the slice, and every state must be seen on both shapes.
 * The colour keys are the ones the backend gives such a node today (`app/core/simulator/viz_rules.py` `node_color_key`).
 * (2026-10-12, before any frame was kept: the first form of this scene had only persons for even / unknown / frozen.)
 */
export function rolesSnapshot(): QaSnapshot {
  const B = { type: 'business' }
  return {
    equivalent: 'UAH',
    generated_at: '2026-02-01T00:00:00.000Z',
    palette: { default: { color: '#64748b', label: 'Default' } },
    limits: { max_particles: 120 },
    nodes: [
      node('cr', 'Creditor', { net_sign: 1, atoms: '3000', key: 'person' }),
      node('crB', 'Creditor shop', { ...B, net_sign: 1, atoms: '4500', key: 'business' }),
      node('db', 'Debtor', { net_sign: -1, atoms: '3000', key: 'debt-4' }),
      node('dbB', 'Debtor shop', { ...B, net_sign: -1, atoms: '4500', key: 'debt-8' }),
      node('ev', 'Even', { net_sign: 0, atoms: '0', key: 'person' }),
      node('evB', 'Even shop', { ...B, net_sign: 0, atoms: '0', key: 'business' }),
      node('un', 'Unknown', { net_sign: null, atoms: null, key: 'person' }),
      node('unB', 'Unknown shop', { ...B, net_sign: null, atoms: null, key: 'business' }),
      node('fz', 'Frozen creditor', { status: 'frozen', net_sign: 1, atoms: '1000', key: 'suspended' }),
      node('fzB', 'Suspended debtor shop', { ...B, status: 'suspended', net_sign: -1, atoms: '1000', key: 'suspended' }),
      node('hub', 'Hub', { type: 'hub', net_sign: 0, atoms: '0', key: 'person' }),
    ],
    links: [
      link('cr', 'db', '30'), // one-way: cr trusts db, db owes 30
      link('crB', 'dbB', '50'), link('dbB', 'crB', '5'), // a mutual pair with different debts
      // The hub trusts everyone: with the hub selected and Labels: Neighbors every name is on screen.
      ...['cr', 'crB', 'db', 'dbB', 'ev', 'evB', 'un', 'unB', 'fz', 'fzB'].map((id) => link('hub', id, '0')),
    ],
  }
}

/** Magnify a kept PNG `k` times with hard pixels (no smoothing): the real pixels of the frame, made big enough to read a 6 px sign. */
async function magnify(page: Page, from: string, to: string, k: number): Promise<void> {
  const zoom = await page.context().newPage()
  await zoom.setContent(`<body style="margin:0;background:#000"><img id="i" style="display:block;image-rendering:pixelated" src="data:image/png;base64,${readFileSync(from).toString('base64')}"></body>`)
  await zoom.waitForFunction(() => { const i = document.images[0]!; return i.complete && i.naturalWidth > 0 })
  const size = await zoom.locator('#i').evaluate((el, k) => { const i = el as HTMLImageElement; i.width = i.naturalWidth * k; i.height = i.naturalHeight * k; return { width: i.width, height: i.height } }, k)
  await zoom.setViewportSize(size)
  await zoom.locator('#i').screenshot({ path: to })
  await zoom.close()
}

async function bootThemed(page: Page, snapshot: QaSnapshot, theme: string): Promise<void> {
  await boot(page, { snapshot, clock: false, query: `&theme=${theme}` })
  await expect(page.locator('.root'), 'the theme of the frame is the theme on screen').toHaveAttribute('data-theme', theme)
  await waitLoopAsleep(page)
}

async function nodesBox(page: Page, pad = 70): Promise<{ x: number; y: number; width: number; height: number }> {
  const pts = [...(await screenNodes(page)).values()]
  const vp = page.viewportSize()!
  const x0 = Math.max(0, Math.min(...pts.map((p) => p.x)) - pad)
  const y0 = Math.max(0, Math.min(...pts.map((p) => p.y)) - pad)
  const x1 = Math.min(vp.width, Math.max(...pts.map((p) => p.x)) + pad)
  const y1 = Math.min(vp.height, Math.max(...pts.map((p) => p.y)) + pad)
  return { x: Math.floor(x0), y: Math.floor(y0), width: Math.ceil(x1 - x0), height: Math.ceil(y1 - y0) }
}

for (const theme of THEMES) {
  test(`039 B1 frames [${theme}] - the 100-node scene`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, fixtureSnapshot100(), theme)
    await page.screenshot({ path: join(dir, `${theme}-scene100.png`) })
  })

  test(`039 B1 frames [${theme}] - the roles scene, its nodes, its bottom bar`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, rolesSnapshot(), theme)
    await page.screenshot({ path: join(dir, `${theme}-roles.png`) })
    await page.screenshot({ path: join(dir, `${theme}-roles-nodes.png`), clip: await nodesBox(page, 40) })
    await magnify(page, join(dir, `${theme}-roles-nodes.png`), join(dir, `${theme}-roles-nodes-zoom3x.png`), 3)
    const bottom = await page.locator('.ds-ov-bottom').first().boundingBox()
    expect(bottom, 'the bottom bar is on screen').not.toBeNull()
    await page.screenshot({ path: join(dir, `${theme}-bottom.png`), clip: bottom! })
  })

  test(`039 B1 frames [${theme}] - name labels (Labels: Neighbors, the hub selected)`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, rolesSnapshot(), theme)
    await openDetails(page) // 043 S1: Labels is behind "Подробнее"
    await page.getByRole('combobox', { name: 'Labels' }).selectOption('neighbors')
    const hub = (await screenNodes(page)).get('hub')!
    await page.mouse.click(hub.x, hub.y)
    await expect(page.locator('.node-label-inner').first(), 'name labels are on screen').toBeVisible()
    await waitLoopAsleep(page)
    await page.screenshot({ path: join(dir, `${theme}-roles-labels.png`) })
  })

  test(`039 B1 frames [${theme}] - the node card of the net debtor`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, rolesSnapshot(), theme)
    const db = (await screenNodes(page)).get('db')!
    await page.mouse.dblclick(db.x, db.y)
    const card = page.locator('.ws-shell[data-win-type="node-card"]')
    await expect(card, 'the card is open').toBeVisible()
    await waitLoopAsleep(page)
    await page.screenshot({ path: join(dir, `${theme}-node-card-frame.png`) })
    await card.screenshot({ path: join(dir, `${theme}-node-card.png`) })
  })
}

test('039 B1 frames - before | after, side by side (only when both phases exist)', async ({ page }) => {
  if (!isAbsolute(DIR)) throw new Error('039 frames: P039_FRAMES_DIR must be an absolute directory')
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
        <figure style="margin:0"><figcaption>before</figcaption><img id="a" src="${uri(join(before, name))}"></figure>
        <figure style="margin:0"><figcaption>after</figcaption><img id="b" src="${uri(join(after, name))}"></figure>
      </div></body>`)
    await page.waitForFunction(() => [...document.images].every((i) => i.complete && i.naturalWidth > 0))
    const size = await page.locator('#c').evaluate((el) => ({ width: Math.ceil(el.getBoundingClientRect().width), height: Math.ceil(el.getBoundingClientRect().height) }))
    await page.setViewportSize({ width: Math.max(320, size.width), height: Math.max(200, size.height) })
    await page.locator('#c').screenshot({ path: join(out, name) })
  }
})
