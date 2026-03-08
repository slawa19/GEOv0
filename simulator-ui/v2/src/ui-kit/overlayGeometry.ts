import { warnOverlayDiagnostics } from './overlayDiagnostics'

export const DEFAULT_WM_CLAMP_PAD_PX = 12
export const DEFAULT_HUD_STACK_HEIGHT_PX = 110
export const DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX = 56

export const DEFAULT_WM_ANCHOR_OFFSET_X_PX = 16
export const DEFAULT_WM_ANCHOR_OFFSET_Y_PX = 16
export const DEFAULT_WM_CASCADE_STEP_PX = 32

export const DEFAULT_WM_INTERACT_MIN_WIDTH_PX = 320
export const DEFAULT_WM_INTERACT_MIN_HEIGHT_PX = 220
export const DEFAULT_WM_INTERACT_PREFERRED_WIDTH_TRUSTLINE_PX = 380
export const DEFAULT_WM_INTERACT_PREFERRED_WIDTH_WIDE_PX = 560
export const DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_LOADING_PX = 260
export const DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_CONFIRM_PX = 360
export const DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_PICKING_PX = 420

export const DEFAULT_WM_EDGE_DETAIL_MIN_WIDTH_PX = 340
export const DEFAULT_WM_EDGE_DETAIL_MIN_HEIGHT_PX = 200
export const DEFAULT_WM_EDGE_DETAIL_PREFERRED_WIDTH_PX = 420
export const DEFAULT_WM_EDGE_DETAIL_PREFERRED_HEIGHT_PX = 320

export const DEFAULT_WM_NODE_CARD_MIN_WIDTH_PX = 320
export const DEFAULT_WM_NODE_CARD_MIN_HEIGHT_PX = 180
export const DEFAULT_WM_NODE_CARD_PREFERRED_WIDTH_PX = 360
export const DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX = 260

// Safe viewport fallback (SSR/tests). Used before a host element is measured.
export const DEFAULT_VIEWPORT_FALLBACK_WIDTH_PX = 1280
export const DEFAULT_VIEWPORT_FALLBACK_HEIGHT_PX = 720

export const DEFAULT_WM_GROUP_Z_INSPECTOR_BASE = 0
export const DEFAULT_WM_GROUP_Z_INTERACT_BASE = 1000000

export type OverlayGeometryPx = {
  wmClampPadPx: number
  hudStackHeightPx: number
  hudBottomStackHeightPx: number

  wmAnchorOffsetXPx: number
  wmAnchorOffsetYPx: number
  wmCascadeStepPx: number

  wmInteractMinWidthPx: number
  wmInteractMinHeightPx: number
  wmInteractPreferredWidthTrustlinePx: number
  wmInteractPreferredWidthWidePx: number
  wmInteractPreferredHeightLoadingPx: number
  wmInteractPreferredHeightConfirmPx: number
  wmInteractPreferredHeightPickingPx: number

  wmEdgeDetailMinWidthPx: number
  wmEdgeDetailMinHeightPx: number
  wmEdgeDetailPreferredWidthPx: number
  wmEdgeDetailPreferredHeightPx: number

  wmNodeCardMinWidthPx: number
  wmNodeCardMinHeightPx: number
  wmNodeCardPreferredWidthPx: number
  wmNodeCardPreferredHeightPx: number

  wmGroupZInspectorBase: number
  wmGroupZInteractBase: number
}

export type MeasuredPublishedGeometryValue = {
  nextEpoch: () => number
  publish: (measuredPx: number | null | undefined, epoch: number) => boolean
  reset: () => void
  snapshot: () => number
}

export function normalizeMeasuredPublishedGeometryPx(
  measuredPx: number | null | undefined,
  fallbackPx: number,
): number {
  if (measuredPx == null || !Number.isFinite(measuredPx) || !(measuredPx > 0)) return fallbackPx
  return Math.round(measuredPx)
}

