/**
 * 039 slice D (T3940) - the last clearing's total debt "was -> now" in the bottom row, through the product's own SSE
 * reader, event pipeline, state and bar (spec 039 :160, target 6, amendment P-5; advisor's decision 2026-10-19, option
 * (a): only a clearing states the pair - "the clearing took this much off" - and payments neither create, replace nor
 * drop it). Grown from the reproducer R7 of T3901 (`p039-repro/r07-debt-before-after.p039-repro.ts`, removed with
 * this file). Until 2026-10-19 (§15 Codex) the first form of this file did NOT make every assertion of R7: R7 also
 * required "now" to be visible, and `pair()` checked only "was"; restored (`afterShown`).
 *
 * The scene separates the candidates for "was":
 *   - settled ............................................ 48.00 (cycle a>b>c>a of 10 each, and d>e of 18)
 *   - a payment raises d>e to 25: the bar says 55.00 and there is NO pair (no clearing yet);
 *   - a clearing takes 10 off each line of the cycle: "clearing: was 55.00 -> now 25.00". Wrong: 48.00 (the total
 *     before the payment) and 35.00 (now + `cleared_amount`, forbidden by P-5). The bar is not behind the data here
 *     (an in-place patch updates it at once; its debounce is for a replaced snapshot), so this case does not tell "the
 *     total before the patches" from "the last displayed value" - the unit test with a held clock does
 *     (`src/composables/debtChange.p039d.test.ts`);
 *   - a status frame keeps the pair; a payment after the clearing (25 -> 27) keeps the clearing's pair 55.00 -> 25.00
 *     while the bar says 27.00; a clearing that changes nothing drops the pair (no false "was").
 *
 * Both languages (`?lang=ru`, `?lang=en`): the words are the dictionary's (`systemBalance.clearingChange`), the figures
 * and the equivalent the same. The pair sits above the bar, out of its flow: the bottom bar keeps its place and size
 * with and without it (inline, the first build wrapped the main row to two lines in Russian at 1366x768, 42 -> 80 px;
 * the third test was red on that form), and the pair is whole on screen, just above the total.
 *
 * NOT seen here (unit tier, `src/composables/debtChange.p039d.test.ts`): the debounce of the bar held by a fake clock,
 * a switch of equivalent, a reload, an unknown debt, exact sums beyond a double. Not seen at all: a real backend.
 */
import { expect, test, type Page } from '@playwright/test'

import { DETAILS_TOGGLE, EQ, RUN_ID, SCENARIO_ID, boot, fixtureSnapshot100, pushEvent, renderedProblems, ringSnapshot, sseFrame, type QaSnapshot } from './stand.js'
import { DEBT } from './p039-repro/contract.js'

const USED: Record<string, string> = { 'a>b': '10.00', 'b>c': '10.00', 'c>a': '10.00', 'd>e': '18.00' }

function sceneWithDebts(): QaSnapshot {
  const s = ringSnapshot()
  const pairs: Array<[string, string]> = [['a', 'b'], ['b', 'c'], ['c', 'a'], ['d', 'e'], ['e', 'f'], ['f', 'd']]
  const links = pairs.map(([source, target]) => {
    const used = USED[`${source}>${target}`] ?? '0.00'
    return { ...s.links[0]!, source, target, used, available: (100 - Number(used)).toFixed(2), trust_limit: '100.00' }
  })
  return { ...s, links }
}

/** The bar's figure the rest of the app reads (`.ds-value`): the current total with its unit. */
const shownTotal = (page: Page) => page.locator(`${DEBT.bar} [data-testid="sbb-total-debt"] .ds-value`).first().textContent()

/** The pair as the page states it: the two fields, the sentence they are in, and whether "was" comes first. */
async function pair(page: Page) {
  return page.evaluate(({ bar, before, after }) => {
    const root = document.querySelector(bar)
    const b = root?.querySelector(before) ?? null
    const a = root?.querySelector(after) ?? null
    const text = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
    // Seen by a person: a box with an area, and neither it nor an ancestor hidden, undisplayed or fully transparent by
    // computed style (§15 Codex 2026-10-19: the first form of this file checked only "was"; R7 checked both fields).
    const shown = (el: Element | null) => {
      if (!el) return false
      const r = el.getBoundingClientRect()
      if (r.width <= 0 || r.height <= 0 || getComputedStyle(el).visibility === 'hidden') return false
      for (let n: Element | null = el; n; n = n.parentElement) {
        const cs = getComputedStyle(n)
        if (cs.display === 'none' || Number(cs.opacity) === 0) return false
      }
      return true
    }
    return {
      before: b ? text(b) : null, after: a ? text(a) : null, beforeShown: shown(b), afterShown: shown(a),
      sentence: text(root?.querySelector('[data-testid="total-debt-change"]')),
      beforeComesFirst: !!a && !!b && !!(b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING),
    }
  }, DEBT)
}

const SENTENCE = {
  ru: (before: string, after: string) => `клиринг: было ${before} → стало ${after} ${EQ}`,
  en: (before: string, after: string) => `clearing: was ${before} → became ${after} ${EQ}`,
} as const

/** The bottom bar's box, rounded: equal with and without the pair. */
const barBox = (page: Page) => page.locator('.ds-ov-bottom').first().evaluate((el) => {
  const r = el.getBoundingClientRect()
  return { top: Math.round(r.top), height: Math.round(r.height), width: Math.round(r.width) }
})

/** Where the pair is: whole in the viewport, above the total's panel, drawn (a width). */
const wasPlaced = (page: Page) => page.evaluate(() => {
  const was = document.querySelector('[data-testid="total-debt-change"]')!.getBoundingClientRect()
  const panel = document.querySelector('[data-testid="system-balance"] [data-testid="sbb-total-debt"]')!.getBoundingClientRect()
  return { inViewport: was.left >= 0 && was.right <= innerWidth && was.top >= 0, abovePanel: was.bottom <= panel.top + 0.5, drawn: was.width > 20 }
})

