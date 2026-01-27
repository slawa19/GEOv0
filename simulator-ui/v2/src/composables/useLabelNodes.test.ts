import { describe, expect, it } from 'vitest'
import { useLabelNodes } from './useLabelNodes'

const layoutNodes = [
  { id: 'A', __x: 10, __y: 20 },
  { id: 'B', __x: 30, __y: 40 },
  { id: 'C', __x: 50, __y: 60 },
] as any

const layoutLinks = [
  { __key: 'A->B', source: 'A', target: 'B' },
  { __key: 'C->A', source: 'C', target: 'A' },
] as any

const snapshot = {
  equivalent: 'UAH',
  nodes: [
    { id: 'A', name: 'Alice' },
    { id: 'B', name: 'Bob' },
    { id: 'C', name: 'Carol' },
  ],
  links: [],
} as any

describe('useLabelNodes', () => {
  it('returns selection label in selection mode', () => {
    const { labelNodes } = useLabelNodes({
      isTestMode: () => false,
      getLabelsLod: () => 'selection' as any,
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
      getLayoutLinks: () => layoutLinks,
      getLayoutNodeById: (id) => layoutNodes.find((n: any) => n.id === id) ?? null,
      getNodeById: (id) => snapshot.nodes.find((n: any) => n.id === id) ?? null,
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 20, h: 20 }),
      fxColorForNode: () => '#fff',
    })

    expect(labelNodes.value.map((n) => n.id)).toEqual(['A'])
    expect(labelNodes.value[0].text).toBe('Alice')
  })

  it('returns neighbors when neighbors mode', () => {
    const { labelNodes } = useLabelNodes({
      isTestMode: () => false,
      getLabelsLod: () => 'neighbors' as any,
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
      getLayoutLinks: () => layoutLinks,
      getLayoutNodeById: (id) => layoutNodes.find((n: any) => n.id === id) ?? null,
      getNodeById: (id) => snapshot.nodes.find((n: any) => n.id === id) ?? null,
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 20, h: 20 }),
      fxColorForNode: () => '#fff',
    })

    const ids = labelNodes.value.map((n) => n.id).sort()
    expect(ids).toEqual(['A', 'B', 'C'])
  })
})
