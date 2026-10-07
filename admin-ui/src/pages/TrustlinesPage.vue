<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { api } from '../api'
import { isUnitIntervalDecimalString } from '../utils/decimal'
import { useEquivalentPrecision } from '../composables/useEquivalentPrecision'
import { formatTs } from '../utils/datetime'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import ListState from '../ui/ListState.vue'
import { debounce } from '../utils/debounce'
import { DEBOUNCE_FILTER_MS } from '../constants/timing'
import { t } from '../i18n'
import { labelTrustlineStatus } from '../i18n/labels'
import type { Trustline } from '../types/domain'
import { toLocationQueryRaw } from '../router/query'
import { usePagedList } from '../composables/usePagedList'
import { useRouteQueryFilters } from '../composables/useRouteQueryFilters'
import { isTrustlineBottleneck } from '../utils/bottleneck'

const router = useRouter()
const route = useRoute()

const DEFAULT_THRESHOLD = '0.10'

const equivalent = ref('')
const creditor = ref('')
const debtor = ref('')
const status = ref('')
const threshold = ref(DEFAULT_THRESHOLD)

const { page, perPage, total, items, loading, error, reload, reloadFromFirstPage } = usePagedList<Trustline>(
  ({ page: requestPage, perPage: requestPerPage }) =>
    api.listTrustlines({
      page: requestPage,
      per_page: requestPerPage,
      equivalent: equivalent.value || undefined,
      creditor: creditor.value || undefined,
      debtor: debtor.value || undefined,
      status: status.value || undefined,
    }),
  { errorKey: 'trustlines.loadFailed' },
)

const drawerOpen = ref(false)
const selected = ref<Trustline | null>(null)

const debouncedReload = debounce(reloadFromFirstPage, DEBOUNCE_FILTER_MS)

// NOTE: the threshold is a UI-only highlight knob: it is linked in the URL but never reloads the list.
const { applyRoute } = useRouteQueryFilters({
  route,
  router,
  path: '/trustlines',
  filters: {
    equivalent: { model: equivalent },
    creditor: { model: creditor },
    debtor: { model: debtor },
    status: { model: status, fromQuery: (raw) => raw.trim().toLowerCase() },
    threshold: {
      model: threshold,
      fromQuery: (raw) => raw.trim() || DEFAULT_THRESHOLD,
      toQuery: (value) => (value.trim() === DEFAULT_THRESHOLD ? '' : value.trim()),
      reloads: false,
    },
  },
  onRouteChange: reloadFromFirstPage,
  onUserChange: debouncedReload,
})

const thresholdValid = computed(() => isUnitIntervalDecimalString(threshold.value))

function isBottleneck(row: Trustline): boolean {
  return isTrustlineBottleneck(row, threshold.value)
}

const { money, catalogueSettled, hasUnknownPrecision, loadEquivalentPrecision } = useEquivalentPrecision()

async function loadEquivalents() {
  try {
    await loadEquivalentPrecision()
  } catch {
    // The catalogue is the only source of precision; without it money cells stay '—'
    // (see precisionMissing) instead of asserting a digit count nobody declared.
  }
}

function openRow(row: Trustline) {
  selected.value = row
  drawerOpen.value = true
}

function goParticipant(pid: string) {
  void router.push({ path: '/participants', query: toLocationQueryRaw({ q: pid }) })
}

function goEquivalent(eq: string) {
  void router.push({ path: '/equivalents', query: toLocationQueryRaw({ q: eq }) })
}

onMounted(() => {
  applyRoute()
  void loadEquivalents()
  void reload()
})

onBeforeUnmount(() => debouncedReload.cancel())

const statusOptions = computed(() => [
  { label: t('common.any'), value: '' },
  { label: t('trustlines.status.active'), value: 'active' },
  { label: t('trustlines.status.closed'), value: 'closed' },
])

// Пока каталог не ответил, «точность неизвестна» — ещё не вывод, а состояние загрузки.
const precisionMissing = computed(
  () => catalogueSettled.value && hasUnknownPrecision(items.value.map((row) => row.equivalent)),
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
            :aria-invalid="!thresholdValid"
            :class="{ 'threshold--invalid': !thresholdValid }"
            style="width: 110px"
          />
        </div>
      </div>
    </template>

    <ListState
      :error="error"
      :loading="loading"
      :empty="items.length === 0"
      :empty-text="t('trustlines.none')"
      @retry="reload"
    >
      <el-alert
        v-if="precisionMissing"
        data-testid="trustlines-precision-unavailable"
        :title="t('money.precisionUnavailable')"
        type="warning"
        show-icon
        :closable="false"
        class="mb"
      />
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
            {{ money(scope.row.limit, scope.row.equivalent) }}
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
            {{ money(scope.row.used, scope.row.equivalent) }}
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
            <span :class="{ bottleneck: isBottleneck(scope.row) }">{{ money(scope.row.available, scope.row.equivalent) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="status"
          width="150"
        >
          <template #header>
            <TooltipLabel
              :label="t('common.status')"
              tooltip-key="trustlines.status"
            />
          </template>
          <template #default="scope">
            {{ labelTrustlineStatus(scope.row.status) }}
            <el-tag
              v-if="scope.row.close_requested_at"
              type="warning"
              size="small"
              data-testid="tl-close-requested"
              :title="formatTs(scope.row.close_requested_at)"
            >
              {{ t('trustlines.closeRequested') }}
            </el-tag>
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
            {{ formatTs(scope.row.created_at) }}
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
    </ListState>
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
          {{ money(selected.limit, selected.equivalent) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.used')">
          {{ money(selected.used, selected.equivalent) }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.available')">
          <span :class="{ bottleneck: isBottleneck(selected) }">{{ money(selected.available, selected.equivalent) }}</span>
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
            :type="selected.status === 'active' ? 'success' : 'info'"
            size="small"
          >
            {{ labelTrustlineStatus(selected.status) }}
          </el-tag>
          <el-tag
            v-if="selected.close_requested_at"
            type="warning"
            size="small"
            style="margin-left: 8px"
          >
            {{ t('trustlines.closeRequested') }} · {{ formatTs(selected.close_requested_at) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('trustlines.createdAt')">
          {{ formatTs(selected.created_at) }}
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
.threshold--invalid :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
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
