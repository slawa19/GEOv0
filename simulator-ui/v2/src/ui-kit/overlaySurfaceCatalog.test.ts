import { describe, expect, it } from 'vitest'

import {
  getOverlaySurfaceDescriptor,
  resolveOverlayDockStyle,
  resolveWindowSurfaceDescriptor,
} from './overlaySurfaceCatalog'
import type { WindowInstance } from '../composables/windowManager/types'

function makeWindow(overrides?: Partial<WindowInstance>): WindowInstance {
  return {
    id: 1,
    type: 'interact-panel',
    state: 'open',
    lifecyclePhase: 'stable',
    policy: {
      group: 'interact',
      singleton: 'reuse',
      sizingMode: 'fixed-width-auto-height',
      widthOwner: 'policy',
      heightOwner: 'measured',
      escBehavior: 'back-then-close',
      closeOnOutsideClick: false,
    },
    anchor: null,
    anchorOffset: null,
    active: true,
    z: 1,
    effectiveZ: 1,
    placement: 'docked-right',
    rect: { left: 10, top: 10, width: 320, height: 240 },
    constraints: {
      minWidth: 320,
      minHeight: 200,
      maxWidth: 100000,
      maxHeight: 100000,
      preferredWidth: 420,
      preferredHeight: 280,
    },
    measured: null,
    data: { panel: 'payment', phase: 'confirm-payment' },
    ...overrides,
  }
}

