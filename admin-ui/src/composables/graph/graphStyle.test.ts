import { describe, expect, it, vi } from 'vitest'

import {
  buildGraphStylesheet,
  darkenHex,
  DEFAULT_NODE_COLOR,
  NODE_COLOR_BY_VIZ_KEY,
  nodeBaseColor,
  selectionBorderColor,
  statusColor,
  zoomStyleRules,
  type StyleRule,
} from './graphStyle'

// The stylesheet module is pure: if anything on its import path loaded Cytoscape at run time, this fails the file.
vi.mock('cytoscape', () => {
  throw new Error('graphStyle must not load cytoscape')
})

const rule = (rules: StyleRule[], selector: string) => rules.filter((r) => r.selector === selector)
const indexOf = (rules: StyleRule[], selector: string) => rules.findIndex((r) => r.selector === selector)

describe('node colour', () => {
  it('lets the status beat the server key, and the server key beat the type', () => {
    expect(nodeBaseColor({ status: 'suspended', vizColorKey: 'debt-8', type: 'person' })).toBe(NODE_COLOR_BY_VIZ_KEY.suspended)
    expect(nodeBaseColor({ status: 'frozen', vizColorKey: '', type: 'business' })).toBe(NODE_COLOR_BY_VIZ_KEY.suspended)
    expect(nodeBaseColor({ status: 'banned', vizColorKey: 'person', type: 'person' })).toBe(NODE_COLOR_BY_VIZ_KEY.deleted)
    expect(nodeBaseColor({ status: 'left', vizColorKey: '', type: '' })).toBe(NODE_COLOR_BY_VIZ_KEY.left)
    expect(nodeBaseColor({ status: 'active', vizColorKey: 'debt-8', type: 'person' })).toBe(NODE_COLOR_BY_VIZ_KEY['debt-8'])
    expect(nodeBaseColor({ status: 'active', vizColorKey: '', type: 'business' })).toBe(NODE_COLOR_BY_VIZ_KEY.business)
  })

  it('falls back to the default for what the palette does not name - including names an object inherits', () => {
    expect(nodeBaseColor({ status: 'active', vizColorKey: '', type: 'hub' })).toBe(DEFAULT_NODE_COLOR)
    expect(nodeBaseColor({ status: 'active', vizColorKey: 'toString', type: 'constructor' })).toBe(DEFAULT_NODE_COLOR)
    expect(statusColor('active')).toBeNull()
  })

  it('darkens by a factor, and turns a colour it cannot read into near-black', () => {
    expect(darkenHex('#ffffff', 0.5)).toBe('#808080')
    expect(darkenHex('#3b82f6', 0)).toBe('#000000')
    expect(darkenHex('#3b82f6', 7)).toBe('#3b82f6')
    expect(darkenHex('blue', 0.5)).toBe('#111318')
    expect(selectionBorderColor('#ffffff')).toBe(darkenHex('#ffffff', 0.35))
  })
})

describe('buildGraphStylesheet', () => {
  const sheet = buildGraphStylesheet({ showLabels: true })

  it('has a colour rule for every palette key, with the palette colour', () => {
    for (const [key, color] of Object.entries(NODE_COLOR_BY_VIZ_KEY)) {
      const found = rule(sheet, `node.viz-${key}`)
      expect(found.length, key).toBeGreaterThanOrEqual(1)
      expect(found[0]!.style['background-color'], key).toBe(color)
    }
    // Not vacuous: the palette is the nine gradient bins plus person, business, debt and three statuses.
    expect(Object.keys(NODE_COLOR_BY_VIZ_KEY)).toHaveLength(15)
  })

  it('orders the colour layers type < server key < status, and the selection contour last', () => {
    const type = indexOf(sheet, 'node.type-business')
    const viz = indexOf(sheet, 'node.viz-debt-3')
    const status = indexOf(sheet, 'node.p-suspended, node.p-frozen')
    const contour = indexOf(sheet, 'node.selected-node')
    expect(type).toBeGreaterThanOrEqual(0)
    expect(type).toBeLessThan(viz)
    expect(viz).toBeLessThan(status)
    expect(status).toBeLessThan(contour)
    expect(indexOf(sheet, 'node.selected-pulse')).toBe(sheet.length - 1)
  })

  it('colours the status aliases like the DB statuses', () => {
    expect(rule(sheet, 'node.p-deleted, node.p-banned')[0]!.style['background-color']).toBe(NODE_COLOR_BY_VIZ_KEY.deleted)
    expect(rule(sheet, 'node.p-left')[0]!.style['background-color']).toBe(NODE_COLOR_BY_VIZ_KEY.left)
  })

  it('draws labels only when asked to, and marks bottleneck and closed lines apart', () => {
    expect(rule(sheet, 'node')[0]!.style.label).toBe('data(label)')
    expect(rule(buildGraphStylesheet({ showLabels: false }), 'node')[0]!.style.label).toBe('')
    expect(rule(sheet, 'edge.bottleneck')[0]!.style['line-color']).toBe('#f56c6c')
    expect(rule(sheet, 'edge.tl-closed')[0]!.style.opacity).toBe(0.45)
  })
})

describe('zoomStyleRules', () => {
  const value = (zoom: number, selector: string, key: string) =>
    Number(rule(zoomStyleRules(zoom), selector)[0]!.style[key])

  it('is the base look at zoom 1', () => {
    expect(value(1, 'node', 'font-size')).toBe(11)
    expect(value(1, 'edge', 'width')).toBeCloseTo(1.2)
    expect(value(1, 'edge.bottleneck', 'width')).toBeCloseTo(2.4)
    expect(value(1, 'node.selected-node', 'border-width')).toBeCloseTo(3.5)
  })

  it('makes text larger and strokes thinner the further you zoom in, the other way out', () => {
    expect(value(4, 'node', 'font-size')).toBeLessThan(value(1, 'node', 'font-size'))
    expect(value(0.4, 'node', 'font-size')).toBeGreaterThan(value(1, 'node', 'font-size'))
    expect(value(4, 'edge', 'width')).toBeLessThan(value(1, 'edge', 'width'))
    expect(value(0.4, 'edge', 'width')).toBeLessThan(value(1, 'edge', 'width'))
  })

  it('keeps every value finite, positive and inside its bounds at absurd zoom levels', () => {
    for (const zoom of [0, 0.001, 0.15, 50, 1000]) {
      for (const r of zoomStyleRules(zoom)) {
        for (const [key, v] of Object.entries(r.style)) {
          expect(Number.isFinite(Number(v)), `${zoom} ${r.selector} ${key}`).toBe(true)
          expect(Number(v), `${zoom} ${r.selector} ${key}`).toBeGreaterThan(0)
        }
      }
      expect(value(zoom, 'node', 'font-size')).toBeGreaterThanOrEqual(3.2)
      expect(value(zoom, 'node', 'font-size')).toBeLessThanOrEqual(12)
      expect(value(zoom, 'edge', 'width')).toBeLessThanOrEqual(1.4)
    }
  })
})
