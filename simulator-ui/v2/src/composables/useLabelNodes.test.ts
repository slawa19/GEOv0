import { describe, expect, it } from 'vitest'
import type { GraphNode, GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'
import type { LabelsLod } from '../types/uiPrefs'
import { useLabelNodes } from './useLabelNodes'

const layoutNodes: LayoutNode[] = [
  { id: 'A', __x: 10, __y: 20 },
  { id: 'B', __x: 30, __y: 40 },
  { id: 'C', __x: 50, __y: 60 },
] 

const layoutLinks: LayoutLink[] = [
  { __key: 'A->B', source: 'A', target: 'B' },
  { __key: 'C->A', source: 'C', target: 'A' },
] 

const snapshot: GraphSnapshot = {
  equivalent: 'UAH',
  generated_at: '2026-01-27T00:00:00Z',
  nodes: [
    { id: 'A', name: 'Alice' },
    { id: 'B', name: 'Bob' },
    { id: 'C', name: 'Carol' },
  ],
  links: [],
}

function findLayoutNode(id: string): LayoutNode | null {
  return layoutNodes.find((n) => n.id === id) ?? null
}

function findSnapshotNode(id: string): GraphNode | null {
  return snapshot.nodes.find((n) => n.id === id) ?? null
}

function createLabelsLodGetter(value: LabelsLod): () => LabelsLod {
  return () => value
}

describe('useLabelNodes', () => {
  it('returns selection label in selection mode', () => {
    const { labelNodes } = useLabelNodes({
      isTestMode: () => false,
      getLabelsLod: createLabelsLodGetter('selection'),
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
      getLayoutLinks: () => layoutLinks,
      getLayoutNodeById: findLayoutNode,
      getNodeById: findSnapshotNode,
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
      getLabelsLod: createLabelsLodGetter('neighbors'),
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
      getLayoutLinks: () => layoutLinks,
      getLayoutNodeById: findLayoutNode,
      getNodeById: findSnapshotNode,
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 20, h: 20 }),
      fxColorForNode: () => '#fff',
    })

    const ids = labelNodes.value.map((n) => n.id).sort()
    expect(ids).toEqual(['A', 'B', 'C'])
  })

  it('hides selected node label when hasNodeCardInspectorOpen returns true', () => {
    const { labelNodes } = useLabelNodes({
      isTestMode: () => false,
      getLabelsLod: createLabelsLodGetter('neighbors'),
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
      getLayoutLinks: () => layoutLinks,
      getLayoutNodeById: findLayoutNode,
      getNodeById: findSnapshotNode,
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 20, h: 20 }),
      fxColorForNode: () => '#fff',
      hasNodeCardInspectorOpen: () => true,
    })

    const ids = labelNodes.value.map((n) => n.id)
    expect(ids).not.toContain('A')
    // соседи B и C должны присутствовать
    expect(ids).toContain('B')
    expect(ids).toContain('C')
  })

  it('shows selected node label when hasNodeCardInspectorOpen returns false', () => {
    const { labelNodes } = useLabelNodes({
      isTestMode: () => false,
      getLabelsLod: createLabelsLodGetter('neighbors'),
      getSnapshot: () => snapshot,
      getSelectedNodeId: () => 'A',
      getLayoutLinks: () => layoutLinks,
      getLayoutNodeById: findLayoutNode,
      getNodeById: findSnapshotNode,
      getCameraZoom: () => 1,
      sizeForNode: () => ({ w: 20, h: 20 }),
      fxColorForNode: () => '#fff',
      hasNodeCardInspectorOpen: () => false,
    })

    const ids = labelNodes.value.map((n) => n.id)
    expect(ids).toContain('A')
  })
})
