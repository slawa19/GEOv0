import { createApp, h, ref } from 'vue'
import { describe, expect, it } from 'vitest'
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
        getNodeById: (id) => (id ? ({ id } as any) : null),
        getLayoutNodeById: (id) => ({ id, __x: 10, __y: 20 }),
        worldToScreen: (x, y) => ({ x, y }),
      })
    })

    expect(api.selectedNode.value?.id).toBe('A')
    expect(api.selectedNodeScreenCenter.value).toBeNull()
  })

  it('selectedNodeScreenCenter uses worldToScreen(layout.__x/__y) when available', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>('A')

      const hostEl = ref<any>({
        getBoundingClientRect: () => ({ width: 300, height: 200 }),
      })

      return useNodeCard({
        hostEl,
        selectedNodeId,
        getNodeById: (id) => (id ? ({ id } as any) : null),
        getLayoutNodeById: (id) => ({ id, __x: 10, __y: 20 }),
        worldToScreen: (x, y) => ({ x: x + 1, y: y + 2 }),
      })
    })

    expect(api.selectedNode.value?.id).toBe('A')
    expect(api.selectedNodeScreenCenter.value).toEqual({ x: 11, y: 22 })
  })
})