export function createMeasuredPublishedGeometryValue(
  fallbackPx: number,
  apply: (nextPx: number) => void,
): MeasuredPublishedGeometryValue {
  let epoch = 0
  let publishedPx = fallbackPx

  return {
    nextEpoch() {
      epoch += 1
      return epoch
    },
    publish(measuredPx, publishEpoch) {
      if (publishEpoch !== epoch) {
        warnOverlayDiagnostics('stale-publish', 'Ignoring stale overlay geometry publish.', {
          publishEpoch,
          currentEpoch: epoch,
          measuredPx,
        })
        return false
      }

      const nextPx = normalizeMeasuredPublishedGeometryPx(measuredPx, fallbackPx)
      if (nextPx === fallbackPx && (measuredPx == null || !Number.isFinite(measuredPx) || !(measuredPx > 0))) {
        warnOverlayDiagnostics('invalid-measured-size', 'Invalid measured overlay geometry; falling back to documented default.', {
          measuredPx,
          fallbackPx,
        })
      }
      if (nextPx === publishedPx) return false

      publishedPx = nextPx
      apply(nextPx)
      return true
    },
    reset() {
      epoch += 1
      if (publishedPx === fallbackPx) return

      publishedPx = fallbackPx
      apply(publishedPx)
    },
    snapshot() {
      return publishedPx
    },
  }
}

function parseCssPx(raw: string): number | null {
  const s = raw.trim()
  if (!s) return null

  // Support plain numbers ("12") and px values ("12px").
  const normalized = s.endsWith('px') ? s.slice(0, -2).trim() : s
  const n = Number(normalized)
  if (!Number.isFinite(n)) return null
  return n
}

function readCssVarFiniteNumber(el: Element, name: string): number | null {
  try {
    const v = window.getComputedStyle(el).getPropertyValue(name)
    return parseCssPx(v)
  } catch {
    return null
  }
}

function readCssVarPositivePx(el: Element, name: string): number | null {
  const value = readCssVarFiniteNumber(el, name)
  if (value == null || !(value > 0)) return null
  return value
}

function readCssVarPositivePxWithFallback(el: Element, name: string, fallback: number): number {
  const value = readCssVarPositivePx(el, name)
  if (value != null) return value

  warnOverlayDiagnostics('css-var-fallback', 'Using fallback overlay geometry token value.', {
    token: name,
    fallbackPx: fallback,
  })
  return fallback
}

function readCssVarFiniteNumberWithFallback(el: Element, name: string, fallback: number): number {
  const value = readCssVarFiniteNumber(el, name)
  if (value != null) return value

  warnOverlayDiagnostics('css-var-fallback', 'Using fallback overlay geometry numeric value.', {
    token: name,
    fallbackPx: fallback,
  })
  return fallback
}

/**
 * Read overlay/window geometry from DS CSS variables.
 * Safe in tests: falls back to defaults if CSS isn't loaded.
 */
