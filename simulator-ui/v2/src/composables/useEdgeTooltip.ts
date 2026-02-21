import type { Ref } from 'vue'
import type { GraphLink } from '../types'

import { placeOverlayNearAnchor } from '../utils/overlayPosition'

type HoveredEdgeLike = {
  key: string | null
  screenX: number
  screenY: number
}

type UseEdgeTooltipDeps = {
  hostEl: Ref<HTMLElement | null>
  hoveredEdge: HoveredEdgeLike
  clamp: (v: number, lo: number, hi: number) => number
  getUnit: () => string
}

type UseEdgeTooltipReturn = {
  formatEdgeAmountText: (link: GraphLink | null | undefined) => string
  edgeTooltipStyle: () => { left?: string; top?: string; display?: string }
}

export function useEdgeTooltip(deps: UseEdgeTooltipDeps): UseEdgeTooltipReturn {
  function formatEdgeAmountText(link: GraphLink | null | undefined) {
    const unit = deps.getUnit()
    const toNum = (v: unknown) => {
      const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
      return Number.isFinite(n) ? n : null
    }
    const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

    const used = toNum(link?.used)
    const limit = toNum(link?.trust_limit)
    const available = toNum(link?.available)

    const fromTxt = used !== null ? fmt.format(used) : available !== null ? fmt.format(available) : '—'
    const toTxt = limit !== null ? fmt.format(limit) : available !== null && used !== null ? fmt.format(available) : '—'

    return `from: ${fromTxt} / to: ${toTxt} ${unit}`
  }

  // §7.1 — Cached overlay size to avoid layout thrash on every pointermove.
  // DOM measurement (querySelector + getBoundingClientRect) is performed only
  // once per "show" event — when hoveredEdge.key transitions from null to a
  // non-null value — and the result is cached for subsequent pointermove calls.
  const FALLBACK_W = 260
  const FALLBACK_H = 120
  const cachedOverlaySize = { w: FALLBACK_W, h: FALLBACK_H }
  let prevKey: string | null = null

  function edgeTooltipStyle() {
    const host = deps.hostEl.value
    if (!host || !deps.hoveredEdge.key) {
      prevKey = null
      return { display: 'none' }
    }

    // Measure overlay size once on show (null→key transition), not on every pointermove.
    if (prevKey === null) {
      const tooltipEl = host.querySelector('[aria-label="Edge tooltip"]') as HTMLElement | null
      const tooltipRect = tooltipEl?.getBoundingClientRect()
      if (tooltipRect && tooltipRect.width > 0) {
        cachedOverlaySize.w = tooltipRect.width
        cachedOverlaySize.h = tooltipRect.height
      }
    }
    prevKey = deps.hoveredEdge.key

    const rect = host.getBoundingClientRect()

    return placeOverlayNearAnchor({
      // Hover coords are relative to host; clamp within host bounds.
      anchor: { x: deps.hoveredEdge.screenX, y: deps.hoveredEdge.screenY },
      overlaySize: { w: cachedOverlaySize.w, h: cachedOverlaySize.h },
      viewport: { w: rect.width, h: rect.height },
      pad: 10,
    })
  }

  return { formatEdgeAmountText, edgeTooltipStyle }
}
