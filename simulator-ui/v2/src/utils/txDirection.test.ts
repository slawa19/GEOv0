import { describe, expect, it } from 'vitest'
import { resolveTxDirection } from './txDirection'

describe('resolveTxDirection', () => {
  it('keeps forward edges when endpoints already match', () => {
    const r = resolveTxDirection({
      from: 'A',
      to: 'C',
      edges: [
        { from: 'A', to: 'B' },
        { from: 'B', to: 'C' },
      ],
    })

    expect(r.from).toBe('A')
    expect(r.to).toBe('C')
    expect(r.edges).toEqual([
      { from: 'A', to: 'B' },
      { from: 'B', to: 'C' },
    ])
  })

  it('fixes edges when each edge is swapped but order is correct', () => {
    const r = resolveTxDirection({
      from: 'A',
      to: 'C',
      edges: [
        { from: 'B', to: 'A' },
        { from: 'C', to: 'B' },
      ],
    })

    expect(r.edges).toEqual([
      { from: 'A', to: 'B' },
      { from: 'B', to: 'C' },
    ])
  })

  it('fixes edges when order is reversed but direction is correct per edge', () => {
    const r = resolveTxDirection({
      from: 'A',
      to: 'C',
      edges: [
        { from: 'B', to: 'C' },
        { from: 'A', to: 'B' },
      ],
    })

    expect(r.edges).toEqual([
      { from: 'A', to: 'B' },
      { from: 'B', to: 'C' },
    ])
  })

  it('fixes edges when both order and direction are reversed', () => {
    const r = resolveTxDirection({
      from: 'A',
      to: 'C',
      edges: [
        { from: 'C', to: 'B' },
        { from: 'B', to: 'A' },
      ],
    })

    expect(r.edges).toEqual([
      { from: 'A', to: 'B' },
      { from: 'B', to: 'C' },
    ])
  })

  it('infers endpoints from edges when from/to are missing', () => {
    const r = resolveTxDirection({ edges: [{ from: 'X', to: 'Y' }] })
    expect(r.from).toBe('X')
    expect(r.to).toBe('Y')
  })
})
