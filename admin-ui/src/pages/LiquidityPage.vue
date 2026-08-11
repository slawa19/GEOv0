<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'

import type { Equivalent, LiquiditySummary, Trustline } from '../types/domain'

import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import OperatorAdvicePanel from '../ui/OperatorAdvicePanel.vue'

import { t } from '../i18n'
import { compareDecimalStrings, formatDecimalFixed, isUnitIntervalDecimalString } from '../utils/decimal'
import { formatIsoInTimeZone } from '../utils/datetime'
import { buildLiquidityAdvice } from '../advice/operatorAdvice'
import { carryScenarioQuery, readQueryString, toLocationQueryRaw } from '../router/query'
import { useConfigStore } from '../stores/config'
import { useRouteHydrationGuard } from '../composables/useRouteHydrationGuard'
import { debounce } from '../utils/debounce'
import { DEBOUNCE_SEARCH_MS } from '../constants/timing'
import { useLatestRequest } from '../composables/useLatestRequest'

const router = useRouter()
const route = useRoute()

const { isApplying: applyingRouteQuery, isActive: isLiquidityRoute, run: withRouteHydration } =
  useRouteHydrationGuard(route, '/liquidity')

const loading = ref(false)
const error = ref<string | null>(null)

const equivalentsList = ref<Equivalent[]>([])
const summary = ref<LiquiditySummary | null>(null)
const lastLoadedAt = ref<Date | null>(null)
const loadRequests = useLatestRequest()

const configStore = useConfigStore()
const timeZone = computed(() => String(configStore.config['ui.timezone'] || 'UTC'))

const eq = ref<string>('ALL')
const threshold = ref<string>('0.10')
const thresholdValid = computed(() => isUnitIntervalDecimalString(threshold.value))

let routeSyncInitialized = false
const debouncedLoad = debounce(() => void load(), DEBOUNCE_SEARCH_MS)
onBeforeUnmount(() => debouncedLoad.cancel())

function syncFromRoute() {
  // Avoid mutating state / query when this component is in the process of being navigated away from.
  if (!isLiquidityRoute.value) return
  withRouteHydration(() => {
    const nextEq = readQueryString(route.query.equivalent).trim().toUpperCase() || 'ALL'
    const nextThr = readQueryString(route.query.threshold).trim()
    if (nextEq) eq.value = nextEq
    if (nextThr) threshold.value = nextThr
  })
}

function updateRouteQuery(patch: Record<string, unknown>) {
  // When leaving the page, route changes first; avoid calling router.replace on the next route.
  if (!isLiquidityRoute.value) return
  const query: Record<string, unknown> = { ...route.query }
  for (const [k, v] of Object.entries(patch)) {
    const s = typeof v === 'string' ? v.trim() : v
    if (s === '' || s === null || s === undefined) delete query[k]
    else query[k] = v
  }
  void router.replace({ query: toLocationQueryRaw(query) })
}

watch(
  () => [route.query.equivalent, route.query.threshold],
  () => {
    syncFromRoute()
    if (!isLiquidityRoute.value) return
    if (!routeSyncInitialized) {
      routeSyncInitialized = true
      void load()
      return
    }
    debouncedLoad()
  },
  { immediate: true },
)

watch(eq, (v) => {
  if (applyingRouteQuery.value) return
  updateRouteQuery({ equivalent: v === 'ALL' ? '' : v })
})
watch(threshold, (v) => {
  if (applyingRouteQuery.value) return
  updateRouteQuery({ threshold: String(v || '').trim() })
})

async function load() {
  const request = loadRequests.begin()
  const requestEq = eq.value
  const requestThreshold = threshold.value.trim()
  if (!thresholdValid.value) {
    summary.value = null
    loading.value = false
    error.value = t('validation.thresholdUnitInterval')
    return
  }
  loading.value = true
  error.value = null
  try {
    const eqRes = assertSuccess(await api.listEquivalents({ include_inactive: false }))
    if (!request.isCurrent()) return
    equivalentsList.value = (eqRes.items || []) as Equivalent[]

    const nextSummary = assertSuccess(
      await api.liquiditySummary({ equivalent: requestEq, threshold: requestThreshold, limit: 10 }),
    ) as LiquiditySummary
    if (!request.isCurrent()) return
    summary.value = nextSummary

    const ts = String(summary.value.updated_at || '').trim()
    lastLoadedAt.value = ts ? new Date(ts) : new Date()
  } catch (e: unknown) {
    if (!request.isCurrent()) return
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('liquidity.loadFailed')
  } finally {
    if (request.isCurrent()) loading.value = false
  }
}

