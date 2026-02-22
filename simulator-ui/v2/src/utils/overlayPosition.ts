import { computed, type ComputedRef } from 'vue'

export type Point = { x: number; y: number }

export function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

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

  const vw = o.viewport?.w ?? (typeof window !== 'undefined' ? window.innerWidth : 10_000)
  const vh = o.viewport?.h ?? (typeof window !== 'undefined' ? window.innerHeight : 10_000)

  const left = clamp(o.anchor.x + offX, pad, vw - o.overlaySize.w - pad)
  const top = clamp(o.anchor.y + offY, pad, vh - o.overlaySize.h - pad)

  return { left: `${left}px`, top: `${top}px` }
}

/**
 * Vue composable: вычисляет inline-style для позиционирования overlay рядом с anchor.
 * Возвращает пустой объект `{}` если anchor не задан → CSS default класса применяется как есть.
 * Возвращает `{ left, top, right: 'auto' }` если anchor задан → переопределяет CSS `right: 12px`.
 *
 * @param getAnchor  getter для anchor-точки; вызывается реактивно внутри computed
 * @param getHostEl  getter для host-элемента (viewport для clamping); может вернуть null
 * @param panelSize  ожидаемые размеры панели в px (w, h) для корректного clamping
 *
 * @example
 * ```ts
 * // В компоненте панели:
 * const positionStyle = useOverlayPositioning(
 *   () => props.anchor,
 *   () => props.hostEl,
 *   { w: 360, h: 340 },
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
    if (!anchor) return {}
    if (!Number.isFinite(anchor.x) || !Number.isFinite(anchor.y)) return {}

    const rect = getHostEl()?.getBoundingClientRect()

    // Safety: tolerate mixed coordinate systems.
    // `anchor` is expected to be host-relative, but some callers may pass
    // viewport-based coords (clientX/clientY). When we have host bounds,
    // detect that case and convert to host-relative.
    if (rect && rect.width > 0 && rect.height > 0) {
      const withinHost = anchor.x >= 0 && anchor.y >= 0 && anchor.x <= rect.width && anchor.y <= rect.height
      const withinViewportRect =
        anchor.x >= rect.left && anchor.y >= rect.top && anchor.x <= rect.right && anchor.y <= rect.bottom
      if (!withinHost && withinViewportRect) {
        anchor = { x: anchor.x - rect.left, y: anchor.y - rect.top }
      }
    }

    const pos = placeOverlayNearAnchor({
      anchor,
      overlaySize: panelSize,
      pad: 12,
      viewport: rect ? { w: rect.width, h: rect.height } : undefined,
    })

    return { ...pos, right: 'auto' } // override CSS `right: 12px`
  })
}
