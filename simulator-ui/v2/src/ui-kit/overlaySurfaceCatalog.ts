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