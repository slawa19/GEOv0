/**
 * 039 slice B, PR 1: while a node is dragged the canvas does not draw it - the DOM preview stands for it, and it
 * must carry the same non-colour cues (the sign, the dashes of a freeze) and the same fill as the node had.
 * NOT seen here: how the preview looks (font, size) - no pixels.
 */
import { ref } from 'vue'
import { describe, expect, it } from 'vitest'

import { fillForNode } from '../render/nodeFill'
import type { GraphNode } from '../types'
import { classifyNode, createVizMapping, type VizMapping } from '../vizMapping'
import { useDragPreview } from './useDragPreview'

const MAPPING: VizMapping = { ...createVizMapping(), node: { palette: { debtor: '#a10000', creditor: '#0000b2', neutral: '#c3c3c3', frozen: '#444444', hub: '#00e500', direction: '#060606' } } }

function preview(node: GraphNode) {
  const el = ref<HTMLDivElement | null>(document.createElement('div'))
  const api = useDragPreview({
    el,
    getDraggedNode: () => null,
    getNodeById: () => node,
    getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
    renderOnce: () => undefined,
    sizeForNode: () => ({ w: 20, h: 20 }),
    fillForNode: (n) => fillForNode(n, MAPPING),
    lookForNode: (n) => classifyNode(n),
    hubColor: () => MAPPING.node.palette.hub,
  })
  api.showDragPreviewForNode(node.id)
  return el.value!
}

describe('drag preview - the dragged node keeps its sign, its freeze and its colour (039 slice B)', () => {
  it('shows the sign of the net position for each role, unknown included', () => {
    expect(preview({ id: 'a', net_sign: -1 }).textContent).toBe('−')
    expect(preview({ id: 'a', net_sign: 1 }).textContent).toBe('+')
    expect(preview({ id: 'a', net_sign: 0 }).textContent).toBe('0')
    expect(preview({ id: 'a', net_sign: null }).textContent).toBe('?')
    expect(preview({ id: 'a' }).textContent).toBe('?')
  })

  it('a frozen node is dashed and keeps its sign; an active one is solid', () => {
    const frozen = preview({ id: 'a', net_sign: -1, status: 'frozen' })
    expect(frozen.style.borderStyle).toBe('dashed')
    expect(frozen.textContent).toBe('−')
    expect(preview({ id: 'a', net_sign: -1, status: 'active' }).style.borderStyle).toBe('solid')
  })

  it('is filled with the role colour of the palette, whatever viz_color_key says', () => {
    expect(preview({ id: 'a', net_sign: -1, viz_color_key: 'person' }).style.background).toContain('#a10000')
    expect(preview({ id: 'a', net_sign: 1, viz_color_key: 'debt-8' }).style.background).toContain('#0000b2')
    expect(preview({ id: 'a', net_sign: 1, status: 'suspended' }).style.background).toContain('#444444')
  })
})
