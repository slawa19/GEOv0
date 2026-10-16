/**
 * 039 slice B, PR 1 - what the node painter issues: the fill of the role, the sign glyph, the dashed outline of a
 * freeze, the ring of a hub. The real painter runs on a context that RECORDS calls and models the part of the canvas
 * state these checks are about (save / restore of the line dash and of the styles).
 *
 * NOT seen here: pixels (anti-aliasing, what a 6 px glyph looks like), compositing, the glow sprites (mocked - their
 * colour argument is recorded). The picture is the owner's frames and the browser tier (`e2e/qa/data-colours.qa.ts`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LayoutNode } from '../types/layout'
import { createVizMapping, type VizMapping } from '../vizMapping'
import { drawGlowSprite } from './glowSprites'
import { drawNodeShape, NODE_SIGN_SIZE_PX } from './nodePainter'

vi.mock('./glowSprites', () => ({ drawGlowSprite: vi.fn() }))

const PALETTE = { debtor: '#110000', creditor: '#000011', neutral: '#111111', frozen: '#222222', hub: '#00ff00', direction: '#060606' } as const
const MAPPING: VizMapping = { ...createVizMapping(), node: { palette: PALETTE } }
const rgb = (hex: string) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},`

type Op = { op: string; args: number[] }
type Draw = { kind: 'stroke' | 'fill'; path: Op[]; strokeStyle: string; fillStyle: string; lineWidth: number; dash: number[]; lineCap: string }

function recorder() {
  const draws: Draw[] = []
  let path: Op[] = []
  type State = { strokeStyle: unknown; fillStyle: unknown; lineWidth: number; dash: number[]; lineCap: string; globalCompositeOperation: string; lineJoin: string }
  let s: State = { strokeStyle: '', fillStyle: '', lineWidth: 1, dash: [], lineCap: 'butt', globalCompositeOperation: 'source-over', lineJoin: 'miter' }
  const stack: State[] = []
  const rec = (op: string) => (...args: number[]) => { path.push({ op, args }) }
  const emit = (kind: Draw['kind']) => () => {
    draws.push({ kind, path: [...path], strokeStyle: String(s.strokeStyle), fillStyle: String(s.fillStyle), lineWidth: s.lineWidth, dash: [...s.dash], lineCap: s.lineCap })
  }
  const ctx = {
    save: () => { stack.push({ ...s, dash: [...s.dash] }) },
    restore: () => { s = stack.pop() ?? s },
    beginPath: () => { path = [] },
    moveTo: rec('moveTo'), lineTo: rec('lineTo'), arc: rec('arc'), quadraticCurveTo: rec('quad'), arcTo: rec('arcTo'), rect: rec('rect'), closePath: rec('close'),
    roundRect: rec('roundRect'),
    // Not part of what is judged here; a painter may call them.
    translate: () => undefined, fillRect: () => undefined,
    setLineDash: (d: number[]) => { s.dash = [...d] },
    getLineDash: () => [...s.dash],
    stroke: emit('stroke'), fill: emit('fill'),
    createLinearGradient: () => ({ addColorStop: () => undefined }),
    get depth() { return stack.length },
  }
  for (const k of ['strokeStyle', 'fillStyle', 'lineWidth', 'lineCap', 'lineJoin', 'globalCompositeOperation'] as const) {
    Object.defineProperty(ctx, k, { get: () => s[k], set: (v) => { (s as Record<string, unknown>)[k] = v } })
  }
  return { ctx: ctx as unknown as CanvasRenderingContext2D & { depth: number; getLineDash(): number[] }, draws }
}

const node = (over: Partial<LayoutNode> = {}): LayoutNode => ({
  id: 'n', type: 'person', status: 'active', net_sign: 0, viz_shape_key: 'circle', viz_size: { w: 20, h: 20 }, __x: 100, __y: 100, ...over,
} as LayoutNode)

const paint = (n: LayoutNode, o: { zoom?: number; quality?: 'low' | 'med' | 'high' } = {}) => {
  const r = recorder()
  drawNodeShape(r.ctx, n, { mapping: MAPPING, cameraZoom: o.zoom ?? 1, quality: o.quality ?? 'low' })
  return r
}

/** The glyph is the one stroke with round caps (the outline and the hub ring are butt-capped). */
const glyphOf = (draws: Draw[]) => draws.filter((d) => d.kind === 'stroke' && d.lineCap === 'round')
const shapeOf = (d: Draw) => d.path.map((p) => p.op).join(' ')

beforeEach(() => { vi.mocked(drawGlowSprite).mockClear() })

