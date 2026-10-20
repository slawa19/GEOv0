/**
 * 038 S2 - windows and menus by the named matrix (F-038-5; replaces the S0 measurements R6 and R8).
 *
 * In each theme the app offers (`saas`, the default, and `hud`; 039 B3, 2026-10-16 - until then `hud` alone here and
 * four themes in the gallery) every entry is opened by its gesture and gets: the tie to the catalogue, its rectangle
 * against the viewport at 1366x768, opened anew at 1920x1080 and resized down to 1024x640, the size of its controls,
 * axe, and the policies its code declares (Escape, a click outside, where the focus returns, Tab). In the same two
 * themes: a frame on a settled graph and axe - the gallery a human looks through. The frozen `shadcn` and `library`
 * (owner's decision R1) are not walked. Tests tagged `@extended` (the gallery, the other
 * viewports of surfaces placed by CSS, the measurement of what the HUD covers) are not part of the required gate.
 *
 * What a green run does NOT say: that a surface looks right (the frames are for a human), that its contrast is
 * enough (axe leaves it undecided over a canvas), anything about states the recipe does not reach, native `<select>`
 * popups, Firefox, WebKit, touch, a real backend. `docs/ru/simulator/frontend/docs/qa-stand.md` has the full list.
 */
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page, type TestInfo } from '@playwright/test'

import { boot, openDetails, ringSnapshot, screenNodes } from './stand.js'
import { AXE_BASELINE, AXE_WHY, KNOWN_FINDINGS } from './surfaceBaseline.js'
import {
  bootEntry, catalogueKeys, claims, emptyCanvasPoint, EXCLUDED_FROM_MATRIX, freeEdge, MATRIX, settled, SURFACE_DOM, THEMES, unmatchedInstances,
  VIEWPORT_LARGE, VIEWPORT_MAIN, VIEWPORT_RESIZED, type Box, type Entry, type Theme, type Viewport,
} from './surfaceMatrix.js'

const note = (info: TestInfo, row: Record<string, unknown>) => {
  console.log('[038 S2]', JSON.stringify(row))
  info.annotations.push({ type: '038-S2', description: JSON.stringify(row) })
}

// --- the tie to the catalogue, side (a): no run of the app needed ---------------------------------------------------

test('matrix <-> catalogue: every catalogue key is in the matrix or excluded by name, and the matrix names no key the catalogue lacks', async () => {
  const keys = await catalogueKeys()
  expect(keys.length, 'counter-check: the catalogue was read').toBeGreaterThan(0)
  const inMatrix = new Set(MATRIX.map((e) => e.catalogue))
  const unknown = [...inMatrix, ...SURFACE_DOM.map((d) => d.key).filter((k): k is string => k !== null), ...Object.keys(EXCLUDED_FROM_MATRIX)]
    .filter((k) => !keys.includes(k))
  expect([...new Set(unknown)], 'a key used by the matrix, by SURFACE_DOM or by the exclusions is not in overlaySurfaceCatalog.ts: '
    + 'either the catalogue lost an entry (restore it) or the matrix is stale (update surfaceMatrix.ts)').toEqual([])
  const uncovered = keys.filter((k) => !inMatrix.has(k) && !(k in EXCLUDED_FROM_MATRIX))
  expect(uncovered, 'a catalogue key has neither an entry of MATRIX nor a named, dated exclusion in EXCLUDED_FROM_MATRIX').toEqual([])
  expect(keys.filter((k) => inMatrix.has(k) && k in EXCLUDED_FROM_MATRIX), 'a key is both covered and excluded').toEqual([])
  for (const [k, x] of Object.entries(EXCLUDED_FROM_MATRIX)) expect(x.why.length > 0 && /^\d{4}-\d{2}-\d{2}$/.test(x.since), `exclusion of ${k} has a reason and a date`).toBe(true)
  expect(new Set(MATRIX.map((e) => e.id)).size, 'entry ids are unique').toBe(MATRIX.length)
  console.log('[038 S2]', JSON.stringify({ catalogue: keys.length, entries: MATRIX.length, perKey: keys.map((k) => `${k}:${MATRIX.filter((e) => e.catalogue === k).length}`) }))
})

