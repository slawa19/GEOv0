/**
 * 038 S2 - the NAMED matrix of user windows and menus (F-038-5). Not a test file.
 *
 * One catalogue type is not one window: `wm-interact-window` is three panels, `hud-dropdown` is four menus. An entry
 * here is what a user opens, with the gesture that opens it and the policies THE CODE declares for it - not a
 * universal list: a toast has no Escape, a `<details>` menu has no outside-click rule.
 *
 * The matrix is tied to the product's catalogue (`src/ui-kit/overlaySurfaceCatalog.ts`) in both directions, see
 * `surfaces.qa.ts`: every catalogue key is in the matrix or in `EXCLUDED_FROM_MATRIX` by name; and whatever is on
 * screen must be claimed by `SURFACE_DOM` under a key the catalogue still has.
 *
 * What this file does NOT see: popups of native `<select>` (the browser draws them outside the DOM, a screenshot does
 * not contain them) - `NATIVE_SELECTS` names them; states of a surface other than the one the recipe reaches (a
 * payment panel on its confirm or result step, an edge popup of a frozen line, a top bar with an alert row).
 */
import { expect, type Page, type Route } from '@playwright/test'

import { boot, distToSegment, EQ, frames, layoutNodes, openDetails, pausedClockTime, pushEvent, ringSnapshot, RUN_ID, screenNodes, syncCssToClock, txUpdated, type Pt } from './stand.js'

/**
 * The themes the app offers (039 B3, 2026-10-16; owner's decision R1): `saas` (the default) and `hud`. Until that date
 * four: `shadcn` and `library` were walked too; they are frozen (tokens kept, out of the switcher) and not walked.
 */
export const THEMES = ['saas', 'hud'] as const
export type Theme = (typeof THEMES)[number]

export type Viewport = { width: number; height: number }
/** The viewports of the matrix (spec 038, S2): two desktop sizes a window is OPENED at, and one it is resized down to. */
export const VIEWPORT_MAIN: Viewport = { width: 1366, height: 768 }
export const VIEWPORT_LARGE: Viewport = { width: 1920, height: 1080 }
export const VIEWPORT_RESIZED: Viewport = { width: 1024, height: 640 }

export type Policies = {
  /**
   * `wm-pad`: kept `--ds-wm-clamp-pad` away from every edge - a window of the window manager (`useWindowManager.ts`,
   * `reclamp`) and a menu of the bars (`useHudDropdownFocus.ts`); the pad is read from the page, not repeated here.
   * `viewport`: any other surface, inside the viewport. `null`: a layer stretched over the scene.
   */
  inside: 'wm-pad' | 'viewport' | null
  /**
   * Escape right after the gesture, with the focus wherever the gesture left it: THIS surface closes, the focus lands
   * on `focusBackTo` (`body` is the declared fallback), and `keeps` - the window that hosts it - stays.
   */
  escape?: { focusBackTo: string; declared: string; keeps?: string }
  /** A click on an empty place of the canvas. `closes: false` is asserted too - "stays" is a policy. */
  outsideClick?: { closes: boolean; declared: string; /** Where to click instead of the empty canvas (a list is left by a click elsewhere in its panel). */ at?: string }
  /**
   * Tab and Shift+Tab never leave the surface. `enterWith`: the key a keyboard user presses to get inside when the
   * mouse gesture leaves the focus on the trigger (a list opened with the mouse).
   */
  tabStaysInside?: { declared: string; enterWith?: string }
}

export type Entry = {
  id: string
  /** The key of `overlaySurfaceCatalog` this instance belongs to. */
  catalogue: string
  /** The element that is measured, framed and given to axe. */
  root: string
  ui: 'interact' | 'auto'
  query?: string
  /** The gesture, in words, for the report and the document. */
  gesture: string
  /** Set when the recipe is NOT a gesture of a user in the page, with the reason. */
  notAGesture?: string
  /** `true`: fake clock (the surface lives on a timer). The recipe resumes and pauses it itself. */
  clock?: true
  /** Runs after the stand's backend is routed, before the app loads. */
  beforeLoad?: (page: Page) => Promise<void>
  /** `false`: the surface exists only while there is no scene. */
  awaitScene?: false
  open: (page: Page) => Promise<void>
  /** The surface exists only under the pointer: after a resize the line has moved from under it, and the gesture is repeated. */
  followsPointer?: true
  /**
   * 043 S1: the surface (or its opener) is a service control, behind "Подробнее". `bootEntry` first holds that
   * `hiddenByDefault` is NOT RENDERED on the first view (the anti-vacuum of the recipe: a surface reachable by default
   * fails here), then turns "Подробнее" on and holds that it is on screen.
   */
  details?: { hiddenByDefault: string }
  policies: Policies
}