describe('node painter - the fill is the role', () => {
  it('+1 is painted with the creditor colour and -1 with the debtor colour, 0 and unknown with the neutral', () => {
    const body = (n: LayoutNode) => paint(n).draws.find((d) => d.kind === 'fill')!.fillStyle
    expect(body(node({ net_sign: 1 }))).toContain(rgb(PALETTE.creditor))
    expect(body(node({ net_sign: -1 }))).toContain(rgb(PALETTE.debtor))
    expect(body(node({ net_sign: 0 }))).toContain(rgb(PALETTE.neutral))
    expect(body(node({ net_sign: null }))).toContain(rgb(PALETTE.neutral))
    expect(body(node({ net_sign: -1, status: 'frozen' }))).toContain(rgb(PALETTE.frozen))
  })

  it('three debtors of different kinds and colour keys get ONE colour - body and glow', () => {
    const kinds = [node({ net_sign: -1, viz_color_key: 'debt-2' }), node({ net_sign: -1, viz_color_key: 'debt-8' }), node({ net_sign: -1, type: 'business', viz_color_key: 'business', viz_shape_key: 'rounded-rect', viz_size: { w: 26, h: 22 } })]
    const bodies = kinds.map((n) => paint(n).draws.find((d) => d.kind === 'fill')!.fillStyle)
    expect(new Set(bodies).size).toBe(1)
    expect(vi.mocked(drawGlowSprite).mock.calls.map((c) => c[1].color)).toEqual(Array(6).fill(PALETTE.debtor))
  })

  it('the shape and the size still come from the wire (viz_shape_key, viz_size)', () => {
    const circle = paint(node({ viz_size: { w: 20, h: 20 } })).draws.find((d) => d.kind === 'fill')!
    expect(shapeOf(circle)).toBe('arc')
    expect(circle.path[0]!.args[2]).toBe(10)
    const big = paint(node({ viz_size: { w: 40, h: 40 } })).draws.find((d) => d.kind === 'fill')!
    expect(big.path[0]!.args[2]).toBe(20)
    const rect = paint(node({ viz_shape_key: 'rounded-rect', viz_size: { w: 26, h: 22 } })).draws.find((d) => d.kind === 'fill')!
    expect(shapeOf(rect)).not.toBe('arc')
  })
})

describe('node painter - the sign glyph', () => {
  const SIGNS = [{ net_sign: -1, name: 'minus' }, { net_sign: 1, name: 'plus' }, { net_sign: 0, name: 'zero' }, { net_sign: null, name: 'unknown' }] as const

  it('each of the four signs is a different drawing, one stroke each', () => {
    const shapes = SIGNS.map((s) => {
      const g = glyphOf(paint(node({ net_sign: s.net_sign })).draws)
      expect(g, s.name).toHaveLength(1)
      return shapeOf(g[0]!)
    })
    expect(shapes).toEqual(['moveTo lineTo', 'moveTo lineTo moveTo lineTo', 'moveTo quad quad quad quad', 'arc lineTo moveTo lineTo'])
  })

  it('minus is a horizontal bar through the centre and plus adds the vertical one: about 6 CSS px on screen', () => {
    const minus = glyphOf(paint(node({ net_sign: -1 })).draws)[0]!.path
    expect(minus.map((p) => p.args)).toEqual([[100 - NODE_SIGN_SIZE_PX / 2, 100], [100 + NODE_SIGN_SIZE_PX / 2, 100]])
    const plus = glyphOf(paint(node({ net_sign: 1 })).draws)[0]!.path
    expect(plus.slice(2).map((p) => p.args)).toEqual([[100, 100 - NODE_SIGN_SIZE_PX / 2], [100, 100 + NODE_SIGN_SIZE_PX / 2]])
    expect(NODE_SIGN_SIZE_PX).toBe(6)
  })

  it('never dropped: every quality and every zoom draws it, at a constant size on screen', () => {
    for (const quality of ['low', 'med', 'high'] as const) {
      for (const zoom of [0.05, 0.2, 1, 4, 20]) {
        for (const s of SIGNS) {
          const g = glyphOf(paint(node({ net_sign: s.net_sign }), { quality, zoom }).draws)
          expect(g, `${s.name} q=${quality} z=${zoom}`).toHaveLength(1)
        }
        const bar = glyphOf(paint(node({ net_sign: -1 }), { quality, zoom }).draws)[0]!.path
        expect((bar[1]!.args[0]! - bar[0]!.args[0]!) * zoom, `bar length on screen, q=${quality} z=${zoom}`).toBeCloseTo(NODE_SIGN_SIZE_PX, 6)
      }
    }
  })

  it('the glyph is light ink, not the fill colour, and is never dashed - also inside a frozen node', () => {
    for (const status of ['active', 'frozen']) {
      const g = glyphOf(paint(node({ net_sign: -1, status })).draws)[0]!
      expect(g.strokeStyle).toContain('rgba(255,255,255,')
      expect(g.dash).toEqual([])
    }
  })

  it('a frozen node keeps the sign of its known role', () => {
    expect(shapeOf(glyphOf(paint(node({ net_sign: 1, status: 'frozen' })).draws)[0]!)).toBe('moveTo lineTo moveTo lineTo')
    expect(shapeOf(glyphOf(paint(node({ net_sign: -1, status: 'suspended' })).draws)[0]!)).toBe('moveTo lineTo')
  })

  it('a node smaller than the glyph gets a smaller glyph, still drawn (the sizing floor is 6 px)', () => {
    const bar = glyphOf(paint(node({ net_sign: -1, viz_size: { w: 6, h: 6 } })).draws)[0]!.path
    const len = bar[1]!.args[0]! - bar[0]!.args[0]!
    expect(len).toBeGreaterThan(2)
    expect(len).toBeLessThan(6)
  })
})

