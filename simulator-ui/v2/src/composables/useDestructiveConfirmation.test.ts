import { createApp, defineComponent, h, nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import WindowShell from '../components/WindowShell.vue'
import type { WindowDataByType, WindowInstance } from './windowManager/types'
import { useDestructiveConfirmation } from './useDestructiveConfirmation'

function makeInstance(overrides?: Partial<WindowInstance>): WindowInstance {
  const data: WindowDataByType['edge-detail'] = { fromPid: 'A', toPid: 'B', title: 'A → B' }
  return {
    id: 1,
    type: 'edge-detail',
    lifecyclePhase: 'stable',
    data,
    rect: { left: 10, top: 10, width: 200, height: 120 },
    z: 1,
    effectiveZ: 1,
    active: true,
    policy: {
      group: 'inspector',
      singleton: 'reuse',
      sizingMode: 'bounded-intrinsic',
      widthOwner: 'measured',
      heightOwner: 'measured',
      escBehavior: 'close',
      closeOnOutsideClick: false,
    },
    constraints: {
      minWidth: 120,
      minHeight: 80,
      maxWidth: 100000,
      maxHeight: 100000,
      preferredWidth: 200,
      preferredHeight: 120,
    },
    anchor: null,
    anchorOffset: null,
    placement: 'docked-right',
    measured: null,
    state: 'open',
    ...overrides,
  }
}

describe('useDestructiveConfirmation (TODO-ESC)', () => {
  it('disarms on Escape keydown dispatched on the window container element (not on global window)', async () => {
    const host = document.createElement('div')
    document.body.appendChild(host)

    let api: ReturnType<typeof useDestructiveConfirmation> | null = null

    const Child = defineComponent({
      name: 'TestChild',
      setup() {
        api = useDestructiveConfirmation()
        return () => h('div', { class: 'child' }, 'x')
      },
    })

    const app = createApp({
      render: () =>
        h(
          WindowShell,
          {
            instance: makeInstance(),
            frameless: true,
          },
          { default: () => h(Child) },
        ),
    })

    try {
      app.mount(host)
      await nextTick()

      const container = host.querySelector('[data-win-id="1"]') as HTMLElement
      expect(container).toBeTruthy()
      expect(api).toBeTruthy()

      api!.arm()
      expect(api!.armed.value).toBe(true)

      const ev1 = new KeyboardEvent('keydown', { key: 'Escape', cancelable: true })
      container.dispatchEvent(ev1)

      expect(api!.armed.value).toBe(false)
      expect(ev1.defaultPrevented).toBe(true)

      // Regression guard: no global `window` listener.
      api!.arm()
      expect(api!.armed.value).toBe(true)

      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', cancelable: true }))
      expect(api!.armed.value).toBe(true)
    } finally {
      app.unmount()
      host.remove()
    }
  })
})