// --- gestures on the canvas ---------------------------------------------------------------------------------------

/**
 * Physics has stopped moving nodes (to a pixel, two equal reads in a row). The matrix boots without waiting for it -
 * a menu does not need a still graph - so every gesture ON the canvas waits here first. A condition, not a sleep.
 * NOT the same as `waitLayoutStill` of `stand.ts` (to 0.1 px, 3 reads, every 150 ms): this one is coarser and sooner.
 */
async function layoutStill(page: Page): Promise<void> {
  let prev = ''
  let same = 0
  await expect
    .poll(async () => {
      const cur = JSON.stringify((await layoutNodes(page)).map((n) => [n.id, Math.round(n.x), Math.round(n.y)]))
      same = cur === prev && cur !== '[]' ? same + 1 : 0
      prev = cur
      return same
    }, { timeout: 30_000, intervals: [100] })
    .toBeGreaterThanOrEqual(2)
}

async function isCanvasAt(page: Page, p: Pt): Promise<boolean> {
  return page.evaluate(({ x, y }) => document.elementFromPoint(x, y) instanceof HTMLCanvasElement, p)
}

/** A node whose centre is not under the HUD or an open window: a click there reaches the canvas. */
export async function freeNode(page: Page): Promise<{ id: string; at: Pt }> {
  await layoutStill(page)
  const nodes = await screenNodes(page)
  for (const [id, at] of nodes) if (await isCanvasAt(page, at)) return { id, at }
  throw new Error('038 matrix: every node of the ring is under a DOM surface - the recipe has nothing to click')
}

/** A trust line whose middle and whose source node both reach the canvas. */
export async function freeEdge(page: Page): Promise<{ from: string; to: string; at: Pt }> {
  await layoutStill(page)
  const nodes = await screenNodes(page)
  for (const l of ringSnapshot().links) {
    const [a, b] = [nodes.get(l.source)!, nodes.get(l.target)!]
    const at = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
    if ((await isCanvasAt(page, at)) && (await isCanvasAt(page, a))) return { from: l.source, to: l.target, at }
  }
  throw new Error('038 matrix: the middle of every trust line is under a DOM surface')
}

/**
 * A point of the canvas away from every node and line: a click there is "outside" for the product.
 * NOT the same as `emptyPoint` of `drag.qa.ts` (the point FARTHEST from every node in a fixed box, lines and DOM not asked):
 * this one is the first point 60 px from nodes and 30 px from lines that hit-tests as the canvas.
 */
export async function emptyCanvasPoint(page: Page): Promise<Pt> {
  await layoutStill(page) // a node that is still drifting could arrive under the click
  const nodes = await screenNodes(page)
  const vp = page.viewportSize()!
  const links = ringSnapshot().links.map((l) => [nodes.get(l.source)!, nodes.get(l.target)!] as const)
  const candidates: Pt[] = []
  for (let y = vp.height - 120; y > 80; y -= 24) {
    for (let x = vp.width - 60; x > 40; x -= 24) {
      const p = { x, y }
      if ([...nodes.values()].some((n) => Math.hypot(n.x - x, n.y - y) < 60)) continue
      if (links.some(([a, b]) => distToSegment(p, a, b) < 30)) continue
      candidates.push(p)
    }
  }
  const hit = await page.evaluate((pts) => pts.find(({ x, y }) => document.elementFromPoint(x, y) instanceof HTMLCanvasElement) ?? null, candidates)
  if (!hit) throw new Error('038 matrix: no empty point of the canvas is reachable')
  return hit
}

// --- backend pieces the stand's mock does not have ----------------------------------------------------------------

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })

async function routeClearing(page: Page, kind: 'cycles' | 'refused'): Promise<void> {
  await page.route(`**/simulator/runs/${RUN_ID}/actions/clearing-real`, (r) =>
    kind === 'refused'
      ? json(r, { code: 'CLEARING_REFUSED', message: 'refused', details: { reason: 'occurrence_amount_not_in_step' } }, 409)
      : json(r, {
          ok: true, equivalent: EQ, cleared_cycles: 1, total_cleared_amount: '7.00', client_action_id: null,
          cycles: [{ cleared_amount: '7.00', edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }] }],
        }))
}

