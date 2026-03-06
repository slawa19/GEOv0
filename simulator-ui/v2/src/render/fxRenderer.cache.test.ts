import { describe, expect, it, beforeEach } from 'vitest'

import { __testing, resetFxRendererCaches } from './fxRenderer'
import type { LayoutNode } from '../types/layout'

// JSDOM does not provide Path2D; fxRenderer caches Path2D instances.
// Provide a tiny stub sufficient for our warmup path.
class MockPath2D {
  arc() {
    // noop
  }
  rect() {
    // noop
  }
  addPath() {
    // noop
  }
}

function setPath2DGlobal(value: typeof Path2D | undefined): void {
  Object.defineProperty(globalThis, 'Path2D', {
    value,
    configurable: true,
    writable: true,
  })
}

function getPath2DGlobal(): typeof Path2D | undefined {
  return (globalThis as typeof globalThis & { Path2D?: typeof Path2D }).Path2D
}

describe('fxRenderer module-level caches', () => {
  beforeEach(() => {
    setPath2DGlobal(getPath2DGlobal() ?? (MockPath2D as unknown as typeof Path2D))
    resetFxRendererCaches()
  })

  it('warms nodeOutline Path2D cache and reset clears it', () => {
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(0)

    // Minimal LayoutNode-compatible object for sizeForNode/getNodeShape usage.
    const n: LayoutNode = {
      id: 'n1',
      __x: 10,
      __y: 20,
      viz_size: { w: 12, h: 12 },
      // getNodeShape() tolerates missing shape fields; default is 'circle'.
    }

    __testing._warmNodeOutlinePath2DCache(n)
    expect(__testing._nodeOutlinePath2DCacheSize()).toBeGreaterThan(0)

    resetFxRendererCaches()
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(0)
  })

  it('reset is idempotent', () => {
    resetFxRendererCaches()
    resetFxRendererCaches()
    expect(__testing._nodeOutlinePath2DCacheSize()).toBe(0)
    expect(__testing._nodeOutlineCacheSnapshotKey()).toBeUndefined()
  })
})