for (const lang of ['ru', 'en'] as const) {
  test(`039 D [${lang}] - the total debt states "was -> now" of the last clearing; payments do not touch it`, async ({ page }) => {
    const snapshot = sceneWithDebts()
    await boot(page, {
      snapshot, clock: false, query: `&lang=${lang}`,
      // The stand's own answer to trustlines-list says used 0.00 for every line; here the lines carry debts.
      beforeLoad: (p) => p.route(new RegExp(`/simulator/runs/${RUN_ID}/actions/trustlines-list`, 'i'), (r) => r.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ items: snapshot.links.map((l) => ({
          from_pid: l.source, from_name: l.source, to_pid: l.target, to_name: l.target, equivalent: EQ,
          limit: '100.00', used: l.used, reverse_used: '0.00', available: l.available, status: 'active',
        })) }),
      })),
    })
    await expect(page.locator(DEBT.bar).first(), 'counter-check: the system balance bar is on screen').toBeVisible()
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the settled bar shows the known total' }).toBe(`48.00 ${EQ}`)
    expect((await pair(page)).beforeShown, 'settled: no pair').toBe(false)
    const settledBar = await barBox(page)

    // A payment with no clearing before it: the total moves, no pair.
    await pushEvent(page, {
      type: 'tx.updated', from: 'e', to: 'd', amount: '7.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
      edge_patch: [{ source: 'd', target: 'e', used: '25.00', available: '75.00' }],
    })
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the payment was applied' }).toBe(`55.00 ${EQ}`)
    expect((await pair(page)).beforeShown, 'a payment states no pair (advisor 2026-10-19, option (a))').toBe(false)

    // The clearing.
    const taken = await page.evaluate((frame) => {
      const sse = (window as unknown as { __qaSse: { push: (t: string) => Promise<number> } }).__qaSse
      return sse.push(frame)
    }, sseFrame({
      type: 'clearing.done', plan_id: 'plan-p039-d', cleared_cycles: 1, cleared_amount: '10.00',
      cycle_edges: [{ from: 'a', to: 'b' }, { from: 'b', to: 'c' }, { from: 'c', to: 'a' }],
      edge_patch: [['a', 'b'], ['b', 'c'], ['c', 'a']].map(([source, target]) => ({ source, target, used: '0.00', available: '100.00' })),
    }).frame)
    expect(taken, 'counter-check: the product took the frame').toBe(1)
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the clearing was APPLIED' }).toBe(`25.00 ${EQ}`)

    const stated = await pair(page)
    expect(stated, 'was 55.00 (just before the clearing\'s patches; not 48.00, not 25 + 10 = 35.00) -> now 25.00, both seen, with the equivalent')
      .toEqual({ before: '55.00', after: '25.00', beforeShown: true, afterShown: true, sentence: SENTENCE[lang]('55.00', '25.00'), beforeComesFirst: true })
    expect(await barBox(page), 'the bottom bar keeps its place and size with the pair').toEqual(settledBar)
    expect(await wasPlaced(page), 'the pair is whole on screen, just above the total').toEqual({ inViewport: true, abovePanel: true, drawn: true })

    // Held through traffic that does not change a debt: a status frame of the run.
    await pushEvent(page, { type: 'run_status', run_id: RUN_ID, scenario_id: SCENARIO_ID, state: 'paused', sim_time_ms: 1000, intensity_percent: 0, ops_sec: 0, queue_depth: 0 })
    expect(await pair(page), 'a status frame keeps the pair').toEqual(stated)

    // A payment after the clearing: the bar moves, the pair stays the clearing's.
    await pushEvent(page, {
      type: 'tx.updated', from: 'e', to: 'd', amount: '2.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: 'e', to: 'd' }],
      edge_patch: [{ source: 'd', target: 'e', used: '27.00', available: '73.00' }],
    })
    await expect.poll(() => shownTotal(page), { message: 'counter-check: the payment was applied' }).toBe(`27.00 ${EQ}`)
    expect(await pair(page), 'a payment after the clearing keeps the clearing\'s pair 55.00 -> 25.00').toEqual(stated)

    // A clearing that changes nothing: no false "was".
    await pushEvent(page, { type: 'clearing.done', plan_id: 'plan-p039-d-0', cleared_cycles: 0, cleared_amount: '0.00', cycle_edges: [], edge_patch: [] })
    await expect.poll(async () => (await pair(page)).beforeShown, { message: 'a clearing that changes nothing drops the pair' }).toBe(false)
    expect((await pair(page)).afterShown, 'and its "now" with it').toBe(false)
    expect(await shownTotal(page)).toBe(`27.00 ${EQ}`)
  })
}

/**
 * 039 D-fix (2026-10-19, closes §15 Codex P2 on slice D: "the stand checks the figure is visible, not that it fits").
 * The sentence and each of its two figures: whole inside the viewport, and overlapping NO neighbour - every other
 * panel, control, legend box and legend entry on the page that is not the sentence itself, inside it or around it.
 * Until the D-fix the sentence (absolute, just above the total's panel, right-aligned) lay over the end of the legend
 * ("организация") when the main row wraps (1024x640 here, both languages and themes; Russian at 1366x768 on the live
 * demo). The "Recent actions" history (`.interact-history`, no panel class of its own) is a neighbour too: the
 * internal adversarial pass of the D-fix found the sentence over it with "Подробнее" on once the sentence moved up.
 * Returns the offenders by a readable name, so a red run says what it lies on.
 */
