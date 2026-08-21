<script setup lang="ts">
import { computed } from 'vue'

import type {
  BottleneckTarget,
  BottlenecksResponse,
  MetricSeries,
  MetricSeriesKey,
  MetricsResponse,
} from '../api/simulatorTypes'
import type { MetricsStreamPhase } from '../composables/useMetricsPolling'
import { getOverlaySurfaceDescriptor } from '../ui-kit/overlaySurfaceCatalog'
import BottlenecksList from './BottlenecksList.vue'
import MetricsKpiCard from './MetricsKpiCard.vue'

const props = defineProps<{
  /**
   * One phase per stream, because there are two streams.
   *
   * `GET /metrics` and `GET /bottlenecks` are answered from two different database sessions and
   * fail independently (`app/core/simulator/metrics_bottlenecks.py:250`, `:422`). A merged phase
   * would have to name one of `ready` / `unavailable` / `error` for the pair
   * `(metrics ready, bottlenecks unavailable)` — and every choice makes this panel state something
   * untrue about what it is holding. So each section below renders its own stream and nothing else.
   */
  metricsPhase: MetricsStreamPhase
  bottlenecksPhase: MetricsStreamPhase
  metrics: MetricsResponse | null
  bottlenecks: BottlenecksResponse | null
  /** Non-empty only in that stream's `error`. */
  metricsError?: string
  bottlenecksError?: string
  /** Non-empty only in that stream's `unavailable`: `storage_disabled` / `db_read_failed`. */
  metricsUnavailableReason?: string
  bottlenecksUnavailableReason?: string
  getNodeName?: (id: string) => string | null
}>()

const emit = defineEmits<{
  (e: 'focus-bottleneck', target: BottleneckTarget): void
}>()

const surface = getOverlaySurfaceDescriptor('real-metrics-panel')

/**
 * Display order. The response may legitimately carry fewer series than the seven declared keys,
 * so the panel intersects this order with what actually arrived instead of indexing blindly.
 */
const SERIES_ORDER: MetricSeriesKey[] = [
  'success_rate',
  'clearing_volume',
  'avg_route_length',
  'total_debt',
  'bottlenecks_score',
  'active_participants',
  'active_trustlines',
]

const orderedSeries = computed<MetricSeries[]>(() => {
  const series = props.metrics?.series ?? []
  const byKey = new Map<MetricSeriesKey, MetricSeries>()
  for (const item of series) byKey.set(item.key, item)
  return SERIES_ORDER.map((key) => byKey.get(key)).filter((s): s is MetricSeries => s !== undefined)
})

const equivalent = computed(() => String(props.metrics?.equivalent ?? '').trim())

/**
 * Why `unavailable` is its own branch and not a flavour of `error`.
 *
 * A 503 here means the backend HAS no measurements and refused to invent plausible ones
 * (spec 007, F-007-1). That is the system being honest, so the panel says "no data" — a neutral,
 * non-alert surface. `error` is a different sentence with a different surface: something broke.
 */
const UNAVAILABLE_DETAIL: Record<string, (noun: string) => string> = {
  storage_disabled: (noun) => `Metric storage is off for this run, so no ${noun} were recorded.`,
  db_read_failed: (noun) => `The metric store could not be read, so there are no ${noun} to show.`,
}

/** What one section says while it is not showing content. `null` means "show the content". */
type SectionState = {
  kind: 'error' | 'unavailable' | 'loading' | 'idle'
  title: string
  detail: string
  /**
   * Correlation handle: the same token the backend logged (AGENTS.md section 12). It is rendered
   * in its own element, apart from the prose, precisely because a token such as `db_read_failed`
   * is a log key and not a sentence — the prose next to it must still read as "no data".
   */
  token: string
}

type SectionCopy = {
  /** Plural noun this section is about, used inside the "no data" sentences. */
  noun: string
  idle: string
  loading: string
  unavailableTitle: string
  errorTitle: string
}

const METRICS_COPY: SectionCopy = {
  noun: 'measurements',
  idle: 'Analytics updates while a run is running.',
  loading: 'Loading measurements…',
  unavailableTitle: 'No measurements recorded',
  errorTitle: 'Analytics request failed',
}

const BOTTLENECKS_COPY: SectionCopy = {
  noun: 'bottlenecks',
  idle: 'Bottlenecks update while a run is running.',
  loading: 'Loading bottlenecks…',
  unavailableTitle: 'No bottlenecks recorded',
  errorTitle: 'Bottlenecks request failed',
}

