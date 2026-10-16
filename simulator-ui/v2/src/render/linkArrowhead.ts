/**
 * The direction marker of a trust line: where its arrowhead is (039 slice B, PR 2; decision 4 of the slice's design).
 *
 * MONEY SEMANTICS (binding; AGENTS.md section 8, `docs/ru/02-protocol-spec.md:505-512`). A trust line
 * `source -> target` is creditor -> debtor: the source lets the target come to owe it, and `used` is the debt of the
 * target to the source. The arrowhead stands at the TARGET - the potential debtor. It shows the direction of TRUST,
 * not a payment and not an existing debt: it is the same triangle when `used` is zero. (A payment travels sender ->
 * receiver, against the line under which the obligation arises; that is the FX layer's beam, another object.)
 *
 * Pure geometry, no canvas. Sizes are in SCREEN (CSS) pixels and are divided by the zoom, as node sizes are
 * (`linkGeometry.ts`, `nodeGeometry.ts`): the triangle has one size on screen at any zoom.
 */
import type { LayoutNode } from '../types/layout'
import { getLinkTermination } from './linkGeometry'

/**
 * THE length of the arrowhead, tip to base, in screen px - the one size the product ships. MEASURED, not chosen: the
 * smallest whole length at which the marker stands out from a bare stretch of its own line at application zoom 0.6,
 * on the 100-node scene and on a line with no debt, in both themes (`e2e/qa/p039-repro/marker-size.p039-repro.ts`, the
 * curve is in `docs/ru/simulator/frontend/docs/visual-language.md` section 3.1). Decision 4 of the slice's design said
 * 5 x 4; revised 2026-10-16 after the external review found it barely legible. A test may override this one line
 * (the size frames of the owner do): everything below is derived from it.
 */
export const ARROW_LENGTH_PX = 5
/** Width to length, kept from decision 4 (4 : 5). */
export const ARROW_ASPECT = 0.8
/** The tip stops this far before the rim of the target (`getLinkTermination`), as in decision 4. */
export const ARROW_TIP_GAP_PX = 2

export const ARROW = {
  /** From the tip back to the base, along the line. */
  lengthPx: ARROW_LENGTH_PX,
  /** The base, across the line. */
  widthPx: ARROW_LENGTH_PX * ARROW_ASPECT,
  tipGapPx: ARROW_TIP_GAP_PX,
  /**
   * With less free line than this between the two rims there is NO marker: it would be drawn inside a node or past
   * the source. 2 x (tip gap + length): the room the two opposite arrowheads of a mutual pair need without
   * overlapping, so one bound serves a one-way line and a pair alike (14 px for decision 4's 5 px).
   */
  minFreeGapPx: 2 * (ARROW_TIP_GAP_PX + ARROW_LENGTH_PX),
} as const

/** The three corners, in world coordinates. `left` / `right` are the two ends of the base. */
export type Arrowhead = { tipX: number; tipY: number; leftX: number; leftY: number; rightX: number; rightY: number }

export const newArrowhead = (): Arrowhead => ({ tipX: 0, tipY: 0, leftX: 0, leftY: 0, rightX: 0, rightY: 0 })

/**
 * The arrowhead of the line `source -> target`, written into `out` (the caller's object: the hot path allocates
 * nothing). Returns `false`, leaving `out` as it was, when there is no room for a marker.
 */
export function trustArrowhead(source: LayoutNode, target: LayoutNode, invZoom: number, out: Arrowhead): boolean {
  const dx = target.__x - source.__x
  const dy = target.__y - source.__y
  const len = Math.hypot(dx, dy)
  // Coincident centres: no direction to point in (`getLinkTermination` returns the centre itself below 0.1).
  if (!(len >= 0.1)) return false
  const ux = dx / len
  const uy = dy / len

  // The same guard as `getLinkTermination`: a zoom of 0 must not put Infinity or NaN into a path.
  const inv = Number.isFinite(invZoom) && invZoom > 0 ? invZoom : 1

  // The pool of `getLinkTermination` hands out reused points: the first is read before the second call.
  const from = getLinkTermination(source, target, invZoom)
  const fromX = from.x
  const fromY = from.y
  const to = getLinkTermination(target, source, invZoom)

  // Free line between the rims, signed along source -> target (negative when the nodes overlap), in screen px.
  const freePx = ((to.x - fromX) * ux + (to.y - fromY) * uy) / inv
  if (!(freePx >= ARROW.minFreeGapPx)) return false

  const tipX = to.x - ux * ARROW.tipGapPx * inv
  const tipY = to.y - uy * ARROW.tipGapPx * inv
  const baseX = tipX - ux * ARROW.lengthPx * inv
  const baseY = tipY - uy * ARROW.lengthPx * inv
  const half = (ARROW.widthPx / 2) * inv

  out.tipX = tipX
  out.tipY = tipY
  out.leftX = baseX - uy * half
  out.leftY = baseY + ux * half
  out.rightX = baseX + uy * half
  out.rightY = baseY - ux * half
  return true
}