const pairFits = (page: Page) => page.evaluate(() => {
  const sentence = document.querySelector('[data-testid="total-debt-change"]')
  if (!sentence) return { present: false, outside: [] as string[], overlaps: [] as string[] }
  // [label, element, its group]: a neighbour that IS the group, is inside it or contains it is not judged against it.
  const subjects: Array<[string, Element, Element]> = [['sentence', sentence, sentence]]
  for (const id of ['total-debt-before', 'total-debt-after']) {
    const el = sentence.querySelector(`[data-testid="${id}"]`)
    if (el) subjects.push([id, el, sentence])
  }
  // 049 S1 (option B): the "Общий долг" plate floats with the sentence above the bar - a subject as well.
  const plate = document.querySelector('[data-testid="system-balance"] [data-testid="sbb-total-debt"]')
  if (plate) subjects.push(['debt plate', plate, plate])
  const visible = (el: Element) => {
    const r = el.getBoundingClientRect()
    if (r.width <= 0 || r.height <= 0) return false
    for (let n: Element | null = el; n; n = n.parentElement) {
      const cs = getComputedStyle(n)
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false
    }
    return true
  }
  const name = (el: Element) => {
    const id = el.getAttribute('data-testid')
    const role = el.getAttribute('data-legend-role')
    const text = (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)
    return `${el.tagName.toLowerCase()}${id ? `[${id}]` : ''}${role ? `[legend:${role}]` : ''}${el.className && typeof el.className === 'string' ? `.${el.className.split(' ')[0]}` : ''} "${text}"`
  }
  // 049 S1: and the node labels of the graph (DOM; none at rest - labels on selection - but held when there are).
  const neighbours = [...document.querySelectorAll('.ds-panel, button, select, input, .ds-legend, .ds-legend__entry, .ds-legend__title, .interact-history, .interact-history__item, .node-label')]
    .filter((el) => visible(el))
  const outside: string[] = []
  const overlaps: string[] = []
  for (const [label, el, group] of subjects) {
    const r = el.getBoundingClientRect()
    if (r.left < 0 || r.top < 0 || r.right > innerWidth || r.bottom > innerHeight) outside.push(`${label} ${JSON.stringify([r.left, r.top, r.right, r.bottom].map(Math.round))}`)
    for (const n of neighbours) {
      if (n === group || group.contains(n) || n.contains(group)) continue
      const q = n.getBoundingClientRect()
      const w = Math.min(r.right, q.right) - Math.max(r.left, q.left)
      const h = Math.min(r.bottom, q.bottom) - Math.max(r.top, q.top)
      if (w > 0.5 && h > 0.5) overlaps.push(`${label} over ${name(n)} (${Math.round(w)}x${Math.round(h)} px)`)
    }
  }
  return { present: true, outside, overlaps }
})

/**
 * 049 S1 (decisions 1 and 4; reproducer (а)): the right edges of the bottom-right group - the clearing sentence, the
 * "Общий долг" plate, "Показать всю сеть" - and of the "Recent actions" history are the CONTROL LINE: the content edge of
 * the bottom bar's main row (a HudBar: its box minus its own padding), read from the page by that rule, never as a px
 * literal (spec 049, Verification plan item 4). The history is measured at its text (a Range over the heading, the
 * last element of each line), not at its box: a box on the line with text inside it is not "on the line".
 * The advisor's refinement of decision 1 (2026-10-20, option B): the sentence and the plate stand OUT of the bar's flow,
 * one right-aligned column above its right end; "Показать всю сеть" stays in the bar (the 85 % canvas floor, 043 R9).
 */
const edges = (page: Page) => page.evaluate(() => {
  const half = (v: number) => Math.round(v * 2) / 2
  const row = document.querySelector('.ds-ov-bottom .bb-main-row')!
  const line = row.getBoundingClientRect().right - parseFloat(getComputedStyle(row).paddingRight)
  const right = (sel: string) => {
    const el = document.querySelector(sel)
    const r = el?.getBoundingClientRect()
    return r && r.width > 0 ? half(r.right) : null
  }
  const history = document.querySelector('.interact-history')
  let historyText: number[] = []
  if (history) {
    const heading = history.querySelector('.interact-history__header')!
    const range = document.createRange()
    range.selectNodeContents(heading)
    historyText = [half(range.getBoundingClientRect().right)]
    for (const li of history.querySelectorAll('.interact-history__item')) historyText.push(half(li.lastElementChild!.getBoundingClientRect().right))
  }
  return {
    line: half(line),
    sentence: right('[data-testid="total-debt-change"]'),
    debt: right('[data-testid="sbb-total-debt"]'),
    fit: right('[data-testid="reset-view"]'),
    historyText,
  }
})

/**
 * BACKLOG 048-10 (implemented here, 049 S1; the advisor's rule on its repeat): a measurement of the bottom row waits
 * until the layout has SETTLED - the bar's height the product publishes (`--ds-hud-bottom-stack-height`, which places the
 * history and the windows) equals the bar's actual height, and the boxes of the bar's groups and their neighbours are the
 * same on three reads in a row. A resize, a theme switch, "Подробнее" and a new total reflow the row asynchronously (a
 * ResizeObserver publishes the height); measured at once, the stand judged the pair against a history that had not yet
 * moved (CI, PR #330: `[en] [hud, details on, 1024x640]`, the pair over "Recent actions").
 */
