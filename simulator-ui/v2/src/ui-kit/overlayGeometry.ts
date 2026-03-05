export const DEFAULT_WM_CLAMP_PAD_PX = 12
export const DEFAULT_HUD_STACK_HEIGHT_PX = 110

export type OverlayGeometryPx = {
  wmClampPadPx: number
  hudStackHeightPx: number
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

function readCssVarPx(el: Element, name: string): number | null {
  try {
    const v = window.getComputedStyle(el).getPropertyValue(name)
    return parseCssPx(v)
  } catch {
    return null
  }
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
    }
  }

  const wmClampPadPx = readCssVarPx(target, '--ds-wm-clamp-pad') ?? DEFAULT_WM_CLAMP_PAD_PX
  const hudStackHeightPx = readCssVarPx(target, '--ds-hud-stack-height') ?? DEFAULT_HUD_STACK_HEIGHT_PX

  return {
    wmClampPadPx,
    hudStackHeightPx,
  }
}
