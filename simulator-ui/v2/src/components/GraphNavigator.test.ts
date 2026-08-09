import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { GraphLink, GraphNode } from '../types'
import { keyEdge } from '../utils/edgeKey'
import GraphNavigator from './GraphNavigator.vue'

const nodes: GraphNode[] = [
  { id: 'bob', name: 'Bob' },
  { id: 'alice', name: 'Alice' },
]
const links: GraphLink[] = [{ source: 'alice', target: 'bob', trust_limit: '100' }]

function mountNavigator(edgeInspectDisabled = false, linkItems: GraphLink[] = links) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const inspectNode = vi.fn()
  const inspectEdge = vi.fn()
  const app = createApp({
    render: () =>
      h(GraphNavigator as Component, {
        nodes,
        links: linkItems,
        edgeInspectDisabled,
        onInspectNode: inspectNode,
        onInspectEdge: inspectEdge,
      }),
  })
  app.mount(host)
  return { app, host, inspectNode, inspectEdge }
}

describe('GraphNavigator', () => {
  it('uses labelled native controls and announces node and edge inspection', async () => {
    const { app, host, inspectNode, inspectEdge } = mountNavigator()
    await nextTick()

    const region = host.querySelector('[role="region"][aria-label="Graph navigator"]') as HTMLElement | null
    expect(region).toBeTruthy()
    const disclosure = region?.querySelector('details') as HTMLDetailsElement | null
    expect(disclosure).toBeTruthy()
    disclosure!.open = true

    const nodeSelect = host.querySelector('#graph-navigator-node') as HTMLSelectElement
    const edgeSelect = host.querySelector('#graph-navigator-edge') as HTMLSelectElement
    expect(host.querySelector('label[for="graph-navigator-node"]')?.textContent).toBe('Node')
    expect(host.querySelector('label[for="graph-navigator-edge"]')?.textContent).toBe('Edge')
    expect(nodeSelect.tagName).toBe('SELECT')
    expect(edgeSelect.tagName).toBe('SELECT')
    expect(nodeSelect.options[0]?.textContent).toContain('Alice')

    nodeSelect.value = 'bob'
    nodeSelect.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    const inspectNodeButton = Array.from(host.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Inspect node')
    ;(inspectNodeButton as HTMLButtonElement).click()
    await nextTick()
    expect(inspectNode).toHaveBeenCalledWith('bob')
    expect(host.querySelector('[data-testid="graph-navigator-status"]')?.textContent).toContain('Node details opened: Bob')

    const inspectEdgeButton = Array.from(host.querySelectorAll('button')).find((button) => button.textContent?.trim() === 'Inspect edge')
    ;(inspectEdgeButton as HTMLButtonElement).click()
    await nextTick()
    expect(inspectEdge).toHaveBeenCalledWith(links[0])
    const status = host.querySelector('[data-testid="graph-navigator-status"]')
    expect(status?.getAttribute('role')).toBe('status')
    expect(status?.getAttribute('aria-live')).toBe('polite')
    expect(status?.textContent).toContain('Trustline details opened: alice → bob')

    app.unmount()
    host.remove()
  })

  it('explains and disables edge inspection outside real Interact mode', async () => {
    const { app, host, inspectEdge } = mountNavigator(true)
    await nextTick()

    const button = Array.from(host.querySelectorAll('button')).find((candidate) => candidate.textContent?.trim() === 'Inspect edge') as
      | HTMLButtonElement
      | undefined
    expect(button?.disabled).toBe(true)
    expect(button?.title).toContain('real Interact mode')
    button?.click()
    expect(inspectEdge).not.toHaveBeenCalled()

    app.unmount()
    host.remove()
  })

  it('retains the selected trustline by directional key when sorted topology changes', async () => {
    const selected = { source: 'bob', target: 'carol', trust_limit: '50' }
    const linkItems = reactive<GraphLink[]>([selected, links[0]!])
    const { app, host, inspectEdge } = mountNavigator(false, linkItems)
    await nextTick()

    const edgeSelect = host.querySelector('#graph-navigator-edge') as HTMLSelectElement
    edgeSelect.value = keyEdge(selected.source, selected.target)
    edgeSelect.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()

    linkItems.push({ source: 'aaron', target: 'alice', trust_limit: '25' })
    await nextTick()
    expect(edgeSelect.value).toBe(keyEdge(selected.source, selected.target))

    const inspectEdgeButton = Array.from(host.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Inspect edge',
    )
    ;(inspectEdgeButton as HTMLButtonElement).click()
    expect(inspectEdge).toHaveBeenCalledWith(selected)

    app.unmount()
    host.remove()
  })
})
