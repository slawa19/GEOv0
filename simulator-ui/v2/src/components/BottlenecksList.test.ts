import { createApp, h, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { BottleneckItem, BottleneckTarget } from '../api/simulatorTypes'
import BottlenecksList from './BottlenecksList.vue'

const bottlenecksList: Component = BottlenecksList

function mount(props: {
  items: BottleneckItem[]
  getNodeName?: (id: string) => string | null
  onFocusBottleneck?: (target: BottleneckTarget) => void
}) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(bottlenecksList, props) })
  app.mount(host)
  return {
    host,
    destroy() {
      app.unmount()
      host.remove()
    },
  }
}

const edgeItem: BottleneckItem = {
  target: { kind: 'edge', from: 'alice', to: 'bob' },
  score: 0.85,
  reason_code: 'FREQUENT_ABORTS',
  label: 'Frequent failures',
  suggested_action: 'Increase trust limits',
}

describe('BottlenecksList', () => {
  it('says so plainly when there is nothing to report', () => {
    const list = mount({ items: [] })
    try {
      expect(list.host.querySelector('[data-state="empty"]')).not.toBeNull()
      expect(list.host.textContent).toContain('No bottlenecks reported')
      expect(list.host.querySelectorAll('.bnl__item')).toHaveLength(0)
    } finally {
      list.destroy()
    }
  })

  it('renders the edge, its reason code, score and advice, resolving participant names', () => {
    const list = mount({
      items: [edgeItem],
      getNodeName: (id) => (id === 'alice' ? 'Alice' : null),
    })
    try {
      const item = list.host.querySelector('.bnl__item')
      expect(item?.getAttribute('data-reason-code')).toBe('FREQUENT_ABORTS')
      expect(item?.getAttribute('data-target-kind')).toBe('edge')
      expect((list.host.querySelector('.bnl__target')?.textContent ?? '').trim()).toBe(
        'Alice → bob',
      )
      expect((list.host.querySelector('.bnl__score')?.textContent ?? '').trim()).toBe('85%')
      expect(list.host.textContent).toContain('Frequent failures')
      expect(list.host.textContent).toContain('Increase trust limits')
    } finally {
      list.destroy()
    }
  })

  it('maps severity onto design-system badge modifiers instead of literal colours', () => {
    const list = mount({
      items: [
        { target: { kind: 'node', id: 'hub' }, score: 0.9, reason_code: 'HIGH_USED' },
        { target: { kind: 'node', id: 'carol' }, score: 0.45, reason_code: 'LOW_AVAILABLE' },
        { target: { kind: 'node', id: 'dave' }, score: 0.1, reason_code: 'CLEARING_PRESSURE' },
      ],
    })
    try {
      const badges = Array.from(list.host.querySelectorAll('.bnl__score'))
      expect(badges).toHaveLength(3)
      expect(badges[0]?.className).toContain('ds-badge--err')
      expect(badges[1]?.className).toContain('ds-badge--warn')
      expect(badges[2]?.className).toContain('ds-badge--ok')
      for (const badge of badges) {
        expect(badge.getAttribute('style')).toBeNull()
      }
    } finally {
      list.destroy()
    }
  })

  it('emits the target of the bottleneck the user asked to focus', () => {
    const onFocusBottleneck = vi.fn()
    const list = mount({
      items: [edgeItem, { target: { kind: 'node', id: 'hub' }, score: 0.4, reason_code: 'HIGH_USED' }],
      onFocusBottleneck,
    })
    try {
      const buttons = list.host.querySelectorAll('button')
      expect(buttons).toHaveLength(2)
      ;(buttons[1] as HTMLButtonElement).click()

      expect(onFocusBottleneck).toHaveBeenCalledTimes(1)
      expect(onFocusBottleneck).toHaveBeenCalledWith({ kind: 'node', id: 'hub' })
    } finally {
      list.destroy()
    }
  })
})