test('matrix <-> baseline: every baselined finding and axe rule belongs to an entry and has a reason', async () => {
  const ids = new Set([...MATRIX.map((e) => e.id), 'page'])
  expect([...Object.keys(KNOWN_FINDINGS), ...Object.keys(AXE_BASELINE)].filter((id) => !ids.has(id)), 'a baseline line of an entry that no longer exists').toEqual([])
  for (const list of Object.values(KNOWN_FINDINGS)) {
    for (const k of list) expect(k.why.length > 10 && /^\d{4}-\d{2}-\d{2}$/.test(k.since), `"${k.finding}" has a reason and a date`).toBe(true)
  }
  const rules = new Set(Object.values(AXE_BASELINE).flatMap((t) => Object.values(t).flatMap((r) => Object.keys(r ?? {}))))
  expect([...rules].filter((r) => !(r in AXE_WHY)), 'a baselined axe rule without a reason in AXE_WHY').toEqual([])
  expect(Object.keys(AXE_WHY).filter((r) => !rules.has(r)), 'a reason for a rule that is no longer baselined').toEqual([])
})

// --- measurements ---------------------------------------------------------------------------------------------------

/** The tie to the catalogue, side (b): what is on screen is claimed, under a key the catalogue has, and the entry's root is of the entry's type. */
async function assertClaimed(page: Page, e: Entry, keys: string[]): Promise<void> {
  const all = await claims(page)
  expect(all.length, 'counter-check: the scan saw the scene and the bars').toBeGreaterThan(3)
  expect(all.filter((c) => c.key === undefined).map((c) => c.el),
    'an element laid over the scene is claimed by no line of SURFACE_DOM: a surface without a catalogue entry. '
    + 'Add it to overlaySurfaceCatalog.ts and to the matrix, or name it in SURFACE_DOM as not a surface').toEqual([])
  expect(all.filter((c) => typeof c.key === 'string' && !keys.includes(c.key)).map((c) => `${c.el} -> ${String(c.key)}`),
    'a surface on screen is of a type that overlaySurfaceCatalog.ts no longer has').toEqual([])
  expect(await unmatchedInstances(page), 'a menu of the bars, a window or an OverlaySelect list is in the DOM and no entry of MATRIX opens it. '
    + 'Add an entry to surfaceMatrix.ts (a list of the same component may be named in SELECTS_WITHOUT_ENTRY instead)').toEqual([])
  const own = await page.locator(e.root).first().evaluate((el, dom) => {
    for (let cur: Element | null = el; cur; cur = cur.parentElement) {
      const hit = dom.find((d) => d.key !== null && cur!.matches(d.selector))
      if (hit) return hit.key
    }
    return null
  }, SURFACE_DOM)
  expect(own, `${e.id}: the element the recipe opened is a "${e.catalogue}" by the DOM`).toBe(e.catalogue)
}

async function insideFindings(page: Page, e: Entry, box: Box, vp: Viewport): Promise<{ findings: string[]; box: Box; pad: number }> {
  if (e.policies.inside === null) return { findings: [], box, pad: 0 }
  const pad = e.policies.inside === 'wm-pad'
    ? await page.locator('.root').evaluate((el) => Number.parseFloat(getComputedStyle(el).getPropertyValue('--ds-wm-clamp-pad')))
    : 0
  expect(Number.isFinite(pad), 'the clamp pad of the window manager was read from the page').toBe(true)
  const at = `${vp.width}x${vp.height}`
  const eps = 0.5
  const f: string[] = []
  if (box.x < pad - eps) f.push(`outside:left@${at}`)
  if (box.y < pad - eps) f.push(`outside:top@${at}`)
  if (box.x + box.width > vp.width - pad + eps) f.push(`outside:right@${at}`)
  if (box.y + box.height > vp.height - pad + eps) f.push(`outside:bottom@${at}`)
  return { findings: f, box, pad }
}