async function routeAnalytics(page: Page): Promise<void> {
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/metrics`, 'i'), (r) =>
    json(r, { api_version: 'simulator-api/1', run_id: RUN_ID, equivalent: EQ, from_ms: 0, to_ms: 0, step_ms: 1000, series: [] }))
  await page.route(new RegExp(`/simulator/runs/${RUN_ID}/bottlenecks`, 'i'), (r) =>
    json(r, { api_version: 'simulator-api/1', run_id: RUN_ID, equivalent: EQ, items: [] }))
}

async function routeToolbars(page: Page): Promise<void> {
  await page.route(`**/simulator/runs/${RUN_ID}/artifacts`, (r) => json(r, { items: [] }))
  await page.route('**/simulator/admin/runs', (r) => json(r, { items: [] }))
}

/** Scene reads that never answer (`hold`) or answer 500: the two states without a scene. */
async function routeScene(page: Page, how: 'hold' | 'fail'): Promise<void> {
  await page.route(/\/graph\/(snapshot|preview)/i, (r) =>
    how === 'hold'
      ? new Promise<void>(() => { /* answered never; Playwright drops the route with the page */ })
      : json(r, { code: 'QA_STAND_NO_SCENE', message: 'qa stand: this scene cannot be loaded' }, 500))
}

// --- recipes ------------------------------------------------------------------------------------------------------

const click = (selector: string) => async (page: Page) => { await page.locator(selector).click() }

async function openList(page: Page, panelButton: string, id: string): Promise<void> {
  if (!(await page.locator('.ws-shell[data-win-type="interact-panel"]').isVisible())) await page.locator(panelButton).click()
  await expect(page.locator(`#${id}__trigger`)).toBeVisible()
  if (!(await page.locator(`#${id}__surface`).isVisible())) await page.locator(`#${id}__trigger`).click()
}

/** A summoned HUD menu: `<details>` driven by `useHudDropdownFocus`. (Named `openDetails` until 043 S1 - the element, not the toggle.) */
const openMenu = (summary: string) => async (page: Page) => {
  const s = page.locator(summary)
  if ((await s.evaluate((el) => (el.parentElement as HTMLDetailsElement).open)) === false) await s.click()
}

/** Run a clearing through the panel: the answer of the backend is what puts a toast and a history line on screen. */
async function runClearing(page: Page, toast: string): Promise<void> {
  await page.clock.resume() // the flow is driven by real clicks; the toast is frozen below, before its timer fires
  await page.locator('[data-testid="actionbar-clearing"]').click()
  await page.locator('[data-testid="clearing-confirm"]').click()
  await expect(page.locator(toast)).toBeVisible()
  await expect.poll(() => page.locator(toast).evaluate((el) => getComputedStyle(el).opacity)).toBe('1')
  // `pauseAt` cannot go back, and the clock is running: a quarter of a second ahead of the read, far from the toast's 2.5 s.
  await page.clock.pauseAt(pausedClockTime((await page.evaluate(() => Date.now())) + 250))
}

const HUD_DROPDOWN: Policies = {
  inside: 'wm-pad', // the menu is shifted in by the same pad (useHudDropdownFocus.ts, clampSurfaceIntoViewport)
  escape: { focusBackTo: '', declared: 'useHudDropdownFocus.ts: onDetailsKeydown -> closeAndRestoreFocus' },
  tabStaysInside: { declared: 'dropdownFocusCore.ts: trapDropdownTabNavigation' },
}
const hudDropdown = (summary: string): Policies => ({ ...HUD_DROPDOWN, escape: { ...HUD_DROPDOWN.escape!, focusBackTo: summary } })

const INTERACT_PANEL = (opener: string): Policies => ({
  inside: 'wm-pad',
  escape: { focusBackTo: opener, declared: 'useWindowManager.ts getPolicy: escBehavior back-then-close; SimulatorAppRoot.vue captureInteractFlowOpener' },
  outsideClick: { closes: true, declared: 'useSimulatorApp.ts __selectNodeFromCanvasStep0: an empty click cancels the flow being filled in' },
  tabStaysInside: { declared: 'WindowShell.vue onShellKeydown (interact-panel only)' },
})

const INSPECTOR: Policies = {
  inside: 'wm-pad',
  escape: { focusBackTo: 'body', declared: 'useWindowManager.ts getPolicy: escBehavior close; restoreFocusAfterClose falls back to body when the opener was the canvas' },
  outsideClick: { closes: true, declared: 'useWindowManager.ts getPolicy: closeOnOutsideClick true' },
}

