/**
 * 039 slice B, PR 1 - the legend, and the agreement of the three places a participant is coloured:
 * the canvas (the real node painter on a recording context), the legend and the node card.
 *
 * The palette is MADE UP and its five values all differ: nothing here knows a real colour, and a consumer that read a
 * token or a constant of its own instead of the object it was handed would be seen.
 * NOT seen here: pixels, and what a browser computes for the swatch (`e2e/qa/data-colours.qa.ts`).
 */
import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Legend from './Legend.vue'
import NodeCardOverlay from './NodeCardOverlay.vue'
import { drawNodeShape } from '../render/nodePainter'
import type { GraphNode } from '../types'
import type { LayoutNode } from '../types/layout'
import { createVizMapping, type DataPalette, type VizMapping } from '../vizMapping'
import { setUiLocale } from '../i18n/locale'
import { t } from '../i18n/t'

vi.mock('../render/glowSprites', () => ({ drawGlowSprite: vi.fn() }))

const PALETTE: DataPalette = { debtor: '#a10000', creditor: '#0000b2', neutral: '#c3c3c3', frozen: '#444444', hub: '#00e500' }
const MAPPING: VizMapping = { ...createVizMapping(), node: { palette: PALETTE } }

/** `#rrggbb`, `rgb(r, g, b)` and `rgba(r,g,b,a)` to one form: "r,g,b". */
function rgbOf(color: string): string {
  const c = color.trim()
  if (c.startsWith('#')) return [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)).join(',')
  const m = /rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/.exec(c)
  if (!m) throw new Error(`not a colour: "${color}"`)
  return `${m[1]},${m[2]},${m[3]}`
}

function mount(component: Component, props: Record<string, unknown>) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(component, props) })
  app.mount(host)
  return { host, done: () => { app.unmount(); host.remove() } }
}

const n = (id: string, over: Partial<GraphNode>): GraphNode => ({ id, name: id, type: 'person', status: 'active', viz_shape_key: 'circle', viz_color_key: 'person', ...over })

const SCENE: GraphNode[] = [
  n('debtor', { net_sign: -1, viz_color_key: 'debt-3' }),
  n('creditor', { net_sign: 1 }),
  n('even', { net_sign: 0 }),
  n('unknown', { net_sign: null }),
  n('frozen-creditor', { net_sign: 1, status: 'frozen', viz_color_key: 'suspended' }),
  n('shop', { net_sign: -1, type: 'business', viz_shape_key: 'rounded-rect', viz_color_key: 'debt-8' }),
  n('hub', { net_sign: 0, type: 'hub' }),
]