/** Controls a pointer can hit, smaller than 24x24 CSS px (WCAG 2.2 "Target Size (Minimum)"; disabled controls are not targets). */
async function smallTargets(page: Page, e: Entry): Promise<{ checked: number; small: Array<{ key: string; size: string }> }> {
  return page.locator(e.root).first().evaluate((root) => {
    const small: Array<{ key: string; size: string }> = []
    let checked = 0
    const sel = 'button, a[href], select, input:not([type="hidden"]), textarea, summary, [role="option"], [role="button"]'
    for (const el of root.querySelectorAll<HTMLElement>(sel)) {
      if ((el as HTMLButtonElement).disabled) continue
      const r = el.getBoundingClientRect()
      const s = getComputedStyle(el)
      if (r.width === 0 || r.height === 0 || s.visibility === 'hidden' || s.display === 'none') continue
      checked += 1
      if (r.width >= 23.95 && r.height >= 23.95) continue
      const name = el.dataset.testid ?? el.getAttribute('aria-label') ?? el.id ?? ''
      const label = name || (el.textContent ?? '').trim().replace(/\s+/g, ' ').slice(0, 24)
      small.push({ key: `small-target:${el.tagName.toLowerCase()}[${label}]`, size: `${Math.round(r.width * 10) / 10}x${Math.round(r.height * 10) / 10}` })
    }
    return { checked, small }
  })
}

type AxeRow = { counts: Record<string, number>; targets: string[]; incomplete: string[]; passes: number }

/**
 * axe on one surface (or on the page). Under a paused clock axe's own timers do not fire, so the fake clock is
 * stepped in 4 ms slices until the scan answers; the surface lives through that (the scan needs well under a second).
 */
async function axe(page: Page, include: string | null, clock: boolean): Promise<AxeRow> {
  // Legacy mode: axe finishes in this page. Otherwise it opens and closes a blank page of its own, the fake clock is
  // the context's, and a step of the clock that meets the closing page dies with "Target page ... has been closed"
  // (seen once in about twenty runs, toast-error; 038 S2). There are no cross-origin frames here for the other mode to serve.
  let b = new AxeBuilder({ page }).setLegacyMode(true).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
  if (include) b = b.include(include)
  let done = false
  const scan = b.analyze().finally(() => { done = true })
  if (clock) for (let i = 0; i < 300 && !done; i += 1) await page.clock.runFor(4)
  const r = await scan
  const counts: Record<string, number> = {}
  const targets: string[] = []
  for (const v of r.violations) {
    if (v.impact !== 'serious' && v.impact !== 'critical') continue
    counts[v.id] = (counts[v.id] ?? 0) + v.nodes.length
    for (const n of v.nodes) targets.push(`${v.impact} ${v.id}: ${n.target.join(' ')}`)
  }
  expect(r.passes.length, 'counter-check: axe did evaluate rules here').toBeGreaterThan(0)
  return { counts, targets, incomplete: r.incomplete.map((v) => `${v.id}x${v.nodes.length}`), passes: r.passes.length }
}

/** A frame for a human: a file in the output folder of the test and an attachment of the standard HTML report. */
async function frame(page: Page, info: TestInfo, name: string): Promise<void> {
  const path = info.outputPath(`${name}.png`)
  await page.screenshot({ path })
  await info.attach(`${name}.png`, { path, contentType: 'image/png' })
}

function expectAxe(id: string, theme: Theme, row: AxeRow): void {
  expect.soft(row.counts, `axe serious/critical on "${id}" in theme ${theme} differs from AXE_BASELINE (surfaceBaseline.ts). `
    + `More: a new violation - fix it or record it with a reason. Fewer: it got better - lower the constant, with a dated line. Nodes: ${JSON.stringify(row.targets)}`)
    .toEqual(AXE_BASELINE[id]?.[theme] ?? {})
}

const activeInside = (page: Page, root: string) =>
  page.evaluate((sel) => { const r = document.querySelector(sel); return !!r && r.contains(document.activeElement) }, root)

const activeIs = (page: Page, sel: string) =>
  page.evaluate((s) => (s === 'body' ? document.activeElement === document.body : !!document.activeElement?.matches(s)), sel)

const activeName = (page: Page) =>
  page.evaluate(() => { const a = document.activeElement; return a ? `${a.tagName.toLowerCase()}#${a.id}.${[...a.classList].join('.')}[${a.getAttribute('aria-label') ?? (a as HTMLElement).dataset?.testid ?? ''}]` : 'null' })

/** The click that is "outside" for this surface: the empty canvas, or the place its policy names. */
async function clickOutside(page: Page, e: Entry): Promise<void> {
  const at = e.policies.outsideClick?.at
  if (at) {
    await page.locator(at).click({ position: { x: 6, y: 6 } })
    return
  }
  const p = await emptyCanvasPoint(page)
  await page.mouse.click(p.x, p.y)
}

