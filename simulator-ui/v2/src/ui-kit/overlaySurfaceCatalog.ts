import {
  isEdgeDetailWindow,
  isInteractPanelWindow,
  isNodeCardWindow,
  type InteractPanelData,
  type WindowInstance,
} from '../composables/windowManager/types'

export type OverlaySurfaceFamily =
  | 'interact-panel'
  | 'inspector-card'
  | 'hud-bar'
  | 'hud-dropdown'
  | 'notification-toast'
  | 'bottom-overlay'
  | 'dev-overlay'
  | 'tooltip'
  | 'canvas-overlay'
  | 'message-overlay'
  /**
   * Analytics surface (spec 007, T703/T705). A family of its own rather than a docked column:
   * the canvas is `position: absolute; inset: 0` (`App.css:38-42`), so turning `.root` into a
   * grid would move the canvas and break the overlay model. Registered here so the panel is a
   * catalogued overlay like every other floating surface.
   */
  | 'analytics-panel'

export type OverlaySizingMode =
  | 'fixed-width-auto-height'
  | 'bounded-intrinsic'
  | 'intrinsic'
  | 'stretch'

export type OverlayPositioningOwner =
  | 'window-manager'
  | 'window-shell-anchor'
  | 'root-top-stack'
  | 'root-bottom-stack'
  | 'details-shell'
  | 'bottom-stack-offset'
  | 'fixed-corner'
  | 'cursor-following-shell'
  | 'viewport'
  | 'root-inset'
  /**
   * A column pinned to one vertical edge of `.root`, clearing the HUD stacks above and below it.
   * Unlike `fixed-corner` it is height-filling, and unlike `root-inset` it does not cover the
   * canvas — the geometry is fully described by `OverlaySurfaceDock` below, so the mount point
   * reads it instead of inventing offsets of its own.
   */
  | 'root-side-dock'

export type OverlayWidthOwner =
  | 'wm-policy'
  | 'measured-fallback'
  | 'stack-container'
  | 'dropdown-token-contract'
  | 'toast-clamp-tokens'
  | 'overlay-max-width-contract'
  | 'content'
  | 'viewport'

export type OverlayHeightOwner =
  | 'measured-fallback'
  | 'min-row-token'
  | 'dropdown-token-contract'
  | 'content'
  | 'content-max-height-token'
  | 'viewport'

export type OverlayZLayerToken =
  | '--ds-z-panel'
  | '--ds-z-top'
  | '--ds-z-bottom'
  | '--ds-z-inset'
  | '--ds-z-alert'
  | '--ds-z-dev'
  | '--ds-z-tooltip'
  | '--ds-z-world-labels'

export type OverlayAriaRole = 'dialog' | 'region' | 'status' | 'alert'
export type OverlayAriaLive = 'polite' | 'assertive'

/**
 * Geometry tokens a docked surface is allowed to name. Deliberately a closed union: a descriptor
 * may pick which token applies, never invent a pixel value — the numbers stay in the design system
 * (`designSystem.tokens.css`, `designSystem.overlays.css`), which this module must not duplicate.
 */
export type OverlayClearanceToken =
  | '--ds-hud-stack-height'
  | '--ds-hud-bottom-stack-height'

export type OverlayInsetToken = '--ds-ov-inset'

export type OverlayDockWidthToken = '--ds-ov-panel-maxw'

/**
 * The complete placement contract of a `root-side-dock` surface: which edge it hugs, what it has
 * to clear vertically, and which width contract bounds it.
 */
export type OverlaySurfaceDock = {
  edge: 'left' | 'right'
  topClearanceToken: OverlayClearanceToken
  bottomClearanceToken: OverlayClearanceToken
  insetToken: OverlayInsetToken
  widthToken: OverlayDockWidthToken
}

export type OverlaySurfaceKey =
  | 'wm-interact-window'
  | 'wm-inspector-window'
  | 'interact-select-dropdown'
  | 'top-hud-stack'
  | 'bottom-hud-stack'
  | 'hud-dropdown'
  | 'success-toast'
  | 'error-toast'
  | 'interact-history-overlay'
  | 'dev-perf-overlay'
  | 'edge-tooltip'
  | 'canvas-labels-overlay'
  | 'canvas-floating-labels-overlay'
  | 'loading-message-overlay'
  | 'error-message-overlay'
  | 'real-metrics-panel'

