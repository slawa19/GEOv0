<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { toastApiError } from '../api/errorToast'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import { formatIsoInTimeZone } from '../utils/datetime'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import OperatorAdvicePanel from '../ui/OperatorAdvicePanel.vue'
import { useConfigStore } from '../stores/config'
import { debounce } from '../utils/debounce'
import { DEBOUNCE_FILTER_MS } from '../constants/timing'
import { t } from '../i18n'
import { labelTrustlineStatus } from '../i18n/labels'
import type { Trustline } from '../types/domain'
import { buildTrustlinesAdvice } from '../advice/operatorAdvice'
import { carryScenarioQuery, readQueryString, toLocationQueryRaw } from '../router/query'
import { useRouteHydrationGuard } from '../composables/useRouteHydrationGuard'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const error = ref<string | null>(null)

const equivalent = ref('')
const creditor = ref('')
const debtor = ref('')
const status = ref('')
const threshold = ref('0.10')

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<Trustline[]>([])

const drawerOpen = ref(false)
const selected = ref<Trustline | null>(null)

const { isApplying: applyingRouteQuery, isActive: isTrustlinesRoute, run: withRouteHydration } =
  useRouteHydrationGuard(route, '/trustlines')

const configStore = useConfigStore()
const timeZone = computed(() => String(configStore.config['ui.timezone'] || 'UTC'))

function applyRouteQueryToFilters(): boolean {
  const reloadChanged = withRouteHydration(() => {
    const nextEq = readQueryString(route.query.equivalent).trim()
    const nextCreditor = readQueryString(route.query.creditor).trim()
    const nextDebtor = readQueryString(route.query.debtor).trim()
    const nextStatus = readQueryString(route.query.status).trim().toLowerCase()
    const nextThr = readQueryString(route.query.threshold).trim()

    let didChange = false
    if (equivalent.value !== nextEq) {
      equivalent.value = nextEq
      didChange = true
    }
    if (creditor.value !== nextCreditor) {
      creditor.value = nextCreditor
      didChange = true
    }
    if (debtor.value !== nextDebtor) {
      debtor.value = nextDebtor
      didChange = true
    }
    if (status.value !== nextStatus) {
      status.value = nextStatus
      didChange = true
    }
    if (nextThr && threshold.value !== nextThr) threshold.value = nextThr
    return didChange
  })

  return Boolean(reloadChanged)
}

function syncFiltersToRouteQuery() {
  // When leaving the page, route changes first; avoid calling router.replace on the next route.
  if (!isTrustlinesRoute.value) return
  const query: Record<string, unknown> = { ...route.query }

  const eq = String(equivalent.value || '').trim()
  const cr = String(creditor.value || '').trim()
  const db = String(debtor.value || '').trim()
  const st = String(status.value || '').trim()
  const thr = String(threshold.value || '').trim()

  if (eq) query.equivalent = eq
  else delete query.equivalent

  if (cr) query.creditor = cr
  else delete query.creditor

  if (db) query.debtor = db
  else delete query.debtor

  if (st) query.status = st
  else delete query.status

  if (thr && thr !== '0.10') query.threshold = thr
  else delete query.threshold

  const curr = route.query as unknown as Record<string, unknown>
  const same =
    String(curr.equivalent ?? '') === String(query.equivalent ?? '') &&
    String(curr.creditor ?? '') === String(query.creditor ?? '') &&
    String(curr.debtor ?? '') === String(query.debtor ?? '') &&
    String(curr.status ?? '') === String(query.status ?? '') &&
    String(curr.threshold ?? '') === String(query.threshold ?? '')

  if (!same) void router.replace({ query: toLocationQueryRaw(query) })
}

function isBottleneck(row: Trustline): boolean {
  return isRatioBelowThreshold({ numerator: row.available, denominator: row.limit, threshold: threshold.value })
}

function money(v: string): string {
  return formatDecimalFixed(v, 2)
}

function fmtTs(iso: string): string {
  return formatIsoInTimeZone(iso, timeZone.value)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(
      await api.listTrustlines({
        page: page.value,
        per_page: perPage.value,
        equivalent: equivalent.value || undefined,
        creditor: creditor.value || undefined,
        debtor: debtor.value || undefined,
        status: status.value || undefined,
      }),
    )
    total.value = data.total
    const maxPage = Math.max(1, Math.ceil(total.value / perPage.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return
    }
    items.value = data.items
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('trustlines.loadFailed')
    void toastApiError(e, { fallbackTitle: error.value || t('trustlines.loadFailed') })
  } finally {
    loading.value = false
  }
}

