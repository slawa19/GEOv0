import { createApp, h, nextTick, reactive, type Component } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { GraphLink, GraphNode } from '../types'
import { keyEdge } from '../utils/edgeKey'
import GraphNavigator from './GraphNavigator.vue'
import { setUiLocale } from '../i18n/locale'

// 046 wave 1, full (2026-10-19): the controls are found by test id, not by their words (which are the dictionary's, in
// the language of the interface - 048 §G "Find on graph · Participant / Line"); the words are asserted where they are
// the subject (the English and Russian labels and announcements below).
const openNode = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('[data-testid="graph-navigator-open-node"]')!
const openEdge = (host: HTMLElement) => host.querySelector<HTMLButtonElement>('[data-testid="graph-navigator-open-edge"]')!

afterEach(() => {
  setUiLocale('en')
  window.localStorage.clear()
})

const nodes: GraphNode[] = [
  { id: 'bob', name: 'Bob' },
  { id: 'alice', name: 'Alice' },
]
const links: GraphLink[] = [{ source: 'alice', target: 'bob', trust_limit: '100' }]

function mountNavigator(edgeInspectDisabled = false, linkItems: GraphLink[] = links, nodeItems: GraphNode[] = nodes) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const inspectNode = vi.fn()
  const inspectEdge = vi.fn()
  const app = createApp({
    render: () =>
      h(GraphNavigator as Component, {
        nodes: nodeItems,
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

    const region = host.querySelector('[role="region"][data-testid="graph-navigator"]') as HTMLElement | null
    expect(region).toBeTruthy()
    expect(region!.getAttribute('aria-label')).toBe('Graph navigator')
    expect(region!.querySelector('summary')?.textContent?.trim()).toBe('Find on graph')
    const disclosure = region?.querySelector('details') as HTMLDetailsElement | null
    expect(disclosure).toBeTruthy()
    disclosure!.open = true

    const nodeSelect = host.querySelector('#graph-navigator-node') as HTMLSelectElement
    const edgeSelect = host.querySelector('#graph-navigator-edge') as HTMLSelectElement
    expect(host.querySelector('label[for="graph-navigator-node"]')?.textContent).toBe('Participant')
    expect(host.querySelector('label[for="graph-navigator-edge"]')?.textContent).toBe('Line')
    expect(nodeSelect.tagName).toBe('SELECT')
    expect(edgeSelect.tagName).toBe('SELECT')
    expect(nodeSelect.options[0]?.textContent).toContain('Alice')

    nodeSelect.value = 'bob'
    nodeSelect.dispatchEvent(new Event('change', { bubbles: true }))
    await nextTick()
    expect(openNode(host).textContent?.trim()).toBe('Open participant')
    openNode(host).click()
    await nextTick()
    expect(inspectNode).toHaveBeenCalledWith('bob')
    expect(host.querySelector('[data-testid="graph-navigator-status"]')?.textContent).toContain('Participant opened: Bob')

    expect(openEdge(host).textContent?.trim()).toBe('Open line')
    openEdge(host).click()
    await nextTick()
    expect(inspectEdge).toHaveBeenCalledWith(links[0])
    const status = host.querySelector('[data-testid="graph-navigator-status"]')
    expect(status?.getAttribute('role')).toBe('status')
    expect(status?.getAttribute('aria-live')).toBe('polite')
    // 044 `T4405`: the participants' names, not their ids.
    expect(status?.textContent).toContain('Line opened: Alice → Bob')

    app.unmount()
    host.remove()
  })

  it('046 wave 1: in Russian the summary, labels, buttons and announcement are the words of 048 §G; names stay as they are', async () => {
    setUiLocale('ru')
    const { app, host, inspectNode } = mountNavigator()
    await nextTick()
    expect(host.querySelector('summary')?.textContent?.trim()).toBe('Найти на графе')
    expect(host.querySelector('[data-testid="graph-navigator"]')?.getAttribute('aria-label')).toBe('Поиск на графе')
    expect(host.querySelector('label[for="graph-navigator-node"]')?.textContent).toBe('Участник')
    expect(host.querySelector('label[for="graph-navigator-edge"]')?.textContent).toBe('Линия')
    expect([openNode(host).textContent?.trim(), openEdge(host).textContent?.trim()]).toEqual(['Открыть участника', 'Открыть линию'])
    openNode(host).click()
    await nextTick()
    expect(inspectNode).toHaveBeenCalledWith('alice')
    expect(host.querySelector('[data-testid="graph-navigator-status"]')?.textContent).toBe('Открыт участник: Alice')
    app.unmount()
    host.remove()
  })

  it('044: two participants with one name read apart (numbered, as in the pickers); values stay full ids', async () => {
    const A = 'run_20261017_101010_ab12cd34:p1'
    const B = 'run_20261017_101010_ab12cd34:p2'
    const C = 'run_20261017_101010_ab12cd34:p3'
    const twins: GraphNode[] = [{ id: A, name: 'Same' }, { id: B, name: 'Same' }, { id: C, name: 'Other' }]
    const twinLinks: GraphLink[] = [{ source: C, target: A, trust_limit: '1' }, { source: C, target: B, trust_limit: '1' }]
    const { app, host } = mountNavigator(false, twinLinks, twins)
    await nextTick()

    const options = (selector: string) =>
      Array.from((host.querySelector(selector) as HTMLSelectElement).querySelectorAll('option'))
        .map((o) => ({ value: (o as HTMLOptionElement).value, text: (o.textContent ?? '').trim() }))
        .filter((o) => o.value !== '')
    const nodeOptions = options('#graph-navigator-node')
    expect(new Set(nodeOptions.map((o) => o.text)).size).toBe(3)
    expect(nodeOptions.map((o) => o.text).sort()).toEqual(['Other', 'Same (1)', 'Same (2)'])
    expect(nodeOptions.map((o) => o.value).sort()).toEqual([A, B, C].sort())
    const edgeOptions = options('#graph-navigator-edge')
    expect(new Set(edgeOptions.map((o) => o.text)).size).toBe(2)
    expect(edgeOptions.map((o) => o.value).sort()).toEqual([keyEdge(C, A), keyEdge(C, B)].sort())

    app.unmount()
    host.remove()
  })

  it('explains and disables edge inspection outside the live network (043 S3: no Interact mode any more)', async () => {
    const { app, host, inspectEdge } = mountNavigator(true)
    await nextTick()

    const button = openEdge(host)
    expect(button?.disabled).toBe(true)
    expect(button?.title).toContain('live network')
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

    openEdge(host).click()
    expect(inspectEdge).toHaveBeenCalledWith(selected)

    app.unmount()
    host.remove()
  })
})
