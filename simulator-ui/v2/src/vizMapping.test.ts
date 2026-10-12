/**
 * 039 slice B, PR 1 - the display layer: the classifier, the legend's entries, the resolution of the data colours.
 *
 * What these tests do NOT see: pixels (the browser tier, `e2e/qa/data-colours.qa.ts`), the wording's fitness for a
 * reader (the owner's), and the stylesheet as a browser applies it - the last block is a FORM guard of the CSS text.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { afterEach, describe, expect, it, vi } from 'vitest'

import type { GraphNode } from './types'
import {
  classifyNode, createVizMapping, DATA_PALETTE_DEFAULT, DATA_TOKEN, fillOf, fillTokenOf, LEGACY_NODE_COLOR_KEYS, LEGEND_VOCABULARY,
  legendEntriesFor, resolveVizColors, VIZ_LINK, type DataColorName,
} from './vizMapping'

const MINUS = '−'
type In = Pick<GraphNode, 'net_sign' | 'status' | 'type'>

describe('classifyNode - the one place that says what a participant looks like', () => {
  it('the sign of the net position: +1 creditor, -1 debtor, 0 neutral; null and absent are UNKNOWN, not zero', () => {
    expect(classifyNode({ net_sign: 1 })).toMatchObject({ role: 'creditor', sign: '+' })
    expect(classifyNode({ net_sign: -1 })).toMatchObject({ role: 'debtor', sign: MINUS })
    expect(classifyNode({ net_sign: 0 })).toMatchObject({ role: 'neutral', sign: '0' })
    expect(classifyNode({ net_sign: null })).toMatchObject({ role: 'unknown', sign: '?' })
    expect(classifyNode({})).toMatchObject({ role: 'unknown', sign: '?' })
    expect(classifyNode({ net_sign: undefined })).toMatchObject({ role: 'unknown', sign: '?' })
  })

  it('a value that is not -1, 0 or 1 is unknown - nothing is coerced into a sign or into zero', () => {
    for (const bad of ['1', '-1', '0', '', 2, -2, 0.5, Number.NaN, true, false, {}, []]) {
      expect(classifyNode({ net_sign: bad as unknown as In['net_sign'] }).role, `net_sign ${JSON.stringify(bad)}`).toBe('unknown')
    }
  })

  it('a role is read from net_sign only: balances, atoms, colour keys, the type and the number of lines do not make one', () => {
    const noisy = { net_balance: '-500.00', net_balance_atoms: '50000', viz_color_key: 'debt-8', links_count: 40, type: 'business', name: 'Big debtor' }
    expect(classifyNode(noisy as In).role).toBe('unknown')
    expect(classifyNode({ ...noisy, net_sign: 1 } as In).role).toBe('creditor')
  })

  it('frozen and suspended are a freeze (any case, trimmed); the known sign is KEPT under it', () => {
    for (const status of ['frozen', 'suspended', 'FROZEN', ' Suspended ']) {
      expect(classifyNode({ status, net_sign: -1 }), status).toMatchObject({ frozen: true, role: 'debtor', sign: MINUS })
      expect(classifyNode({ status, net_sign: 1 }), status).toMatchObject({ frozen: true, role: 'creditor', sign: '+' })
      expect(classifyNode({ status, net_sign: 0 }), status).toMatchObject({ frozen: true, role: 'neutral', sign: '0' })
      expect(classifyNode({ status }), status).toMatchObject({ frozen: true, role: 'unknown', sign: '?' })
    }
    for (const status of ['active', 'left', 'deleted', 'banned', '', undefined]) expect(classifyNode({ status, net_sign: 1 }).frozen, String(status)).toBe(false)
  })

  it('a hub is ONLY a participant of type "hub": not a busy one, not one called Hub, not a profile', () => {
    expect(classifyNode({ type: 'hub' }).hub).toBe(true)
    expect(classifyNode({ type: ' HUB ' }).hub).toBe(true)
    const lookalikes = [
      { type: 'business', links_count: 99, name: 'Hub', id: 'hub' },
      { type: 'person', links_count: 99, viz_badge_key: 'hub', viz_color_key: 'hub' },
      { type: 'anchor_hub' }, { type: 'community-hub' }, { type: undefined, links_count: 500 }, {},
    ]
    for (const n of lookalikes) expect(classifyNode(n as In).hub, JSON.stringify(n)).toBe(false)
  })

  it('the hub mark is independent of the role and of a freeze', () => {
    expect(classifyNode({ type: 'hub', net_sign: -1, status: 'frozen' })).toEqual({ role: 'debtor', frozen: true, hub: true, sign: MINUS })
    expect(classifyNode({ type: 'hub', net_sign: 1 })).toEqual({ role: 'creditor', frozen: false, hub: true, sign: '+' })
  })
})

describe('fill - which data colour a look is painted with', () => {
  const palette = { debtor: '#010101', creditor: '#020202', neutral: '#030303', frozen: '#040404', hub: '#050505' }

  it('by role; unknown like neutral; a freeze over any role; never the hub colour', () => {
    const token = (n: In) => DATA_TOKEN[fillTokenOf(classifyNode(n))]
    expect(token({ net_sign: -1 })).toBe('--ds-data-debtor')
    expect(token({ net_sign: 1 })).toBe('--ds-data-creditor')
    expect(token({ net_sign: 0 })).toBe('--ds-data-neutral')
    expect(token({ net_sign: null })).toBe('--ds-data-neutral')
    expect(token({ net_sign: -1, status: 'frozen' })).toBe('--ds-data-frozen')
    expect(token({ net_sign: 1, status: 'suspended' })).toBe('--ds-data-frozen')
    for (const net_sign of [-1, 0, 1, null] as const) expect(token({ type: 'hub', net_sign }), `hub ${String(net_sign)}`).not.toBe('--ds-data-hub')
  })

  it('the value is the palette entry of that token', () => {
    expect(fillOf(classifyNode({ net_sign: -1 }), palette)).toBe(palette.debtor)
    expect(fillOf(classifyNode({ net_sign: 1 }), palette)).toBe(palette.creditor)
    expect(fillOf(classifyNode({ net_sign: 1, status: 'frozen' }), palette)).toBe(palette.frozen)
  })
})

describe('legendEntriesFor - only what the snapshot contains, in the fixed order', () => {
  const person = (over: Partial<GraphNode>): GraphNode => ({ id: 'x', type: 'person', status: 'active', viz_shape_key: 'circle', ...over })

  it('an empty snapshot has no entries', () => {
    expect(legendEntriesFor([])).toEqual([])
  })

  it('the vocabulary and its order are fixed', () => {
    expect(LEGEND_VOCABULARY.map((e) => `${e.id}: ${e.text}`)).toEqual([
      'debtor: Owes more', 'creditor: Is owed more', 'neutral: Even overall', 'unknown: Balance unknown',
      'frozen: Frozen', 'hub: Hub', 'person: Person', 'organisation: Organisation',
    ])
  })

  it('one entry per feature that is present, whatever the order of the nodes', () => {
    const nodes = [
      person({ net_sign: null }), person({ net_sign: 1 }), person({ net_sign: 0 }),
      person({ net_sign: -1, type: 'business', viz_shape_key: 'rounded-rect' }), person({ net_sign: 1, status: 'frozen' }), person({ type: 'hub', net_sign: 0 }),
    ]
    expect(legendEntriesFor(nodes)).toEqual(['debtor', 'creditor', 'neutral', 'unknown', 'frozen', 'hub', 'person', 'organisation'])
    expect(legendEntriesFor([...nodes].reverse())).toEqual(legendEntriesFor(nodes))
  })

  it('an absent feature has no entry: creditors only', () => {
    expect(legendEntriesFor([person({ net_sign: 1 }), person({ net_sign: 1 })])).toEqual(['creditor', 'person'])
  })

  it('no Hub entry without a participant of type hub - however connected or however named', () => {
    const nodes = [person({ net_sign: 1, links_count: 99, name: 'Hub' }), person({ net_sign: -1, type: 'business', viz_shape_key: 'rounded-rect', links_count: 80 })]
    expect(legendEntriesFor(nodes)).not.toContain('hub')
    expect(legendEntriesFor([...nodes, person({ type: 'hub', net_sign: 0 })])).toContain('hub')
  })

  it('a frozen debtor is both: its sign is drawn inside the frozen node, so the role is explained too', () => {
    expect(legendEntriesFor([person({ net_sign: -1, status: 'suspended' })])).toEqual(['debtor', 'frozen', 'person'])
  })

  it('a hub drawn as a circle is not called a Person', () => {
    expect(legendEntriesFor([person({ type: 'hub', net_sign: 0 })])).toEqual(['neutral', 'hub'])
  })
})

describe('the resolved colours - read at the app root, not cached, one object for every consumer', () => {
  const made: HTMLElement[] = []
  const root = (vars: Record<string, string>): HTMLElement => {
    const el = document.createElement('div')
    for (const [k, v] of Object.entries(vars)) el.style.setProperty(k, v)
    document.body.appendChild(el)
    made.push(el)
    return el
  }
  afterEach(() => { for (const el of made.splice(0)) el.remove() })

  it('counter-check of the reader: a custom property set on an element is read from it', () => {
    const el = root({ '--ds-data-debtor': '#123456' })
    expect(getComputedStyle(el).getPropertyValue('--ds-data-debtor').trim()).toBe('#123456')
  })

  it('without a stylesheet the palette is the default one', () => {
    expect(createVizMapping().node.palette).toEqual(DATA_PALETTE_DEFAULT)
  })

  it('the tokens are read at the scope element, by their names', () => {
    const el = root({ '--ds-data-debtor': '#aa0001', '--ds-data-creditor': '#aa0002', '--ds-data-neutral': '#aa0003', '--ds-data-frozen': '#aa0004', '--ds-data-hub': '#aa0005' })
    expect(createVizMapping(el).node.palette).toEqual({ debtor: '#aa0001', creditor: '#aa0002', neutral: '#aa0003', frozen: '#aa0004', hub: '#aa0005' })
  })

  it('no eternal cache: a second resolution sees the changed value, in the SAME mapping object', () => {
    const el = root({ '--ds-data-debtor': '#aa0001', '--ds-glint': '#eeeeee' })
    const mapping = createVizMapping(el)
    const before = mapping.node.palette
    expect(before.debtor).toBe('#aa0001')
    el.style.setProperty('--ds-data-debtor', '#bb0001')
    el.style.setProperty('--ds-glint', '#dddddd')
    const after = resolveVizColors(mapping, el)
    expect(after.debtor).toBe('#bb0001')
    expect(mapping.node.palette, 'the renderer holds the mapping: it must see the new palette').toBe(after)
    expect(mapping.fx.glint).toBe('#dddddd')
    expect(before.debtor, 'a palette object once handed out is never mutated').toBe('#aa0001')
  })

  it('a token that is not #rrggbb is refused for the canvas and the DOM alike: the default stands in', () => {
    for (const bad of ['red', 'rgb(1, 2, 3)', '#abc', '#12345678', 'var(--nope)']) {
      const el = root({ '--ds-data-debtor': bad })
      expect(createVizMapping(el).node.palette.debtor, bad).toBe(DATA_PALETTE_DEFAULT.debtor)
    }
    expect(createVizMapping(root({ '--ds-data-debtor': '#ABCDEF' })).node.palette.debtor).toBe('#abcdef')
  })

  it('resolving reads the stylesheet once per token - and painting a node reads nothing', async () => {
    const el = root({})
    const spy = vi.spyOn(window, 'getComputedStyle')
    const mapping = createVizMapping(el)
    const perResolve = spy.mock.calls.length
    expect(perResolve, 'data tokens and FX tokens, each read once').toBe(Object.keys(DATA_TOKEN).length + 7)
    const { fillForNode } = await import('./render/nodeFill')
    for (let i = 0; i < 1000; i++) fillForNode({ net_sign: i % 2 ? 1 : -1 }, mapping)
    expect(spy.mock.calls.length, 'no read of a token per node').toBe(perResolve)
    spy.mockRestore()
  })
})

describe('DOM-independent settings', () => {
  it('the admissible wire colour keys are the fifteen the backend and the fixtures use; none of them is a colour here', () => {
    expect([...LEGACY_NODE_COLOR_KEYS].sort()).toEqual([
      'business', 'debt-0', 'debt-1', 'debt-2', 'debt-3', 'debt-4', 'debt-5', 'debt-6', 'debt-7', 'debt-8', 'deleted', 'left', 'person', 'suspended', 'unknown',
    ])
  })

  it('link widths and alphas are numbers', () => {
    for (const v of [...Object.values(VIZ_LINK.width_px), ...Object.values(VIZ_LINK.alpha)]) expect(typeof v).toBe('number')
  })
})

/**
 * FORM GUARD of `designSystem.tokens.css` (an architecture guard, AGENTS.md section 11). It reads the TEXT of the
 * stylesheet: that the five data tokens are declared exactly once, inside the first `:root` block, before any
 * `[data-theme]` block, as `#rrggbb`, and that the defaults of `vizMapping.ts` are those values.
 * It does NOT see what a browser computes (a later rule elsewhere, a typo in a selector) - that is
 * `e2e/qa/data-colours.qa.ts`, which reads the tokens under every theme in a real page.
 */
