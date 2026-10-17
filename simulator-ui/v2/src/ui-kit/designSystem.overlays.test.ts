import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import { getOverlaySurfaceDescriptor, resolveOverlayDockStyle } from './overlaySurfaceCatalog'

function readHere(rel: string): string {
  return readFileSync(new URL(rel, import.meta.url), 'utf8')
}

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