const SELECT_LIST = (trigger: string): Policies => ({
  inside: 'viewport',
  outsideClick: { closes: true, at: '.ws-shell[data-win-type="interact-panel"] .ds-panel__header', declared: 'OverlaySelect.vue onGlobalPointerDown: a press outside the trigger and the list closes the list' },
  escape: { focusBackTo: trigger, keeps: '.ws-shell[data-win-type="interact-panel"]', declared: 'useOverlayDropdownFocus.ts: Escape -> closeAndRestoreFocus (the list, not its panel)' },
  tabStaysInside: { enterWith: 'ArrowDown', declared: 'useOverlayDropdownFocus.ts onSurfaceKeydown -> trapDropdownTabNavigation' },
})

export const MATRIX: Entry[] = [
  // wm-interact-window: three panels
  { id: 'payment-panel', catalogue: 'wm-interact-window', ui: 'interact', gesture: 'click "Send Payment" in the action bar',
    root: '.ws-shell:has([data-testid="manual-payment-panel"])', open: click('[data-testid="actionbar-payment"]'),
    policies: INTERACT_PANEL('[data-testid="actionbar-payment"]') },
  { id: 'trustline-panel', catalogue: 'wm-interact-window', ui: 'interact', gesture: 'click "Manage Trustline" in the action bar',
    root: '.ws-shell:has([data-testid="trustline-panel"])', open: click('[data-testid="actionbar-trustline"]'),
    policies: INTERACT_PANEL('[data-testid="actionbar-trustline"]') },
  { id: 'clearing-panel', catalogue: 'wm-interact-window', ui: 'interact', gesture: 'click "Run Clearing" in the action bar',
    root: '.ws-shell:has([data-testid="clearing-panel"])', open: click('[data-testid="actionbar-clearing"]'),
    policies: INTERACT_PANEL('[data-testid="actionbar-clearing"]') },

  // wm-inspector-window: two inspectors
  { id: 'node-card', catalogue: 'wm-inspector-window', ui: 'interact', gesture: 'double click a node on the canvas',
    root: '.ws-shell[data-win-type="node-card"]',
    open: async (page) => { const n = await freeNode(page); await page.mouse.dblclick(n.at.x, n.at.y) },
    policies: INSPECTOR },
  { id: 'edge-popup', catalogue: 'wm-inspector-window', ui: 'interact', gesture: 'click a trust line on the canvas',
    root: '.ws-shell[data-win-type="edge-detail"]',
    open: async (page) => { const e = await freeEdge(page); await page.mouse.click(e.at.x, e.at.y) },
    policies: INSPECTOR },

  // interact-select-dropdown: the list of OverlaySelect, in two panels
  { id: 'payment-from-list', catalogue: 'interact-select-dropdown', ui: 'interact', gesture: 'click the "From" list of the payment panel',
    root: '#mp-from__surface', open: (page) => openList(page, '[data-testid="actionbar-payment"]', 'mp-from'),
    policies: SELECT_LIST('#mp-from__trigger') },
  { id: 'trustline-from-list', catalogue: 'interact-select-dropdown', ui: 'interact', gesture: 'click the "From" list of the trust line panel',
    root: '#tl-from__surface', open: (page) => openList(page, '[data-testid="actionbar-trustline"]', 'tl-from'),
    policies: SELECT_LIST('#tl-from__trigger') },

  // hud-dropdown: four menus of the two bars (all four behind "Подробнее" since 043 S1)
  { id: 'menu-advanced', catalogue: 'hud-dropdown', ui: 'auto', gesture: 'click "Advanced" in the top bar (Auto-Run, "Подробнее" on)',
    details: { hiddenByDefault: 'summary[aria-label="Advanced settings"]' },
    root: 'details[aria-label="Advanced"] > .ds-ov-dropdown', open: openMenu('summary[aria-label="Advanced settings"]'),
    policies: hudDropdown('summary[aria-label="Advanced settings"]') },
  { id: 'menu-admin', catalogue: 'hud-dropdown', ui: 'interact', gesture: 'click "Admin" in the top bar (admin token, "Подробнее" on)',
    beforeLoad: routeToolbars, details: { hiddenByDefault: 'summary[aria-label="Admin"]' },
    root: 'details[aria-label="Admin controls"] > .ds-ov-dropdown', open: openMenu('summary[aria-label="Admin"]'),
    policies: hudDropdown('summary[aria-label="Admin"]') },
  { id: 'menu-artifacts', catalogue: 'hud-dropdown', ui: 'auto', gesture: 'click "Artifacts" in the bottom bar (Auto-Run, "Подробнее" on)',
    beforeLoad: routeToolbars, details: { hiddenByDefault: 'details[aria-label="Artifacts"] > summary' },
    root: 'details[aria-label="Artifacts"] > .ds-ov-dropdown', open: openMenu('details[aria-label="Artifacts"] > summary'),
    policies: hudDropdown('details[aria-label="Artifacts"] > summary') },
  { id: 'menu-dev-tools', catalogue: 'hud-dropdown', ui: 'interact', gesture: 'click "Dev Tools" in the bottom bar (dev build only, "Подробнее" on)',
    details: { hiddenByDefault: 'details[aria-label="Dev tools"] > summary' },
    root: 'details[aria-label="Dev tools"] > .ds-ov-dropdown', open: openMenu('details[aria-label="Dev tools"] > summary'),
    policies: hudDropdown('details[aria-label="Dev tools"] > summary') },

  // the two bars and what expands inside them
  { id: 'top-bar-interact', catalogue: 'top-hud-stack', ui: 'interact', gesture: 'always on screen (Interact, the first view)',
    root: '.ds-ov-top .ds-ov-top-stack', open: async () => {}, policies: { inside: 'viewport' } },
  { id: 'top-bar-interact-details', catalogue: 'top-hud-stack', ui: 'interact', gesture: 'click "Подробнее" (Interact: the row of service controls and the metrics)',
    details: { hiddenByDefault: '[data-testid="top-details-row"]' },
    root: '.ds-ov-top .ds-ov-top-stack', open: async () => {}, policies: { inside: 'viewport' } },
  { id: 'top-bar-auto-run', catalogue: 'top-hud-stack', ui: 'auto', gesture: 'click "Подробнее" (Auto-Run: scenario and run controls)',
    details: { hiddenByDefault: 'select[aria-label="Scenario"]' },
    root: '.ds-ov-top .ds-ov-top-stack', open: async () => {}, policies: { inside: 'viewport' } },
  { id: 'graph-navigator', catalogue: 'bottom-hud-stack', ui: 'interact', gesture: 'click "Inspect graph" in the bottom bar ("Подробнее" on)',
    details: { hiddenByDefault: '.graph-navigator' },
    root: '.graph-navigator',
    open: async (page) => {
      const d = page.locator('.graph-navigator > details')
      if (!(await d.evaluate((el) => (el as HTMLDetailsElement).open))) await d.locator('summary').click()
    },
    policies: { inside: 'viewport' } },
  { id: 'bottom-bar', catalogue: 'bottom-hud-stack', ui: 'interact', gesture: 'always on screen (the first view)',
    root: '.ds-ov-bottom:not(.sar-interact-history-overlay)', open: async () => {}, policies: { inside: 'viewport' } },
  { id: 'bottom-bar-details', catalogue: 'bottom-hud-stack', ui: 'interact', gesture: 'click "Подробнее" (the row of view settings and tools)',
    details: { hiddenByDefault: '[data-testid="bottom-details-row"]' },
    root: '.ds-ov-bottom:not(.sar-interact-history-overlay)', open: async () => {}, policies: { inside: 'viewport' } },

  // toasts, history, tooltip, analytics: no Escape, no focus, no outside click declared
  { id: 'toast-success', catalogue: 'success-toast', ui: 'interact', clock: true,
    gesture: 'run a clearing that clears a cycle ("Run Clearing", "Confirm")', beforeLoad: (p) => routeClearing(p, 'cycles'),
    root: '.ds-ov-toast--success', open: (page) => runClearing(page, '.ds-ov-toast--success'), policies: { inside: 'viewport' } },
  { id: 'toast-error', catalogue: 'error-toast', ui: 'interact', clock: true,
    gesture: 'run a clearing the backend refuses', beforeLoad: (p) => routeClearing(p, 'refused'),
    root: '.ds-ov-toast--error', open: (page) => runClearing(page, '.ds-ov-toast--error'), policies: { inside: 'viewport' } },
  { id: 'interact-history', catalogue: 'interact-history-overlay', ui: 'interact', clock: true,
    gesture: 'appears after the first completed action (here: a clearing)', beforeLoad: (p) => routeClearing(p, 'cycles'),
    root: '.sar-interact-history-overlay', open: (page) => runClearing(page, '.ds-ov-toast--success'), policies: { inside: 'viewport' } },
  { id: 'edge-tooltip', catalogue: 'edge-tooltip', ui: 'interact', followsPointer: true,
    gesture: 'click a node, then move the mouse over one of its trust lines', root: '.ds-ov-tooltip',
    open: async (page) => {
      // `useEdgeHover.ts`: a line answers the pointer only while one of its two nodes is selected.
      const e = await freeEdge(page)
      const from = (await screenNodes(page)).get(e.from)!
      await page.mouse.click(from.x, from.y)
      await page.mouse.move(e.at.x - 6, e.at.y - 6)
      await page.mouse.move(e.at.x, e.at.y, { steps: 4 })
    },
    policies: { inside: 'viewport' } },
  { id: 'analytics-panel', catalogue: 'real-metrics-panel', ui: 'interact', gesture: 'click "Analytics" in the bottom bar ("Подробнее" on)',
    beforeLoad: routeAnalytics, root: '[data-surface="real-metrics-panel"]', details: { hiddenByDefault: '[data-testid="bottombar-analytics-toggle"]' },
    open: async (page) => {
      if (!(await page.locator('[data-surface="real-metrics-panel"]').isVisible())) await page.locator('[data-testid="bottombar-analytics-toggle"]').click()
    },
    policies: { inside: 'viewport' } },

  // layers and messages: a frame, and where it applies - inside the viewport; no window policies
  { id: 'node-labels', catalogue: 'canvas-labels-overlay', ui: 'interact', gesture: 'click a node on the canvas (Labels: Selection)',
    root: '.labels-layer', open: async (page) => { const n = await freeNode(page); await page.mouse.click(n.at.x, n.at.y) },
    policies: { inside: null } },
  { id: 'event-labels', catalogue: 'canvas-floating-labels-overlay', ui: 'interact', clock: true,
    gesture: 'a payment event of the run', notAGesture: 'the label is drawn for an event of the backend; the stand writes one SSE frame',
    root: '.floating-layer',
    open: async (page) => {
      await pushEvent(page, txUpdated(['a', 'b']))
      for (let i = 0; i < 160 && (await page.locator('.floating-label').count()) === 0; i += 1) await frames(page, 1)
      await frames(page, 12)
      await syncCssToClock(page)
    },
    policies: { inside: null } },
  { id: 'loading-message', catalogue: 'loading-message-overlay', ui: 'interact', awaitScene: false,
    gesture: 'open the page while the scene has not answered', beforeLoad: (p) => routeScene(p, 'hold'),
    root: '.ds-ov-inset[role="status"]', open: async () => {}, policies: { inside: 'viewport' } },
  { id: 'error-message', catalogue: 'error-message-overlay', ui: 'interact', awaitScene: false,
    gesture: 'open the page when the scene cannot be loaded', beforeLoad: (p) => routeScene(p, 'fail'),
    root: '.ds-ov-inset[role="alert"]', open: async () => {}, policies: { inside: 'viewport' } },
  { id: 'dev-perf-overlay', catalogue: 'dev-perf-overlay', ui: 'interact', query: '&perf=1',
    gesture: 'open the page with ?perf=1', root: '.ds-ov-dev-perf', open: async () => {}, policies: { inside: 'viewport' } },
]