/** Close the surface the way its own policy says a user can: a click on the empty canvas where that closes it, Escape otherwise. */
async function closeIt(page: Page, e: Entry): Promise<void> {
  const root = page.locator(e.root).first()
  if (!(await root.isVisible())) return
  if (e.policies.outsideClick?.closes) {
    await clickOutside(page, e)
  } else {
    await page.keyboard.press('Escape')
  }
  await expect(root, `${e.id}: closes before it is opened anew`).toBeHidden()
}

/**
 * The declared policies, on a surface that is open. Order: a click outside, then Tab, then Escape pressed right after
 * a fresh gesture (the focus is wherever the gesture left it). Escape is last, and nothing is opened after it: see the
 * reproducer of the trust line popup below.
 */
async function checkPolicies(page: Page, e: Entry): Promise<Record<string, unknown>> {
  const p = e.policies
  const seen: Record<string, unknown> = {}
  const root = page.locator(e.root).first()
  const openAnew = async () => { await closeIt(page, e); await e.open(page); await settled(page, e) }

  if (p.outsideClick) {
    await clickOutside(page, e)
    if (p.outsideClick.closes) await expect(root, `${e.id}: a click on the empty canvas closes it (${p.outsideClick.declared})`).toBeHidden()
    else await expect(root, `${e.id}: a click on the empty canvas leaves it open (${p.outsideClick.declared})`).toBeVisible()
    if (p.outsideClick.at) await expect(page.locator(p.outsideClick.at), `: the click outside closed this surface only`).toBeVisible()
    seen.outside = p.outsideClick.closes ? 'closed' : 'stayed'
  }

  if (p.tabStaysInside) {
    await openAnew()
    if (p.tabStaysInside.enterWith && !(await activeInside(page, e.root))) {
      await page.keyboard.press(p.tabStaysInside.enterWith)
      await expect.poll(() => activeInside(page, e.root), { message: `${e.id}: ${p.tabStaysInside.enterWith} on the trigger takes the focus into the list` }).toBe(true)
    }
    const stops = await root.evaluate((el) => el.querySelectorAll('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled])').length)
    const presses = Math.min(stops, 10) + 2
    for (const key of ['Tab', 'Shift+Tab']) {
      for (let i = 0; i < presses; i += 1) {
        await page.keyboard.press(key)
        expect(await activeInside(page, e.root), `${e.id}: ${key} #${i + 1} left the surface, the focus is on ${await activeName(page)} (${p.tabStaysInside.declared})`).toBe(true)
      }
    }
    seen.tab = `${presses} x Tab and Shift+Tab stayed inside (${stops} stops)`
  }

  if (p.escape) {
    await openAnew()
    // Pressed right after the gesture: a user who opened a list with the mouse has the focus on its trigger, not inside.
    const focusWas = await activeName(page)
    await page.keyboard.press('Escape')
    await expect(root, `${e.id}: Escape closes it (${p.escape.declared})`).toBeHidden()
    // Two frames: a window closed by the same key is still in the DOM while it fades, and the focus has already left it.
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))))
    expect(await activeIs(page, p.escape.focusBackTo), `${e.id}: after Escape (pressed with the focus on ${focusWas}) the focus is on `
      + `"${p.escape.focusBackTo}"; it is on ${await activeName(page)} (${p.escape.declared})`).toBe(true)
    if (p.escape.keeps) await expect(page.locator(p.escape.keeps), `${e.id}: Escape closed this surface only, "${p.escape.keeps}" stays`).toBeVisible()
    seen.escape = `closed, focus on ${p.escape.focusBackTo}${p.escape.keeps ? ', host stays' : ''}`
  }
  return seen
}

/**
 * The other two viewports - opened anew at 1920x1080, resized down to 1024x640 - are part of the REQUIRED test only for
 * the menus of the bars and the lists of OverlaySelect: their place is computed by script at the moment they open, and
 * the check costs them about a second. For a window of the window manager it costs several seconds (after every resize
 * the graph is laid out again, and the recipes click the canvas), and the required gate has a time budget; its clamp
 * also has unit tests (`useWindowManager.test.ts`). Windows and everything placed by CSS alone get the two viewports
 * in a test of their own, tagged `@extended`.
 */
