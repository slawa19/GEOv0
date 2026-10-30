import type { Ref } from 'vue'
import type { GraphLink } from '../types'

import { equivalentPrecision } from '../config/equivalentPrecision'
import { formatMoney, moneyText } from '../utils/money'
import { placeOverlayNearAnchor } from '../utils/overlayPosition'
import { t } from '../i18n/t'

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
    // 028 F-028-48: money as text at the equivalent's precision (was: Number + 2 fraction digits).
    const precision = equivalentPrecision(unit)
    const toNum = (v: unknown) => moneyText(v)
    const fmt = { format: (v: string) => formatMoney(v, precision) }

    const used = toNum(link?.used)
    const limit = toNum(link?.trust_limit)
    const available = toNum(link?.available)

    const fromTxt = used !== null ? fmt.format(used) : available !== null ? fmt.format(available) : '—'
    const toTxt = limit !== null ? fmt.format(limit) : available !== null && used !== null ? fmt.format(available) : '—'

    // 046 wave 1, full: through the dictionary (internal adversarial pass: the English literal under ?lang=ru).
    return t('edgeTooltip.amounts', { from: fromTxt, to: toTxt, unit })
  }

  // §7.1 — Cached overlay size to avoid layout thrash on every pointermove: the tooltip is measured once per show and the
  // result is reused by the following pointermoves.
  //
  // 054-B-9: the measurement waits for the tooltip to EXIST. The call that sets a new key comes before it is rendered
  // (`EdgeTooltip.vue` is under `v-if="edge.key"`, its `:style` is this function), so measuring only on the null->key call
  // never found it and the clamp always used the fallback. Now the first call of a show on which the element is there
  // measures it; on a call that changed the key the element still holds the previous line, so it is not measured then.
  // The fallback is used only until the first measurement; afterwards the last measured size stands in until the next.
  const FALLBACK_W = 260
  const FALLBACK_H = 120
  const cachedOverlaySize = { w: FALLBACK_W, h: FALLBACK_H }
  let prevKey: string | null = null
  let measuredKey: string | null = null

  function edgeTooltipStyle() {
    const host = deps.hostEl.value
    if (!host || !deps.hoveredEdge.key) {
      prevKey = null
      measuredKey = null
      return { display: 'none' }
    }

    const key = deps.hoveredEdge.key
    if (measuredKey !== key && (prevKey === null || prevKey === key)) {
      // 046 wave 1: found by its test id, not by its accessible name (which is in the language of the interface).
      const tooltipEl = host.querySelector('[data-testid="edge-tooltip"]') as HTMLElement | null
      const tooltipRect = tooltipEl?.getBoundingClientRect()
      if (tooltipRect && tooltipRect.width > 0) {
        cachedOverlaySize.w = tooltipRect.width
        cachedOverlaySize.h = tooltipRect.height
        measuredKey = key
      }
    }
    prevKey = key

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