/**
 * Catalogue keys that have no entry in the matrix. Empty today: every key has at least one. A key may be put here
 * only with a reason and a date (spec 038: "silently excluding a surface from the matrix" is forbidden).
 */
export const EXCLUDED_FROM_MATRIX: Record<string, { why: string; since: string }> = {}

/**
 * Native `<select>` controls of the HUD. Their popups are drawn by the browser, not by the page: they have no DOM
 * rectangle, are absent from a screenshot and from axe. Named here so that their absence from the matrix is a
 * recorded blind zone (2026-10-11), not an oversight.
 */
export const NATIVE_SELECTS = [
  'top bar: "UI theme"', 'top bar (Auto-Run): "Scenario"',
  'bottom bar: "Equivalent"', 'bottom bar: "Layout"', 'bottom bar: "Quality"', 'bottom bar: "Labels"', 'bottom bar (offline): "Scene"',
  'graph navigator: "Node"', 'graph navigator: "Edge"',
]

/**
 * What an element on screen IS, by the DOM alone - the reverse side of the tie to the catalogue. `key: null` names
 * what is not an overlay surface (the scene itself, the container of windows). Order matters: first match wins.
 */
export const SURFACE_DOM: Array<{ selector: string; key: string | null }> = [
  { selector: 'canvas', key: null },
  { selector: '.drag-preview', key: null },
  { selector: '.wm-layer', key: null },
  { selector: '.ws-shell[data-win-type="interact-panel"]', key: 'wm-interact-window' },
  { selector: '.ws-shell[data-win-type="node-card"], .ws-shell[data-win-type="edge-detail"]', key: 'wm-inspector-window' },
  { selector: '.overlay-select__surface', key: 'interact-select-dropdown' },
  { selector: 'details.ds-ov-details > .ds-ov-dropdown', key: 'hud-dropdown' },
  { selector: '.ds-ov-top', key: 'top-hud-stack' },
  { selector: '.sar-interact-history-overlay', key: 'interact-history-overlay' },
  { selector: '.ds-ov-bottom', key: 'bottom-hud-stack' },
  { selector: '.ds-ov-toast--success', key: 'success-toast' },
  { selector: '.ds-ov-toast--error', key: 'error-toast' },
  { selector: '.ds-ov-tooltip', key: 'edge-tooltip' },
  { selector: '[data-surface="real-metrics-panel"]', key: 'real-metrics-panel' },
  { selector: '.labels-layer', key: 'canvas-labels-overlay' },
  { selector: '.floating-layer', key: 'canvas-floating-labels-overlay' },
  { selector: '.ds-ov-inset[role="status"]', key: 'loading-message-overlay' },
  { selector: '.ds-ov-inset[role="alert"]', key: 'error-message-overlay' },
  { selector: '.ds-ov-dev-perf', key: 'dev-perf-overlay' },
]