const equivalents = computed(() => (equivalentsList.value || []).filter((e) => e.is_active))

const precisionByEq = computed(() => {
  const m = new Map<string, number>()
  for (const e of equivalentsList.value || []) {
    const code = String(e.code || '').trim().toUpperCase()
    const precision = Number(e.precision)
    if (code && Number.isInteger(precision) && precision >= 0) m.set(code, precision)
  }
  return m
})

const selectedEq = computed(() => {
  const k = String(eq.value || '').trim().toUpperCase()
  return k === 'ALL' ? null : k
})

const activeTrustlinesCount = computed(() => summary.value?.active_trustlines)
const bottlenecksCount = computed(() => summary.value?.bottlenecks)
const incidentsOverSlaCount = computed(() => summary.value?.incidents_over_sla)

const selectedPrecision = computed(() => {
  const k = selectedEq.value
  if (!k) return null
  return precisionByEq.value.get(k) ?? null
})

const totalLimit = computed(() => (summary.value ? String(summary.value.total_limit) : null))
const totalUsed = computed(() => (summary.value ? String(summary.value.total_used) : null))
const totalAvailable = computed(() => (summary.value ? String(summary.value.total_available) : null))

const topCreditors = computed(() => summary.value?.top_creditors ?? [])
const topDebtors = computed(() => summary.value?.top_debtors ?? [])
const topByAbsNet = computed(() => summary.value?.top_by_abs_net ?? [])
const topBottleneckEdges = computed(() => (summary.value?.top_bottleneck_edges ?? []) as Trustline[])

const adviceItems = computed(() => {
  if (!summary.value) return []
  const current = summary.value
  return buildLiquidityAdvice({
    ctx: {
      eq: selectedEq.value,
      threshold: threshold.value,
      trustlinesTotal: current.active_trustlines,
      bottlenecks: current.bottlenecks,
      incidentsOverSla: current.incidents_over_sla,
    },
    baseQuery: route.query,
  })
})

function goTrustlinesEdge(row: Trustline) {
  void router.push({
    path: '/trustlines',
    query: toLocationQueryRaw({
      ...carryScenarioQuery(route.query),
      equivalent: String(row.equivalent || '').toUpperCase(),
      creditor: row.from,
      debtor: row.to,
      threshold: String(threshold.value || '').trim(),
    }),
  })
}

function goParticipant(pid: string) {
  void router.push({ path: '/participants', query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), q: pid }) })
}

function goGraph() {
  void router.push({
    path: '/graph',
    query: toLocationQueryRaw({
      ...carryScenarioQuery(route.query),
      ...(selectedEq.value ? { equivalent: selectedEq.value } : {}),
      threshold: threshold.value,
    }),
  })
}

const lastUpdatedLabel = computed(() => {
  if (!lastLoadedAt.value) return ''
  return formatIsoInTimeZone(lastLoadedAt.value.toISOString(), timeZone.value)
})