describe('node painter - a freeze is a dashed outline', () => {
  const outlineOf = (draws: Draw[]) => draws.filter((d) => d.kind === 'stroke' && d.lineCap !== 'round' && d.strokeStyle.includes('rgba(255,255,255,'))

  it('frozen and suspended: the outline is dashed; active: it is solid', () => {
    for (const status of ['frozen', 'suspended']) {
      const o = outlineOf(paint(node({ status, net_sign: 1 })).draws)
      expect(o, status).toHaveLength(1)
      expect(o[0]!.dash.length, `${status}: dashed`).toBe(2)
      expect(o[0]!.dash.every((d) => d > 0)).toBe(true)
    }
    expect(outlineOf(paint(node({ status: 'active' })).draws)[0]!.dash).toEqual([])
  })

  it('the dash keeps its size on screen at any zoom', () => {
    const at = (zoom: number) => outlineOf(paint(node({ status: 'frozen' }), { zoom }).draws)[0]!.dash.map((d) => d * zoom)
    expect(at(4)[0]).toBeCloseTo(at(1)[0]!, 6)
    expect(at(0.25)[1]).toBeCloseTo(at(1)[1]!, 6)
  })

  it('a frozen node has no continuous rim under its dashes (the bloom stays)', () => {
    paint(node({ status: 'frozen' }))
    expect(vi.mocked(drawGlowSprite).mock.calls.map((c) => c[1].kind)).toEqual(['bloom'])
    vi.mocked(drawGlowSprite).mockClear()
    paint(node({ status: 'active' }))
    expect(vi.mocked(drawGlowSprite).mock.calls.map((c) => c[1].kind)).toEqual(['bloom', 'rim'])
  })

  it('the canvas state is restored: nothing drawn after a frozen node inherits its dash', () => {
    const r = recorder()
    drawNodeShape(r.ctx, node({ status: 'frozen', type: 'hub' }), { mapping: MAPPING, cameraZoom: 1, quality: 'low' })
    expect(r.ctx.getLineDash(), 'the dash after the painter returns').toEqual([])
    expect(r.ctx.depth, 'every save has its restore').toBe(0)
    drawNodeShape(r.ctx, node({ status: 'active', type: 'hub' }), { mapping: MAPPING, cameraZoom: 1, quality: 'low' })
    const second = r.draws.slice(r.draws.length / 2)
    expect(second.filter((d) => d.kind === 'stroke').map((d) => d.dash)).toEqual([[], [], []])
  })
})

describe('node painter - the hub mark', () => {
  const ringOf = (draws: Draw[]) => draws.filter((d) => d.kind === 'stroke' && d.strokeStyle === PALETTE.hub)

  it('a participant of type hub gets ONE thin ring in the hub colour, outside its outline, solid', () => {
    const { draws } = paint(node({ type: 'hub', net_sign: 1 }))
    const ring = ringOf(draws)
    expect(ring).toHaveLength(1)
    expect(ring[0]!.dash).toEqual([])
    expect(ring[0]!.lineWidth).toBeLessThan(2)
    expect(ring[0]!.path[0]!.args[2], 'ring radius > node radius 10').toBeGreaterThan(10)
    // The fill is still the role, and the size is not changed.
    const body = draws.find((d) => d.kind === 'fill')!
    expect(body.fillStyle).toContain(rgb(PALETTE.creditor))
    expect(body.path[0]!.args[2]).toBe(10)
  })

  it('no ring for anything else: a busy organisation, a participant named Hub, a missing type', () => {
    for (const over of [{ type: 'business', links_count: 99 }, { type: 'person', name: 'Hub', id: 'hub' }, { type: undefined }, { type: 'anchor_hub' }]) {
      expect(ringOf(paint(node(over as Partial<LayoutNode>)).draws), JSON.stringify(over)).toHaveLength(0)
    }
  })

  it('nothing is FILLED with the hub colour', () => {
    const { draws } = paint(node({ type: 'hub' }))
    expect(draws.filter((d) => d.kind === 'fill' && d.fillStyle.includes(rgb(PALETTE.hub)))).toHaveLength(0)
    expect(vi.mocked(drawGlowSprite).mock.calls.map((c) => c[1].color)).not.toContain(PALETTE.hub)
  })
})