export type Claim = { el: string; key: string | null | undefined }

/**
 * Every element that can be an overlay surface, and the key `SURFACE_DOM` gives it (`undefined`: nobody claims it).
 * Candidates are structural, not a list of names: the children of `.root` (the scene is two canvases, everything else
 * there is laid over it), the children of the window layer, whatever was teleported to `body`, every window shell,
 * dropdown surface and toast. An element counts as on screen when it OR ANY DESCENDANT has a box - a 0x0 portal with
 * a `position: fixed` child is on screen.
 *
 * WHAT THIS DOES NOT SEE (038 S2, D3; injected and confirmed): an arbitrary descendant of a container that is itself
 * claimed - a popover put inside `.ds-ov-top` or inside the bottom bar is part of a named surface for this scan. A
 * new surface mounted INSIDE the bars therefore needs its own catalogue entry and matrix entry by review, not by this
 * check; the tour and legend surfaces of 034 S9 are the known coming case. `unmatchedInstances` below narrows the gap
 * for the three kinds that come in several instances.
 */
export async function claims(page: Page): Promise<Claim[]> {
  return page.evaluate((dom) => {
    const root = document.querySelector('.root')
    const set = new Set<Element>()
    for (const el of root?.children ?? []) set.add(el)
    for (const el of document.querySelectorAll('.wm-layer > *')) set.add(el)
    for (const el of document.body.children) if (!el.contains(root) && !['SCRIPT', 'STYLE', 'LINK'].includes(el.tagName)) set.add(el)
    for (const el of document.querySelectorAll('.ws-shell, .ds-ov-dropdown, .ds-ov-toast')) set.add(el)
    const boxed = (el: Element) => {
      const r = el.getBoundingClientRect()
      const s = getComputedStyle(el)
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
    }
    const shown = (el: Element) => getComputedStyle(el).display !== 'none' && (boxed(el) || [...el.querySelectorAll('*')].some(boxed))
    return [...set].filter(shown).map((el) => ({
      el: `${el.tagName.toLowerCase()}${el.id ? `#${el.id}` : ''}.${[...el.classList].join('.')}`,
      key: dom.find((d) => el.matches(d.selector))?.key,
    }))
  }, SURFACE_DOM)
}