function money(v: string, equivalent: unknown = selectedEq.value): string {
  const code = String(equivalent || '').trim().toUpperCase()
  const precision = code ? precisionByEq.value.get(code) : undefined
  return precision === undefined ? '—' : formatDecimalFixed(v, precision)
}
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('liquidity.title')"
          tooltip-key="nav.liquidity"
        />

        <div class="hdr__right">
          <div class="toolbar">
            <el-select
              v-model="eq"
              size="small"
              filterable
              style="width: 140px"
            >
              <el-option
                value="ALL"
                :label="t('common.all')"
              />
              <el-option
                v-for="e in equivalents"
                :key="e.code"
                :value="String(e.code || '').toUpperCase()"
                :label="String(e.code || '').toUpperCase()"
              />
            </el-select>

            <div class="toolbar__threshold">
              <TooltipLabel
                :label="t('liquidity.controls.threshold')"
                :tooltip-text="t('liquidity.controls.thresholdHelp')"
                :max-lines="4"
              />
              <el-input
                v-model="threshold"
                size="small"
                style="width: 110px"
                :aria-invalid="!thresholdValid"
                :placeholder="t('liquidity.controls.thresholdPlaceholder')"
              />
            </div>

            <el-button
              size="small"
              :loading="loading"
              :disabled="!thresholdValid"
              @click="load"
            >
              {{ t('common.refresh') }}
            </el-button>
          </div>
        </div>
      </div>

      <div class="geoHint" style="margin-top: 6px">
        <span>{{ t('liquidity.controls.snapshotNote') }}</span>
        <span v-if="lastUpdatedLabel"> · {{ t('common.updated') }}: {{ lastUpdatedLabel }}</span>
      </div>
    </template>

    <OperatorAdvicePanel
      v-if="adviceItems.length"
      :items="adviceItems"
      :show-title="false"
      class="mb"
    />

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="mb"
    >
      <template #default>
        <el-button
          size="small"
          type="primary"
          @click="load"
        >
          {{ t('common.refresh') }}
        </el-button>
      </template>
    </el-alert>

    <el-divider />

    <el-alert
      v-if="summary && selectedPrecision === null"
      type="warning"
      show-icon
      :closable="false"
      class="mb"
      :title="t('liquidity.precisionUnavailable')"
    />

    <el-row
      v-if="summary && selectedPrecision !== null"
      :gutter="12"
    >
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.activeTrustlines')"
          :value="activeTrustlinesCount"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.bottlenecks')"
          :value="bottlenecksCount"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.incidentsOverSla')"
          :value="incidentsOverSlaCount"
        />
      </el-col>
    </el-row>

    <el-divider />

    <el-row
      v-if="summary"
      :gutter="12"
    >
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.totalLimit')"
          :value="Number(totalLimit)"
          :precision="selectedPrecision"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.totalUsed')"
          :value="Number(totalUsed)"
          :precision="selectedPrecision"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.totalAvailable')"
          :value="Number(totalAvailable)"
          :precision="selectedPrecision"
        />
      </el-col>
    </el-row>

    <el-divider />

    <el-row :gutter="12">
      <el-col :span="12">
        <el-card class="geoCard geoCard--inner">
          <template #header>
            <div class="hdr">
              <div class="hdr__title">
                <TooltipLabel
                  :label="t('liquidity.watchlist.topBottleneckEdges')"
                  :tooltip-text="t('liquidity.help.topBottleneckEdges')"
                />
              </div>
              <div class="hdr__right">
                <el-button
                  size="small"
                  @click="router.push({ path: '/trustlines', query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), ...(selectedEq ? { equivalent: selectedEq } : {}), threshold }) })"
                >
                  {{ t('liquidity.actions.openTrustlines') }}
                </el-button>
              </div>
            </div>
          </template>

          <el-table
            :data="topBottleneckEdges"
            size="small"
            class="geoTable"
          >
            <el-table-column
              prop="from"
              :label="t('trustlines.from')"
              width="140"
            >
              <template #default="scope">
                <el-button
                  link
                  type="primary"
                  @click="goParticipant(scope.row.from)"
                >
                  <TableCellEllipsis :text="scope.row.from" />
                </el-button>
              </template>
            </el-table-column>

            <el-table-column
              prop="to"
              :label="t('trustlines.to')"
              width="140"
            >
              <template #default="scope">
                <el-button
                  link
                  type="primary"
                  @click="goParticipant(scope.row.to)"
                >
                  <TableCellEllipsis :text="scope.row.to" />
                </el-button>
              </template>
            </el-table-column>

            <el-table-column
              prop="available"
              :label="t('trustlines.available')"
              width="120"
            >
              <template #default="scope">
                <el-text type="danger">{{ money(scope.row.available, scope.row.equivalent) }}</el-text>
              </template>
            </el-table-column>

            <el-table-column
              prop="limit"
              :label="t('trustlines.limit')"
              width="120"
            >
              <template #default="scope">{{ money(scope.row.limit, scope.row.equivalent) }}</template>
            </el-table-column>

            <el-table-column
              :label="t('liquidity.actions.openEdge')"
              width="110"
            >
              <template #default="scope">
                <el-button
                  size="small"
                  @click="goTrustlinesEdge(scope.row)"
                >
                  {{ t('common.open') }}
                </el-button>
              </template>
            </el-table-column>
          </el-table>

          <el-empty
            v-if="!topBottleneckEdges.length && !loading"
            :description="t('liquidity.empty.noBottlenecks')"
          />
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card class="geoCard geoCard--inner">
          <template #header>
            <div class="hdr">
              <div class="hdr__title">
                <TooltipLabel
                  :label="t('liquidity.watchlist.topNetPositions')"
                  :tooltip-text="t('liquidity.help.topNetPositions')"
                />
              </div>
              <div class="hdr__right">
                <el-button
                  size="small"
                  @click="goGraph"
                >
                  {{ t('liquidity.actions.openGraph') }}
                </el-button>
              </div>
            </div>
          </template>

          <el-table
            :data="topByAbsNet"
            size="small"
            class="geoTable"
          >
            <el-table-column
              prop="pid"
              :label="t('participant.columns.pid')"
              width="140"
            >
              <template #default="scope">
                <el-button
                  link
                  type="primary"
                  @click="goParticipant(scope.row.pid)"
                >
                  <TableCellEllipsis :text="scope.row.pid" />
                </el-button>
              </template>
            </el-table-column>

            <el-table-column
              prop="display_name"
              :label="t('participant.drawer.displayName')"
            >
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.display_name || '—'" />
              </template>
            </el-table-column>

            <el-table-column
              prop="net"
              :label="t('liquidity.net.net')"
              width="140"
            >
              <template #default="scope">
                <el-text :type="compareDecimalStrings(scope.row.net, '0') >= 0 ? 'success' : 'danger'">
                  {{ money(scope.row.net) }}
                </el-text>
              </template>
            </el-table-column>
          </el-table>

          <el-empty
            v-if="!topByAbsNet.length && !loading"
            :description="t('liquidity.empty.noDebts')"
          />

          <el-divider />

          <el-row :gutter="12">
            <el-col :span="12">
              <el-text type="success">{{ t('liquidity.net.topCreditors') }}</el-text>
              <el-table
                :data="topCreditors"
                size="small"
                class="geoTable"
              >
                <el-table-column
                  prop="pid"
                  :label="t('participant.columns.pid')"
                  width="140"
                >
                  <template #default="scope">
                    <el-button
                      link
                      type="primary"
                      @click="goParticipant(scope.row.pid)"
                    >
                      <TableCellEllipsis :text="scope.row.pid" />
                    </el-button>
                  </template>
                </el-table-column>
                <el-table-column
                  prop="net"
                  :label="t('liquidity.net.net')"
                  width="140"
                >
                  <template #default="scope">{{ money(scope.row.net) }}</template>
                </el-table-column>
              </el-table>
            </el-col>

            <el-col :span="12">
              <el-text type="danger">{{ t('liquidity.net.topDebtors') }}</el-text>
              <el-table
                :data="topDebtors"
                size="small"
                class="geoTable"
              >
                <el-table-column
                  prop="pid"
                  :label="t('participant.columns.pid')"
                  width="140"
                >
                  <template #default="scope">
                    <el-button
                      link
                      type="primary"
                      @click="goParticipant(scope.row.pid)"
                    >
                      <TableCellEllipsis :text="scope.row.pid" />
                    </el-button>
                  </template>
                </el-table-column>
                <el-table-column
                  prop="net"
                  :label="t('liquidity.net.net')"
                  width="140"
                >
                  <template #default="scope">{{ money(scope.row.net) }}</template>
                </el-table-column>
              </el-table>
            </el-col>
          </el-row>
        </el-card>
      </el-col>
    </el-row>
  </el-card>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}

.hdr {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.hdr__title {
  flex: 1;
  min-width: 0;
}

.hdr__right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: flex-end;
}

.toolbar__threshold {
  display: flex;
  align-items: center;
  gap: 6px;
}

@media (max-width: 720px) {
  .hdr {
    flex-direction: column;
    align-items: stretch;
  }

  .hdr__right {
    width: 100%;
    justify-content: flex-start;
  }

  .toolbar {
    width: 100%;
    flex-wrap: wrap;
  }

  .toolbar :deep(.el-input),
  .toolbar :deep(.el-select) {
    width: 100% !important;
  }
}
</style>