async function settledLayout(page: Page, where: string): Promise<void> {
  let prev = ''
  let same = 0
  let last = ''
  await expect.poll(async () => {
    const g = await page.evaluate(() => {
      const bar = [...document.querySelectorAll<HTMLElement>('.ds-ov-bottom')].find((el) => !el.classList.contains('sar-interact-history-overlay'))!
      const published = getComputedStyle(document.querySelector('.root')!).getPropertyValue('--ds-hud-bottom-stack-height').trim()
      const actual = `${Math.round(bar.getBoundingClientRect().height)}px`
      const sel = '[data-testid="total-debt-change"], [data-testid="sbb-total-debt"], [data-testid="reset-view"], .interact-history, [data-testid="bottom-details-row"], .graph-navigator, [data-testid="legend"]'
      const boxes = [...document.querySelectorAll(sel)].map((el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom].map((v) => Math.round(v * 2) / 2) })
      return { published, actual, key: JSON.stringify(boxes) }
    })
    last = JSON.stringify(g)
    same = g.published === g.actual && g.key === prev ? same + 1 : 0
    prev = g.key
    return same >= 2
  }, { message: `${where} the layout settles: the published bar height equals the actual one and the groups stop moving`, intervals: [50, 100, 100, 150] }).toBe(true)
    .catch((e: Error) => { throw new Error(`${e.message}\nlast read: ${last}`) })
}

/**
 * 049 S1 (option B, the advisor's condition 1): the floating column lies over the canvas, so it is held against what
 * the canvas shows there - no node of the graph under it (a node's centre inside the column grown by the node's radius on
 * screen). The node labels are DOM and are neighbours of `pairFits`; at rest the demo draws none (labels on selection).
 */
const nodesUnderColumn = (page: Page, radiusById: Record<string, number>) => page.evaluate((radiusById) => {
  const sim = (window as unknown as { __geoSim?: { layoutNodes?: Array<{ id: string; x: number; y: number }>; camera: { panX: number; panY: number; zoom: number } } }).__geoSim!
  const canvas = document.querySelector('canvas')!.getBoundingClientRect()
  const boxes = ['[data-testid="total-debt-change"]', '[data-testid="sbb-total-debt"]'].map((s) => document.querySelector(s)?.getBoundingClientRect()).filter((r): r is DOMRect => !!r && r.width > 0)
  const cam = sim.camera
  const nodes = sim.layoutNodes ?? []
  let nearest = Infinity
  const under = nodes.filter((n) => {
    // The node's own half-size on screen (its `viz_size`, the larger side), not one radius for all (adversarial pass, P3).
    const radius = (radiusById[n.id] ?? 12) * cam.zoom
    const x = canvas.left + n.x * cam.zoom + cam.panX
    const y = canvas.top + n.y * cam.zoom + cam.panY
    for (const r of boxes) nearest = Math.min(nearest, Math.max(r.left - x, 0, x - r.right) + Math.max(r.top - y, 0, y - r.bottom) - radius)
    return boxes.some((r) => x > r.left - radius && x < r.right + radius && y > r.top - radius && y < r.bottom + radius)
  }).map((n) => `${n.id}@${Math.round(canvas.left + n.x * cam.zoom + cam.panX)},${Math.round(canvas.top + n.y * cam.zoom + cam.panY)}`)
  return { nodes: nodes.length, columns: boxes.length, under, nearestRimPx: Math.round(nearest), boxes: boxes.map((r) => [r.left, r.top, r.right, r.bottom].map(Math.round)), zoom: cam.zoom }
}, radiusById)

/**
 * A total of the bar set to an exact figure by one payment frame (exact cents): a rise goes onto one line, a fall is
 * taken from the largest debts first. `lines` is the test's copy of the debts of the scene, kept in step.
 */
type DebtLine = { source: string; target: string; used: bigint }
/** Exact cents of a money text ("86973.5 UAH" -> 8697350n); a debt has two fraction digits in this equivalent. */
function cents(text: string): bigint {
  const [int = '0', frac = ''] = text.replace(/[^0-9.-]/g, '').split('.')
  return BigInt(int) * 100n + BigInt((frac + '00').slice(0, 2))
}
const money = (c: bigint) => `${c / 100n}.${String(c % 100n).padStart(2, '0')}`
async function setTotal(page: Page, lines: DebtLine[], target: string): Promise<void> {
  let delta = cents(target) - cents((await shownTotal(page)) ?? '')
  const changed = new Set<DebtLine>()
  if (delta >= 0n) {
    lines[0]!.used += delta
    changed.add(lines[0]!)
  } else {
    for (const line of [...lines].sort((x, y) => (y.used > x.used ? 1 : y.used < x.used ? -1 : 0))) {
      if (delta === 0n) break
      const take = line.used < -delta ? line.used : -delta
      if (take === 0n) continue
      line.used -= take
      delta += take
      changed.add(line)
    }
  }
  const first = [...changed][0]!
  await pushEvent(page, {
    type: 'tx.updated', from: first.target, to: first.source, amount: '1.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: first.target, to: first.source }],
    edge_patch: [...changed].map((x) => ({ source: x.source, target: x.target, used: money(x.used), available: '0.00' })),
  })
  await expect.poll(() => shownTotal(page), { message: `counter-check: the total is ${target}` }).toBe(`${target} ${EQ}`)
}

/**
 * 049 S1 (§15 Codex P3): the subjects of the edge and overlap checks, each held as SEEN in every measured combination
 * (`renderedProblems`, `stand.ts`) - a coordinate check alone passes an invisible plate.
 */
const SEEN = ['[data-testid="total-debt-change"]', '[data-testid="total-debt-before"]', '[data-testid="total-debt-after"]', '[data-testid="sbb-total-debt"]',
  '[data-testid="reset-view"]', '.interact-history__header', '.interact-history__text', '.interact-history__time'] as const

/**
 * 049 S2 (decision 5; Verification plan 2): with «Показывать события» on the history is five lines - each line, the
 * heading and the whole log whole inside the viewport and over NO neighbour (the bars' panels and controls, the legend,
 * the floating column, the node labels). Returns the offenders by a readable name.
 */
