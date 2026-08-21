<script setup lang="ts">
import { computed } from 'vue'

import type { MetricPoint, MetricSeries, MetricSeriesKey, MetricUnit } from '../api/simulatorTypes'

const props = defineProps<{
  series: MetricSeries
  /** Equivalent code, appended only to `amount` series. Never parsed, never computed with. */
  equivalent?: string
}>()

/**
 * Human titles for the seven declared series keys. Typed as a total `Record`, so adding a key to
 * `MetricSeriesKey` breaks the build here instead of silently rendering a raw wire token.
 */
const SERIES_TITLE: Record<MetricSeriesKey, string> = {
  success_rate: 'Success rate',
  avg_route_length: 'Avg route length',
  total_debt: 'Total debt',
  clearing_volume: 'Clearing volume',
  bottlenecks_score: 'Bottlenecks score',
  active_participants: 'Active participants',
  active_trustlines: 'Active trustlines',
}

/** Sparkline geometry, in viewBox units. Not pixels: the SVG stretches to its container. */
const CHART_W = 100
const CHART_H = 32
const CHART_PAD = 2

/**
 * The exact shape `decimalStringAt` (`api/simulatorContracts.ts`) admits. Re-checked here rather
 * than assumed: a value that does not match is a contract breach, and a breach must stay visible
 * as a breach — not quietly redrawn as a gap, which is a different statement about the run.
 */
const PLAIN_DECIMAL = /^-?\d+(?:\.\d+)?$/

function fractionDigits(text: string): number {
  const dot = text.indexOf('.')
  return dot < 0 ? 0 : text.length - dot - 1
}

/**
 * Exact decimal-string to integer conversion at a fixed scale, via `BigInt`.
 *
 * This is why the chart never goes through `Number`: two of the seven series are money
 * (`total_debt`, `clearing_volume`) and arrive as decimal strings precisely because a JS double
 * cannot hold them (AGENTS.md section 8). Scaling to a common power of ten and comparing as
 * integers keeps ordering and ratios exact; only the final 0..1000 ratio — a small integer that
 * is exactly representable — ever becomes a `Number`.
 */
function toScaled(text: string, scale: number): bigint {
  const negative = text.startsWith('-')
  const body = negative ? text.slice(1) : text
  const dot = body.indexOf('.')
  const intPart = dot < 0 ? body : body.slice(0, dot)
  const fracPart = dot < 0 ? '' : body.slice(dot + 1)
  const magnitude = BigInt(`${intPart}${fracPart.padEnd(scale, '0')}`)
  return negative ? -magnitude : magnitude
}

/**
 * Trailing fraction zeros are removed as a pure string operation — no significant digit can be
 * lost this way, and no intermediate number exists in which one could be. `"0.00000000"` becomes
 * `"0"`, which is still a measured zero and still not a gap.
 */
function trimDecimalZeros(text: string): string {
  if (!text.includes('.')) return text
  const trimmed = text.replace(/0+$/, '').replace(/\.$/, '')
  if (trimmed === '' || trimmed === '-') return '0'
  return trimmed === '-0' ? '0' : trimmed
}

type PlottedPoint = { tMs: number; raw: string; x: number; y: number }

type Chart = {
  /** Runs of consecutive measured points. A `null` value ends a run: that break IS the gap. */
  segments: PlottedPoint[][]
  /** Number of points that carried `v: null` — "no measurement", never "measured zero". */
  gapCount: number
  dots: PlottedPoint[]
  /** True when a value did not match the wire contract: the line is withheld, not guessed. */
  malformed: boolean
}

const points = computed<MetricPoint[]>(() => props.series.points ?? [])