const tourInGate = (e: Entry) => e.catalogue === 'hud-dropdown' || e.catalogue === 'interact-select-dropdown'
const OTHER_VIEWPORT = /@(1920x1080|1024x640)$/

/** Opened anew on the large viewport, then resized down with the surface on screen. The surface is closed or absent on entry. */
async function otherViewports(page: Page, e: Entry, info: TestInfo, theme: Theme): Promise<{ findings: string[]; large: Box; resized: Box }> {
  await page.setViewportSize(VIEWPORT_LARGE)
  await e.open(page)
  let box = await settled(page, e)
  const large = await insideFindings(page, e, box, VIEWPORT_LARGE)
  await frame(page, info, `${e.id}-${theme}-${VIEWPORT_LARGE.width}x${VIEWPORT_LARGE.height}`)
  await page.setViewportSize(VIEWPORT_RESIZED)
  if (e.followsPointer) await e.open(page)
  box = await settled(page, e)
  const resized = await insideFindings(page, e, box, VIEWPORT_RESIZED)
  await frame(page, info, `${e.id}-${theme}-resized-${VIEWPORT_RESIZED.width}x${VIEWPORT_RESIZED.height}`)
  return { findings: [...large.findings, ...resized.findings], large: large.box, resized: resized.box }
}

function expectFindings(e: Entry, findings: string[], scope: (finding: string) => boolean): void {
  expect.soft([...findings].sort(), `findings on "${e.id}" differ from KNOWN_FINDINGS (surfaceBaseline.ts). More: a new defect - fix it, or record it for its `
    + 'recipient with a reason. Fewer: it got better - remove the line, with a dated note')
    .toEqual((KNOWN_FINDINGS[e.id] ?? []).map((k) => k.finding).filter(scope).sort())
}

for (const theme of THEMES) for (const e of MATRIX) {
  const all = tourInGate(e)
  test(`matrix [${theme}] ${e.id}: ${e.gesture} - catalogue, viewport${all ? 's' : ''}, targets, frame, axe, declared policies`, async ({ page }, info) => {
    const keys = await catalogueKeys()
    await bootEntry(page, e, theme, VIEWPORT_MAIN)
    await e.open(page)
    const box = await settled(page, e)
    await assertClaimed(page, e, keys)

    const findings: string[] = []
    const main = await insideFindings(page, e, box, VIEWPORT_MAIN)
    findings.push(...main.findings)
    const { checked: targetsChecked, small } = await smallTargets(page, e)
    findings.push(...new Set(small.map((s) => s.key)))
    await frame(page, info, `${e.id}-${theme}-${VIEWPORT_MAIN.width}x${VIEWPORT_MAIN.height}`)
    const ax = await axe(page, e.root, e.clock === true)

    let other: Awaited<ReturnType<typeof otherViewports>> | null = null
    if (all) {
      await closeIt(page, e)
      other = await otherViewports(page, e, info, theme)
      findings.push(...other.findings)
      // Policies last, back on the main viewport, on the surface as it stands after the resizes.
      await page.setViewportSize(VIEWPORT_MAIN)
      await settled(page, e)
    }
    const policies = await checkPolicies(page, e)

    note(info, {
      entry: e.id, catalogue: e.catalogue, theme, gesture: e.gesture, notAGesture: e.notAGesture ?? null,
      box: { main: main.box, large: other?.large ?? null, resized: other?.resized ?? null }, pad: main.pad, findings, targetsChecked, smallTargets: small, policies,
      axe: ax.counts, axeNodes: ax.targets, axeIncomplete: ax.incomplete,
    })
    expectFindings(e, findings, (f) => all || !OTHER_VIEWPORT.test(f))
    expectAxe(e.id, theme, ax)
  })

  if (!all && e.policies.inside !== null) {
    test(`matrix [${theme}] ${e.id}: opened at 1920x1080 and resized to 1024x640 @extended`, async ({ page }, info) => {
      await bootEntry(page, e, theme, VIEWPORT_LARGE)
      const other = await otherViewports(page, e, info, theme)
      note(info, { entry: e.id, theme, box: { large: other.large, resized: other.resized }, findings: other.findings })
      expectFindings(e, other.findings, (f) => OTHER_VIEWPORT.test(f))
    })
  }
}

