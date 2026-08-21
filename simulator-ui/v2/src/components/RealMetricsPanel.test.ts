import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createApp, h, type Component } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { BottleneckTarget, BottlenecksResponse, MetricsResponse } from '../api/simulatorTypes'
import type { MetricsStreamPhase } from '../composables/useMetricsPolling'
import { getOverlaySurfaceDescriptor } from '../ui-kit/overlaySurfaceCatalog'
import RealMetricsPanel from './RealMetricsPanel.vue'

const panel: Component = RealMetricsPanel

type PanelProps = {
  metricsPhase: MetricsStreamPhase
  bottlenecksPhase: MetricsStreamPhase
  metrics: MetricsResponse | null
  bottlenecks: BottlenecksResponse | null
  metricsError?: string
  bottlenecksError?: string
  metricsUnavailableReason?: string
  bottlenecksUnavailableReason?: string
  getNodeName?: (id: string) => string | null
  onFocusBottleneck?: (target: BottleneckTarget) => void
}

function normalise(text: string | null | undefined): string {
  return (text ?? '').replace(/\s+/g, ' ').trim()
}

function mount(props: PanelProps) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(panel, props) })
  app.mount(host)
  return {
    host,
    text: () => normalise(host.textContent),
    /** The subtree that answers for one stream, and for nothing else. */
    section(name: 'metrics' | 'bottlenecks'): Element {
      const el = host.querySelector(`[data-section="${name}"]`)
      expect(el).not.toBeNull()
      return el as Element
    },
    sectionText(name: 'metrics' | 'bottlenecks'): string {
      return normalise(this.section(name).textContent)
    },
    destroy() {
      app.unmount()
      host.remove()
    },
  }
}

/**
 * The section's words, with the correlation token removed.
 *
 * The token is a log key (`db_read_failed`), not a sentence, and it is rendered in an element of
 * its own for exactly that reason. Reading the prose apart from it is what lets the "this must not
 * read as a failure" assertion apply to BOTH unavailable reasons instead of only the one whose
 * token happens to contain no failure word.
 */
function proseOf(el: Element): string {
  const clone = el.cloneNode(true) as Element
  for (const token of Array.from(clone.querySelectorAll('.rmp__state-reason'))) token.remove()
  return normalise(clone.textContent)
}

const FAILURE_WORDING = /error|fail(ed|ure)?|went wrong|broke/i

/**
 * Deliberately NOT in the panel's declared display order: `total_debt` comes fifth in
 * `SERIES_ORDER` and arrives first here. A fixture already sorted the right way makes the
 * intersection with the declared order indistinguishable from `props.metrics.series` verbatim.
 */