describe('form guard - the data tokens in designSystem.tokens.css', () => {
  const css = readFileSync(resolve(process.cwd(), 'src/ui-kit/designSystem.tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  const firstTheme = css.indexOf('[data-theme')
  const rootStart = css.indexOf(':root')
  const rootEnd = css.indexOf('}', rootStart)

  it('counter-check: the file has a :root block before its theme blocks', () => {
    expect(rootStart).toBeGreaterThanOrEqual(0)
    expect(firstTheme).toBeGreaterThan(rootEnd)
    expect(css.match(/\[data-theme=['"](saas|hud|library|shadcn)['"]\]\s*\{/g)?.length, 'the four theme blocks are still there').toBeGreaterThanOrEqual(4)
  })

  for (const name of Object.keys(DATA_TOKEN) as DataColorName[]) {
    it(`${DATA_TOKEN[name]} is declared once, in the common :root, as the default of vizMapping.ts`, () => {
      const decl = [...css.matchAll(new RegExp(`${DATA_TOKEN[name]}\\s*:\\s*([^;]+);`, 'g'))]
      expect(decl.length, 'declared exactly once in the whole file (no theme overrides it)').toBe(1)
      expect(decl[0]!.index!, 'inside the first :root block').toBeGreaterThan(rootStart)
      expect(decl[0]!.index!, 'inside the first :root block').toBeLessThan(rootEnd)
      expect(decl[0]![1]!.trim()).toBe(DATA_PALETTE_DEFAULT[name])
      expect(DATA_PALETTE_DEFAULT[name]).toMatch(/^#[0-9a-f]{6}$/)
    })
  }

  it('there are five data tokens and no sixth', () => {
    expect([...new Set(css.match(/--ds-data-[a-z-]+/g))].sort()).toEqual(Object.values(DATA_TOKEN).sort())
  })
})
