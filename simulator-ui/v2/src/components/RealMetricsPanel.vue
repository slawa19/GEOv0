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
  /** The single stream state from `useMetricsPolling`. This panel owns no polling of its own. */
  phase: MetricsStreamPhase
  metrics: MetricsResponse | null
  bottlenecks: BottlenecksResponse | null
  /** Non-empty only in `error`. */
  lastError?: string
  /** Non-empty only in `unavailable`: `storage_disabled` / `db_read_failed`. */
  unavailableReason?: string
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
const UNAVAILABLE_DETAIL: Record<string, string> = {
  storage_disabled: 'Metric storage is off for this run, so nothing was recorded to show.',
  db_read_failed: 'The metric store could not be read, so there are no measurements to show.',
}

const unavailableDetail = computed(() => {
  const reason = String(props.unavailableReason ?? '').trim()
  return (
    UNAVAILABLE_DETAIL[reason] ??
    'This run has no recorded measurements yet. Nothing is being substituted for them.'
  )
})

/** Correlation handle: the same token the backend logged (AGENTS.md section 12). */
const unavailableReasonToken = computed(() => String(props.unavailableReason ?? '').trim())

const errorDetail = computed(() => {
  const text = String(props.lastError ?? '').trim()
  return text || 'The analytics request did not complete.'
})
</script>

<template>
  <section
    class="rmp ds-ov-item ds-ov-surface ds-panel"
    :role="surface.a11y?.role"
    :aria-label="surface.a11y?.ariaLabel"
    :data-phase="phase"
  >
    <header class="rmp__header ds-panel__header">
      <span class="rmp__title">Analytics</span>
      <span v-if="equivalent" class="rmp__equivalent ds-mono ds-muted">{{ equivalent }}</span>
    </header>

    <div class="rmp__body ds-panel__body">
      <!-- error: a failure, announced as one. -->
      <div
        v-if="phase === 'error'"
        class="rmp__state ds-alert ds-alert--err"
        data-state="error"
        role="alert"
      >
        <span class="rmp__state-title">Analytics request failed</span>
        <span class="rmp__state-detail">{{ errorDetail }}</span>
      </div>

      <!-- unavailable: no measurements exist. Deliberately NOT an error surface. -->
      <div
        v-else-if="phase === 'unavailable'"
        class="rmp__state ds-alert ds-alert--info"
        data-state="unavailable"
        role="status"
      >
        <span class="rmp__state-title">No measurements recorded</span>
        <span class="rmp__state-detail">{{ unavailableDetail }}</span>
        <span v-if="unavailableReasonToken" class="rmp__state-reason ds-mono ds-muted">
          {{ unavailableReasonToken }}
        </span>
      </div>

      <p v-else-if="phase === 'loading'" class="rmp__state ds-muted" data-state="loading">
        Loading measurements…
      </p>

      <p v-else-if="phase === 'idle'" class="rmp__state ds-muted" data-state="idle">
        Analytics updates while a run is running.
      </p>

      <template v-else>
        <div class="rmp__kpis" data-state="ready">
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

        <div class="rmp__section">
          <div class="ds-section-label">Bottlenecks</div>
          <BottlenecksList
            :items="bottlenecks?.items ?? []"
            :get-node-name="getNodeName"
            @focus-bottleneck="(target) => emit('focus-bottleneck', target)"
          />
        </div>
      </template>
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