function openRow(row: Trustline) {
  selected.value = row
  drawerOpen.value = true
}

function goParticipant(pid: string) {
  void router.push({ path: '/participants', query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), q: pid }) })
}

function goEquivalent(eq: string) {
  void router.push({ path: '/equivalents', query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), q: eq }) })
}

onMounted(() => {
  applyRouteQueryToFilters()
  void load()
})
watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})

watch(
  () => [route.query.equivalent, route.query.creditor, route.query.debtor, route.query.status, route.query.threshold],
  () => {
    const reloadChanged = applyRouteQueryToFilters()
    if (reloadChanged) {
      page.value = 1
      void load()
    }
  },
)

const debouncedReload = debounce(() => {
  page.value = 1
  void load()
}, DEBOUNCE_FILTER_MS)

// NOTE: threshold is a UI-only highlight knob; do not reload the list when it changes.
watch([equivalent, creditor, debtor, status], () => {
  if (applyingRouteQuery.value) return
  syncFiltersToRouteQuery()
  debouncedReload()
})

watch(threshold, () => {
  if (applyingRouteQuery.value) return
  syncFiltersToRouteQuery()
})

const statusOptions = computed(() => [
  { label: t('common.any'), value: '' },
  { label: t('trustlines.status.active'), value: 'active' },
  { label: t('trustlines.status.frozen'), value: 'frozen' },
  { label: t('trustlines.status.closed'), value: 'closed' },
])

const bottlenecksCount = computed(() =>
  items.value.filter((row) => String(row.status || '').trim().toLowerCase() === 'active' && isBottleneck(row)).length,
)