// The gallery for a human: every entry in both themes, on a settled graph. The required tests above frame them
// without waiting for the graph; here their axe numbers are judged a second time against the same constant.
for (const theme of THEMES) {
  for (const e of MATRIX) {
    test(`matrix [${theme}] ${e.id}: frame and axe @extended`, async ({ page }, info) => {
      await bootEntry(page, e, theme, VIEWPORT_MAIN, { stillLayout: true })
      await e.open(page)
      const box = await settled(page, e)
      await frame(page, info, `gallery-${e.id}-${theme}-${VIEWPORT_MAIN.width}x${VIEWPORT_MAIN.height}`)
      const ax = await axe(page, e.root, e.clock === true)
      note(info, { entry: e.id, theme, box, axe: ax.counts, axeNodes: ax.targets, axeIncomplete: ax.incomplete })
      expectAxe(e.id, theme, ax)
    })
  }
}

for (const theme of THEMES) {
  test(`matrix [${theme}] page: axe on the whole page with nothing summoned`, async ({ page }, info) => {
    await page.setViewportSize(VIEWPORT_MAIN)
    await boot(page, { snapshot: ringSnapshot(), clock: false, query: `&theme=${theme}`, awaitLayout: false })
    await frame(page, info, `page-${theme}-${VIEWPORT_MAIN.width}x${VIEWPORT_MAIN.height}`)
    const ax = await axe(page, null, false)
    note(info, { entry: 'page', theme, axe: ax.counts, axeNodes: ax.targets, axeIncomplete: ax.incomplete })
    expectAxe('page', theme, ax)
  })
}

// --- counter-checks of the instruments (anti-vacuum): each of them does report what it is there to report -----------

test('counter-check: the instruments of the matrix see a shrunk control, a window pushed out of the viewport and an unclaimed surface', async ({ page }) => {
  const e = MATRIX.find((x) => x.id === 'clearing-panel')!
  await bootEntry(page, e, 'hud', VIEWPORT_MAIN)
  await e.open(page)
  const box = await settled(page, e)
  expect((await insideFindings(page, e, box, VIEWPORT_MAIN)).findings, 'the window as the product places it').toEqual([])
  expect((await smallTargets(page, e)).small, 'the controls as the product draws them').toEqual([])

  await page.locator('[data-testid="clearing-cancel"]').evaluate((el) => { (el as HTMLElement).style.cssText += ';height:20px;min-height:0;padding:0' })
  expect((await smallTargets(page, e)).small.map((s) => s.key), 'a control made 20 px high is reported').toEqual(['small-target:button[clearing-cancel]'])

  const moved = { ...box, x: VIEWPORT_MAIN.width - box.width - 2 }
  expect((await insideFindings(page, e, moved, VIEWPORT_MAIN)).findings, 'a window 2 px from the right edge is inside the viewport but not inside the pad').toEqual(['outside:right@1366x768'])

  await page.evaluate(() => {
    const el = document.createElement('div')
    el.className = 'qa-uncatalogued'
    el.style.cssText = 'position:absolute;left:40px;top:300px;width:80px;height:40px'
    document.querySelector('.root')!.append(el)
  })
  expect((await claims(page)).filter((c) => c.key === undefined).map((c) => c.el), 'an element laid over the scene that no catalogue type claims is reported').toEqual(['div.qa-uncatalogued'])
})