const entry = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-legend-role="${id}"]`)
const swatch = (host: HTMLElement, id: string) => entry(host, id)!.querySelector<HTMLElement>('[data-legend-swatch]')!

afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
})

const rowsOf = (host: HTMLElement) => [...host.querySelectorAll<HTMLElement>('[data-legend-role]')].map((el) => `${el.querySelector('[data-legend-swatch]')!.textContent}|${el.querySelector('.ds-legend__text')!.textContent}`)

describe('Legend', () => {
  // 046 wave 1, full (2026-10-19): the words are the dictionary's (`legend.*`), those of 048 §G - in English they changed
  // with §G (was "Owes more", "Is owed more", "Even overall", "Balance unknown", "Frozen", "Hub", "Person", "Organisation").
  it('says each present feature in the fixed words and order, with the sign the canvas draws', () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
    expect(rowsOf(host)).toEqual(['−|owes more', '+|is owed more', '0|even', '?|unknown', '|frozen', '|hub', '|person', '|organisation'])
    done()
  })

  it('046 wave 1: in Russian the same entries and order in the words of 048 §G; a switch re-renders without a remount', async () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
    setUiLocale('ru')
    await nextTick()
    expect(rowsOf(host)).toEqual([
      '−|должен больше', '+|ему должны больше', '0|ровно', '?|неизвестно', '|заморожен', '|хаб', '|человек', '|организация',
    ])
    expect(host.querySelector('[data-testid="legend-title"]')!.textContent).toBe('Цвет участника — баланс · UAH')
    expect(document.getElementById(host.querySelector('[data-testid="legend"]')!.getAttribute('aria-describedby')!)!.textContent).toBe(t('legend.help', undefined, 'ru'))
    done()
  })

  it('the title carries the equivalent it was given, and the plain-words explanation is its accessible description', () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: 'HOUR', palette: PALETTE })
    const legend = host.querySelector<HTMLElement>('[data-testid="legend"]')!
    const title = host.querySelector<HTMLElement>('[data-testid="legend-title"]')!
    expect(title.textContent).toBe('Node colour — balance · HOUR')
    expect(legend.getAttribute('role')).toBe('group')
    expect(legend.getAttribute('aria-labelledby')).toBe(title.id)
    const help = document.getElementById(legend.getAttribute('aria-describedby')!)!
    expect(help.textContent).toBe('Net position: what others owe minus what this participant owes.')
    expect(title.getAttribute('title')).toBe(help.textContent)
    done()
  })

  it('without an equivalent the title names none (it does not borrow one)', () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: '', palette: PALETTE })
    expect(host.querySelector('[data-testid="legend-title"]')!.textContent).toBe('Node colour — balance')
    done()
  })

  it('nothing at all for an empty or missing list of nodes', () => {
    for (const nodes of [[], null, undefined]) {
      const { host, done } = mount(Legend, { nodes, equivalent: 'UAH', palette: PALETTE })
      expect(host.querySelector('[data-testid="legend"]'), String(nodes)).toBeNull()
      expect(host.textContent).toBe('')
      done()
    }
  })

  it('an entry appears and disappears with the feature - a patch of net_sign is enough, nothing is reloaded', async () => {
    const nodes = reactive<GraphNode[]>([n('a', { net_sign: 1 }), n('b', { net_sign: 1 })])
    const { host, done } = mount(Legend, { nodes, equivalent: 'UAH', palette: PALETTE })
    const ids = () => [...host.querySelectorAll('[data-legend-role]')].map((el) => el.getAttribute('data-legend-role'))
    expect(ids()).toEqual(['creditor', 'person'])
    nodes[1]!.net_sign = -1
    await nextTick()
    expect(ids()).toEqual(['debtor', 'creditor', 'person'])
    nodes[1]!.net_sign = null
    await nextTick()
    expect(ids()).toEqual(['creditor', 'unknown', 'person'])
    nodes[0]!.status = 'suspended'
    await nextTick()
    expect(ids()).toEqual(['creditor', 'unknown', 'frozen', 'person'])
    done()
  })

  it('a swatch is coloured from the palette it was handed: a new palette object recolours it', async () => {
    const props = reactive({ nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
    const host = document.createElement('div')
    document.body.appendChild(host)
    const app = createApp({ render: () => h(Legend, props) })
    app.mount(host)
    expect(rgbOf(swatch(host, 'debtor').style.backgroundColor)).toBe(rgbOf(PALETTE.debtor))
    props.palette = { ...PALETTE, debtor: '#010203' }
    await nextTick()
    expect(rgbOf(swatch(host, 'debtor').style.backgroundColor)).toBe('1,2,3')
    app.unmount()
    host.remove()
  })

  it('the hub is a ring: its colour is an outline, never a fill; shapes carry no data colour', () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
    expect(swatch(host, 'hub').style.backgroundColor).toBe('')
    expect(rgbOf(swatch(host, 'hub').style.borderColor)).toBe(rgbOf(PALETTE.hub))
    for (const id of ['person', 'organisation']) expect(swatch(host, id).getAttribute('style') ?? '', id).toBe('')
    done()
  })
})

describe('the canvas, the legend and the node card agree', () => {
  /** What the REAL painter fills the body of this node with. */
  function canvasFill(node: GraphNode): string {
    const fills: string[] = []
    let fillStyle: unknown = ''
    const ctx = new Proxy({}, {
      get: (_t, k) => {
        if (k === 'fill') return () => { fills.push(String(fillStyle)) }
        if (k === 'fillStyle') return fillStyle
        return () => undefined
      },
      set: (_t, k, v) => { if (k === 'fillStyle') fillStyle = v; return true },
    }) as unknown as CanvasRenderingContext2D
    drawNodeShape(ctx, { ...node, viz_size: { w: 20, h: 20 }, __x: 0, __y: 0 } as LayoutNode, { mapping: MAPPING, cameraZoom: 1, quality: 'low' })
    expect(fills.length, 'the painter filled the body').toBeGreaterThan(0)
    return rgbOf(fills[0]!)
  }

  const legendEntryOf: Record<string, string> = { debtor: 'debtor', creditor: 'creditor', even: 'neutral', unknown: 'unknown', 'frozen-creditor': 'frozen', shop: 'debtor', hub: 'neutral' }

  for (const node of SCENE) {
    it(`${node.id}: one colour on the canvas, in the legend and in the card`, () => {
      const onCanvas = canvasFill(node)
      const legend = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
      const inLegend = rgbOf(swatch(legend.host, legendEntryOf[node.id]!).style.backgroundColor)
      const card = mount(NodeCardOverlay, {
        node, edgeStats: null, equivalentText: 'UAH', palette: PALETTE, showPinActions: false, isPinned: false, pin: () => undefined, unpin: () => undefined,
      })
      const cardSwatch = card.host.querySelector<HTMLElement>('[data-testid="node-card-role-swatch"]')!
      const balance = card.host.querySelector<HTMLElement>('.ds-ov-node-card__balance')!
      expect(inLegend, 'legend == canvas').toBe(onCanvas)
      expect(rgbOf(cardSwatch.style.backgroundColor), 'card swatch == canvas').toBe(onCanvas)
      expect(rgbOf(balance.style.color), 'card balance == canvas').toBe(onCanvas)
      // And the sign: the card shows the glyph of the same classifier answer the legend entry of the ROLE shows.
      const roleEntry = { debtor: 'debtor', creditor: 'creditor', even: 'neutral', unknown: 'unknown', 'frozen-creditor': 'creditor', shop: 'debtor', hub: 'neutral' }[node.id]!
      expect(cardSwatch.textContent).toBe(swatch(legend.host, roleEntry).textContent)
      legend.done()
      card.done()
    })
  }

  it('counter-check: the made-up palette has five different colours, and the cases cover four of them as fills', () => {
    expect(new Set(Object.values(PALETTE)).size).toBe(5)
    expect(new Set(SCENE.map(canvasFill)).size).toBe(4)
  })

  // 046 wave 1 (2026-10-19): the card keeps its own English words (`ROLE_TEXT`) until wave 3 moves it; the legend's are
  // the dictionary's since. What is held here is the card: the role first, a freeze and a hub added, never replacing it.
  it('the card says the role in its words; a freeze and a hub are added, the role is not replaced', () => {
    const words = (node: GraphNode) => {
      const c = mount(NodeCardOverlay, { node, edgeStats: null, equivalentText: 'UAH', palette: PALETTE, showPinActions: false, isPinned: false, pin: () => undefined, unpin: () => undefined })
      const el = c.host.querySelector<HTMLElement>('[data-testid="node-card-role"]')!
      const out = { text: [...el.children].map((c) => c.textContent!.trim()).join(' '), role: el.getAttribute('data-role') }
      c.done()
      return out
    }
    expect(words(SCENE[0]!)).toEqual({ text: '− Owes more', role: 'debtor' })
    expect(words(SCENE[1]!)).toEqual({ text: '+ Is owed more', role: 'creditor' })
    expect(words(SCENE[2]!)).toEqual({ text: '0 Even overall', role: 'neutral' })
    expect(words(SCENE[3]!)).toEqual({ text: '? Balance unknown', role: 'unknown' })
    expect(words(SCENE[4]!)).toEqual({ text: '+ Is owed more · Frozen', role: 'creditor' })
    expect(words(SCENE[6]!)).toEqual({ text: '0 Even overall · Hub', role: 'neutral' })
  })

  it('an unknown sign shows no balance in the card: magnitude atoms are not printed as a positive amount', () => {
    const balanceOf = (over: Partial<GraphNode>) => {
      const c = mount(NodeCardOverlay, { node: n('x', over), edgeStats: null, equivalentText: 'UAH', palette: PALETTE, showPinActions: false, isPinned: false, pin: () => undefined, unpin: () => undefined })
      const text = c.host.querySelector('.ds-ov-node-card__balance')!.textContent
      c.done()
      return text
    }
    expect(balanceOf({ net_sign: null, net_balance_atoms: '4200' })).toBe('—')
    expect(balanceOf({ net_balance_atoms: '4200' })).toBe('—')
    // Counter-checks: the known signs still print, and zero is zero.
    expect(balanceOf({ net_sign: 1, net_balance_atoms: '4200' })).toBe('42.00')
    expect(balanceOf({ net_sign: -1, net_balance_atoms: '4200' })).toBe('-42.00')
    expect(balanceOf({ net_sign: 0, net_balance_atoms: '0' })).toBe('0.00')
  })
})

// Carried over 2026-10-16 (039 B3) from the withdrawn branch of slice B, PR 2 (commit 16fff143), the swatch alone.
describe('Legend - the frozen swatch shows its dashed outline', () => {
  it('the outline is a circle of separate dashes round the swatch, and the fill is still the freeze colour of the palette', () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
    const s = swatch(host, 'frozen')
    expect(rgbOf(s.style.backgroundColor)).toBe(rgbOf(PALETTE.frozen))
    const circle = s.querySelector('[data-legend-dash] circle')!
    expect(circle, 'the dashed outline').not.toBeNull()
    const [dash, gap] = circle.getAttribute('stroke-dasharray')!.split(/\s+/).map(Number) as [number, number]
    const total = Number(circle.getAttribute('pathLength'))
    expect(dash).toBeGreaterThan(0)
    expect(gap, 'there IS a gap between dashes').toBeGreaterThan(0)
    expect(Number.isInteger(Math.round((total / (dash + gap)) * 1e6) / 1e6), 'a whole number of dashes: no seam where the circle closes').toBe(true)
    // On screen (r = 7 px): each dash and each gap is long enough to be told apart at legend size.
    const unitPx = (2 * Math.PI * Number(circle.getAttribute('r'))) / total
    expect(dash * unitPx, 'dash length in px').toBeGreaterThanOrEqual(2.5)
    expect(gap * unitPx, 'gap length in px').toBeGreaterThanOrEqual(2)
    done()
  })

  it('only the frozen swatch carries it', () => {
    const { host, done } = mount(Legend, { nodes: SCENE, equivalent: 'UAH', palette: PALETTE })
    expect(host.querySelectorAll('[data-legend-dash]')).toHaveLength(1)
    for (const id of ['debtor', 'creditor', 'neutral', 'unknown', 'hub']) {
      const e = entry(host, id)
      if (e) expect(e.querySelector('[data-legend-dash]'), `${id} has no dashed outline`).toBeNull()
    }
    done()
  })
})
