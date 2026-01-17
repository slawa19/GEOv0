<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'

import { api } from '../api'
import { assertSuccess } from '../api/envelope'

import type { Debt, GraphSnapshot, Participant, Trustline } from '../types/domain'

import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import OperatorAdvicePanel from '../ui/OperatorAdvicePanel.vue'

import { t } from '../i18n'
import { formatDecimalFixed, isRatioBelowThreshold, addDecimalStrings, compareDecimalStrings, absDecimalString } from '../utils/decimal'
import { formatIsoInTimeZone } from '../utils/datetime'
import { buildLiquidityAdvice } from '../advice/operatorAdvice'
import { carryScenarioQuery, readQueryString, toLocationQueryRaw } from '../router/query'
import { useConfigStore } from '../stores/config'
import { useRouteHydrationGuard } from '../composables/useRouteHydrationGuard'

const router = useRouter()
const route = useRoute()

const { isApplying: applyingRouteQuery, isActive: isLiquidityRoute, run: withRouteHydration } =
  useRouteHydrationGuard(route, '/liquidity')

const loading = ref(false)
const error = ref<string | null>(null)

const snapshot = ref<GraphSnapshot | null>(null)
const lastLoadedAt = ref<Date | null>(null)

const configStore = useConfigStore()
const timeZone = computed(() => String(configStore.config['ui.timezone'] || 'UTC'))