/**
 * Lists of OverlaySelect that exist in the panels and have no entry of their own: the same component and code path as
 * the two "From" lists of the matrix. Named so that a NEW list is noticed (2026-10-11).
 */
export const SELECTS_WITHOUT_ENTRY: Record<string, string> = {
  'mp-to': 'payment panel, "To": disabled until a sender is chosen; the same OverlaySelect as "From"',
  'tl-to': 'trust line panel, "To": appears on the next step of the flow; the same OverlaySelect as "From"',
  'tl-pick': 'trust line panel, "Existing": appears on the edit step; the same OverlaySelect as "From"',
}

/**
 * INSTANCES in the DOM of the three kinds that come in several - menus of the bars (`<details>`, open or not), windows
 * of the window manager, lists of OverlaySelect (by their triggers) - that no entry of the matrix opens. A type being
 * in the catalogue is not enough for them: a fifth menu is a `hud-dropdown` like the four, and would pass `claims`.
 */
export async function unmatchedInstances(page: Page): Promise<string[]> {
  return page.evaluate(({ roots, selects }) => {
    const out: string[] = []
    const entryOpens = (el: Element) => roots.some((sel) => el.matches(sel))
    for (const el of document.querySelectorAll('details.ds-ov-details > .ds-ov-dropdown')) {
      if (!entryOpens(el)) out.push(`menu: details[aria-label="${el.parentElement?.getAttribute('aria-label') ?? ''}"]`)
    }
    for (const el of document.querySelectorAll('.ws-shell')) {
      if (!entryOpens(el)) out.push(`window: .ws-shell[data-win-type="${el.getAttribute('data-win-type') ?? ''}"][aria-label="${el.getAttribute('aria-label') ?? ''}"]`)
    }
    for (const el of document.querySelectorAll('.overlay-select__trigger')) {
      const id = el.id.replace(/__trigger$/, '')
      if (!roots.includes(`#${id}__surface`) && !(id in selects)) out.push(`list: #${id}`)
    }
    return out
  }, { roots: MATRIX.map((e) => e.root), selects: SELECTS_WITHOUT_ENTRY })
}