// The four injections of the adversarial pass on a7f7e1a6 (038 S2, D3), each with what the scan answers. Three are
// seen since then; the fourth is the named limit of the scan, kept here so that the limit is a fact and not a hope.
test('counter-check: injected surfaces - a stray child of the window layer, a 0x0 portal with a fixed child and a fifth menu are reported; a popover inside the top bar is NOT (the named limit)', async ({ page }) => {
  const e = MATRIX.find((x) => x.id === 'bottom-bar')!
  await bootEntry(page, e, 'hud', VIEWPORT_MAIN)
  const unclaimed = async () => (await claims(page)).filter((c) => c.key === undefined).map((c) => c.el)
  expect(await unclaimed(), 'the page as the product draws it').toEqual([])
  expect(await unmatchedInstances(page), 'the page as the product draws it').toEqual([])
  const inject = (parent: string, html: string) => page.evaluate(({ parent, html }) => { document.querySelector(parent)!.insertAdjacentHTML('beforeend', html) }, { parent, html })
  const remove = (sel: string) => page.evaluate((s) => { document.querySelector(s)!.remove() }, sel)
  const box = 'position:absolute;left:40px;top:300px;width:80px;height:40px'

  await inject('.wm-layer', `<div class="qa-in-wm" style="${box}">x</div>`)
  expect(await unclaimed(), 'a visible child of the window layer that is not a window shell').toEqual(['div.qa-in-wm'])
  await remove('.qa-in-wm')

  await inject('body', '<div class="qa-portal" style="width:0;height:0"><div style="position:fixed;left:40px;top:300px;width:80px;height:40px">x</div></div>')
  expect(await unclaimed(), 'a 0x0 portal in body whose fixed child is on screen').toEqual(['div.qa-portal'])
  await remove('.qa-portal')

  await inject('.ds-ov-bottom .hud-bar__right', '<details class="ds-ov-details qa-fifth" aria-label="Fifth"><summary>Fifth</summary><div class="ds-panel ds-ov-surface ds-ov-dropdown">x</div></details>')
  expect(await unclaimed(), 'a fifth menu is a hud-dropdown by its type: the scan of types does not object').toEqual([])
  expect(await unmatchedInstances(page), 'the scan of instances does: no entry of the matrix opens it').toEqual(['menu: details[aria-label="Fifth"]'])
  await remove('.qa-fifth')

  await inject('.ds-ov-top', `<div class="qa-popover" style="${box}">x</div>`)
  expect(await unclaimed(), 'NOT SEEN: an arbitrary descendant of a claimed container (qa-stand.md, section 5)').toEqual([])
  expect(await unmatchedInstances(page)).toEqual([])
})

// --- one declared policy the matrix recipe cannot show: the focus goes back to a keyboard-reachable opener -----------

test('matrix [hud] node-card opened from the graph navigator: Escape returns the focus to "Inspect node"', async ({ page }) => {
  await page.setViewportSize(VIEWPORT_MAIN)
  // Pinned: since 039 B3 the default theme is saas, and the title promises hud (external review 2026-10-16).
  await boot(page, { snapshot: ringSnapshot(), clock: false, query: '&theme=hud' })
  await openDetails(page) // 043 S1: "Inspect graph" is a button of the bottom row, behind "Подробнее"
  await page.locator('.graph-navigator summary').click()
  const opener = page.getByTestId('graph-navigator-open-node')
  await opener.focus()
  await page.keyboard.press('Enter')
  const card = page.locator('.ws-shell[data-win-type="node-card"]')
  await expect(card).toBeVisible()
  // The focus is taken off the opener first: left on it, "the focus is on the opener after Escape" would hold with no
  // restore at all (038 S2, D5a).
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
  await expect(opener, 'counter-check: the focus is not on the opener before the card is closed').not.toBeFocused()
  await page.keyboard.press('Escape')
  await expect(card, 'Escape closes the card (useWindowManager.ts getPolicy: escBehavior close)').toBeHidden()
  await expect(opener, 'the focus is back on the control that opened the card (useWindowManager.ts restoreFocusAfterClose)').toBeFocused()
})

// --- the keyboard policy of a HUD menu --------------------------------------------------------------------------------
// 048 S2 (amendment 9, 2026-10-19): "Admin ▾", the last menu of the bars, is gone - and with it its two tests here:
// "matrix [hud] menu-admin: when its focused control is disabled under the focus (runs are loading), Escape still closes
// the menu and Tab goes back inside" and "matrix [hud] menu-admin left open, then a node card opened from the canvas:
// Escape and Tab belong to the card, not to the menu" (a removed contract: no menu is left to open). The policy itself
// stays unit-tested (`useHudDropdownFocus.test.ts`), and the scan of instances still reports a menu nobody opens (the
// injected "fifth menu" above).

// --- BACKLOG 037-4 (к): fixed by 048 T4840 (PR #329, 2026-10-20) - this reproducer passes, the `test.fail` annotation
// is removed (the dispatch run 39382382179 reported «Expected to fail, but passed»); it now guards the fix. ---------

