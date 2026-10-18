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
import { t, type MessageKey } from '../i18n/t'
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
 *
 * 046 wave 2: one whole sentence per section and reason (`metrics.*StorageOff` / `*ReadFailed` / `*None`) - no noun is
 * glued into a shared sentence; the reason token is a closed set, an unknown one gets the "none yet" sentence.
 */

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
  idle: MessageKey
  loading: MessageKey
  unavailableTitle: MessageKey
  errorTitle: MessageKey
  /** The "no data" sentence by the unavailable reason token, and for any other token. */
  unavailable: Readonly<Record<string, MessageKey>>
  none: MessageKey
}

const METRICS_COPY: SectionCopy = {
  idle: 'metrics.measurementsIdle',
  loading: 'metrics.measurementsLoading',
  unavailableTitle: 'metrics.measurementsUnavailableTitle',
  errorTitle: 'metrics.measurementsErrorTitle',
  unavailable: { storage_disabled: 'metrics.measurementsStorageOff', db_read_failed: 'metrics.measurementsReadFailed' },
  none: 'metrics.measurementsNone',
}

const BOTTLENECKS_COPY: SectionCopy = {
  idle: 'metrics.bottlenecksIdle',
  loading: 'metrics.bottlenecksLoading',
  unavailableTitle: 'metrics.bottlenecksUnavailableTitle',
  errorTitle: 'metrics.bottlenecksErrorTitle',
  unavailable: { storage_disabled: 'metrics.bottlenecksStorageOff', db_read_failed: 'metrics.bottlenecksReadFailed' },
  none: 'metrics.bottlenecksNone',
}

function sectionState(
  phase: MetricsStreamPhase,
  error: string | undefined,
  reason: string | undefined,
  copy: SectionCopy,
): SectionState | null {
  if (phase === 'ready') return null

  if (phase === 'error') {
    // 046 wave 2 (Codex §15 round 1): the prose is the panel's; the failure's reference (its status or code and request
    // id, `useMetricsPolling` -> `errorReference`) is shown apart, labelled, in the token's element.
    const text = String(error ?? '').trim()
    return {
      kind: 'error',
      title: t(copy.errorTitle),
      detail: t('metrics.errorDetail'),
      token: text ? t('metrics.errorReference', { ref: text }) : '',
    }
  }

  if (phase === 'unavailable') {
    const token = String(reason ?? '').trim()
    const known = Object.hasOwn(copy.unavailable, token) ? copy.unavailable[token] : undefined
    return { kind: 'unavailable', title: t(copy.unavailableTitle), detail: t(known ?? copy.none), token }
  }

  if (phase === 'loading') {
    return { kind: 'loading', title: t(copy.loading), detail: '', token: '' }
  }

  return { kind: 'idle', title: t(copy.idle), detail: '', token: '' }
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
    :aria-label="surface.a11y?.ariaLabel ? t('metrics.panelAria') : undefined"
    :data-metrics-phase="metricsPhase"
    :data-bottlenecks-phase="bottlenecksPhase"
  >
    <header class="rmp__header ds-panel__header">
      <span class="rmp__title">{{ t('metrics.title') }}</span>
      <span v-if="equivalent" class="rmp__equivalent ds-mono ds-muted">{{ equivalent }}</span>
    </header>

    <div class="rmp__body ds-panel__body">
      <!-- Measurements: this section answers for `GET /metrics` and for nothing else. -->
      <div class="rmp__section" data-section="metrics">
        <div class="ds-section-label">{{ t('metrics.measurements') }}</div>

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
            {{ t('metrics.noSeries') }}
          </p>
        </div>
      </div>

      <!-- Bottlenecks: this section answers for `GET /bottlenecks` and for nothing else. -->
      <div class="rmp__section" data-section="bottlenecks">
        <div class="ds-section-label">{{ t('bottlenecks.title') }}</div>

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
