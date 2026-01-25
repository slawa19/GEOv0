import type { Ref } from 'vue'
import type { GraphLink } from '../types'

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

  function edgeTooltipStyle() {
    const host = deps.hostEl.value
    if (!host || !deps.hoveredEdge.key) return { display: 'none' }

    const rect = host.getBoundingClientRect()
    const pad = 10
    const x = deps.clamp(deps.hoveredEdge.screenX + 12, pad, rect.width - pad)
    const y = deps.clamp(deps.hoveredEdge.screenY + 12, pad, rect.height - pad)
    return { left: `${x}px`, top: `${y}px` }
  }

  return { formatEdgeAmountText, edgeTooltipStyle }
}
