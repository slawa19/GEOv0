import { createApp, h, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type {
  BottleneckTarget,
  BottlenecksResponse,
  MetricsResponse,
} from '../api/simulatorTypes'
import type { MetricsStreamPhase } from '../composables/useMetricsPolling'
import RealMetricsPanel from './RealMetricsPanel.vue'

const panel: Component = RealMetricsPanel

type PanelProps = {
  phase: MetricsStreamPhase
  metrics: MetricsResponse | null
  bottlenecks: BottlenecksResponse | null
  lastError?: string
  unavailableReason?: string
  getNodeName?: (id: string) => string | null
  onFocusBottleneck?: (target: BottleneckTarget) => void
}

function mount(props: PanelProps) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(panel, props) })
  app.mount(host)
  return {
    host,
    text: () => (host.textContent ?? '').replace(/\s+/g, ' ').trim(),
    destroy() {
      app.unmount()
      host.remove()
    },
  }
}

const metrics: MetricsResponse = {
  api_version: 'v1',
  run_id: 'run-1',
  equivalent: 'UAH',
  from_ms: 0,
  to_ms: 10_000,
  step_ms: 5_000,
  series: [
    {
      key: 'success_rate',
      unit: '%',
      points: [
        { t_ms: 0, v: '90' },
        { t_ms: 5_000, v: '87' },
      ],
    },
    {
      key: 'total_debt',
      unit: 'amount',
      points: [
        { t_ms: 0, v: '12500.00000000' },
        { t_ms: 5_000, v: '12500.00000000' },
      ],
    },
  ],
}

const bottlenecks: BottlenecksResponse = {
  api_version: 'v1',
  run_id: 'run-1',
  equivalent: 'UAH',
  items: [
    {
      target: { kind: 'edge', from: 'alice', to: 'bob' },
      score: 0.85,
      reason_code: 'FREQUENT_ABORTS',
      label: null,
      suggested_action: null,
    },
  ],
}

function readyProps(): PanelProps {
  return { phase: 'ready', metrics, bottlenecks }
}

function unavailableProps(): PanelProps {
  return {
    phase: 'unavailable',
    metrics: null,
    bottlenecks: null,
    unavailableReason: 'storage_disabled',
  }
}

function errorProps(): PanelProps {
  return {
    phase: 'error',
    metrics: null,
    bottlenecks: null,
    lastError: 'HTTP 500 boom',
  }
}

describe('RealMetricsPanel', () => {
  it('gives ready, unavailable and error three different results on screen', () => {
    const ready = mount(readyProps())
    const unavailable = mount(unavailableProps())
    const failed = mount(errorProps())
    try {
      expect(ready.host.querySelector('[data-state="ready"]')).not.toBeNull()
      expect(ready.host.querySelectorAll('.mkc')).toHaveLength(2)
      expect(ready.host.querySelector('.ds-alert')).toBeNull()

      expect(unavailable.host.querySelector('[data-state="unavailable"]')).not.toBeNull()
      expect(unavailable.host.querySelector('.mkc')).toBeNull()

      expect(failed.host.querySelector('[data-state="error"]')).not.toBeNull()
      expect(failed.host.querySelector('.mkc')).toBeNull()

      const texts = [ready.text(), unavailable.text(), failed.text()]
      expect(new Set(texts).size).toBe(3)

      const phases = [ready, unavailable, failed].map((m) =>
        m.host.querySelector('.rmp')?.getAttribute('data-phase'),
      )
      expect(phases).toEqual(['ready', 'unavailable', 'error'])
    } finally {
      ready.destroy()
      unavailable.destroy()
      failed.destroy()
    }
  })

  it('reads "no measurements" as absent data, not as something being broken', () => {
    const unavailable = mount(unavailableProps())
    try {
      const state = unavailable.host.querySelector('[data-state="unavailable"]')
      expect(state).not.toBeNull()

      // Not an alert: no assertive role, no danger surface, no failure wording.
      expect(state?.getAttribute('role')).toBe('status')
      expect(unavailable.host.querySelector('[role="alert"]')).toBeNull()
      expect(state?.className).not.toContain('ds-alert--err')
      expect(unavailable.text()).not.toMatch(/error|fail(ed|ure)?|went wrong/i)

      expect(unavailable.text()).toContain('No measurements recorded')
      // The reason token is on screen so a user report can be found in the backend log.
      expect(unavailable.text()).toContain('storage_disabled')
    } finally {
      unavailable.destroy()
    }
  })

  it('explains each unavailable reason in its own words', () => {
    const readFailed = mount({ ...unavailableProps(), unavailableReason: 'db_read_failed' })
    const storageOff = mount(unavailableProps())
    try {
      expect(readFailed.text()).not.toBe(storageOff.text())
      expect(readFailed.text()).toContain('could not be read')
      expect(storageOff.text()).toContain('storage is off')
      // Still not an error surface, whatever the reason.
      expect(readFailed.host.querySelector('[role="alert"]')).toBeNull()
    } finally {
      readFailed.destroy()
      storageOff.destroy()
    }
  })

  it('reports a real failure as a failure, with the message that caused it', () => {
    const failed = mount(errorProps())
    try {
      const state = failed.host.querySelector('[data-state="error"]')
      expect(state?.getAttribute('role')).toBe('alert')
      expect(state?.className).toContain('ds-alert--err')
      expect(failed.text()).toContain('Analytics request failed')
      expect(failed.text()).toContain('HTTP 500 boom')
      expect(failed.text()).not.toContain('No measurements recorded')
    } finally {
      failed.destroy()
    }
  })

  it('renders only the series that arrived, in the declared order, without indexing blindly', () => {
    const ready = mount(readyProps())
    try {
      const keys = Array.from(ready.host.querySelectorAll('.mkc')).map((el) =>
        el.getAttribute('data-series-key'),
      )
      expect(keys).toEqual(['success_rate', 'total_debt'])
      // `amount` series are labelled with the run's equivalent, taken from the response.
      expect(ready.text()).toContain('UAH')
    } finally {
      ready.destroy()
    }
  })

  it('forwards the focus request from the bottlenecks list to its owner', () => {
    const onFocusBottleneck = vi.fn()
    const ready = mount({ ...readyProps(), onFocusBottleneck })
    try {
      const button = ready.host.querySelector('.bnl__item button') as HTMLButtonElement | null
      expect(button).not.toBeNull()
      button?.click()

      expect(onFocusBottleneck).toHaveBeenCalledTimes(1)
      expect(onFocusBottleneck).toHaveBeenCalledWith({ kind: 'edge', from: 'alice', to: 'bob' })
    } finally {
      ready.destroy()
    }
  })

  it('keeps idle and loading apart from both no-data and failure', () => {
    const idle = mount({ phase: 'idle', metrics: null, bottlenecks: null })
    const loading = mount({ phase: 'loading', metrics: null, bottlenecks: null })
    try {
      expect(idle.host.querySelector('[data-state="idle"]')).not.toBeNull()
      expect(loading.host.querySelector('[data-state="loading"]')).not.toBeNull()
      expect(idle.text()).not.toBe(loading.text())
      for (const m of [idle, loading]) {
        expect(m.host.querySelector('[role="alert"]')).toBeNull()
        expect(m.host.querySelector('[data-state="unavailable"]')).toBeNull()
      }
    } finally {
      idle.destroy()
      loading.destroy()
    }
  })
})
