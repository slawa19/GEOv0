import { createApp, h, type Component } from 'vue'
import { describe, expect, it } from 'vitest'

import type { MetricPoint, MetricSeries } from '../api/simulatorTypes'
import MetricsKpiCard from './MetricsKpiCard.vue'

const kpiCard: Component = MetricsKpiCard

function mount(series: MetricSeries, equivalent?: string) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const app = createApp({ render: () => h(kpiCard, { series, equivalent }) })
  app.mount(host)
  return {
    host,
    destroy() {
      app.unmount()
      host.remove()
    },
  }
}

function svgOf(host: HTMLElement): Element {
  const svg = host.querySelector('.mkc__spark')
  expect(svg).not.toBeNull()
  return svg as Element
}

function dots(host: HTMLElement): Element[] {
  return Array.from(host.querySelectorAll('.mkc__spark-dot'))
}

function valueText(host: HTMLElement): string {
  return (host.querySelector('.mkc__value')?.textContent ?? '').trim()
}

const GAP_POINTS: MetricPoint[] = [
  { t_ms: 0, v: '10' },
  { t_ms: 5_000, v: null },
  { t_ms: 10_000, v: '10' },
]

/** Identical shape, except the middle point was MEASURED and the measurement was zero. */
const ZERO_POINTS: MetricPoint[] = [
  { t_ms: 0, v: '10' },
  { t_ms: 5_000, v: '0.00000000' },
  { t_ms: 10_000, v: '10' },
]

describe('MetricsKpiCard', () => {
  it('draws a null point as a break in the line and a measured zero as a point on the line', () => {
    const gap = mount({ key: 'success_rate', unit: '%', points: GAP_POINTS })
    const zero = mount({ key: 'success_rate', unit: '%', points: ZERO_POINTS })
    try {
      const gapSvg = svgOf(gap.host)
      const zeroSvg = svgOf(zero.host)

      // A `null` splits the run in two: two polylines with nothing drawn across t=5000.
      expect(gapSvg.getAttribute('data-segment-count')).toBe('2')
      expect(gapSvg.getAttribute('data-gap-count')).toBe('1')
      expect(gap.host.querySelectorAll('.mkc__spark-line')).toHaveLength(2)
      expect(gap.host.querySelector('.mkc__spark-dot[data-t-ms="5000"]')).toBeNull()

      // A measured zero keeps the run whole: one polyline, three plotted points.
      expect(zeroSvg.getAttribute('data-segment-count')).toBe('1')
      expect(zeroSvg.getAttribute('data-gap-count')).toBe('0')
      expect(zero.host.querySelectorAll('.mkc__spark-line')).toHaveLength(1)

      const zeroDot = zero.host.querySelector('.mkc__spark-dot[data-t-ms="5000"]')
      expect(zeroDot).not.toBeNull()
      expect(zeroDot?.getAttribute('data-v')).toBe('0.00000000')

      // ...and it sits at the bottom of the chart, i.e. the zero baseline: strictly lower on
      // screen (larger y) than the two 10s, which sit at the top.
      const ys = dots(zero.host).map((d) => Number(d.getAttribute('cy')))
      expect(ys).toHaveLength(3)
      expect(ys[1]).toBeGreaterThan(ys[0] as number)
      expect(ys[1]).toBeGreaterThan(ys[2] as number)
      expect(Math.max(...ys)).toBe(ys[1])

      // The two renderings must not be interchangeable.
      expect(gapSvg.innerHTML).not.toBe(zeroSvg.innerHTML)
      expect(dots(gap.host)).toHaveLength(2)
      expect(dots(zero.host)).toHaveLength(3)
    } finally {
      gap.destroy()
      zero.destroy()
    }
  })

  it('reads a trailing null as "no measurement" and never carries the previous value forward', () => {
    const gap = mount({ key: 'total_debt', unit: 'amount', points: [...GAP_POINTS.slice(0, 2)] })
    try {
      expect(gap.host.querySelector('.mkc')?.getAttribute('data-latest-kind')).toBe('gap')
      expect(valueText(gap.host)).toBe('—')
      // The carried-forward "10" is exactly the fabrication T711 removed from the backend.
      expect(valueText(gap.host)).not.toContain('10')
    } finally {
      gap.destroy()
    }
  })

  it('reads a trailing measured zero as a zero, not as a missing measurement', () => {
    const zero = mount({ key: 'total_debt', unit: 'amount', points: [...ZERO_POINTS.slice(0, 2)] })
    try {
      expect(zero.host.querySelector('.mkc')?.getAttribute('data-latest-kind')).toBe('value')
      expect(valueText(zero.host)).toBe('0')
      expect(valueText(zero.host)).not.toBe('—')
    } finally {
      zero.destroy()
    }
  })

  it('renders a money decimal string into the DOM without a round-trip through a JS number', () => {
    // 28 significant digits: a double cannot hold this, so any Number() on the path is visible.
    const exact = '12345678901234567890.12345678'
    expect(String(Number(exact))).not.toBe(exact)

    const card = mount(
      {
        key: 'total_debt',
        unit: 'amount',
        points: [
          { t_ms: 0, v: '1' },
          { t_ms: 5_000, v: exact },
        ],
      },
      'UAH',
    )
    try {
      expect(valueText(card.host)).toBe(exact)
      expect(valueText(card.host)).not.toBe(String(Number(exact)))
      expect((card.host.querySelector('.mkc__unit')?.textContent ?? '').trim()).toBe('UAH')

      // The chart carries the same untouched string.
      const lastDot = card.host.querySelector('.mkc__spark-dot[data-t-ms="5000"]')
      expect(lastDot?.getAttribute('data-v')).toBe(exact)
    } finally {
      card.destroy()
    }
  })

  it('orders two money values a double would collapse into one', () => {
    // These differ only in the 21st significant digit: as doubles they are equal.
    const low = '100000000000000000001'
    const high = '100000000000000000002'
    expect(Number(low)).toBe(Number(high))

    const card = mount({
      key: 'clearing_volume',
      unit: 'amount',
      points: [
        { t_ms: 0, v: low },
        { t_ms: 5_000, v: high },
      ],
    })
    try {
      const ys = dots(card.host).map((d) => Number(d.getAttribute('cy')))
      // Larger value plots higher on screen (smaller y). A Number()-based scale would tie them.
      expect(ys[1]).toBeLessThan(ys[0] as number)
    } finally {
      card.destroy()
    }
  })

  it('withholds the line when a value is outside the wire contract instead of showing it as a gap', () => {
    const card = mount({
      key: 'success_rate',
      unit: '%',
      points: [
        { t_ms: 0, v: '1' },
        { t_ms: 5_000, v: '1e3' },
      ],
    })
    try {
      const svg = svgOf(card.host)
      expect(svg.getAttribute('data-malformed')).toBe('1')
      expect(svg.getAttribute('data-gap-count')).toBe('0')
      expect(card.host.querySelectorAll('.mkc__spark-line')).toHaveLength(0)
      expect(card.host.textContent).toContain('outside the wire contract')
    } finally {
      card.destroy()
    }
  })

  it('labels every declared series key with prose rather than the wire token', () => {
    const card = mount({ key: 'active_trustlines', unit: 'count', points: [{ t_ms: 0, v: '7' }] })
    try {
      expect((card.host.querySelector('.mkc__label')?.textContent ?? '').trim()).toBe(
        'Active trustlines',
      )
      expect(valueText(card.host)).toBe('7')
      expect(card.host.querySelector('.mkc__unit')).toBeNull()
    } finally {
      card.destroy()
    }
  })
})
