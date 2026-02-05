import { describe, expect, it, vi } from 'vitest'

import type { LayoutLink, LayoutNode } from '../types/layout'
import { createPhysicsManager } from './usePhysicsManager'

vi.mock('../layout/physicsD3', () => {
  // Minimal engine stub; we assert wakeUp calls at manager layer.
  const engine = {
    stop: vi.fn(),
    isRunning: vi.fn(() => true),
    start: vi.fn(),
    reheat: vi.fn(),
    tick: vi.fn(),
    syncFromLayout: vi.fn(),
    syncToLayout: vi.fn(),
    pin: vi.fn(),
    unpin: vi.fn(),
    updateViewport: vi.fn(),
  }

  return {
    createDefaultConfig: vi.fn(() => ({ __cfg: true })),
    createPhysicsEngine: vi.fn(() => engine),
  }
})

function makeNode(id: string): LayoutNode {
  return { id, __x: 0, __y: 0 }
}

describe('createPhysicsManager wakeUp integration', () => {
  it('calls wakeUp() after recreateForCurrentLayout creates and reheats engine', () => {
    const wakeUp = vi.fn()

    const nodes: LayoutNode[] = [makeNode('A')]
    const links: LayoutLink[] = []

    const mgr = createPhysicsManager({
      isEnabled: () => true,
      getLayoutNodes: () => nodes,
      getLayoutLinks: () => links,
      getQuality: () => 'low',
      getPinnedPos: () => new Map(),
      wakeUp,
    })

    mgr.recreateForCurrentLayout({ w: 100, h: 50 })

    expect(wakeUp).toHaveBeenCalledTimes(1)
  })

  it('does NOT call wakeUp() when recreateForCurrentLayout early-returns (disabled)', () => {
    const wakeUp = vi.fn()

    const mgr = createPhysicsManager({
      isEnabled: () => false,
      getLayoutNodes: () => [makeNode('A')],
      getLayoutLinks: () => [],
      getQuality: () => 'low',
      getPinnedPos: () => new Map(),
      wakeUp,
    })

    mgr.recreateForCurrentLayout({ w: 100, h: 50 })

    expect(wakeUp).toHaveBeenCalledTimes(0)
  })

  it('calls wakeUp() on reheat(alpha>0) but not on reheat(0)', () => {
    const wakeUp = vi.fn()

    const mgr = createPhysicsManager({
      isEnabled: () => true,
      getLayoutNodes: () => [makeNode('A')],
      getLayoutLinks: () => [],
      getQuality: () => 'low',
      getPinnedPos: () => new Map(),
      wakeUp,
    })

    // engine is created here
    mgr.recreateForCurrentLayout({ w: 100, h: 50 })
    wakeUp.mockClear()

    mgr.reheat(0)
    expect(wakeUp).toHaveBeenCalledTimes(0)

    mgr.reheat(0.2)
    expect(wakeUp).toHaveBeenCalledTimes(1)
  })

  it('calls wakeUp() on updateViewport when reheatAlpha>0 but not when reheatAlpha=0', () => {
    const wakeUp = vi.fn()

    const mgr = createPhysicsManager({
      isEnabled: () => true,
      getLayoutNodes: () => [makeNode('A')],
      getLayoutLinks: () => [],
      getQuality: () => 'low',
      getPinnedPos: () => new Map(),
      wakeUp,
    })

    mgr.recreateForCurrentLayout({ w: 100, h: 50 })
    wakeUp.mockClear()

    mgr.updateViewport(120, 80, 0)
    expect(wakeUp).toHaveBeenCalledTimes(0)

    mgr.updateViewport(121, 81, 0.15)
    expect(wakeUp).toHaveBeenCalledTimes(1)
  })
})