test('reproducer (BACKLOG 037-4, Interact): Escape on the trust line popup releases the flow, and the same line opens the popup again @extended', async ({ page }) => {
  await page.setViewportSize(VIEWPORT_MAIN)
  await boot(page, { snapshot: ringSnapshot(), clock: false })
  const popup = page.locator('.ws-shell[data-win-type="edge-detail"]')
  const edge = await freeEdge(page)
  await page.mouse.click(edge.at.x, edge.at.y)
  await expect(popup, 'counter-check: a click on the line opens its popup').toBeVisible()
  await page.keyboard.press('Escape')
  await expect(popup, 'counter-check: Escape closes the popup').toBeHidden()

  // What is on screen now: no window, yet the action bar is locked and says "Cancel current action first (press ESC)".
  await expect.soft(page.locator('[data-testid="actionbar-locked-hint"]'), 'with no window on screen no flow is held: the action bar is not locked').toBeHidden({ timeout: 1000 })
  await page.keyboard.press('Escape')
  await expect.soft(page.locator('[data-testid="actionbar-payment"]'), 'the Escape the hint asks for frees the action bar').toBeEnabled({ timeout: 1000 })
  await page.mouse.click(edge.at.x, edge.at.y)
  await expect.soft(popup, 'a click on the same line opens its popup again').toBeVisible({ timeout: 1000 })
})

// --- a measurement for the owner: how much of the scene the HUD covers ---------------------------------------------

for (const vp of [VIEWPORT_MAIN, { width: 1280, height: 720 }]) {
  test(`measurement: what lies under the HUD at ${vp.width}x${vp.height} (Interact, nothing summoned, navigator collapsed and expanded) @extended`, async ({ page }, info) => {
    await page.setViewportSize(vp)
    await boot(page, { snapshot: ringSnapshot(), clock: false })
    const measure = async () => {
      const nodes = [...(await screenNodes(page)).entries()].map(([id, at]) => ({ id, ...at }))
      return page.evaluate(({ nodes, w, h }) => {
        const step = 4
        let total = 0
        let covered = 0
        const by: Record<string, number> = {}
        for (let y = step / 2; y < h; y += step) {
          for (let x = step / 2; x < w; x += step) {
            total += 1
            const el = document.elementFromPoint(x, y)
            if (!el || el instanceof HTMLCanvasElement) continue
            covered += 1
            const owner = el.closest('.graph-navigator, .action-bar, .ds-ov-bottom, .ds-ov-top-stack > *, .ds-ov-top')
            const name = owner ? `${owner.tagName.toLowerCase()}.${[...owner.classList].slice(0, 2).join('.')}` : el.tagName.toLowerCase()
            by[name] = (by[name] ?? 0) + 1
          }
        }
        const rect = (sel: string) => { const r = document.querySelector(sel)?.getBoundingClientRect(); return r ? { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } : null }
        return {
          coveredShare: Math.round((covered / total) * 1000) / 10,
          shareBy: Object.fromEntries(Object.entries(by).map(([k, v]) => [k, Math.round((v / total) * 1000) / 10])),
          navigator: rect('.graph-navigator'), topStack: rect('.ds-ov-top .ds-ov-top-stack'), bottomBar: rect('.ds-ov-bottom'),
          nodesUnderHud: nodes.filter((n) => !(document.elementFromPoint(n.x, n.y) instanceof HTMLCanvasElement)).map((n) => n.id),
          nodes: nodes.length,
        }
      }, { nodes, w: vp.width, h: vp.height })
    }
    const collapsed = await measure()
    await frame(page, info, `hud-cover-collapsed-${vp.width}x${vp.height}`)
    await openDetails(page) // 043 S1: the navigator is behind "Подробнее"; "collapsed" above is the first view
    await page.locator('.graph-navigator summary').click()
    await expect(page.locator('#graph-navigator-node')).toBeVisible()
    const expanded = await measure()
    await frame(page, info, `hud-cover-expanded-${vp.width}x${vp.height}`)
    note(info, { measurement: 'hud-cover', viewport: `${vp.width}x${vp.height}`, collapsed, expanded })
    expect(collapsed.coveredShare, 'counter-check: the HUD covers something and not everything').toBeGreaterThan(0)
    expect(collapsed.coveredShare).toBeLessThan(100)
    expect(collapsed.nodes, 'counter-check: the scene has its six nodes').toBe(6)
  })
}