const feedFits = (page: Page) => page.evaluate(() => {
  const log = document.querySelector('.interact-history')
  if (!log) return { present: false, lines: 0, outside: [] as string[], overlaps: [] as string[] }
  const subjects = [log.querySelector('.interact-history__header')!, ...log.querySelectorAll('.interact-history__item')]
  const visible = (el: Element) => {
    const r = el.getBoundingClientRect()
    if (r.width <= 0 || r.height <= 0) return false
    for (let n: Element | null = el; n; n = n.parentElement) {
      const cs = getComputedStyle(n)
      if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false
    }
    return true
  }
  const name = (el: Element) => `${el.tagName.toLowerCase()}${el.getAttribute('data-testid') ? `[${el.getAttribute('data-testid')}]` : ''}.${String(el.className).split(' ')[0]} "${(el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40)}"`
  const neighbours = [...document.querySelectorAll('.ds-panel, button, select, input, .ds-legend, .ds-legend__entry, .ds-legend__title, .node-label, [data-testid="total-debt-change"], .ws-shell')]
    .filter((el) => visible(el) && !log.contains(el) && !el.contains(log))
  const outside: string[] = []
  const overlaps: string[] = []
  for (const el of subjects) {
    const r = el.getBoundingClientRect()
    if (r.left < 0 || r.top < 0 || r.right > innerWidth || r.bottom > innerHeight) outside.push(`${name(el)} ${JSON.stringify([r.left, r.top, r.right, r.bottom].map(Math.round))}`)
    for (const n of neighbours) {
      const q = n.getBoundingClientRect()
      const w = Math.min(r.right, q.right) - Math.max(r.left, q.left)
      const h = Math.min(r.bottom, q.bottom) - Math.max(r.top, q.top)
      if (w > 0.5 && h > 0.5) overlaps.push(`${name(el)} over ${name(n)} (${Math.round(w)}x${Math.round(h)} px)`)
    }
  }
  return { present: true, lines: subjects.length - 1, outside, overlaps }
})

/**
 * 049 S2 (CI run 39474653429, ubuntu fonts): the history shows as many of its newest lines as fit UNDER the top stack -
 * between the stack's bottom edge and the history's own bottom anchor - never more than its five. Read from the page:
 * the stack's bottom, the heading's top, the step between two lines. `oneMoreCrosses` - the line the rule left out would
 * have crossed the stack (so the rule drops nothing needlessly); `crosses` - the heading is above the stack's bottom.
 */
const feedFitRule = (page: Page) => page.evaluate(() => {
  const log = document.querySelector('.interact-history')
  const top = document.querySelector('.ds-ov-top')
  if (!log || !top) return null
  const items = [...log.querySelectorAll('.interact-history__item')].map((el) => el.getBoundingClientRect())
  const header = log.querySelector('.interact-history__header')!.getBoundingClientRect()
  const limit = top.getBoundingClientRect().bottom
  // The clearance the component keeps between the stack and the heading: the list's own row gap (`--ds-ihl-list-gap`), as
  // the page computes it - the component reads the same value (adversarial pass, P2-a).
  const gap = Number.parseFloat(getComputedStyle(log.querySelector('.interact-history__list')!).rowGap) || 0
  // One shown line: the step is its height and the gap (P3), as the component takes it.
  const step = items.length > 1 ? items[1]!.top - items[0]!.top : items.length === 1 ? items[0]!.height + gap : null
  return {
    lines: items.length,
    crosses: header.top < limit,
    // The component shows one more line only when its heading would stay `gap` below the stack - the same inequality.
    oneMoreCrosses: step === null ? null : header.top - step < limit + gap,
    room: Math.round((header.top - limit) * 10) / 10,
    limit: Math.round(limit * 10) / 10,
    gap,
  }
})

/** The canvas share of 043 R9 (`layout.qa.ts`): the bars' rectangles against the viewport, and the hit-tested share. */
const canvasShare = (page: Page) => page.evaluate(() => {
  const vh = window.innerHeight
  const vw = window.innerWidth
  const top = document.querySelector('.ds-ov-top .ds-ov-top-stack')!.getBoundingClientRect()
  const bottom = [...document.querySelectorAll<HTMLElement>('.ds-ov-bottom')].find((el) => !el.classList.contains('sar-interact-history-overlay'))!.getBoundingClientRect()
  let total = 0
  let free = 0
  for (let y = 2; y < vh; y += 4) for (let x = 2; x < vw; x += 4) { total += 1; if (document.elementFromPoint(x, y) instanceof HTMLCanvasElement) free += 1 }
  const pct = (n: number) => Math.round(n * 1000) / 10
  return { heightSharePct: pct((bottom.top - top.bottom) / vh), hitTestSharePct: pct(free / total) }
})

/** The viewports the pair is held at: the main desktop size of the demo and the narrowest the stand resizes a window to. */
const FIT_VIEWPORTS = [{ width: 1366, height: 768 }, { width: 1024, height: 640 }] as const
/** 049 S1 (decision 1): the total debt at five and at seven digits before the point - the spec's two figures. */
const TOTALS = ['49745.90', '1049745.90'] as const

