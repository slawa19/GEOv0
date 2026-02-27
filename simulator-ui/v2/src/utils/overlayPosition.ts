import { computed, type ComputedRef } from 'vue'
import type { Point } from '../types/layout'

import { clamp } from './math'

export type { Point } from '../types/layout'

/**
 * Clamp an overlay with known (or fallback) size to the viewport.
 * Coordinates must be in the same coordinate system as the viewport.
 *
 * - If `viewport` is omitted, the viewport is the browser window (`window.innerWidth/innerHeight`)
 *   and coordinates are expected to be viewport-based (clientX/clientY).
 * - If `viewport` is provided (e.g. host element bounds), coordinates are expected to be
 *   relative to that viewport (e.g. clientToScreen).
 */
export function placeOverlayNearAnchor(o: {
  anchor: Point
  overlaySize: { w: number; h: number }
  offset?: { x: number; y: number }
  pad?: number
  viewport?: { w: number; h: number }
}): { left: string; top: string } {
  const pad = o.pad ?? 10
  const offX = o.offset?.x ?? 12
  const offY = o.offset?.y ?? 12

  const w = (globalThis as any).window as any
  const vw = o.viewport?.w ?? (w?.innerWidth ?? 10_000)
  const vh = o.viewport?.h ?? (w?.innerHeight ?? 10_000)

  const left = clamp(o.anchor.x + offX, pad, vw - o.overlaySize.w - pad)
  const top = clamp(o.anchor.y + offY, pad, vh - o.overlaySize.h - pad)

  return { left: `${left}px`, top: `${top}px` }
}

/**
 * Normalizes an overlay anchor to a host-relative coordinate system.
 *
 * Some call sites historically mixed coordinate systems:
 * - expected: host-relative coords (e.g. clientToScreen)
 * - sometimes provided: viewport-based coords (clientX/clientY)
 *
 * When host bounds are available, we detect the viewport-based case and convert it.
 *
 * IMPORTANT: behavior is intentionally heuristic and must remain stable
 * (see call sites in EdgeDetailPopup + useOverlayPositioning).
 */
export function normalizeAnchorToHostViewport(anchor: Point, hostRect: DOMRect | null | undefined): Point {
  const rect = hostRect
  if (!rect || !(rect.width > 0) || !(rect.height > 0)) return anchor

  const withinHost = anchor.x >= 0 && anchor.y >= 0 && anchor.x <= rect.width && anchor.y <= rect.height
  const withinViewportRect = anchor.x >= rect.left && anchor.y >= rect.top && anchor.x <= rect.right && anchor.y <= rect.bottom
  if (!withinHost && withinViewportRect) {
    return { x: anchor.x - rect.left, y: anchor.y - rect.top }
  }
  return anchor
}

/**
 * Vue composable: вычисляет inline-style для позиционирования overlay рядом с anchor.
 * Возвращает пустой объект `{}` если anchor не задан → CSS default класса применяется как есть.
 * Возвращает `{ left, top, right: 'auto' }` если anchor задан → переопределяет CSS `right: 12px`.
 *
 * @param getAnchor  getter для anchor-точки; вызывается реактивно внутри computed
 * @param getHostEl  getter для host-элемента (viewport для clamping); может вернуть null
 * @param panelSize  ожидаемые МАКСИМАЛЬНЫЕ размеры панели в px (w, h) для корректного clamping.
 *
 * ⚠️  ВАЖНО: panelSize.w должен соответствовать CSS max-width панели, а НЕ min-width!
 *    Для .ds-ov-panel: max-width = min(560px, 100vw - 24px) → передавай { w: 560, ... }.
 *    Если использовать меньшее значение (напр. 360), clamping будет считать правый край
 *    слишком близко → панель вылезет за правый край экрана на (560 - 360) = 200px.
 *
 * PositionING math (placeOverlayNearAnchor):
 *   left = clamp(anchor.x + offX,  pad,  vw - panelSize.w - pad)
 *   top  = clamp(anchor.y + offY,  pad,  vh - panelSize.h - pad)
 *   where offX = offY = 12, pad = 12 (defaults).
 *
 * Типичные источники anchor и их семантика:
 *   • NodeCard → selectedNodeScreenCenter = worldToScreen(node.__x, node.__y)
 *     (центр ноды в host-relative координатах)
 *   • ActionBar → getActionBarAnchor() = { x: hostRect.width, y: 98 }
 *     (x > vw → зажимается до right: 12px; y+12 = 110px = топ под ActionBar)
 *   • EdgeDetailPopup → state.edgeAnchor (midpoint ребра, host-relative)
 *   • null → CSS default (.ds-ov-panel: right: 12px; top: 110px)
 *
 * @example
 * ```ts
 * // В компоненте панели — ВСЕГДА используй CSS max-width как panelSize.w:
 * const positionStyle = useOverlayPositioning(
 *   () => props.anchor,
 *   () => props.hostEl,
 *   { w: 560, h: 340 },  // 560 = max-width .ds-ov-panel
 * )
 * // В template: <div class="ds-ov-panel" :style="positionStyle">
 * ```
 */
export function useOverlayPositioning(
  getAnchor: () => Point | null | undefined,
  getHostEl: () => HTMLElement | null | undefined,
  panelSize: { w: number; h: number },
): ComputedRef<Record<string, string>> {
  return computed(() => {
    let anchor = getAnchor()
    if (!anchor) { console.warn('[PANEL-DEBUG] useOverlayPositioning: anchor is null => CSS default'); return {} }
    if (!Number.isFinite(anchor.x) || !Number.isFinite(anchor.y)) { console.warn('[PANEL-DEBUG] useOverlayPositioning: anchor not finite => CSS default'); return {} }

    const rect = getHostEl()?.getBoundingClientRect()

    // Safety: tolerate mixed coordinate systems (host-relative vs viewport-based).
    anchor = normalizeAnchorToHostViewport(anchor, rect)

    const pos = placeOverlayNearAnchor({
      anchor,
      overlaySize: panelSize,
      pad: 12,
      viewport: rect ? { w: rect.width, h: rect.height } : undefined,
    })

    console.warn('[PANEL-DEBUG] useOverlayPositioning: anchor =>', JSON.stringify(anchor), '=> style', JSON.stringify(pos))
    return { ...pos, right: 'auto' } // override CSS `right: 12px`
  })
}
