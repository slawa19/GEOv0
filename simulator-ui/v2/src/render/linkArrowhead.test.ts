/**
 * 039 slice B, PR 2 - the GEOMETRY of the direction marker of a trust line (decision 4 of the slice's design).
 *
 * MONEY SEMANTICS (AGENTS.md section 8; `docs/ru/02-protocol-spec.md:505-512`): a trust line `source -> target` is
 * creditor -> debtor. The arrowhead stands at the TARGET (the potential debtor). These tests judge where the triangle
 * is and how big; what is painted with it is `baseGraph.direction.test.ts`, pixels are the stand's `direction.qa.ts`.
 */
import { describe, expect, it } from 'vitest'

import type { LayoutNode } from '../types/layout'
import { ARROW, newArrowhead, trustArrowhead, type Arrowhead } from './linkArrowhead'
import { getLinkTermination } from './linkGeometry'

const person = (id: string, x: number, y: number, size = 24): LayoutNode =>
  ({ id, __x: x, __y: y, viz_shape_key: 'circle', viz_size: { w: size, h: size } }) as LayoutNode
const shop = (id: string, x: number, y: number, w = 40, h = 30): LayoutNode =>
  ({ id, __x: x, __y: y, viz_shape_key: 'rounded-rect', viz_size: { w, h } }) as LayoutNode

const d = (ax: number, ay: number, bx: number, by: number) => Math.hypot(ax - bx, ay - by)

function arrow(source: LayoutNode, target: LayoutNode, zoom = 1): Arrowhead | null {
  const out = newArrowhead()
  return trustArrowhead(source, target, 1 / zoom, out) ? { ...out } : null
}

/** Sizes of the triangle in SCREEN px at `zoom` (world x zoom). */
function sizes(a: Arrowhead, zoom: number) {
  const baseX = (a.leftX + a.rightX) / 2
  const baseY = (a.leftY + a.rightY) / 2
  return { length: d(a.tipX, a.tipY, baseX, baseY) * zoom, width: d(a.leftX, a.leftY, a.rightX, a.rightY) * zoom }
}