const chart = computed<Chart>(() => {
  const list = points.value
  const measured = list.filter((p): p is MetricPoint & { v: string } => p.v !== null)
  const gapCount = list.length - measured.length

  if (measured.some((p) => !PLAIN_DECIMAL.test(p.v))) {
    return { segments: [], gapCount, dots: [], malformed: true }
  }
  if (measured.length === 0) {
    return { segments: [], gapCount, dots: [], malformed: false }
  }

  const scale = measured.reduce((acc, p) => Math.max(acc, fractionDigits(p.v)), 0)
  const scaled = measured.map((p) => toScaled(p.v, scale))
  let min = scaled[0] as bigint
  let max = scaled[0] as bigint
  for (const value of scaled) {
    if (value < min) min = value
    if (value > max) max = value
  }
  const span = max - min

  const firstT = list[0]?.t_ms ?? 0
  const lastT = list[list.length - 1]?.t_ms ?? firstT
  const tSpan = lastT - firstT
  const inner = CHART_H - CHART_PAD * 2
  const usableW = CHART_W - CHART_PAD * 2

  const segments: PlottedPoint[][] = []
  const dots: PlottedPoint[] = []
  let current: PlottedPoint[] | null = null
  let measuredIndex = 0

  list.forEach((point, index) => {
    if (point.v === null) {
      // End the run. One polyline is drawn per run, so "no measurement" shows up as the line
      // being absent across this timestamp — which is what a gap is.
      current = null
      return
    }
    const value = scaled[measuredIndex] as bigint
    measuredIndex += 1

    // The scale is min..max of THIS window, not zero..max: `total_debt` sits far from zero and
    // moves by fractions of a percent, and a zero baseline would draw every such window as the
    // same flat line at the top. The cost — the floor of the card is the window minimum, not a
    // zero — is accepted knowingly and pinned in `MetricsKpiCard.test.ts`.
    //
    // `ratio` is an integer in 0..1000 computed entirely in BigInt; converting THAT to a Number is
    // lossless. The decimal value itself never becomes a Number.
    const ratio = span === 0n ? 500 : Number(((value - min) * 1000n) / span)

    let x: number
    if (tSpan > 0) {
      x = CHART_PAD + ((point.t_ms - firstT) / tSpan) * usableW
    } else if (list.length > 1) {
      x = CHART_PAD + (index / (list.length - 1)) * usableW
    } else {
      x = CHART_W / 2
    }
    const y = CHART_PAD + (1 - ratio / 1000) * inner

    const plotted: PlottedPoint = {
      tMs: point.t_ms,
      raw: point.v,
      x: Number(x.toFixed(2)),
      y: Number(y.toFixed(2)),
    }
    dots.push(plotted)
    if (current === null) {
      current = [plotted]
      segments.push(current)
    } else {
      current.push(plotted)
    }
  })

  return { segments, gapCount, dots, malformed: false }
})

function polylinePoints(segment: PlottedPoint[]): string {
  return segment.map((p) => `${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(' ')
}

const title = computed(() => SERIES_TITLE[props.series.key] ?? props.series.key)

const latest = computed<MetricPoint | null>(() => {
  const list = points.value
  return list.length === 0 ? null : (list[list.length - 1] ?? null)
})

/**
 * The headline reads the LAST point, and a `null` there stays a dash.
 *
 * Carrying the previous measurement forward is exactly the fabrication the backend stopped doing
 * (spec 007, T711); re-introducing it in the card would put the lie back on screen.
 */
const latestKind = computed<'none' | 'gap' | 'value'>(() => {
  const point = latest.value
  if (point === null) return 'none'
  return point.v === null ? 'gap' : 'value'
})

/** The measured value verbatim: the decimal string the backend sent, never a parsed number. */
const latestText = computed(() => {
  const point = latest.value
  if (point === null || point.v === null) return '—'
  return trimDecimalZeros(point.v)
})

const unit = computed<MetricUnit>(() => props.series.unit ?? null)

const unitText = computed(() => {
  if (latestKind.value !== 'value') return ''
  const u = unit.value
  if (u === '%') return '%'
  if (u === 'amount') return String(props.equivalent ?? '').trim()
  return ''
})
</script>

<template>
  <div class="mkc" :data-series-key="series.key" :data-latest-kind="latestKind">
    <div class="mkc__label ds-section-label">{{ title }}</div>
    <div class="mkc__reading">
      <span class="mkc__value ds-value ds-mono">{{ latestText }}</span>
      <span v-if="unitText" class="mkc__unit ds-muted">{{ unitText }}</span>
    </div>
    <svg
      class="mkc__spark"
      :viewBox="`0 0 ${CHART_W} ${CHART_H}`"
      preserveAspectRatio="none"
      role="img"
      :aria-label="`${title} trend`"
      :data-segment-count="chart.segments.length"
      :data-gap-count="chart.gapCount"
      :data-point-count="chart.dots.length"
      :data-malformed="chart.malformed ? '1' : '0'"
    >
      <polyline
        v-for="(segment, i) in chart.segments"
        :key="i"
        class="mkc__spark-line"
        fill="none"
        :points="polylinePoints(segment)"
      />
      <circle
        v-for="dot in chart.dots"
        :key="`${dot.tMs}:${dot.raw}`"
        class="mkc__spark-dot"
        r="1.4"
        :cx="dot.x"
        :cy="dot.y"
        :data-t-ms="dot.tMs"
        :data-v="dot.raw"
      />
    </svg>
    <p v-if="chart.malformed" class="mkc__note ds-help">Series value outside the wire contract</p>
    <p v-else-if="chart.gapCount > 0" class="mkc__note ds-help">
      {{ chart.gapCount }} of {{ points.length }} points have no measurement
    </p>
  </div>
</template>

<style scoped>
/* Layout only. Every colour, spacing and type value comes from a DS token. */
.mkc {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-1);
  min-width: 0;
}

.mkc__reading {
  display: flex;
  align-items: baseline;
  gap: var(--ds-space-1);
  min-width: 0;
}

.mkc__value {
  overflow-wrap: anywhere;
}

.mkc__spark {
  display: block;
  width: 100%;
  height: var(--ds-space-4);
}

.mkc__spark-line {
  stroke: var(--ds-accent);
  stroke-width: 1.5;
  vector-effect: non-scaling-stroke;
}

.mkc__spark-dot {
  fill: var(--ds-accent);
}

.mkc__note {
  margin: 0;
}
</style>