export type OverlaySurfaceA11y = {
  role: OverlayAriaRole
  ariaLabel?: string
  ariaLive?: OverlayAriaLive
}

export type OverlaySurfaceDescriptor = {
  key: OverlaySurfaceKey
  family: OverlaySurfaceFamily
  sizingMode: OverlaySizingMode
  positioningOwner: OverlayPositioningOwner
  widthOwner: OverlayWidthOwner
  heightOwner: OverlayHeightOwner
  zLayerToken: OverlayZLayerToken
  a11y?: OverlaySurfaceA11y
  /** Present exactly when `positioningOwner === 'root-side-dock'`. */
  dock?: OverlaySurfaceDock
}

export type ResolvedWindowSurfaceDescriptor = {
  descriptor: OverlaySurfaceDescriptor
  title: string
  role: Extract<OverlayAriaRole, 'dialog' | 'region'>
  ariaLabel: string
}

export const overlaySurfaceCatalog = {
  'wm-interact-window': {
    key: 'wm-interact-window',
    family: 'interact-panel',
    sizingMode: 'fixed-width-auto-height',
    positioningOwner: 'window-manager',
    widthOwner: 'wm-policy',
    heightOwner: 'measured-fallback',
    zLayerToken: '--ds-z-panel',
    a11y: { role: 'dialog' },
  },
  'wm-inspector-window': {
    key: 'wm-inspector-window',
    family: 'inspector-card',
    sizingMode: 'bounded-intrinsic',
    positioningOwner: 'window-manager',
    widthOwner: 'wm-policy',
    heightOwner: 'measured-fallback',
    zLayerToken: '--ds-z-panel',
    a11y: { role: 'region' },
  },
  'top-hud-stack': {
    key: 'top-hud-stack',
    family: 'hud-bar',
    sizingMode: 'stretch',
    positioningOwner: 'root-top-stack',
    widthOwner: 'stack-container',
    heightOwner: 'min-row-token',
    zLayerToken: '--ds-z-top',
  },
  'bottom-hud-stack': {
    key: 'bottom-hud-stack',
    family: 'hud-bar',
    sizingMode: 'stretch',
    positioningOwner: 'root-bottom-stack',
    widthOwner: 'stack-container',
    heightOwner: 'min-row-token',
    zLayerToken: '--ds-z-bottom',
  },
  'interact-select-dropdown': {
    key: 'interact-select-dropdown',
    family: 'hud-dropdown',
    sizingMode: 'bounded-intrinsic',
    positioningOwner: 'window-shell-anchor',
    widthOwner: 'dropdown-token-contract',
    heightOwner: 'dropdown-token-contract',
    zLayerToken: '--ds-z-inset',
  },
  'hud-dropdown': {
    key: 'hud-dropdown',
    family: 'hud-dropdown',
    sizingMode: 'bounded-intrinsic',
    positioningOwner: 'details-shell',
    widthOwner: 'dropdown-token-contract',
    heightOwner: 'dropdown-token-contract',
    zLayerToken: '--ds-z-inset',
  },
  'success-toast': {
    key: 'success-toast',
    family: 'notification-toast',
    sizingMode: 'intrinsic',
    positioningOwner: 'bottom-stack-offset',
    widthOwner: 'toast-clamp-tokens',
    heightOwner: 'content',
    zLayerToken: '--ds-z-alert',
    a11y: {
      role: 'status',
      ariaLive: 'polite',
      ariaLabel: 'Success notification',
    },
  },
  'error-toast': {
    key: 'error-toast',
    family: 'notification-toast',
    sizingMode: 'intrinsic',
    positioningOwner: 'bottom-stack-offset',
    widthOwner: 'toast-clamp-tokens',
    heightOwner: 'content',
    zLayerToken: '--ds-z-alert',
    a11y: {
      role: 'alert',
      ariaLive: 'assertive',
      ariaLabel: 'Error notification',
    },
  },
  'interact-history-overlay': {
    key: 'interact-history-overlay',
    family: 'bottom-overlay',
    sizingMode: 'intrinsic',
    positioningOwner: 'root-bottom-stack',
    widthOwner: 'content',
    heightOwner: 'content',
    zLayerToken: '--ds-z-bottom',
  },
  'dev-perf-overlay': {
    key: 'dev-perf-overlay',
    family: 'dev-overlay',
    sizingMode: 'intrinsic',
    positioningOwner: 'fixed-corner',
    widthOwner: 'overlay-max-width-contract',
    heightOwner: 'content-max-height-token',
    zLayerToken: '--ds-z-dev',
    a11y: {
      role: 'region',
      ariaLabel: 'Performance diagnostics',
    },
  },
  'edge-tooltip': {
    key: 'edge-tooltip',
    family: 'tooltip',
    sizingMode: 'intrinsic',
    positioningOwner: 'cursor-following-shell',
    widthOwner: 'content',
    heightOwner: 'content',
    zLayerToken: '--ds-z-tooltip',
    a11y: {
      role: 'region',
      ariaLabel: 'Edge tooltip',
    },
  },
  'canvas-labels-overlay': {
    key: 'canvas-labels-overlay',
    family: 'canvas-overlay',
    sizingMode: 'stretch',
    positioningOwner: 'viewport',
    widthOwner: 'viewport',
    heightOwner: 'viewport',
    zLayerToken: '--ds-z-world-labels',
  },
  'canvas-floating-labels-overlay': {
    key: 'canvas-floating-labels-overlay',
    family: 'canvas-overlay',
    sizingMode: 'stretch',
    positioningOwner: 'viewport',
    widthOwner: 'viewport',
    heightOwner: 'viewport',
    zLayerToken: '--ds-z-world-labels',
  },
  'loading-message-overlay': {
    key: 'loading-message-overlay',
    family: 'message-overlay',
    sizingMode: 'intrinsic',
    positioningOwner: 'root-inset',
    widthOwner: 'content',
    heightOwner: 'content',
    zLayerToken: '--ds-z-inset',
  },
  'error-message-overlay': {
    key: 'error-message-overlay',
    family: 'message-overlay',
    sizingMode: 'intrinsic',
    positioningOwner: 'root-inset',
    widthOwner: 'content',
    heightOwner: 'content',
    zLayerToken: '--ds-z-inset',
  },
  'real-metrics-panel': {
    key: 'real-metrics-panel',
    family: 'analytics-panel',
    sizingMode: 'bounded-intrinsic',
    /**
     * T705 placement. A right-hand column, not a `root-inset` cover: the panel is read alongside
     * the graph, so it must not sit on top of it. It clears the top and bottom HUD stacks by the
     * same published tokens those stacks measure themselves into
     * (`SimulatorAppRoot` writes `--ds-hud-stack-height` / `--ds-hud-bottom-stack-height`), which
     * is why the panel follows a growing TopBar instead of being clipped by it.
     */
    positioningOwner: 'root-side-dock',
    widthOwner: 'overlay-max-width-contract',
    heightOwner: 'content-max-height-token',
    zLayerToken: '--ds-z-panel',
    dock: {
      edge: 'right',
      topClearanceToken: '--ds-hud-stack-height',
      bottomClearanceToken: '--ds-hud-bottom-stack-height',
      insetToken: '--ds-ov-inset',
      widthToken: '--ds-ov-panel-maxw',
    },
    a11y: {
      role: 'region',
      ariaLabel: 'Run analytics',
    },
  },
} as const satisfies Record<OverlaySurfaceKey, OverlaySurfaceDescriptor>