/** The product's catalogue, loaded at run time (the source tree does not type-check inside the e2e program). */
export async function catalogueKeys(): Promise<string[]> {
  const mod = (await import(new URL('../../src/ui-kit/overlaySurfaceCatalog.ts', import.meta.url).href)) as { overlaySurfaceCatalog: Record<string, unknown> }
  return Object.keys(mod.overlaySurfaceCatalog)
}

/**
 * `stillLayout`: wait for physics to stop before the recipe - for the frames a human looks at (trust lines are drawn in full
 * only on a settled graph). The required tests do not wait: a menu does not need a still graph, and the gate has a time budget.
 */
export async function bootEntry(page: Page, e: Entry, theme: Theme, viewport: Viewport, o: { stillLayout?: boolean } = {}): Promise<void> {
  await page.setViewportSize(viewport)
  await boot(page, {
    snapshot: ringSnapshot(), clock: e.clock === true, ui: e.ui, query: `&theme=${theme}${e.query ?? ''}`,
    beforeLoad: e.beforeLoad, ...(e.awaitScene === false ? { awaitScene: false as const } : {}), ...(e.clock || o.stillLayout ? {} : { awaitLayout: false as const }),
  })
  if (e.awaitScene !== false) await expect(page.locator('.root'), 'the theme of the test is the theme on screen').toHaveAttribute('data-theme', theme)
  if (e.details) {
    // 043 S1, anti-vacuum of the recipe: on the first view the service control is not rendered at all.
    await expect(page.locator(e.details.hiddenByDefault), `${e.id}: "${e.details.hiddenByDefault}" is not on the first view (043 S1)`).toHaveCount(0)
    await openDetails(page)
    await expect(page.locator(e.details.hiddenByDefault).first(), `${e.id}: "${e.details.hiddenByDefault}" is on screen after "Подробнее"`).toBeVisible()
  }
}

export type Box = { x: number; y: number; width: number; height: number }

/**
 * The surface has stopped moving and fading: equal rectangle and opacity on consecutive reads. A condition, not a
 * sleep. Under a paused clock the reads are separated by frames of the fake clock instead of real time.
 */
export async function settled(page: Page, e: Entry): Promise<Box> {
  const loc = page.locator(e.root).first()
  await expect(loc, `${e.id}: the surface is on screen after "${e.gesture}"`).toBeVisible()
  let prev = ''
  let same = 0
  await expect
    .poll(async () => {
      if (e.clock) await frames(page, 2)
      const cur = JSON.stringify(await loc.evaluate((el) => {
        const r = el.getBoundingClientRect()
        return [r.x, r.y, r.width, r.height, getComputedStyle(el).opacity]
      }))
      same = cur === prev ? same + 1 : 0
      prev = cur
      return same
    }, { timeout: 15_000, intervals: [60] })
    .toBeGreaterThanOrEqual(2)
  const [x, y, width, height] = JSON.parse(prev) as number[]
  return { x: x!, y: y!, width: width!, height: height! }
}