export function readOverlayGeometryPx(el?: Element | null): OverlayGeometryPx {
  const hasDom = typeof window !== 'undefined' && typeof document !== 'undefined'
  const target = el ?? (hasDom ? document.documentElement : null)

  if (!target) {
    return {
      wmClampPadPx: DEFAULT_WM_CLAMP_PAD_PX,
      hudStackHeightPx: DEFAULT_HUD_STACK_HEIGHT_PX,
      hudBottomStackHeightPx: DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX,

      wmAnchorOffsetXPx: DEFAULT_WM_ANCHOR_OFFSET_X_PX,
      wmAnchorOffsetYPx: DEFAULT_WM_ANCHOR_OFFSET_Y_PX,
      wmCascadeStepPx: DEFAULT_WM_CASCADE_STEP_PX,

      wmInteractMinWidthPx: DEFAULT_WM_INTERACT_MIN_WIDTH_PX,
      wmInteractMinHeightPx: DEFAULT_WM_INTERACT_MIN_HEIGHT_PX,
      wmInteractPreferredWidthTrustlinePx: DEFAULT_WM_INTERACT_PREFERRED_WIDTH_TRUSTLINE_PX,
      wmInteractPreferredWidthWidePx: DEFAULT_WM_INTERACT_PREFERRED_WIDTH_WIDE_PX,
      wmInteractPreferredHeightLoadingPx: DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_LOADING_PX,
      wmInteractPreferredHeightConfirmPx: DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_CONFIRM_PX,
      wmInteractPreferredHeightPickingPx: DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_PICKING_PX,

      wmEdgeDetailMinWidthPx: DEFAULT_WM_EDGE_DETAIL_MIN_WIDTH_PX,
      wmEdgeDetailMinHeightPx: DEFAULT_WM_EDGE_DETAIL_MIN_HEIGHT_PX,
      wmEdgeDetailPreferredWidthPx: DEFAULT_WM_EDGE_DETAIL_PREFERRED_WIDTH_PX,
      wmEdgeDetailPreferredHeightPx: DEFAULT_WM_EDGE_DETAIL_PREFERRED_HEIGHT_PX,

      wmNodeCardMinWidthPx: DEFAULT_WM_NODE_CARD_MIN_WIDTH_PX,
      wmNodeCardMinHeightPx: DEFAULT_WM_NODE_CARD_MIN_HEIGHT_PX,
      wmNodeCardPreferredWidthPx: DEFAULT_WM_NODE_CARD_PREFERRED_WIDTH_PX,
      wmNodeCardPreferredHeightPx: DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX,

      wmGroupZInspectorBase: DEFAULT_WM_GROUP_Z_INSPECTOR_BASE,
      wmGroupZInteractBase: DEFAULT_WM_GROUP_Z_INTERACT_BASE,
    }
  }

  const wmClampPadPx = readCssVarPositivePxWithFallback(target, '--ds-wm-clamp-pad', DEFAULT_WM_CLAMP_PAD_PX)
  const hudStackHeightPx = readCssVarPositivePxWithFallback(target, '--ds-hud-stack-height', DEFAULT_HUD_STACK_HEIGHT_PX)
  const hudBottomStackHeightPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-hud-bottom-stack-height',
    DEFAULT_HUD_BOTTOM_STACK_HEIGHT_PX,
  )

  const wmAnchorOffsetXPx = readCssVarPositivePxWithFallback(target, '--ds-wm-anchor-offset-x', DEFAULT_WM_ANCHOR_OFFSET_X_PX)
  const wmAnchorOffsetYPx = readCssVarPositivePxWithFallback(target, '--ds-wm-anchor-offset-y', DEFAULT_WM_ANCHOR_OFFSET_Y_PX)
  const wmCascadeStepPx = readCssVarPositivePxWithFallback(target, '--ds-wm-cascade-step', DEFAULT_WM_CASCADE_STEP_PX)

  const wmInteractMinWidthPx = readCssVarPositivePxWithFallback(target, '--ds-wm-interact-minw', DEFAULT_WM_INTERACT_MIN_WIDTH_PX)
  const wmInteractMinHeightPx = readCssVarPositivePxWithFallback(target, '--ds-wm-interact-minh', DEFAULT_WM_INTERACT_MIN_HEIGHT_PX)
  const wmInteractPreferredWidthTrustlinePx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-interact-prefw-trustline',
    DEFAULT_WM_INTERACT_PREFERRED_WIDTH_TRUSTLINE_PX,
  )
  const wmInteractPreferredWidthWidePx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-interact-prefw-wide',
    DEFAULT_WM_INTERACT_PREFERRED_WIDTH_WIDE_PX,
  )
  const wmInteractPreferredHeightLoadingPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-interact-prefh-loading',
    DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_LOADING_PX,
  )
  const wmInteractPreferredHeightConfirmPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-interact-prefh-confirm',
    DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_CONFIRM_PX,
  )
  const wmInteractPreferredHeightPickingPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-interact-prefh-picking',
    DEFAULT_WM_INTERACT_PREFERRED_HEIGHT_PICKING_PX,
  )

  const wmEdgeDetailMinWidthPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-edge-detail-minw',
    DEFAULT_WM_EDGE_DETAIL_MIN_WIDTH_PX,
  )
  const wmEdgeDetailMinHeightPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-edge-detail-minh',
    DEFAULT_WM_EDGE_DETAIL_MIN_HEIGHT_PX,
  )
  const wmEdgeDetailPreferredWidthPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-edge-detail-prefw',
    DEFAULT_WM_EDGE_DETAIL_PREFERRED_WIDTH_PX,
  )
  const wmEdgeDetailPreferredHeightPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-edge-detail-prefh',
    DEFAULT_WM_EDGE_DETAIL_PREFERRED_HEIGHT_PX,
  )

  const wmNodeCardMinWidthPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-node-card-minw',
    DEFAULT_WM_NODE_CARD_MIN_WIDTH_PX,
  )
  const wmNodeCardMinHeightPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-node-card-minh',
    DEFAULT_WM_NODE_CARD_MIN_HEIGHT_PX,
  )
  const wmNodeCardPreferredWidthPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-node-card-prefw',
    DEFAULT_WM_NODE_CARD_PREFERRED_WIDTH_PX,
  )
  const wmNodeCardPreferredHeightPx = readCssVarPositivePxWithFallback(
    target,
    '--ds-wm-node-card-prefh',
    DEFAULT_WM_NODE_CARD_PREFERRED_HEIGHT_PX,
  )

  let wmGroupZInspectorBase = readCssVarFiniteNumberWithFallback(
    target,
    '--ds-wm-group-z-inspector-base',
    DEFAULT_WM_GROUP_Z_INSPECTOR_BASE,
  )
  let wmGroupZInteractBase = readCssVarFiniteNumberWithFallback(
    target,
    '--ds-wm-group-z-interact-base',
    DEFAULT_WM_GROUP_Z_INTERACT_BASE,
  )

  if (wmGroupZInteractBase <= wmGroupZInspectorBase) {
    warnOverlayDiagnostics('z-layer-mismatch', 'Invalid WM z-base ordering; reverting to documented defaults.', {
      wmGroupZInspectorBase,
      wmGroupZInteractBase,
      fallbackInspectorBase: DEFAULT_WM_GROUP_Z_INSPECTOR_BASE,
      fallbackInteractBase: DEFAULT_WM_GROUP_Z_INTERACT_BASE,
    })
    wmGroupZInspectorBase = DEFAULT_WM_GROUP_Z_INSPECTOR_BASE
    wmGroupZInteractBase = DEFAULT_WM_GROUP_Z_INTERACT_BASE
  }

  return {
    wmClampPadPx,
    hudStackHeightPx,
    hudBottomStackHeightPx,

    wmAnchorOffsetXPx,
    wmAnchorOffsetYPx,
    wmCascadeStepPx,

    wmInteractMinWidthPx,
    wmInteractMinHeightPx,
    wmInteractPreferredWidthTrustlinePx,
    wmInteractPreferredWidthWidePx,
    wmInteractPreferredHeightLoadingPx,
    wmInteractPreferredHeightConfirmPx,
    wmInteractPreferredHeightPickingPx,

    wmEdgeDetailMinWidthPx,
    wmEdgeDetailMinHeightPx,
    wmEdgeDetailPreferredWidthPx,
    wmEdgeDetailPreferredHeightPx,

    wmNodeCardMinWidthPx,
    wmNodeCardMinHeightPx,
    wmNodeCardPreferredWidthPx,
    wmNodeCardPreferredHeightPx,

    wmGroupZInspectorBase,
    wmGroupZInteractBase,
  }
}