const eq = ref<string>('ALL')
const threshold = ref<string>('0.10')

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
  () => syncFromRoute(),
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
  loading.value = true
  error.value = null
  try {
    snapshot.value = assertSuccess(await api.graphSnapshot()) as GraphSnapshot
    lastLoadedAt.value = new Date()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('liquidity.loadFailed')
    ElMessage.error(error.value || t('liquidity.loadFailed'))
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

const equivalents = computed(() => (snapshot.value?.equivalents || []).filter((e) => e.is_active))

const precisionByEq = computed(() => {
  const m = new Map<string, number>()
  for (const e of snapshot.value?.equivalents || []) {
    m.set(String(e.code || '').toUpperCase(), Number(e.precision ?? 2) || 2)
  }
  return m
})

const selectedEq = computed(() => {
  const k = String(eq.value || '').trim().toUpperCase()
  return k === 'ALL' ? null : k
})

function isActiveTrustline(tl: Trustline): boolean {
  return String(tl.status || '').trim().toLowerCase() === 'active'
}

function isBottleneck(tl: Trustline): boolean {
  return isRatioBelowThreshold({ numerator: tl.available, denominator: tl.limit, threshold: threshold.value })
}

const activeTrustlines = computed(() => {
  const all = snapshot.value?.trustlines || []
  const eqKey = selectedEq.value
  return all.filter((t) => isActiveTrustline(t) && (!eqKey || String(t.equivalent || '').toUpperCase() === eqKey))
})

const bottleneckTrustlines = computed(() => activeTrustlines.value.filter(isBottleneck))

const incidentsOverSla = computed(() => {
  const all = snapshot.value?.incidents || []
  const eqKey = selectedEq.value
  return all.filter((i) => (!eqKey || String(i.equivalent || '').toUpperCase() === eqKey) && i.age_seconds > i.sla_seconds)
})

function sumTrustlinesDecimal(items: Trustline[], field: keyof Pick<Trustline, 'limit' | 'used' | 'available'>): string {
  let acc = '0'
  for (const t of items) acc = addDecimalStrings(acc, String(t[field] || '0'))
  return acc
}

const selectedPrecision = computed(() => {
  const k = selectedEq.value
  if (!k) return 2
  return precisionByEq.value.get(k) ?? 2
})

const totalLimit = computed(() => sumTrustlinesDecimal(activeTrustlines.value, 'limit'))
const totalUsed = computed(() => sumTrustlinesDecimal(activeTrustlines.value, 'used'))
const totalAvailable = computed(() => sumTrustlinesDecimal(activeTrustlines.value, 'available'))

type NetRow = { pid: string; display_name: string; net: string }

function computeNetPositions(debts: Debt[], participants: Participant[]): NetRow[] {
  const byPid = new Map<string, Participant>()
  for (const p of participants) byPid.set(p.pid, p)

  const m = new Map<string, string>()

  const eqKey = selectedEq.value
  for (const d of debts) {
    if (eqKey && String(d.equivalent || '').toUpperCase() !== eqKey) continue

    const debtor = String(d.debtor || '').trim()
    const creditor = String(d.creditor || '').trim()
    const amt = String(d.amount || '0')

    if (debtor) m.set(debtor, addDecimalStrings(m.get(debtor) || '0', '-' + amt))
    if (creditor) m.set(creditor, addDecimalStrings(m.get(creditor) || '0', amt))
  }

  const rows: NetRow[] = []
  for (const [pid, net] of m.entries()) {
    const dn = byPid.get(pid)?.display_name || ''
    rows.push({ pid, display_name: dn, net })
  }
  return rows
}

const netRows = computed(() => {
  return computeNetPositions(snapshot.value?.debts || [], snapshot.value?.participants || [])
})

const topCreditors = computed(() => {
  const rows = netRows.value.filter((r) => compareDecimalStrings(r.net, '0') > 0)
  rows.sort((a, b) => compareDecimalStrings(b.net, a.net))
  return rows.slice(0, 10)
})

const topDebtors = computed(() => {
  const rows = netRows.value.filter((r) => compareDecimalStrings(r.net, '0') < 0)
  rows.sort((a, b) => compareDecimalStrings(a.net, b.net))
  return rows.slice(0, 10)
})

const topByAbsNet = computed(() => {
  const rows = [...netRows.value]
  rows.sort((a, b) => compareDecimalStrings(absDecimalString(b.net), absDecimalString(a.net)))
  return rows.slice(0, 10)
})

const topBottleneckEdges = computed(() => {
  const rows = [...bottleneckTrustlines.value]
  rows.sort((a, b) => {
    // smaller available first; fallback: older first
    const c = compareDecimalStrings(a.available, b.available)
    if (c !== 0) return c
    return String(a.created_at || '').localeCompare(String(b.created_at || ''))
  })
  return rows.slice(0, 10)
})

const adviceItems = computed(() => {
  if (!snapshot.value) return []
  return buildLiquidityAdvice({
    ctx: {
      eq: selectedEq.value,
      threshold: threshold.value,
      trustlinesTotal: activeTrustlines.value.length,
      bottlenecks: bottleneckTrustlines.value.length,
      incidentsOverSla: incidentsOverSla.value.length,
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

const lastUpdatedLabel = computed(() => {
  if (!lastLoadedAt.value) return ''
  return formatIsoInTimeZone(lastLoadedAt.value.toISOString(), timeZone.value)
})

function money(v: string): string {
  return formatDecimalFixed(v, selectedPrecision.value)
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
                :placeholder="t('liquidity.controls.thresholdPlaceholder')"
              />
            </div>

            <el-button
              size="small"
              :loading="loading"
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
      class="mb"
    />

    <el-divider />

    <el-row :gutter="12">
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.activeTrustlines')"
          :value="activeTrustlines.length"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.bottlenecks')"
          :value="bottleneckTrustlines.length"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.incidentsOverSla')"
          :value="incidentsOverSla.length"
        />
      </el-col>
    </el-row>

    <el-divider />

    <el-row :gutter="12">
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.totalLimit')"
          :value="money(totalLimit)"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.totalUsed')"
          :value="money(totalUsed)"
        />
      </el-col>
      <el-col :span="8">
        <el-statistic
          :title="t('liquidity.kpi.totalAvailable')"
          :value="money(totalAvailable)"
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
                <el-text type="danger">{{ money(scope.row.available) }}</el-text>
              </template>
            </el-table-column>

            <el-table-column
              prop="limit"
              :label="t('trustlines.limit')"
              width="120"
            >
              <template #default="scope">{{ money(scope.row.limit) }}</template>
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
                  @click="router.push({ path: '/graph', query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), ...(selectedEq ? { equivalent: selectedEq } : {}) }) })"
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
