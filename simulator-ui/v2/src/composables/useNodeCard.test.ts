import { createApp, h, ref } from 'vue'
import { describe, expect, it } from 'vitest'
import type { GraphNode } from '../types'
import type { LayoutNodeWithId } from '../types/layout'
import { useNodeCard } from './useNodeCard'

function withSetup<T>(fn: () => T): T {
  let out!: T
  const host = document.createElement('div')
  document.body.appendChild(host)

  const Child = {
    name: 'Child',
    setup() {
      out = fn()
      return () => null
    },
  }

  const Root = {
    name: 'Root',
    setup() {
      return () => h(Child)
    },
  }

  const app = createApp(Root)

  app.mount(host)
  app.unmount()
  host.remove()
  return out
}

describe('useNodeCard', () => {
  function makeNode(id: string): GraphNode {
    return { id }
  }

  function makeLayoutNode(id: string, x: number, y: number): LayoutNodeWithId {
    return { id, __x: x, __y: y }
  }

  it('selectedNode is null when there is no selection', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>(null)

      return useNodeCard({
        hostEl: ref(null),
        selectedNodeId,
        getNodeById: () => null,
        getLayoutNodeById: () => null,
        worldToScreen: (x, y) => ({ x, y }),
      })
    })

    expect(api.selectedNode.value).toBeNull()
    expect(api.selectedNodeScreenCenter.value).toBeNull()
  })

  it('selectedNodeScreenCenter is null when host is missing', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>('A')

      return useNodeCard({
        hostEl: ref(null),
        selectedNodeId,
        getNodeById: (id) => (id ? makeNode(id) : null),
        getLayoutNodeById: (id) => makeLayoutNode(id, 10, 20),
        worldToScreen: (x, y) => ({ x, y }),
      })
    })

    expect(api.selectedNode.value?.id).toBe('A')
    expect(api.selectedNodeScreenCenter.value).toBeNull()
  })

  it('selectedNodeScreenCenter uses worldToScreen(layout.__x/__y) when available', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>('A')

      const hostEl = ref<HTMLElement | null>({
        getBoundingClientRect: () => ({ width: 300, height: 200 }),
      } as HTMLElement)

      return useNodeCard({
        hostEl,
        selectedNodeId,
        getNodeById: (id) => (id ? makeNode(id) : null),
        getLayoutNodeById: (id) => makeLayoutNode(id, 10, 20),
        worldToScreen: (x, y) => ({ x: x + 1, y: y + 2 }),
      })
    })

    expect(api.selectedNode.value?.id).toBe('A')
    expect(api.selectedNodeScreenCenter.value).toEqual({ x: 11, y: 22 })
  })
})