describe('trustArrowhead - the arrowhead of a trust line stands at the target (debtor) end', () => {
  const creditor = person('creditor', 100, 300)
  const debtor = person('debtor', 300, 300)

  it('one-way line creditor -> debtor: the tip is 2 px before the rim of the DEBTOR, the base 5 px farther back, 4 px wide', () => {
    const a = arrow(creditor, debtor)!
    expect(a).not.toBeNull()
    // The debtor is a 24 px circle at x = 300: its rim towards the creditor is x = 288.
    expect(a.tipX).toBeCloseTo(286, 6)
    expect(a.tipY).toBeCloseTo(300, 6)
    expect(a.leftX).toBeCloseTo(281, 6)
    expect(a.rightX).toBeCloseTo(281, 6)
    expect([a.leftY, a.rightY].sort((p, q) => p - q)).toEqual([298, 302])
    expect(ARROW).toEqual({ lengthPx: 5, widthPx: 4, tipGapPx: 2, minFreeGapPx: 14 })
  })

  it('swapping source and target moves the arrowhead to the other end: it follows the TARGET, not a position', () => {
    const a = arrow(debtor, creditor)!
    // Now `creditor` (x = 100, rim at 112) is the target of the call.
    expect(a.tipX).toBeCloseTo(114, 6)
    expect(a.leftX).toBeCloseTo(119, 6)
  })

  it('every point of the triangle is in the free stretch between the two rims: never inside a node, never beyond the source', () => {
    const cases: Array<[LayoutNode, LayoutNode]> = [
      [creditor, debtor], [debtor, creditor],
      [person('p', 0, 0, 30), shop('s', 70, 45)], [shop('s', 70, 45), person('p', 0, 0, 30)],
      [shop('s1', 0, 0, 49, 42), shop('s2', -90, 60, 27, 23)],
    ]
    for (const [s, t] of cases) {
      for (const zoom of [0.5, 1, 2, 3.7]) {
        // At a small zoom these pairs can lose their room: then there is no marker, which the next block judges.
        const a = arrow(s, t, zoom)
        if (!a) continue
        const from = { ...getLinkTermination(s, t, 1 / zoom) }
        const to = { ...getLinkTermination(t, s, 1 / zoom) }
        const len = d(from.x, from.y, to.x, to.y)
        const along = (x: number, y: number) => ((x - from.x) * (to.x - from.x) + (y - from.y) * (to.y - from.y)) / len
        for (const [x, y] of [[a.tipX, a.tipY], [a.leftX, a.leftY], [a.rightX, a.rightY]] as const) {
          expect(along(x, y), `${s.id} -> ${t.id} at zoom ${zoom}: not beyond the source rim`).toBeGreaterThanOrEqual(-1e-9)
          expect(along(x, y), `${s.id} -> ${t.id} at zoom ${zoom}: not inside the target`).toBeLessThanOrEqual(len + 1e-9)
        }
        // The tip is nearer to the target than the base is: the triangle POINTS at the target.
        expect(along(a.tipX, a.tipY)).toBeGreaterThan(along(a.leftX, a.leftY))
      }
    }
  })

  it('the size on screen is the same at zoom 0.5, 1 and 2: 5 px long, 4 px wide, tip 2 px from the rim', () => {
    const far = person('far', 1000, 300) // far enough to have room at zoom 0.5 too
    for (const zoom of [0.5, 1, 2]) {
      const a = arrow(creditor, far, zoom)!
      expect(a, `a marker at zoom ${zoom}`).not.toBeNull()
      const s = sizes(a, zoom)
      expect(s.length, `length at zoom ${zoom}`).toBeCloseTo(5, 6)
      expect(s.width, `width at zoom ${zoom}`).toBeCloseTo(4, 6)
      const rim = { ...getLinkTermination(far, creditor, 1 / zoom) }
      expect(d(a.tipX, a.tipY, rim.x, rim.y) * zoom, `tip to rim at zoom ${zoom}`).toBeCloseTo(2, 6)
    }
  })

  it('a mutual pair: each direction has its arrowhead at ITS target, both on the common axis, and at 14 px of room their bases meet without overlap', () => {
    const ab = arrow(creditor, debtor)!
    const ba = arrow(debtor, creditor)!
    expect(ab.tipX).toBeCloseTo(286, 6)
    expect(ba.tipX).toBeCloseTo(114, 6)
    expect((ab.leftY + ab.rightY) / 2).toBeCloseTo(300, 6)
    expect((ba.leftY + ba.rightY) / 2).toBeCloseTo(300, 6)
    // Two 24 px circles 38 px apart: exactly 14 px between the rims.
    const near = person('near', 138, 300)
    const one = arrow(creditor, near)!
    const two = arrow(near, creditor)!
    expect(one, 'at exactly 14 px there is a marker').not.toBeNull()
    expect(one.leftX, 'base of creditor -> near').toBeCloseTo(119, 6)
    expect(two.leftX, 'base of near -> creditor: the same line, the two triangles touch').toBeCloseTo(119, 6)
  })

  it('less than 14 screen px between the rims: no marker - at zoom 1 by distance, and at a small zoom because the nodes keep their size on screen', () => {
    // 24 px circles: 37.9 px apart = 13.9 px of room.
    expect(arrow(creditor, person('n', 137.9, 300))).toBeNull()
    expect(arrow(creditor, person('n', 138, 300))).not.toBeNull()
    // Overlapping nodes (the rims have crossed) and coincident centres.
    expect(arrow(creditor, person('n', 110, 300))).toBeNull()
    expect(arrow(creditor, person('n', 100, 300))).toBeNull()
    // 200 world px apart: 176 px of room at zoom 1, and 200 * 0.18 - 24 = 12 px at zoom 0.18.
    expect(arrow(creditor, debtor, 1)).not.toBeNull()
    expect(arrow(creditor, debtor, 0.18)).toBeNull()
    expect(arrow(creditor, debtor, 0.2)).not.toBeNull() // 40 - 24 = 16
  })

  it('an organisation as the target: the tip is 2 px before the side of the rectangle the line comes to', () => {
    const s = shop('s', 300, 300, 40, 30) // the side towards x = 100 is x = 280
    const a = arrow(creditor, s)!
    expect(a.tipX).toBeCloseTo(278, 6)
    expect(a.tipY).toBeCloseTo(300, 6)
  })

  it('a non-finite or zero inverse zoom is treated as 1, as `getLinkTermination` does - no NaN reaches the canvas', () => {
    for (const inv of [0, Number.NaN, Number.POSITIVE_INFINITY, -1]) {
      const out = newArrowhead()
      expect(trustArrowhead(creditor, debtor, inv, out)).toBe(true)
      expect(Object.values(out).every(Number.isFinite)).toBe(true)
      expect(out.tipX).toBeCloseTo(286, 6)
    }
  })
})