type InteractPanelKind = InteractPanelData['panel']

function interactPanelTitle(panel: InteractPanelKind): string {
  switch (panel) {
    case 'payment':
      return 'Manual payment'
    case 'trustline':
      return 'Trustline'
    case 'clearing':
      return 'Clearing'
  }
}

function interactPanelAriaLabel(panel: InteractPanelKind): string {
  switch (panel) {
    case 'payment':
      return 'Manual payment panel'
    case 'trustline':
      return 'Trustline management panel'
    case 'clearing':
      return 'Clearing panel'
  }
}

export function getOverlaySurfaceDescriptor<Key extends OverlaySurfaceKey>(
  key: Key,
): (typeof overlaySurfaceCatalog)[Key] {
  return overlaySurfaceCatalog[key]
}

/**
 * The custom properties a `root-side-dock` mount point consumes. Named here, not in the mount
 * point, so that a stylesheet reading them cannot drift from the descriptor that produces them.
 */
export type OverlayDockStyle = {
  '--ds-ov-dock-top': string
  '--ds-ov-dock-bottom': string
  '--ds-ov-dock-left': string
  '--ds-ov-dock-right': string
  '--ds-ov-dock-width': string
  '--ds-ov-dock-z': string
}

/**
 * Translate a docked descriptor into inline custom properties for its mount point.
 *
 * Custom properties rather than `top`/`right`/`z-index` directly: the mount point still owns the
 * declarations (one scoped rule), but every value in them — which edge, which clearances, which
 * width contract, which z layer — comes from the catalog. Nothing numeric appears here; the
 * numbers stay in the design-system stylesheets that define these tokens on `.ds-ov-vars`, and a
 * docked surface is only ever mounted inside that scope.
 *
 * Throws when the descriptor is not a docked surface: a caller asking for dock geometry that the
 * catalog does not define has a wiring bug, and silently returning an empty style would place the
 * surface at the top-left corner of the canvas without saying why.
 */