function sectionState(
  phase: MetricsStreamPhase,
  error: string | undefined,
  reason: string | undefined,
  copy: SectionCopy,
): SectionState | null {
  if (phase === 'ready') return null

  if (phase === 'error') {
    const text = String(error ?? '').trim()
    return {
      kind: 'error',
      title: copy.errorTitle,
      detail: text || 'The analytics request did not complete.',
      token: '',
    }
  }

  if (phase === 'unavailable') {
    const token = String(reason ?? '').trim()
    const detail =
      UNAVAILABLE_DETAIL[token]?.(copy.noun) ??
      `This run has no recorded ${copy.noun} yet. Nothing is being substituted for them.`
    return { kind: 'unavailable', title: copy.unavailableTitle, detail, token }
  }

  if (phase === 'loading') {
    return { kind: 'loading', title: copy.loading, detail: '', token: '' }
  }

  return { kind: 'idle', title: copy.idle, detail: '', token: '' }
}

const metricsState = computed(() =>
  sectionState(props.metricsPhase, props.metricsError, props.metricsUnavailableReason, METRICS_COPY),
)

const bottlenecksState = computed(() =>
  sectionState(
    props.bottlenecksPhase,
    props.bottlenecksError,
    props.bottlenecksUnavailableReason,
    BOTTLENECKS_COPY,
  ),
)

/** An alert is reserved for a failure; "no data" is a status, and idle/loading are neither. */
function stateClass(state: SectionState): string {
  if (state.kind === 'error') return 'ds-alert ds-alert--err'
  if (state.kind === 'unavailable') return 'ds-alert ds-alert--info'
  return 'ds-muted'
}

function stateRole(state: SectionState): string | undefined {
  if (state.kind === 'error') return 'alert'
  if (state.kind === 'unavailable') return 'status'
  return undefined
}
</script>

<template>
  <section
    class="rmp ds-ov-item ds-ov-surface ds-panel"
    :role="surface.a11y?.role"
    :aria-label="surface.a11y?.ariaLabel"
    :data-metrics-phase="metricsPhase"
    :data-bottlenecks-phase="bottlenecksPhase"
  >
    <header class="rmp__header ds-panel__header">
      <span class="rmp__title">Analytics</span>
      <span v-if="equivalent" class="rmp__equivalent ds-mono ds-muted">{{ equivalent }}</span>
    </header>

    <div class="rmp__body ds-panel__body">
      <!-- Measurements: this section answers for `GET /metrics` and for nothing else. -->
      <div class="rmp__section" data-section="metrics">
        <div class="ds-section-label">Measurements</div>

        <div
          v-if="metricsState"
          class="rmp__state"
          :class="stateClass(metricsState)"
          :data-state="metricsState.kind"
          :role="stateRole(metricsState)"
        >
          <span class="rmp__state-title">{{ metricsState.title }}</span>
          <span v-if="metricsState.detail" class="rmp__state-detail">{{
            metricsState.detail
          }}</span>
          <span v-if="metricsState.token" class="rmp__state-reason ds-mono ds-muted">
            {{ metricsState.token }}
          </span>
        </div>

        <div v-else class="rmp__kpis" data-state="ready">
          <MetricsKpiCard
            v-for="series in orderedSeries"
            :key="series.key"
            :series="series"
            :equivalent="equivalent"
          />
          <p v-if="orderedSeries.length === 0" class="rmp__empty ds-muted" data-state="no-series">
            No series in this window
          </p>
        </div>
      </div>

      <!-- Bottlenecks: this section answers for `GET /bottlenecks` and for nothing else. -->
      <div class="rmp__section" data-section="bottlenecks">
        <div class="ds-section-label">Bottlenecks</div>

        <div
          v-if="bottlenecksState"
          class="rmp__state"
          :class="stateClass(bottlenecksState)"
          :data-state="bottlenecksState.kind"
          :role="stateRole(bottlenecksState)"
        >
          <span class="rmp__state-title">{{ bottlenecksState.title }}</span>
          <span v-if="bottlenecksState.detail" class="rmp__state-detail">{{
            bottlenecksState.detail
          }}</span>
          <span v-if="bottlenecksState.token" class="rmp__state-reason ds-mono ds-muted">
            {{ bottlenecksState.token }}
          </span>
        </div>

        <BottlenecksList
          v-else
          data-state="ready"
          :items="bottlenecks?.items ?? []"
          :get-node-name="getNodeName"
          @focus-bottleneck="(target) => emit('focus-bottleneck', target)"
        />
      </div>
    </div>
  </section>
</template>

<style scoped>
/*
  Layout only. Placement of this surface on screen belongs to its owner layer (T705), exactly as
  `ds-ov-bar` leaves positioning to its consumer.
*/
.rmp {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-height: 100%;
  overflow: hidden;
}

.rmp__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--ds-space-2);
}

.rmp__body {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-3);
  overflow: auto;
  min-height: 0;
}

.rmp__kpis {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-3);
}

.rmp__section {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-2);
}

.rmp__state {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-1);
  margin: 0;
}

.rmp__empty {
  margin: 0;
}
</style>
