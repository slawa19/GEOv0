import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import { getOverlaySurfaceDescriptor, resolveOverlayDockStyle } from './overlaySurfaceCatalog'

function readHere(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

describe('designSystem.overlays dropdown contract', () => {
  it('uses shared token defaults for dropdown width and height bounds', () => {
    const overlays = readHere('./designSystem.overlays.css')
    const tokens = readHere('./designSystem.tokens.css')

    expect(overlays).toContain('min-width: var(--ds-ov-dropdown-minw, var(--ds-ov-dropdown-default-minw));')
    expect(overlays).toContain('max-width: min(var(--ds-ov-dropdown-maxw, var(--ds-ov-dropdown-default-maxw)), calc(100vw - var(--ds-ov-dropdown-vw-inset, var(--ds-ov-panel-maxw-inset))));')
    expect(overlays).toContain('max-height: min(var(--ds-ov-dropdown-maxh-vh, var(--ds-ov-dropdown-default-maxh-vh)), var(--ds-ov-dropdown-maxh, var(--ds-ov-dropdown-default-maxh)));')
    expect(overlays).toContain('min-width: var(--ds-ov-dropdown-narrow-minw);')

    expect(tokens).toContain('--ds-ov-dropdown-default-minw: 320px;')
    expect(tokens).toContain('--ds-ov-dropdown-default-maxw: 520px;')
    expect(tokens).toContain('--ds-ov-dropdown-default-maxh: 520px;')
    expect(tokens).toContain('--ds-ov-dropdown-default-maxh-vh: 70vh;')
    expect(tokens).toContain('--ds-ov-dropdown-narrow-minw: 260px;')
  })

  it('uses a shared toast primitive with tokenized bottom-stack, clamp, and z-layer ownership', () => {
    const overlays = readHere('./designSystem.overlays.css')
    const tokens = readHere('./designSystem.tokens.css')
    const appCss = readHere('../App.css')

    expect(overlays).toContain('.ds-ov-toast {')
    expect(overlays).toContain('z-index: var(--ds-z-alert, 200);')
    expect(overlays).toContain('.ds-ov-toast--error {')
    expect(overlays).toContain('bottom: calc(var(--ds-hud-bottom-stack-height, 56px) + var(--ds-toast-stack-gap, 12px));')
    expect(overlays).toContain('.ds-ov-toast--success {')
    expect(overlays).toContain('var(--ds-toast-stack-step, 60px)')
    expect(overlays).toContain('min-width: min(var(--ds-toast-minw), calc(100% - var(--ds-wm-clamp-pad, 12px) - var(--ds-wm-clamp-pad, 12px)));')
    expect(overlays).toContain('max-width: min(var(--ds-toast-maxw), calc(100% - var(--ds-wm-clamp-pad, 12px) - var(--ds-wm-clamp-pad, 12px)));')

    expect(tokens).toContain('--ds-toast-gap: 8px;')
    expect(tokens).toContain('--ds-toast-close-min-size: 24px;')
    expect(tokens).toContain('--ds-toast-enter-shift-y: 8px;')

    expect(appCss).toContain('--ds-z-bottom: 30;')
    expect(appCss).toContain('--ds-z-top: 40;')
    expect(appCss).toContain('--ds-z-panel: 42;')
    expect(appCss).toContain('--ds-z-dev: 50;')
    expect(appCss).toContain('--ds-z-tooltip: 55;')
    expect(appCss).toContain('--ds-z-inset: 60;')
    expect(appCss).toContain('--ds-z-alert: 200;')
  })

  it('keeps pointer transparency on passive overlays while interactive surfaces opt in explicitly', () => {
    const overlays = readHere('./designSystem.overlays.css')

    expect(overlays).toContain('.ds-ov-layer {')
    expect(overlays).toContain('.ds-ov-item {')
    expect(overlays).toContain('.ds-ov-top {')
    expect(overlays).toContain('.ds-ov-bottom {')
    expect(overlays).toContain('.ds-ov-inset {')
    expect(overlays).toContain('.ds-ov-tooltip {')

    expect(overlays).toContain('.ds-ov-layer {\n  position: absolute;\n  inset: 0;\n  pointer-events: none;\n}')
    expect(overlays).toContain('.ds-ov-item {\n  pointer-events: auto;\n}')
    expect(overlays).toContain('.ds-ov-top {\n  position: absolute;')
    expect(overlays).toContain('z-index: var(--ds-z-top, 40);\n  pointer-events: none;')
    expect(overlays).toContain('.ds-ov-bottom {\n  position: absolute;')
    expect(overlays).toContain('z-index: var(--ds-z-bottom, 30);\n  pointer-events: auto;')
    expect(overlays).toContain('.ds-ov-inset {\n  position: absolute;\n  inset: 0;\n  z-index: var(--ds-z-inset, 60);\n  pointer-events: none;')
    expect(overlays).toContain('.ds-ov-tooltip {')
    expect(overlays).toContain('z-index: var(--ds-z-tooltip, 55);\n  pointer-events: none;')
  })
})
/**
 * Spec 007, T705. The analytics dock is a fixed surface; WindowManager windows are summoned and
 * dragged by the user, and a window must be able to come out in front of the dock.
 *
 * Both sat on `--ds-z-panel`, and `.wm-layer` establishes the stacking context every window lives
 * in: `effectiveZ` orders windows inside that layer and can never lift one out of it. So with the
 * dock a later sibling in `SimulatorAppRoot`'s template, it covered the WHOLE layer — every
 * window at once — and took their pointer input with it. Markup order is not a layering decision,
 * so the relation is stated numerically instead.
 */
describe('analytics dock vs the WindowManager layer', () => {
  function zValueOf(token: string): number {
    const sources = [readHere('./designSystem.tokens.css'), readHere('../App.css')]
    for (const source of sources) {
      const found = new RegExp(`${token}:\\s*(\\d+);`).exec(source)
      if (found) return Number(found[1])
    }
    throw new Error(`z token ${token} is not defined in the design system`)
  }

  it('puts the dock on its own layer, strictly below the layer .wm-layer owns', () => {
    const root = readHere('../components/SimulatorAppRoot.vue')

    // The layer every window is confined to.
    expect(root).toMatch(/\.wm-layer \{[^}]*z-index: var\(--ds-z-panel\);/)

    // The dock's layer is whatever the catalog says it is — asserting a literal here would pass
    // just as happily with the descriptor pointing back at `--ds-z-panel`.
    const dockToken = getOverlaySurfaceDescriptor('real-metrics-panel').zLayerToken
    expect(zValueOf(dockToken)).toBeLessThan(zValueOf('--ds-z-panel'))

    // And the dock's mount point takes its z from that token, not from its own stylesheet.
    expect(root).toMatch(/\.sar-analytics-dock \{[^}]*z-index: var\(--ds-ov-dock-z\);/)
    expect(resolveOverlayDockStyle('real-metrics-panel')['--ds-ov-dock-z']).toBe(
      `var(${dockToken})`,
    )
  })
})
