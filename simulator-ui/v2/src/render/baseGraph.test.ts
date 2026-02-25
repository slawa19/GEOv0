/**
 * ITEM-14 — null-guard for pos.get()! in both links-passes of drawBaseGraph.
 *
 * Verifies:
 *  1. A dangling link (target absent from pos Map) does NOT throw (base pass + overlay pass).
 *  2. Valid links cause ctx.stroke() to be called (smoke).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LayoutLink } from '../types/layout'
import type { VizMapping } from '../vizMapping'
import { drawBaseGraph } from './baseGraph'

// ------------------------------------------------------------------ mocks --
// Heavy sub-renderers require a real Canvas / DOM –
// replace with no-op stubs so tests run in Node environment.

vi.mock('./nodePainter', () => ({
  drawNodeShape: vi.fn(),
  fillForNode: vi.fn(() => '#ffffff'),
}))

vi.mock('./glowSprites', () => ({
  drawGlowSprite: vi.fn(),
}))

// ----------------------------------------------------------------- helpers -

const MAPPING: VizMapping = {
  node: { color: {} },
  link: {
    width_px: { hairline: 0.6, thin: 0.9, mid: 1.1, thick: 1.25, highlight: 2.2 },
    alpha: { bg: 0.06, muted: 0.12, active: 0.20, hi: 0.32 },
    color: { default: '#64748b' },
  },
  fx: {
    tx_spark: { core: '#fff', trail: '#fff' },
    clearing_credit: '#fff',
    clearing_debt: '#fff',
    flash: { clearing: { from: '#fff', to: '#fff' } },
  },
}

type MockCtx = Omit<CanvasRenderingContext2D, 'canvas'> & {
  stroke: ReturnType<typeof vi.fn>
  beginPath: ReturnType<typeof vi.fn>
  moveTo: ReturnType<typeof vi.fn>
  lineTo: ReturnType<typeof vi.fn>
}

function makeCtx(): MockCtx {
  const noop = vi.fn()
  return {
    // writable properties accessed as plain assignments:
    strokeStyle: '' as string,
    lineWidth: 0 as number,
    fillStyle: '' as string,
    globalAlpha: 1 as number,
    globalCompositeOperation: 'source-over' as string,
    shadowBlur: 0 as number,
    shadowColor: '' as string,
    font: '' as string,
    textAlign: 'start' as string,
    textBaseline: 'alphabetic' as string,
    imageSmoothingEnabled: false as boolean,
    // tracked methods:
    stroke: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    // remaining ctx surface – stubbed so they don't throw:
    fill: noop,
    arc: noop,
    rect: noop,
    fillRect: noop,
    roundRect: noop,
    closePath: noop,
    quadraticCurveTo: noop,
    save: noop,
    restore: noop,
    translate: noop,
    scale: noop,
    rotate: noop,
    clearRect: noop,
    drawImage: noop,
    clip: noop,
    fillText: noop,
    strokeText: noop,
    setTransform: noop,
    measureText: vi.fn(() => ({ width: 0 })),
    createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
    getImageData: noop,
    putImageData: noop,
    createImageData: noop,
    getTransform: vi.fn(() => ({})),
    isPointInPath: vi.fn(() => false),
    isPointInStroke: vi.fn(() => false),
    resetTransform: noop,
    transform: noop,
    strokeRect: noop,
    drawFocusIfNeeded: noop,
    scrollPathIntoView: noop,
    direction: 'ltr' as string,
    letterSpacing: '' as string,
    wordSpacing: '' as string,
    fontKerning: 'auto' as string,
    fontStretch: 'normal' as string,
    fontVariantCaps: 'normal' as string,
    textRendering: 'auto' as string,
    strokeStyle_: '' as string,
  } as unknown as MockCtx
}

function makeNode(id: string, x = 0, y = 0): any {
  return {
    id,
    __x: x,
    __y: y,
    viz_shape_key: 'circle',
    viz_size: { w: 12, h: 12 },
    viz_color_key: 'unknown',
  }
}

function makeLink(source: string, target: string): LayoutLink {
  return { source, target, __key: `${source}-${target}` } as LayoutLink
}

function baseOpts(overrides: Partial<Parameters<typeof drawBaseGraph>[1]> = {}) {
  return {
    w: 400,
    h: 300,
    nodes: [],
    links: [],
    mapping: MAPPING,
    selectedNodeId: null,
    activeEdges: new Map<string, number>(),
    ...overrides,
  } as Parameters<typeof drawBaseGraph>[1]
}

// ------------------------------------------------------------------- tests -

describe('drawBaseGraph — ITEM-14 orphan-link null-guard', () => {
  let ctx: MockCtx

  beforeEach(() => {
    ctx = makeCtx()
  })

  // ── base pass ──────────────────────────────────────────────────────────────

  it('base pass: dangling target → does NOT throw', () => {
    const nodeA = makeNode('A', 10, 10)
    const linkAB = makeLink('A', 'B') // 'B' absent from nodes

    expect(() =>
      drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
        nodes: [nodeA],
        links: [linkAB],
      })),
    ).not.toThrow()
  })

  it('base pass: dangling source → does NOT throw', () => {
    const nodeB = makeNode('B', 20, 20)
    const linkAB = makeLink('A', 'B') // 'A' absent from nodes

    expect(() =>
      drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
        nodes: [nodeB],
        links: [linkAB],
      })),
    ).not.toThrow()
  })

  it('base pass: fully dangling link (both endpoints missing) → does NOT throw', () => {
    const linkAB = makeLink('A', 'B') // neither in nodes

    expect(() =>
      drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
        nodes: [],
        links: [linkAB],
      })),
    ).not.toThrow()
  })

  // ── overlay pass ───────────────────────────────────────────────────────────

  it('overlay pass: dangling target on focus-incident link → does NOT throw', () => {
    const nodeA = makeNode('A', 10, 10)
    const linkAB = makeLink('A', 'B') // 'B' absent

    // selectedNodeId='A' → link is focus-incident → enters overlay pass
    expect(() =>
      drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
        nodes: [nodeA],
        links: [linkAB],
        selectedNodeId: 'A',
      })),
    ).not.toThrow()
  })

  it('overlay pass: dangling target on active link → does NOT throw', () => {
    const nodeA = makeNode('A', 10, 10)
    const linkAB = makeLink('A', 'B') // 'B' absent
    const activeEdges = new Map([['A-B', 0.8]])

    expect(() =>
      drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
        nodes: [nodeA],
        links: [linkAB],
        activeEdges,
      })),
    ).not.toThrow()
  })

  // ── valid links smoke ──────────────────────────────────────────────────────

  it('valid link (both endpoints present): ctx.stroke() is called in base pass', () => {
    const nodeA = makeNode('A', 0, 0)
    const nodeB = makeNode('B', 100, 0)
    const linkAB = makeLink('A', 'B')

    drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
      nodes: [nodeA, nodeB],
      links: [linkAB],
    }))

    expect(ctx.stroke).toHaveBeenCalled()
    expect(ctx.moveTo).toHaveBeenCalled()
    expect(ctx.lineTo).toHaveBeenCalled()
  })

  it('valid link with selectedNodeId: ctx.stroke() is called in overlay pass too', () => {
    const nodeA = makeNode('A', 0, 0)
    const nodeB = makeNode('B', 100, 0)
    const linkAB = makeLink('A', 'B')

    drawBaseGraph(ctx as unknown as CanvasRenderingContext2D, baseOpts({
      nodes: [nodeA, nodeB],
      links: [linkAB],
      selectedNodeId: 'A',
    }))

    // base pass + overlay focus pass → stroke ≥ 2
    expect(ctx.stroke.mock.calls.length).toBeGreaterThanOrEqual(2)
  })
})