const metrics: MetricsResponse = {
  api_version: 'v1',
  run_id: 'run-1',
  equivalent: 'UAH',
  from_ms: 0,
  to_ms: 10_000,
  step_ms: 5_000,
  series: [
    {
      key: 'total_debt',
      unit: 'amount',
      points: [
        { t_ms: 0, v: '12500.00000000' },
        { t_ms: 5_000, v: '12500.00000000' },
      ],
    },
    {
      key: 'success_rate',
      unit: '%',
      points: [
        { t_ms: 0, v: '90' },
        { t_ms: 5_000, v: '87' },
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
  return { metricsPhase: 'ready', bottlenecksPhase: 'ready', metrics, bottlenecks }
}

function unavailableProps(): PanelProps {
  return {
    metricsPhase: 'unavailable',
    bottlenecksPhase: 'unavailable',
    metrics: null,
    bottlenecks: null,
    metricsUnavailableReason: 'storage_disabled',
    bottlenecksUnavailableReason: 'storage_disabled',
  }
}

function errorProps(): PanelProps {
  return {
    metricsPhase: 'error',
    bottlenecksPhase: 'error',
    metrics: null,
    bottlenecks: null,
    metricsError: 'HTTP 500 boom',
    bottlenecksError: 'HTTP 500 boom',
  }
}

const PHASES: MetricsStreamPhase[] = ['idle', 'loading', 'ready', 'unavailable', 'error']

describe('RealMetricsPanel', () => {
  /**
   * The finding this panel was rebuilt for.
   *
   * `/metrics` and `/bottlenecks` are answered from two different database sessions and each can
   * raise `db_read_failed` on its own, so `(metrics ready, bottlenecks unavailable)` is an
   * ordinary poll outcome. The previous panel took one merged word for both streams, and in that
   * pair the word was "unavailable": the screen read "No measurements recorded / Nothing is being
   * substituted for them" while holding a decoded `12500.00000000`.
   *
   * The assertion below is that sentence, not a list of phases: across all 25 pairs, whatever the
   * panel holds is on screen, and no section denies data its own stream delivered.
   */
  it('never denies data it is holding, in any of the 25 stream-state pairs', () => {
    for (const metricsPhase of PHASES) {
      for (const bottlenecksPhase of PHASES) {
        const m = mount({
          metricsPhase,
          bottlenecksPhase,
          metrics: metricsPhase === 'ready' ? metrics : null,
          bottlenecks: bottlenecksPhase === 'ready' ? bottlenecks : null,
          metricsError: metricsPhase === 'error' ? 'HTTP 500 metrics' : '',
          bottlenecksError: bottlenecksPhase === 'error' ? 'HTTP 500 bottlenecks' : '',
          metricsUnavailableReason: metricsPhase === 'unavailable' ? 'db_read_failed' : '',
          bottlenecksUnavailableReason:
            bottlenecksPhase === 'unavailable' ? 'storage_disabled' : '',
        })
        const where = `${metricsPhase}/${bottlenecksPhase}`
        try {
          const metricsSection = m.section('metrics')
          const bottlenecksSection = m.section('bottlenecks')

          if (metricsPhase === 'ready') {
            // Held: both series are rendered, with the money value verbatim...
            expect(metricsSection.querySelectorAll('.mkc'), where).toHaveLength(2)
            expect(m.sectionText('metrics'), where).toContain('12500')
            // ...and nothing in this section says those measurements are absent or broken.
            expect(proseOf(metricsSection), where).not.toMatch(FAILURE_WORDING)
            expect(m.sectionText('metrics'), where).not.toContain('No measurements recorded')
            expect(m.sectionText('metrics'), where).not.toContain('no recorded measurements')
          } else {
            expect(metricsSection.querySelector('.mkc'), where).toBeNull()
          }

          if (bottlenecksPhase === 'ready') {
            expect(bottlenecksSection.querySelector('.bnl__item'), where).not.toBeNull()
            expect(m.sectionText('bottlenecks'), where).toContain('alice')
            expect(proseOf(bottlenecksSection), where).not.toMatch(FAILURE_WORDING)
            expect(m.sectionText('bottlenecks'), where).not.toContain('No bottlenecks recorded')
          } else {
            expect(bottlenecksSection.querySelector('.bnl__item'), where).toBeNull()
          }

          // An alert belongs to the stream that actually failed, and to no other.
          expect(Boolean(metricsSection.querySelector('[role="alert"]')), where).toBe(
            metricsPhase === 'error',
          )
          expect(Boolean(bottlenecksSection.querySelector('[role="alert"]')), where).toBe(
            bottlenecksPhase === 'error',
          )
        } finally {
          m.destroy()
        }
      }
    }
  })

  it('shows the measurements it has while reporting that the other stream has none', () => {
    const m = mount({
      metricsPhase: 'ready',
      bottlenecksPhase: 'unavailable',
      metrics,
      bottlenecks: null,
      bottlenecksUnavailableReason: 'db_read_failed',
    })
    try {
      // The exact datum the review measured: decoded, held, and on screen.
      const debt = m.section('metrics').querySelector('.mkc[data-series-key="total_debt"]')
      expect(debt).not.toBeNull()
      expect(normalise(debt?.querySelector('.mkc__value')?.textContent)).toBe('12500')

      // ...while the section that really has nothing says so, in its own words, with its own token.
      const state = m.section('bottlenecks').querySelector('[data-state="unavailable"]')
      expect(state).not.toBeNull()
      expect(normalise(state?.textContent)).toContain('db_read_failed')
      expect(m.sectionText('metrics')).not.toContain('db_read_failed')
    } finally {
      m.destroy()
    }
  })

  it('keeps a 503 on one stream from being drawn as the other stream failing', () => {
    const m = mount({
      metricsPhase: 'unavailable',
      bottlenecksPhase: 'error',
      metrics: null,
      bottlenecks: null,
      metricsUnavailableReason: 'db_read_failed',
      bottlenecksError: 'HTTP 500 bottlenecks',
    })
    try {
      // The honest 503 stays a status with its reason, not an alert...
      const unavailable = m.section('metrics').querySelector('[data-state="unavailable"]')
      expect(unavailable?.getAttribute('role')).toBe('status')
      expect(normalise(unavailable?.textContent)).toContain('db_read_failed')
      expect(m.section('metrics').querySelector('[role="alert"]')).toBeNull()

      // ...and the failure stays a failure, with the message that caused it, in its own section.
      const failed = m.section('bottlenecks').querySelector('[data-state="error"]')
      expect(failed?.getAttribute('role')).toBe('alert')
      expect(normalise(failed?.textContent)).toContain('HTTP 500 bottlenecks')
      expect(m.sectionText('metrics')).not.toContain('HTTP 500 bottlenecks')
    } finally {
      m.destroy()
    }
  })

  it('gives ready, unavailable and error three different results on screen', () => {
    const ready = mount(readyProps())
    const unavailable = mount(unavailableProps())
    const failed = mount(errorProps())
    try {
      expect(ready.section('metrics').querySelector('[data-state="ready"]')).not.toBeNull()
      expect(ready.host.querySelectorAll('.mkc')).toHaveLength(2)
      expect(ready.host.querySelector('.ds-alert')).toBeNull()

      expect(unavailable.section('metrics').querySelector('[data-state="unavailable"]')).not.toBeNull()
      expect(unavailable.host.querySelector('.mkc')).toBeNull()

      expect(failed.section('metrics').querySelector('[data-state="error"]')).not.toBeNull()
      expect(failed.host.querySelector('.mkc')).toBeNull()

      const texts = [ready.text(), unavailable.text(), failed.text()]
      expect(new Set(texts).size).toBe(3)

      // Both phases are published, separately: the surface exposes what it was told, per stream.
      const root = (m: { host: HTMLElement }) => m.host.querySelector('.rmp')
      expect(
        [ready, unavailable, failed].map((m) => root(m)?.getAttribute('data-metrics-phase')),
      ).toEqual(['ready', 'unavailable', 'error'])
      expect(
        [ready, unavailable, failed].map((m) => root(m)?.getAttribute('data-bottlenecks-phase')),
      ).toEqual(['ready', 'unavailable', 'error'])
    } finally {
      ready.destroy()
      unavailable.destroy()
      failed.destroy()
    }
  })

  it.each(['storage_disabled', 'db_read_failed'])(
    'reads "no measurements" as absent data and not as breakage, for reason %s',
    (reason) => {
      const unavailable = mount({
        ...unavailableProps(),
        metricsUnavailableReason: reason,
        bottlenecksUnavailableReason: reason,
      })
      try {
        const state = unavailable.section('metrics').querySelector('[data-state="unavailable"]')
        expect(state).not.toBeNull()

        // Not an alert: no assertive role, no danger surface, no failure wording.
        expect(state?.getAttribute('role')).toBe('status')
        expect(unavailable.host.querySelector('[role="alert"]')).toBeNull()
        expect(state?.className).not.toContain('ds-alert--err')

        // The prose, apart from the token. `db_read_failed` used to exempt itself from this check
        // by being the thing that matched it; the token now lives in its own element.
        expect(proseOf(state as Element)).not.toMatch(FAILURE_WORDING)
        expect(proseOf(state as Element)).toContain('No measurements recorded')

        // The reason token is still on screen so a user report can be found in the backend log.
        expect(normalise(state?.textContent)).toContain(reason)
        expect(state?.querySelector('.rmp__state-reason')?.textContent).toContain(reason)
      } finally {
        unavailable.destroy()
      }
    },
  )

  it('explains each unavailable reason in its own words', () => {
    const readFailed = mount({
      ...unavailableProps(),
      metricsUnavailableReason: 'db_read_failed',
      bottlenecksUnavailableReason: 'db_read_failed',
    })
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
      const state = failed.section('metrics').querySelector('[data-state="error"]')
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
    // The wire order is not the display order, and this is what makes the test able to fail:
    // rendering `props.metrics.series` verbatim would produce the reverse of the expectation.
    expect(metrics.series.map((s) => s.key)).toEqual(['total_debt', 'success_rate'])

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
    const idle = mount({
      metricsPhase: 'idle',
      bottlenecksPhase: 'idle',
      metrics: null,
      bottlenecks: null,
    })
    const loading = mount({
      metricsPhase: 'loading',
      bottlenecksPhase: 'loading',
      metrics: null,
      bottlenecks: null,
    })
    try {
      expect(idle.section('metrics').querySelector('[data-state="idle"]')).not.toBeNull()
      expect(loading.section('metrics').querySelector('[data-state="loading"]')).not.toBeNull()
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

  /**
   * The ui-kit guide's surface table is documentation that the same file (section 6.1) declares
   * subordinate to `overlaySurfaceCatalog.ts`. It said `root inset` for this panel — the one
   * placement the catalog explicitly rejects, and which `overlaySurfaceCatalog.test.ts` exists to
   * deny. A row nobody checks drifts; this checks it.
   */
  it('is described in the ui-kit guide exactly as the catalog defines it', () => {
    const here = dirname(fileURLToPath(import.meta.url))
    const guide = readFileSync(resolve(here, '../ui-kit/AI-AGENT-GUIDE.md'), 'utf8')
    const row = guide.split('\n').find((line) => line.startsWith('| RealMetricsPanel |'))
    expect(row).toBeDefined()

    const descriptor = getOverlaySurfaceDescriptor('real-metrics-panel')
    expect(row).toContain(`\`${descriptor.family}\``)
    expect(row).toContain(`\`${descriptor.sizingMode}\``)
    expect(row).toContain(`\`${descriptor.positioningOwner}\``)
    expect(row).toContain(`\`${descriptor.zLayerToken}\``)

    // Counter-probe: the value the row used to carry, which the catalog denies.
    expect(descriptor.positioningOwner).not.toBe('root-inset')
    expect(row).not.toMatch(/\broot inset\b/)
  })
})
