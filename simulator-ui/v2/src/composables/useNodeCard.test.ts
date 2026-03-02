import { createApp, h, ref } from 'vue'
import { describe, expect, it } from 'vitest'
import { useNodeCard } from './useNodeCard'
import { provideWindowManagerEnabled } from './windowManager/featureFlag'

function withSetup<T>(fn: () => T, o?: { wmEnabled?: boolean }): T {
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
      provideWindowManagerEnabled(o?.wmEnabled ?? false)
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
  it('returns display:none when no host or no selection', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>(null)

      return useNodeCard({
        hostEl: ref(null),
        selectedNodeId,
        getNodeById: () => null,
        getLayoutNodeById: () => null,
        getNodeScreenSize: () => ({ w: 10, h: 10 }),
        worldToScreen: (x, y) => ({ x, y }),
      })
    }, { wmEnabled: false })

    expect(api.nodeCardStyle.value).toEqual({ display: 'none' })
  })

  it('computes clamped style from worldToScreen', () => {
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
        getNodeScreenSize: () => ({ w: 10, h: 10 }),
        worldToScreen: () => ({ x: 1000, y: 1000 }),
      })
    }, { wmEnabled: false })

    const s = api.nodeCardStyle.value
    expect(typeof s.left).toBe('string')
    expect(typeof s.top).toBe('string')
  })

  it('cardRef is exported and initially null', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>(null)

      return useNodeCard({
        hostEl: ref(null),
        selectedNodeId,
        getNodeById: () => null,
        getLayoutNodeById: () => null,
        getNodeScreenSize: () => ({ w: 10, h: 10 }),
        worldToScreen: (x, y) => ({ x, y }),
      })
    }, { wmEnabled: false })

    // cardRef должен быть Ref и изначально равен null
    expect('cardRef' in api).toBe(true)
    expect(api.cardRef.value).toBeNull()
  })

  it('nodeCardStyle returns { display: block } in WM mode (reclamp не применяется)', () => {
    const api = withSetup(() => {
      const selectedNodeId = ref<string | null>('A')

      return useNodeCard({
        hostEl: ref(null),
        selectedNodeId,
        getNodeById: (id) => (id ? ({ id } as any) : null),
        getLayoutNodeById: (id) => ({ id, __x: 10, __y: 20 }),
        getNodeScreenSize: () => ({ w: 10, h: 10 }),
        worldToScreen: (x, y) => ({ x, y }),
      })
    }, { wmEnabled: true })

    expect(api.nodeCardStyle.value).toEqual({ display: 'block' })
  })
})