export function resolveOverlayDockStyle(key: OverlaySurfaceKey): OverlayDockStyle {
  const descriptor: OverlaySurfaceDescriptor = getOverlaySurfaceDescriptor(key)
  const dock = descriptor.dock
  if (!dock) {
    throw new Error(`Overlay surface "${key}" is not a docked surface: no dock geometry to resolve`)
  }

  const inset = `var(${dock.insetToken})`
  const edgeGap = `calc(${inset} + var(--ds-ov-safe-${dock.edge}))`

  return {
    '--ds-ov-dock-top': `calc(var(${dock.topClearanceToken}) + ${inset} + var(--ds-ov-safe-top))`,
    '--ds-ov-dock-bottom': `calc(var(${dock.bottomClearanceToken}) + ${inset} + var(--ds-ov-safe-bottom))`,
    '--ds-ov-dock-left': dock.edge === 'left' ? edgeGap : 'auto',
    '--ds-ov-dock-right': dock.edge === 'right' ? edgeGap : 'auto',
    '--ds-ov-dock-width': `min(var(${dock.widthToken}), calc(100% - var(--ds-ov-panel-maxw-inset)))`,
    '--ds-ov-dock-z': `var(${descriptor.zLayerToken})`,
  }
}

export function resolveWindowSurfaceDescriptor(
  win: WindowInstance,
  options: {
    getNodeName?: (nodeId: string) => string | null
  } = {},
): ResolvedWindowSurfaceDescriptor {
  if (isInteractPanelWindow(win)) {
    const descriptor = getOverlaySurfaceDescriptor('wm-interact-window')
    const panel = win.data.panel
    return {
      descriptor,
      title: interactPanelTitle(panel),
      role: descriptor.a11y?.role ?? 'dialog',
      ariaLabel: interactPanelAriaLabel(panel),
    }
  }

  const descriptor = getOverlaySurfaceDescriptor('wm-inspector-window')

  if (isEdgeDetailWindow(win)) {
    const fromPid = String(win.data.fromPid ?? '').trim()
    const toPid = String(win.data.toPid ?? '').trim()
    const label = fromPid && toPid ? `Trustline details: ${fromPid} to ${toPid}` : 'Trustline details'
    return {
      descriptor,
      title: '',
      role: descriptor.a11y?.role ?? 'region',
      ariaLabel: label,
    }
  }

  if (isNodeCardWindow(win)) {
    const nodeId = String(win.data.nodeId ?? '').trim()
    const nodeName = nodeId ? String(options.getNodeName?.(nodeId) ?? '').trim() : ''
    const label = nodeName || nodeId
    return {
      descriptor,
      title: '',
      role: descriptor.a11y?.role ?? 'region',
      ariaLabel: label ? `Node details: ${label}` : 'Node details',
    }
  }

  return {
    descriptor,
    title: '',
    role: descriptor.a11y?.role ?? 'region',
    ariaLabel: 'Window',
  }
}