import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { LayoutLink, LayoutNode } from '../types/layout'

// We mock d3-force to observe forceLink().distance(...) retuning without relying on d3 internals.
type MockLinkForce = {
  _distance: number | undefined
  _distanceCalls: number[]
  id: () => MockLinkForce
  strength: () => MockLinkForce
  distance: (v?: number) => MockLinkForce | number | undefined
}

type MockAxisForce = {
  _x?: number
  _y?: number
  x?: (v?: number) => MockAxisForce | number | undefined
  y?: (v?: number) => MockAxisForce | number | undefined
  strength: () => MockAxisForce
}

type MockSimulation = {
  alpha: (v?: number) => MockSimulation | number
  alphaMin: (v?: number) => MockSimulation | number
  alphaDecay: () => MockSimulation
  velocityDecay: () => MockSimulation
  force: (name: string, f?: unknown) => MockSimulation | unknown
  stop: () => MockSimulation
  tick: () => MockSimulation
}

let lastLinkForce: MockLinkForce | null = null

function makeNode(id: string): LayoutNode {
  return { id, __x: 0, __y: 0 }
}

function makeLink(source: string, target: string): LayoutLink {
  return { __key: `${source}->${target}`, source, target }
}

vi.mock('d3-force', () => {
  const mkLinkForce = () => {
    const f: MockLinkForce = {
      _distance: undefined as number | undefined,
      _distanceCalls: [] as number[],
      id: () => f,
      strength: () => f,
      distance: (v?: number) => {
        if (typeof v === 'number') {
          f._distance = v
          f._distanceCalls.push(v)
          return f
        }
        return f._distance
      },
    }
    return f
  }

  const mkXYForce = (axis: 'x' | 'y') => {
    const f: MockAxisForce = {
      [`_${axis}`]: undefined as number | undefined,
      [axis]: (v?: number) => {
        if (typeof v === 'number') {
          if (axis === 'x') f._x = v
          else f._y = v
          return f
        }
        return axis === 'x' ? f._x : f._y
      },
      strength: () => f,
    }
    return f
  }

  const mkSimulation = () => {
    const forces = new Map<string, unknown>()
    let alphaV = 1
    let alphaMinV = 0

    const sim: MockSimulation = {
      alpha: (v?: number) => {
        if (typeof v === 'number') {
          alphaV = v
          return sim
        }
        return alphaV
      },
      alphaMin: (v?: number) => {
        if (typeof v === 'number') {
          alphaMinV = v
          return sim
        }
        return alphaMinV
      },
      alphaDecay: () => sim,
      velocityDecay: () => sim,
      force: (name: string, f?: unknown) => {
        if (typeof f === 'undefined') return forces.get(name)
        forces.set(name, f)
        return sim
      },
      stop: () => sim,
      tick: () => sim,
    }

    return sim
  }

  return {
    forceSimulation: () => mkSimulation(),
    forceManyBody: () => {
      const f = { strength: () => f }
      return f
    },
    forceCollide: () => {
      const f = {
        radius: () => f,
        strength: () => f,
      }
      return f
    },
    forceX: () => mkXYForce('x'),
    forceY: () => mkXYForce('y'),
    forceLink: () => {
      lastLinkForce = mkLinkForce()
      return lastLinkForce
    },
  }
})

const mod = await import('./physicsD3')
const { createDefaultConfig, createPhysicsEngine, __testOnly_computeLinkDistancePx } = mod

describe('physicsD3: retune forces on significant viewport resize', () => {
  beforeEach(() => {
    lastLinkForce = null
  })

  it('retunes link distance when viewport area changes >=4x', async () => {
    const nodes = Array.from({ length: 100 }, (_, i) => makeNode(`n${i}`))
    const links = [makeLink('n0', 'n1')]

    const config = createDefaultConfig({ width: 200, height: 200, nodeCount: nodes.length, quality: 'low' })
    const engine = createPhysicsEngine({ nodes, links, config })

    const d0 = __testOnly_computeLinkDistancePx({ width: 200, height: 200, nodeCount: nodes.length })
    const d1 = __testOnly_computeLinkDistancePx({ width: 800, height: 800, nodeCount: nodes.length })
    expect(d1).not.toBe(d0)

    expect(lastLinkForce).toBeTruthy()
    const linkForce = lastLinkForce
    if (!linkForce) throw new Error('Link force was not captured')
    expect(linkForce.distance()).toBe(d0)

    engine.updateViewport(800, 800)

    expect(linkForce.distance()).toBe(d1)
    expect(linkForce._distanceCalls).toEqual([d0, d1])
  })

  it('does not retune link distance when resize is small (area ratio <4x)', async () => {
    const nodes = Array.from({ length: 100 }, (_, i) => makeNode(`n${i}`))
    const links = [makeLink('n0', 'n1')]

    const config = createDefaultConfig({ width: 800, height: 800, nodeCount: nodes.length, quality: 'low' })
    const engine = createPhysicsEngine({ nodes, links, config })

    const d0 = __testOnly_computeLinkDistancePx({ width: 800, height: 800, nodeCount: nodes.length })
    const d1 = __testOnly_computeLinkDistancePx({ width: 820, height: 820, nodeCount: nodes.length })
    expect(d1).not.toBe(d0)

    expect(lastLinkForce).toBeTruthy()
    const linkForce = lastLinkForce
    if (!linkForce) throw new Error('Link force was not captured')
    expect(linkForce.distance()).toBe(d0)

    engine.updateViewport(820, 820)

    // Still the old distance (no retune, despite d1 being different).
    expect(linkForce.distance()).toBe(d0)
    expect(linkForce._distanceCalls).toEqual([d0])
  })
})