const trustlinesAdviceItems = computed(() =>
  buildTrustlinesAdvice({
    summary: { threshold: threshold.value, total: items.value.length, bottlenecks: bottlenecksCount.value },
    baseQuery: route.query,
    equivalent: equivalent.value || undefined,
    creditor: creditor.value || undefined,
    debtor: debtor.value || undefined,
  }),
)
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('trustlines.title')"
          tooltip-key="nav.trustlines"
        />
        <div class="filters">
          <el-input
            v-model="equivalent"
            size="small"
            :placeholder="t('trustlines.filter.equivalentPlaceholder')"
            clearable
            style="width: 170px"
          />
          <el-input
            v-model="creditor"
            size="small"
            :placeholder="t('trustlines.creditorFrom')"
            clearable
            style="width: 220px"
          />
          <el-input
            v-model="debtor"
            size="small"
            :placeholder="t('trustlines.debtorTo')"
            clearable
            style="width: 220px"
          />
          <el-select
            v-model="status"
            size="small"
            style="width: 140px"
          >
            <el-option
              v-for="o in statusOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
          <el-input
            v-model="threshold"
            size="small"
            :placeholder="t('trustlines.filter.thresholdPlaceholder')"
            style="width: 110px"
          />
        </div>
      </div>
    </template>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      class="mb"
    />
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <el-empty
      v-else-if="items.length === 0"
      :description="t('trustlines.none')"
    />

    <div v-else>
      <OperatorAdvicePanel :items="trustlinesAdviceItems" />
      <el-table
        :data="items"
        size="small"
        table-layout="fixed"
        class="geoTable"
        @row-click="openRow"
      >
        <el-table-column
          prop="equivalent"
          width="100"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.equivalent')"
              tooltip-key="trustlines.eq"
            />
          </template>
          <template #default="scope">
            <span>
              {{ scope.row.equivalent }}
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="from"
          min-width="190"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.from')"
              tooltip-key="trustlines.from"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.from" />
              <CopyIconButton
                :text="scope.row.from"
                :label="t('trustlines.fromPidLabel')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="to"
          min-width="190"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.to')"
              tooltip-key="trustlines.to"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.to" />
              <CopyIconButton
                :text="scope.row.to"
                :label="t('trustlines.toPidLabel')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="limit"
          width="110"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.limit')"
              tooltip-key="trustlines.limit"
            />
          </template>
          <template #default="scope">
            {{ money(scope.row.limit) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="used"
          width="110"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.used')"
              tooltip-key="trustlines.used"
            />
          </template>
          <template #default="scope">
            {{ money(scope.row.used) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="available"
          width="110"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.available')"
              tooltip-key="trustlines.available"
            />
          </template>
          <template #default="scope">
            <span :class="{ bottleneck: isBottleneck(scope.row) }">{{ money(scope.row.available) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="status"
          width="95"
        >
          <template #header>
            <TooltipLabel
              :label="t('common.status')"
              tooltip-key="trustlines.status"
            />
          </template>
        </el-table-column>
        <el-table-column
          prop="created_at"
          width="170"
        >
          <template #header>
            <TooltipLabel
              :label="t('trustlines.createdAt')"
              tooltip-key="trustlines.createdAt"
            />
          </template>
          <template #default="scope">
            {{ fmtTs(scope.row.created_at) }}
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint geoHint">
          {{ t('trustlines.pager.hint', { count: items.length, perPage }) }}
        </div>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="perPage"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          :total="total"
          background
        />
      </div>
    </div>
  </el-card>

  <el-drawer
    v-model="drawerOpen"
    :title="t('trustlines.detailsTitle')"
    size="45%"
  >
    <div v-if="selected">
      <el-descriptions
        class="geoDescriptions"
        :column="1"
        border
      >
        <el-descriptions-item :label="t('trustlines.equivalent')">
          <el-link
            type="primary"
            @click="goEquivalent(selected.equivalent)"
          >
            {{ selected.equivalent }}
          </el-link>
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.fromCreditor')">
          <span class="geoInlineRow">
            <el-link
              type="primary"
              @click="goParticipant(selected.from)"
            >
              {{ selected.from }}
            </el-link>
            <CopyIconButton
              :text="selected.from"
              :label="t('trustlines.fromPidLabel')"
            />
          </span>
          <span
            v-if="selected.from_display_name"
            class="display-name"
          >
            ({{ selected.from_display_name }})
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.toDebtor')">
          <span class="geoInlineRow">
            <el-link
              type="primary"
              @click="goParticipant(selected.to)"
            >
              {{ selected.to }}
            </el-link>
            <CopyIconButton
              :text="selected.to"
              :label="t('trustlines.toPidLabel')"
            />
          </span>
          <span
            v-if="selected.to_display_name"
            class="display-name"
          >
            ({{ selected.to_display_name }})
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.limit')">
          {{ money(selected.limit) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.used')">
          {{ money(selected.used) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.available')">
          <span :class="{ bottleneck: isBottleneck(selected) }">{{ money(selected.available) }}</span>
          <el-tag
            v-if="isBottleneck(selected)"
            type="danger"
            size="small"
            style="margin-left: 8px"
          >
            {{ t('trustlines.bottleneck') }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('common.status')">
          <el-tag
            :type="selected.status === 'active' ? 'success' : selected.status === 'frozen' ? 'warning' : 'info'"
            size="small"
          >
            {{ labelTrustlineStatus(selected.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.createdAt')">
          {{ fmtTs(selected.created_at) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.policy')">
          <pre class="json">{{ JSON.stringify(selected.policy, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>{{ t('trustlines.relatedParticipants') }}</el-divider>

      <div class="drawer-actions">
        <el-button
          type="primary"
          size="small"
          @click="goParticipant(selected.from)"
        >
          {{ t('trustlines.viewCreditorFrom') }}
        </el-button>
        <el-button
          type="primary"
          size="small"
          @click="goParticipant(selected.to)"
        >
          {{ t('trustlines.viewDebtorTo') }}
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}

@media (max-width: 720px) {
  .hdr {
    flex-direction: column;
    align-items: stretch;
  }

  .filters {
    width: 100%;
    justify-content: flex-start;
  }

  .filters :deep(.el-input),
  .filters :deep(.el-select) {
    width: 100% !important;
  }
}
.mb {
  margin-bottom: 12px;
}
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
.pager__hint {
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-sub);
}
.bottleneck {
  color: var(--el-color-danger);
  font-weight: 700;
}
.json {
  margin: 0;
  font-size: var(--geo-font-size-sub);
}
.display-name {
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}
.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
