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
 * PR 2 (2026-10-12, the direction marker) added its scenes to this same file - see `directionSnapshot` below:
 *   direction                 four pairs of 24 px circles, every node with one neighbour: a one-way line with debt, a
 *                             mutual pair with different debts, a line with no debt, a pair for the next frame;
 *   direction-<pair>[-zoom3x] each pair cut out, and that cut magnified three times with hard pixels;
 *   direction-close-dragged   h dragged to 10 px of free line from g - less than a marker needs;
 *   direction-selected        participant a selected, the pointer on its line;
 *   direction-payment         a payment b > a in flight over the line a > b (fake clock, 12 frames in);
 *   direction-clearing[-fading] the mutual pair under the highlight of a clearing, and in the fade of it.
 * The last test lays `before` and `after` side by side into `compare/` when both exist.
 *
 * LIMITS: the layout is the product's (force layout) - positions are equal between phases only while the layout code
 * is; the frames are of the stand's browser, not of a user's GPU machine; nothing here judges the picture.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { boot, fixtureSnapshot100, frames, pushEvent, screenNodes, txUpdated, waitLoopAsleep, type QaSnapshot } from '../stand.js'

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

/* ------------------------------------------------------------------------------------------------------------- */
/*  039 slice B, PR 2 (the direction marker of trust) - scenes added to the SAME script, committed before the      */
/*  product change, so that "before" and "after" are taken by one file.                                           */
/* ------------------------------------------------------------------------------------------------------------- */

/** `link_alpha_key` of `app/core/simulator/viz_rules.py` for an active line: what the backend gives a line by its debt. */
function alphaKeyOf(used: number, limit: number): string {
  const r = Math.abs(used) / limit
  return r >= 0.75 ? 'hi' : r >= 0.4 ? 'active' : r >= 0.15 ? 'muted' : 'bg'
}

const trust = (source: string, target: string, used: number) => ({ ...link(source, target, String(used)), viz_alpha_key: alphaKeyOf(used, 100) })

/** The pairs of `directionSnapshot`: [creditor (source), debtor (target)], what the pair shows. Every node has one neighbour. */
export const DIRECTION_PAIRS = {
  oneWayDebt: ['a', 'b'], // a trusts b; b owes 80 of 100
  mutual: ['c', 'd'], // c trusts d (d owes 50) and d trusts c (c owes 5): different debts in the two directions
  noDebt: ['e', 'f'], // e trusts f; nothing is owed - trust only
  close: ['g', 'h'], // g trusts h (h owes 80); the frames drag h next to g: less room than a marker needs
} as const

export function directionSnapshot(): QaSnapshot {
  const p = (id: string, name: string) => ({ ...node(id, name, { net_sign: 0, atoms: '0', key: 'person' }), viz_size: { w: 24, h: 24 } })
  return {
    equivalent: 'UAH',
    generated_at: '2026-02-01T00:00:00.000Z',
    palette: { default: { color: '#64748b', label: 'Default' } },
    limits: { max_particles: 120 },
    nodes: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'].map((id) => p(id, id.toUpperCase())),
    links: [trust('a', 'b', 80), trust('c', 'd', 50), trust('d', 'c', 5), trust('e', 'f', 0), trust('g', 'h', 80)],
  }
}

type Box = { x: number; y: number; width: number; height: number }

async function pairBox(page: Page, ids: readonly [string, string], pad = 26): Promise<Box> {
  const n = await screenNodes(page)
  const [p, q] = [n.get(ids[0])!, n.get(ids[1])!]
  const vp = page.viewportSize()!
  const x0 = Math.max(0, Math.min(p.x, q.x) - pad)
  const y0 = Math.max(0, Math.min(p.y, q.y) - pad)
  return { x: Math.floor(x0), y: Math.floor(y0), width: Math.ceil(Math.min(vp.width, Math.max(p.x, q.x) + pad) - x0), height: Math.ceil(Math.min(vp.height, Math.max(p.y, q.y) + pad) - y0) }
}

/** A pair of the scene cut out and magnified three times with hard pixels: the ends of a line can be read. */
async function pairCut(page: Page, dir: string, name: string, ids: readonly [string, string]): Promise<void> {
  await page.screenshot({ path: join(dir, `${name}.png`), clip: await pairBox(page, ids) })
  await magnify(page, join(dir, `${name}.png`), join(dir, `${name}-zoom3x.png`), 3)
}