describe('overlaySurfaceCatalog', () => {
  it('defines a typed runtime contract for WM, HUD, toast, tooltip, dev, bottom, canvas, and message surfaces', () => {
    expect(getOverlaySurfaceDescriptor('wm-interact-window')).toMatchObject({
      family: 'interact-panel',
      sizingMode: 'fixed-width-auto-height',
      positioningOwner: 'window-manager',
      zLayerToken: '--ds-z-panel',
    })

    expect(getOverlaySurfaceDescriptor('wm-inspector-window')).toMatchObject({
      family: 'inspector-card',
      sizingMode: 'bounded-intrinsic',
      positioningOwner: 'window-manager',
      zLayerToken: '--ds-z-panel',
    })

    expect(getOverlaySurfaceDescriptor('top-hud-stack')).toMatchObject({
      family: 'hud-bar',
      sizingMode: 'stretch',
      zLayerToken: '--ds-z-top',
    })

    expect(getOverlaySurfaceDescriptor('bottom-hud-stack')).toMatchObject({
      family: 'hud-bar',
      sizingMode: 'stretch',
      zLayerToken: '--ds-z-bottom',
    })

    expect(getOverlaySurfaceDescriptor('interact-select-dropdown')).toMatchObject({
      family: 'hud-dropdown',
      sizingMode: 'bounded-intrinsic',
      positioningOwner: 'window-shell-anchor',
      zLayerToken: '--ds-z-inset',
    })

    expect(getOverlaySurfaceDescriptor('hud-dropdown')).toMatchObject({
      family: 'hud-dropdown',
      sizingMode: 'bounded-intrinsic',
      zLayerToken: '--ds-z-inset',
    })

    expect(getOverlaySurfaceDescriptor('success-toast')).toMatchObject({
      family: 'notification-toast',
      zLayerToken: '--ds-z-alert',
      a11y: { role: 'status', ariaLive: 'polite', ariaLabel: 'Success notification' },
    })

    expect(getOverlaySurfaceDescriptor('error-toast')).toMatchObject({
      family: 'notification-toast',
      zLayerToken: '--ds-z-alert',
      a11y: { role: 'alert', ariaLive: 'assertive', ariaLabel: 'Error notification' },
    })

    expect(getOverlaySurfaceDescriptor('interact-history-overlay')).toMatchObject({
      family: 'bottom-overlay',
      zLayerToken: '--ds-z-bottom',
    })

    expect(getOverlaySurfaceDescriptor('dev-perf-overlay')).toMatchObject({
      family: 'dev-overlay',
      zLayerToken: '--ds-z-dev',
      a11y: { role: 'region', ariaLabel: 'Performance diagnostics' },
    })

    expect(getOverlaySurfaceDescriptor('edge-tooltip')).toMatchObject({
      family: 'tooltip',
      zLayerToken: '--ds-z-tooltip',
      a11y: { role: 'region', ariaLabel: 'Edge tooltip' },
    })

    expect(getOverlaySurfaceDescriptor('canvas-labels-overlay')).toMatchObject({
      family: 'canvas-overlay',
      sizingMode: 'stretch',
      zLayerToken: '--ds-z-world-labels',
    })

    expect(getOverlaySurfaceDescriptor('loading-message-overlay')).toMatchObject({
      family: 'message-overlay',
      zLayerToken: '--ds-z-inset',
    })
  })

  it('resolves interact window shell semantics from the shared descriptor layer', () => {
    const resolved = resolveWindowSurfaceDescriptor(
      makeWindow({ data: { panel: 'trustline', phase: 'editing-trustline' } }),
    )

    expect(resolved.descriptor.key).toBe('wm-interact-window')
    expect(resolved.title).toBe('Trustline')
    expect(resolved.role).toBe('dialog')
    expect(resolved.ariaLabel).toBe('Trustline management panel')
  })

  it('resolves edge-detail inspector semantics from the shared descriptor layer', () => {
    const resolved = resolveWindowSurfaceDescriptor(
      makeWindow({
        type: 'edge-detail',
        data: { fromPid: 'alice', toPid: 'bob' },
      }),
    )

    expect(resolved.descriptor.key).toBe('wm-inspector-window')
    expect(resolved.title).toBe('')
    expect(resolved.role).toBe('region')
    expect(resolved.ariaLabel).toBe('Trustline details: alice to bob')
  })

  it('resolves node-card inspector semantics from the shared descriptor layer', () => {
    const resolved = resolveWindowSurfaceDescriptor(
      makeWindow({
        type: 'node-card',
        data: { nodeId: 'bob' },
      }),
      { getNodeName: (nodeId) => (nodeId === 'bob' ? 'Bob' : null) },
    )

    expect(resolved.descriptor.key).toBe('wm-inspector-window')
    expect(resolved.role).toBe('region')
    expect(resolved.ariaLabel).toBe('Node details: Bob')
  })
})
describe('analytics panel surface (spec 007, T705)', () => {
  it('is a docked overlay surface of its own family, not a cover over the canvas', () => {
    expect(getOverlaySurfaceDescriptor('real-metrics-panel')).toMatchObject({
      family: 'analytics-panel',
      // `root-inset` would sit on top of the graph; the panel is read alongside it.
      positioningOwner: 'root-side-dock',
      // NOT `--ds-z-panel`: that layer belongs to the WindowManager, and a fixed dock sharing it
      // would win on markup order alone and cover every window the user opens.
      zLayerToken: '--ds-z-analytics-dock',
      a11y: { role: 'region', ariaLabel: 'Run analytics' },
    })

    expect(getOverlaySurfaceDescriptor('real-metrics-panel').dock).toEqual({
      edge: 'right',
      topClearanceToken: '--ds-hud-stack-height',
      bottomClearanceToken: '--ds-hud-bottom-stack-height',
      insetToken: '--ds-ov-inset',
      widthToken: '--ds-ov-panel-maxw',
    })
  })

  it('resolves placement into custom properties that name tokens, never pixels', () => {
    const style = resolveOverlayDockStyle('real-metrics-panel')

    // Docked right: the opposite edge is released, not given a competing value.
    expect(style['--ds-ov-dock-left']).toBe('auto')
    expect(style['--ds-ov-dock-right']).toBe('calc(var(--ds-ov-inset) + var(--ds-ov-safe-right))')

    // Clears both HUD stacks by the tokens those stacks publish their measured heights into.
    expect(style['--ds-ov-dock-top']).toContain('var(--ds-hud-stack-height)')
    expect(style['--ds-ov-dock-bottom']).toContain('var(--ds-hud-bottom-stack-height)')

    // Width follows the same overlay contract as `.ds-ov-panel`.
    expect(style['--ds-ov-dock-width']).toBe(
      'min(var(--ds-ov-panel-maxw), calc(100% - var(--ds-ov-panel-maxw-inset)))',
    )

    // The z layer is the descriptor's, so stacking cannot be re-decided in a stylesheet.
    expect(style['--ds-ov-dock-z']).toBe('var(--ds-z-analytics-dock)')

    // The whole point of the indirection: no number appears here. Every value is a token
    // reference, so the design system stays the only place the geometry is written down.
    for (const value of Object.values(style)) {
      if (value === 'auto') continue
      expect(value).not.toMatch(/\d+px/)
    }
  })

  it('refuses to resolve dock geometry for a surface that has none', () => {
    // Silently returning `{}` would pin the surface to the canvas corner with no explanation.
    expect(() => resolveOverlayDockStyle('edge-tooltip')).toThrow(/not a docked surface/)
  })
})
