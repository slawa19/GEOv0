import { describe, it, expect, vi, beforeEach } from 'vitest'

// We mock d3-force to observe forceLink().distance(...) retuning without relying on d3 internals.
let lastLinkForce: any = null

vi.mock('d3-force', () => {
  const mkLinkForce = () => {
    const f: any = {
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
    const f: any = {
      [`_${axis}`]: undefined as number | undefined,
      [axis]: (v?: number) => {
        if (typeof v === 'number') {
          f[`_${axis}`] = v
          return f
        }
        return f[`_${axis}`]
      },
      strength: () => f,
    }
    return f
  }

  const mkSimulation = () => {
    const forces = new Map<string, any>()
    let alphaV = 1
    let alphaMinV = 0

    const sim: any = {
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
      force: (name: string, f?: any) => {
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
      const f: any = { strength: () => f }
      return f
    },
    forceCollide: () => {
      const f: any = {
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

describe('physicsD3: retune forces on significant viewport resize', () => {
  beforeEach(() => {
    lastLinkForce = null
  })

  it('retunes link distance when viewport area changes >=4x', async () => {
    const mod = await import('./physicsD3')
    const { createDefaultConfig, createPhysicsEngine, __testOnly_computeLinkDistancePx } = mod

    const nodes = Array.from({ length: 100 }, (_, i) => ({ id: `n${i}`, __x: 0, __y: 0 }))
    const links = [{ source: 'n0', target: 'n1' }]

    const config = createDefaultConfig({ width: 200, height: 200, nodeCount: nodes.length, quality: 'low' })
    const engine = createPhysicsEngine({ nodes: nodes as any, links: links as any, config })

    const d0 = __testOnly_computeLinkDistancePx({ width: 200, height: 200, nodeCount: nodes.length })
    const d1 = __testOnly_computeLinkDistancePx({ width: 800, height: 800, nodeCount: nodes.length })
    expect(d1).not.toBe(d0)

    expect(lastLinkForce).toBeTruthy()
    expect(lastLinkForce.distance()).toBe(d0)

    engine.updateViewport(800, 800)

    expect(lastLinkForce.distance()).toBe(d1)
    expect(lastLinkForce._distanceCalls).toEqual([d0, d1])
  })

  it('does not retune link distance when resize is small (area ratio <4x)', async () => {
    const mod = await import('./physicsD3')
    const { createDefaultConfig, createPhysicsEngine, __testOnly_computeLinkDistancePx } = mod

    const nodes = Array.from({ length: 100 }, (_, i) => ({ id: `n${i}`, __x: 0, __y: 0 }))
    const links = [{ source: 'n0', target: 'n1' }]

    const config = createDefaultConfig({ width: 800, height: 800, nodeCount: nodes.length, quality: 'low' })
    const engine = createPhysicsEngine({ nodes: nodes as any, links: links as any, config })

    const d0 = __testOnly_computeLinkDistancePx({ width: 800, height: 800, nodeCount: nodes.length })
    const d1 = __testOnly_computeLinkDistancePx({ width: 820, height: 820, nodeCount: nodes.length })
    expect(d1).not.toBe(d0)

    expect(lastLinkForce).toBeTruthy()
    expect(lastLinkForce.distance()).toBe(d0)

    engine.updateViewport(820, 820)

    // Still the old distance (no retune, despite d1 being different).
    expect(lastLinkForce.distance()).toBe(d0)
    expect(lastLinkForce._distanceCalls).toEqual([d0])
  })
})