// The widest first view the demo shows: the 100-node scene (its full legend), each language, both themes, with
// "Подробнее" off and on (its service row is the neighbour right above the main row), at 1366x768 and the narrowest
// width of the stand (1024x640, `surfaceMatrix.ts` VIEWPORT_RESIZED). One boot per language: the theme is switched with
// the product's own switcher, the viewport resized in place. Here the inline form of the first build of slice D
// (e10ac818: "Общий долг было X → стало Y" inside the row) wrapped the main row of the bottom bar to two lines,
// 42 -> 80 px (red on `the bottom bar keeps its place and size`); and the out-of-flow form of 039 D lay over the end of
// the Russian legend (red on `overlaps no neighbour`, 039 D-fix).
for (const lang of ['ru', 'en'] as const) {
  test(`039 D [${lang}] [100 nodes, saas|hud, details off|on, 1366x768|1024x640] - the pair fits: no neighbour under it, whole on screen, the bar unmoved; 049 S1: the edge group on the control line at 49745.90 and 1049745.90; 049 S2: the five-line feed fits`, async ({ page }, info) => {
    await page.setViewportSize(FIT_VIEWPORTS[0])
    const snapshot = fixtureSnapshot100()
    // The line with the largest debt: a clearing of it (the scene's total is about 88 thousand: a realistic magnitude).
    const l = snapshot.links.reduce((m, x) => (Number(x.used ?? 0) > Number(m.used ?? 0) ? x : m))
    expect(Number(l.used ?? 0), 'counter-check: the scene has a line with a debt to clear').toBeGreaterThan(0)
    await boot(page, { snapshot, clock: false, query: `&lang=${lang}&theme=saas`, beforeLoad: (p) => p.route(`**/simulator/runs/${RUN_ID}/actions/clearing-real`, (r) => r.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ ok: true, equivalent: EQ, cleared_cycles: 0, total_cleared_amount: '0.00', client_action_id: null, cycles: [] }),
    })) })
    await expect(page.locator('[data-testid="legend"]').first(), 'counter-check: the full legend is on screen').toBeVisible()
    // A manual clearing through the action bar: its answer puts a line in "Recent actions", the neighbour at the right
    // edge above the bar. Its window is closed: what is measured is the HUD at rest (049 S2: no success toast to wait out).
    await page.locator('[data-testid="actionbar-clearing"]').click()
    await page.locator('[data-testid="clearing-confirm"]').click()
    await expect(page.locator('[data-testid="recent-actions"]'), 'counter-check: the history is on screen').toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.ws-shell')).toHaveCount(0)
    const settled = await barBox(page)
    expect((await pair(page)).beforeShown, 'counter-check: settled, no pair').toBe(false)
    await pushEvent(page, {
      type: 'clearing.done', plan_id: 'plan-p039-d-100', cleared_cycles: 1, cleared_amount: String(l.used),
      cycle_edges: [{ from: String(l.source), to: String(l.target) }],
      edge_patch: [{ source: l.source, target: l.target, used: '0.00', available: String(l.trust_limit ?? '0') }],
    })
    await expect.poll(async () => (await pair(page)).beforeShown, { message: 'counter-check: the clearing states its pair' }).toBe(true)
    expect(await barBox(page), 'the bottom bar keeps its place and size with the pair').toEqual(settled)
    expect(await wasPlaced(page), 'the pair is whole on screen, just above the total').toEqual({ inViewport: true, abovePanel: true, drawn: true })

    // 049 S1: each node's half-size (its `viz_size`, the larger side) for "no node under the floating column".
    const radii = Object.fromEntries(snapshot.nodes.map((n) => {
      const v = (n as { viz_size?: { w?: number; h?: number } }).viz_size
      return [String(n.id), Math.max(Number(v?.w ?? 24), Number(v?.h ?? 24)) / 2]
    }))
    // 049 S1: the test's copy of the debts (the cleared line is at 0.00 now), for totals set to an exact figure.
    const debts: DebtLine[] = snapshot.links.map((x) => ({
      source: String(x.source), target: String(x.target), used: x === l ? 0n : cents(String(x.used ?? '0')),
    }))

    const toggle = page.locator(DETAILS_TOGGLE)
    const setDetails = async (on: boolean) => {
      if (((await toggle.getAttribute('aria-expanded')) === 'true') !== on) await toggle.evaluate((el) => (el as HTMLButtonElement).click())
      await expect(toggle).toHaveAttribute('aria-expanded', on ? 'true' : 'false')
    }
    const seen: string[] = []
    for (const theme of ['saas', 'hud'] as const) {
      if (theme !== 'saas') {
        await setDetails(true)
        await page.getByTestId('theme-select').selectOption(theme)
      }
      await expect(page.locator('.root'), `counter-check: the theme is ${theme}`).toHaveAttribute('data-theme', theme)
      for (const details of [false, true]) {
        await setDetails(details)
        if (details) await expect(page.locator('[data-testid="bottom-details-row"]'), 'counter-check: the service row is the neighbour above').toBeVisible()
        for (const vp of FIT_VIEWPORTS) {
          await page.setViewportSize(vp)
          await expect.poll(async () => (await pair(page)).afterShown, { message: `counter-check: [${theme}] the pair is still stated` }).toBe(true)
          // 049 S1 (reproducer (а)): at a total of five and of seven digits before the point - the plate grows to the left,
          // its right edge stays on the line with the sentence's and the button's.
          for (const total of TOTALS) {
            await setTotal(page, debts, total)
            const where = `[${theme}, details ${details ? 'on' : 'off'}, ${vp.width}x${vp.height}, total ${total}]`
            await settledLayout(page, where)
            expect(await renderedProblems(page, SEEN), `${where} every measured subject is drawn and seen`).toEqual([])
            expect(await pairFits(page), `${where} the pair overlaps no neighbour and is whole inside the viewport`)
              .toEqual({ present: true, outside: [], overlaps: [] })
            const e = await edges(page)
            expect(e.line, `${where} counter-check: the control line is inset from the edge: ${JSON.stringify(e)}`).toBeLessThan(vp.width - 1)
            for (const k of ['sentence', 'debt', 'fit'] as const) {
              expect(e[k], `${where} ${k} is drawn: ${JSON.stringify(e)}`).not.toBeNull()
              expect(Math.abs(e[k]! - e.line), `${where} ${k}: its right edge is the control line: ${JSON.stringify(e)}`).toBeLessThanOrEqual(1)
            }
            expect(e.historyText.length, `${where} counter-check: the history heading and its line are measured`).toBeGreaterThanOrEqual(2)
            for (const x of e.historyText) expect(Math.abs(x - e.line), `${where} the history text ends on the control line: ${JSON.stringify(e)}`).toBeLessThanOrEqual(1)
            const g = await nodesUnderColumn(page, radii)
            expect(g.nodes, `${where} counter-check: the graph's nodes are read`).toBeGreaterThan(50)
            expect(g.columns, `${where} counter-check: the sentence and the plate are measured`).toBe(2)
            expect(g.under, `${where} no node of the graph under the floating column ${JSON.stringify(g)}`).toEqual([])
            seen.push(where)
          }
        }
      }
      await page.setViewportSize(FIT_VIEWPORTS[0])
    }

    // 049 S1 (internal adversarial pass, P3): a clearing whose two figures have seven digits before the point - the
    // longest phrase, two lines at 1366 px as well; the column grows upwards and the history above it must still clear it.
    await setTotal(page, debts, '1049745.90')
    const big = [...debts].sort((x, y) => (y.used > x.used ? 1 : -1))[0]!
    big.used -= cents('49745.90')
    await pushEvent(page, {
      type: 'clearing.done', plan_id: 'plan-p049-s1-7', cleared_cycles: 1, cleared_amount: '49745.90',
      cycle_edges: [{ from: big.source, to: big.target }],
      edge_patch: [{ source: big.source, target: big.target, used: money(big.used), available: '0.00' }],
    })
    await expect.poll(async () => { const p = await pair(page); return `${p.before} -> ${p.after}` }, { message: 'counter-check: the seven-digit pair is stated' })
      .toBe('1049745.90 -> 1000000.00')
    for (const details of [false, true]) {
      await setDetails(details)
      for (const vp of FIT_VIEWPORTS) {
        await page.setViewportSize(vp)
        const where = `[hud, details ${details ? 'on' : 'off'}, ${vp.width}x${vp.height}, pair 1049745.90 -> 1000000.00]`
        await settledLayout(page, where)
        expect(await renderedProblems(page, SEEN), `${where} every measured subject is drawn and seen`).toEqual([])
        expect(await pairFits(page), `${where} the pair overlaps no neighbour and is whole inside the viewport`)
          .toEqual({ present: true, outside: [], overlaps: [] })
        const e = await edges(page)
        for (const k of ['sentence', 'debt', 'fit'] as const) {
          expect(Math.abs(e[k]! - e.line), `${where} ${k}: its right edge is the control line: ${JSON.stringify(e)}`).toBeLessThanOrEqual(1)
        }
        for (const x of e.historyText) expect(Math.abs(x - e.line), `${where} the history text ends on the control line: ${JSON.stringify(e)}`).toBeLessThanOrEqual(1)
        expect((await nodesUnderColumn(page, radii)).under, `${where} no node of the graph under the floating column`).toEqual([])
        seen.push(where)
      }
    }
    expect(seen, 'counter-check: every combination was measured').toHaveLength(20)

    // 049 S2 (decision 5; Verification plan 2): «Показывать события». Anti-vacuum FIRST: every payment and clearing above
    // arrived (each frame taken by the product, `pushEvent`) with the switch off - and not one line of the network was
    // written; the history is the own action alone, as before 049.
    const log = page.locator('[data-testid="recent-actions"]')
    await expect(log).toHaveAttribute('data-feed', 'off')
    await expect(page.locator('.interact-history__item[data-source="network"]'), 'switch off: no network line, though events arrived').toHaveCount(0)
    await expect(page.locator('.interact-history__item'), 'switch off: the own line only').toHaveCount(1)
    await setDetails(true)
    await page.getByTestId('events-feed-toggle').check()
    await expect(log).toHaveAttribute('data-feed', 'on')
    // Four events of the network: a payment, a failure (no amount), the growth of trust, a clearing that lowers a debt by
    // 1.00 (the pair is restated, not dropped). With the own line: five lines.
    const [d1, d2] = debts
    await pushEvent(page, { type: 'tx.updated', from: d1!.target, to: d1!.source, amount: '12.00', amount_flyout: false, ttl_ms: 1200, edges: [{ from: d1!.target, to: d1!.source }] })
    await pushEvent(page, { type: 'tx.failed', from: d2!.target, to: d2!.source, error: { code: 'ROUTING_NO_CAPACITY', message: 'no capacity', at: '2026-02-01T00:00:01.000Z' } })
    await pushEvent(page, { type: 'topology.changed', reason: 'trust_drift_growth', payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [], edge_patch: [{ source: d2!.source, target: d2!.target, trust_limit: '999999.00' }] } })
    const top = [...debts].sort((x, y) => (y.used > x.used ? 1 : -1))[0]!
    top.used -= 100n
    await pushEvent(page, {
      type: 'clearing.done', plan_id: 'plan-p049-s2', cleared_cycles: 1, cleared_amount: '1.00',
      cycle_edges: [{ from: top.source, to: top.target }], edge_patch: [{ source: top.source, target: top.target, used: money(top.used), available: '0.00' }],
    })
    // The precondition is read where the five fit for certain - the first view at 1366x768 (adversarial pass, P2-b: the
    // viewport the loops above left, 1024x640 with «Подробнее», is where the rule may rightly show fewer).
    await page.setViewportSize(FIT_VIEWPORTS[0])
    await setDetails(false)
    await settledLayout(page, '[feed on, precondition, 1366x768]')
    await expect(page.locator('.interact-history__item'), 'counter-check: five lines - the own one and four of the network').toHaveCount(5)
    await expect(page.locator('.interact-history__item[data-source="network"]')).toHaveCount(4)
    const FEED_SEEN = [...SEEN, '.interact-history__item[data-source="network"] .interact-history__text'] as const
    const feedSeen: string[] = []
    const fitLines: Record<string, number> = {}
    const fitRules: Record<string, NonNullable<Awaited<ReturnType<typeof feedFitRule>>>> = {}
    /**
     * The lines shown overlap no neighbour and are whole in the viewport, the pair is clear of them, their text ends on the
     * control line - and they are as many as fit under the top stack (the rule above), at most five.
     */
    const expectFeedFits = async (where: string) => {
      const rule = await feedFitRule(page)
      expect(rule, `${where} counter-check: the history and the top stack are measured`).not.toBeNull()
      expect(rule!.crosses, `${where} the history's heading stays under the top stack: ${JSON.stringify(rule)}`).toBe(false)
      expect(rule!.lines, `${where} at least one line and at most five: ${JSON.stringify(rule)}`).toBeGreaterThanOrEqual(1)
      expect(rule!.lines).toBeLessThanOrEqual(5)
      if (rule!.lines < 5) expect(rule!.oneMoreCrosses, `${where} a line was left out only because it would cross the top stack: ${JSON.stringify(rule)}`).toBe(true)
      fitLines[where] = rule!.lines
      fitRules[where] = rule!
      const fits = await feedFits(page)
      expect({ ...fits, lines: undefined }, `${where} the lines overlap no neighbour and are whole inside the viewport`)
        .toEqual({ present: true, lines: undefined, outside: [], overlaps: [] })
      expect(fits.lines, `${where} counter-check: the lines measured are the lines shown`).toBe(rule!.lines)
      expect(await pairFits(page), `${where} the pair overlaps no neighbour - the lines neither`)
        .toEqual({ present: true, outside: [], overlaps: [] })
      const e = await edges(page)
      expect(e.historyText, `${where} counter-check: the heading and every line are measured`).toHaveLength(rule!.lines + 1)
      for (const x of e.historyText) expect(Math.abs(x - e.line), `${where} the history text ends on the control line: ${JSON.stringify(e)}`).toBeLessThanOrEqual(1)
    }
    const shares: Record<string, unknown> = {}
    for (const theme of ['hud', 'saas'] as const) {
      await setDetails(true)
      await page.getByTestId('theme-select').selectOption(theme)
      await expect(page.locator('.root')).toHaveAttribute('data-theme', theme)
      for (const details of [false, true]) {
        await setDetails(details)
        for (const vp of FIT_VIEWPORTS) {
          await page.setViewportSize(vp)
          const where = `[feed on, ${theme}, details ${details ? 'on' : 'off'}, ${vp.width}x${vp.height}]`
          await settledLayout(page, where)
          expect(await renderedProblems(page, FEED_SEEN), `${where} every measured subject - the network's lines too - is drawn and seen`).toEqual([])
          await expectFeedFits(where)
          // Measured and recorded, not asserted (spec 049, Verification plan 2): the canvas share of the first view with the feed.
          if (!details && vp.width === 1366) shares[theme] = await canvasShare(page)
          feedSeen.push(where)
        }
      }
    }
    expect(feedSeen, 'counter-check: every combination with the feed was measured').toHaveLength(8)
    // The first view has room for the five: the rule drops nothing there (anti-vacuum of the rule).
    for (const theme of ['hud', 'saas']) expect(fitLines[`[feed on, ${theme}, details off, 1366x768]`], `[${theme}] all five lines at 1366x768`).toBe(5)
    // 049 S2 (CI run 39474653429, ubuntu): at 1024x640 with «Подробнее» on, the top stack of CI ended about 50 px lower than
    // here (49 px of room between it and the heading here, -1 px there): its rows wrap with other fonts. A top stack 60 px
    // taller must not put the history over it - reproduced by a taller metrics row of «Подробнее», the row the CI run hit.
    await setDetails(true)
    await page.setViewportSize(FIT_VIEWPORTS[1])
    const base = fitRules['[feed on, saas, details on, 1024x640]']!
    expect(base, 'counter-check: the unstressed combination was measured').toBeTruthy()
    const taller = async (px: number, where: string) => {
      const style = await page.addStyleTag({ content: `.ds-ov-top [data-testid="system-metrics"] { padding-bottom: ${px}px !important; }` })
      await settledLayout(page, where)
      await expectFeedFits(where)
      const rule = fitRules[where]!
      // Anti-vacuum of the stress (P3): the stack's bottom really moved down by the padding, and the history answered.
      expect(Math.abs(rule.limit - base.limit - px), `${where} the stress took effect: the stack ends ${px} px lower ${JSON.stringify({ base, rule })}`).toBeLessThanOrEqual(1)
      expect(rule.lines < base.lines || rule.room < base.room, `${where} the history answered the stress ${JSON.stringify({ base, rule })}`).toBe(true)
      await style.evaluate((el) => el.remove())
      await settledLayout(page, `${where} restored`)
      return rule
    }
    const tall = await taller(60, '[feed on, saas, details on, 1024x640, top stack +60 px]')
    expect(tall.lines, 'the taller stack leaves room for fewer lines').toBeLessThan(base.lines)
    // The clearance itself (P2-a): a stack lowered until the five-line heading would stand 1.5 px under it - inside the gap
    // the component keeps, so it shows four, and the stand must agree that the fifth would have crossed (gap included).
    if (base.lines === 5) {
      const edge = await taller(base.room - 1.5, `[feed on, saas, details on, 1024x640, heading 1.5 px under the stack]`)
      expect(edge.lines, 'inside the clearance: the fifth line is left out').toBe(4)
    }
    console.log('[049 S2 feed lines that fit]', JSON.stringify({ lang, fitLines }))
    const row = { lang, viewport: '1366x768', details: 'off', feed: 'on (5 lines)', shares }
    console.log('[049 S2 feed canvas share]', JSON.stringify(row))
    info.annotations.push({ type: '049-S2-feed-canvas-share', description: JSON.stringify(row) })
  })
}