async function dragNode(page: Page, id: string, to: { x: number; y: number }): Promise<void> {
  const from = (await screenNodes(page)).get(id)!
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  for (let i = 1; i <= 10; i += 1) await page.mouse.move(from.x + ((to.x - from.x) * i) / 10, from.y + ((to.y - from.y) * i) / 10)
  await page.mouse.up()
  await waitLoopAsleep(page)
}

for (const theme of THEMES) {
  test(`039 B2 frames [${theme}] - trust lines: one-way with debt, mutual pair, no debt; the ends of each, magnified`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, directionSnapshot(), theme)
    await page.screenshot({ path: join(dir, `${theme}-direction.png`) })
    for (const [name, ids] of Object.entries(DIRECTION_PAIRS)) await pairCut(page, dir, `${theme}-direction-${name}`, ids)
  })

  test(`039 B2 frames [${theme}] - two nodes closer than a marker needs (h dragged next to g)`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, directionSnapshot(), theme)
    const [g, h] = DIRECTION_PAIRS.close
    const at = (await screenNodes(page)).get(g)!
    // Both ends are pinned by a drag (the creditor by a drag of 3 px), so physics does not pull them apart again.
    await dragNode(page, g, { x: at.x + 3, y: at.y })
    const pinned = (await screenNodes(page)).get(g)!
    await dragNode(page, h, { x: pinned.x + 34, y: pinned.y }) // two 24 px circles 34 px apart: 10 px between the rims
    const n = await screenNodes(page)
    expect(Math.hypot(n.get(h)!.x - n.get(g)!.x, n.get(h)!.y - n.get(g)!.y), 'the two centres are 34 px apart: 10 px of free line').toBeCloseTo(34, 0)
    await page.mouse.move(5, 5)
    await page.screenshot({ path: join(dir, `${theme}-direction-close-frame.png`) })
    await pairCut(page, dir, `${theme}-direction-close-dragged`, DIRECTION_PAIRS.close)
  })

  test(`039 B2 frames [${theme}] - a selected participant and a hovered line of it`, async ({ page }) => {
    const dir = outDir()
    await bootThemed(page, directionSnapshot(), theme)
    const n = await screenNodes(page)
    const [a, b] = [n.get('a')!, n.get('b')!]
    await page.mouse.click(a.x, a.y)
    await page.mouse.move((a.x + b.x) / 2, (a.y + b.y) / 2)
    await waitLoopAsleep(page)
    await page.screenshot({ path: join(dir, `${theme}-direction-selected-frame.png`) })
    await pairCut(page, dir, `${theme}-direction-selected`, DIRECTION_PAIRS.oneWayDebt)
  })

  test(`039 B2 frames [${theme}] - a payment travelling debtor > creditor over the line, and a clearing highlight of the mutual pair`, async ({ page }) => {
    const dir = outDir()
    await boot(page, { snapshot: directionSnapshot(), clock: true, query: `&theme=${theme}` })
    await expect(page.locator('.root'), 'the theme of the frame is the theme on screen').toHaveAttribute('data-theme', theme)
    // b pays a: the beam runs b > a, AGAINST the line a > b under which b's debt to a arises.
    await pushEvent(page, txUpdated(['b', 'a'], '10.00', { amount_flyout: false }))
    await frames(page, 12)
    await pairCut(page, dir, `${theme}-direction-payment`, DIRECTION_PAIRS.oneWayDebt)
    await page.screenshot({ path: join(dir, `${theme}-direction-payment-frame.png`) })
    await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-frames', cleared_cycles: 1, cleared_amount: '5.00', cycle_edges: [{ from: 'c', to: 'd' }, { from: 'd', to: 'c' }] })
    await frames(page, 30)
    await pairCut(page, dir, `${theme}-direction-clearing`, DIRECTION_PAIRS.mutual)
    await frames(page, 300) // 4.8 s more: the pulse is over, the highlight of the lines is in its fade
    await pairCut(page, dir, `${theme}-direction-clearing-fading`, DIRECTION_PAIRS.mutual)
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
