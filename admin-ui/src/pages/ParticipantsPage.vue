<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { toastApiError } from '../api/errorToast'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import { useAuthStore } from '../stores/auth'
import { debounce } from '../utils/debounce'
import { DEBOUNCE_SEARCH_MS } from '../constants/timing'
import { t } from '../i18n'
import { labelParticipantType } from '../i18n/labels'
import type { Participant } from '../types/domain'
import { carryScenarioQuery, readQueryString, toLocationQueryRaw } from '../router/query'
import { useRouteHydrationGuard } from '../composables/useRouteHydrationGuard'
import {
  isLockedParticipantStatus,
  labelParticipantStatus,
  participantStatusOptions,
  participantStatusTagType,
} from '../ui/participantStatus'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const error = ref<string | null>(null)

const authStore = useAuthStore()

const q = ref('')
const status = ref<string>('')
const type = ref<string>('')

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<Participant[]>([])

const drawerOpen = ref(false)
const selected = ref<Participant | null>(null)

const { isApplying: applyingRouteQuery, isActive: isParticipantsRoute, run: withRouteHydration } =
  useRouteHydrationGuard(route, '/participants')

function applyRouteQueryToFilters(): boolean {
  const changed = withRouteHydration(() => {
    const nextQ = readQueryString(route.query.q).trim()
    const nextStatus = readQueryString(route.query.status).trim().toLowerCase()
    const nextType = readQueryString(route.query.type).trim().toLowerCase()

    let didChange = false
    if (q.value !== nextQ) {
      q.value = nextQ
      didChange = true
    }
    if (status.value !== nextStatus) {
      status.value = nextStatus
      didChange = true
    }
    if (type.value !== nextType) {
      type.value = nextType
      didChange = true
    }
    return didChange
  })
  return Boolean(changed)
}

function syncFiltersToRouteQuery() {
  // Avoid calling router.replace after the user navigated away (prevents double navigation/flicker).
  if (!isParticipantsRoute.value) return
  const query: Record<string, unknown> = { ...route.query }

  const qq = String(q.value || '').trim()
  const st = String(status.value || '').trim()
  const ty = String(type.value || '').trim()

  if (qq) query.q = qq
  else delete query.q

  if (st) query.status = st
  else delete query.status

  if (ty) query.type = ty
  else delete query.type

  const curr = route.query as unknown as Record<string, unknown>
  const same =
    String(curr.q ?? '') === String(query.q ?? '') &&
    String(curr.status ?? '') === String(query.status ?? '') &&
    String(curr.type ?? '') === String(query.type ?? '')

  if (!same) void router.replace({ query: toLocationQueryRaw(query) })
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(
      await api.listParticipants({
        page: page.value,
        per_page: perPage.value,
        status: status.value || undefined,
        type: type.value || undefined,
        q: q.value || undefined,
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
    error.value = msg || t('participant.loadFailed')
  } finally {
    loading.value = false
  }
}

async function promptReason(title: string): Promise<string | null> {
  try {
    const r = await ElMessageBox.prompt(t('common.reasonRequired'), title, {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      inputPlaceholder: t('participant.prompt.reasonPlaceholder'),
      inputValidator: (v) => (String(v || '').trim().length > 0 ? true : t('common.reasonIsRequired')),
      type: 'warning',
    })
    return r.value
  } catch {
    return null
  }
}

async function freeze(row: Participant) {
  const reason = await promptReason(t('participant.prompt.freezeTitle', { pid: row.pid }))
  if (!reason) return
  try {
    assertSuccess(await api.freezeParticipant(row.pid, reason))
    ElMessage.success(t('participant.frozen', { pid: row.pid }))
    await load()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    void toastApiError(e, { fallbackTitle: msg || t('participant.freezeFailed') })
  }
}

async function unfreeze(row: Participant) {
  const reason = await promptReason(t('participant.prompt.unfreezeTitle', { pid: row.pid }))
  if (!reason) return
  try {
    assertSuccess(await api.unfreezeParticipant(row.pid, reason))
    ElMessage.success(t('participant.unfrozen', { pid: row.pid }))
    await load()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    void toastApiError(e, { fallbackTitle: msg || t('participant.unfreezeFailed') })
  }
}

function openRow(row: Participant) {
  selected.value = row
  drawerOpen.value = true
}

function goTrustlines(pid: string) {
  void router.push({
    path: '/trustlines',
    query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), creditor: pid, debtor: undefined }),
  })
}

function goTrustlinesAsDebtor(pid: string) {
  void router.push({
    path: '/trustlines',
    query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), creditor: undefined, debtor: pid }),
  })
}

function goAuditLog(pid: string) {
  void router.push({ path: '/audit-log', query: toLocationQueryRaw({ ...carryScenarioQuery(route.query), q: pid }) })
}

onMounted(() => {
  applyRouteQueryToFilters()
  void load()
})

watch(
  () => [route.query.q, route.query.status, route.query.type],
  () => {
    const changed = applyRouteQueryToFilters()
    if (changed) {
      page.value = 1
      void load()
    }
  },
)

watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})

const debouncedReload = debounce(() => {
  page.value = 1
  void load()
}, DEBOUNCE_SEARCH_MS)

watch([q, status, type], () => {
  if (applyingRouteQuery.value) return
  syncFiltersToRouteQuery()
  debouncedReload()
})

const statusOptions = computed(() => participantStatusOptions())

const typeOptions = computed(() => [
  { label: t('participant.type.any'), value: '' },
  { label: t('participant.type.person'), value: 'person' },
  { label: t('participant.type.business'), value: 'business' },
  { label: t('participant.type.hub'), value: 'hub' },
])
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('participant.title')"
          tooltip-key="nav.participants"
        />
        <div class="filters">
          <el-input
            v-model="q"
            size="small"
            :placeholder="t('participant.filter.searchPlaceholder')"
            clearable
            data-testid="participants-filter-q"
            style="width: 240px"
          />
          <el-select
            v-model="type"
            size="small"
            style="width: 140px"
            :placeholder="t('participant.filter.typePlaceholder')"
            data-testid="participants-filter-type"
          >
            <el-option
              v-for="o in typeOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
          <el-select
            v-model="status"
            size="small"
            style="width: 140px"
            :placeholder="t('participant.filter.statusPlaceholder')"
            data-testid="participants-filter-status"
          >
            <el-option
              v-for="o in statusOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
        </div>
      </div>
    </template>

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
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <el-empty
      v-else-if="items.length === 0"
      :description="t('participant.none')"
    />

    <div v-else>
      <el-table
        :data="items"
        size="small"
        table-layout="fixed"
        class="clickable-table geoTable"
        data-testid="participants-table"
        @row-click="openRow"
      >
        <el-table-column
          prop="pid"
          min-width="210"
        >
          <template #header>
            <TooltipLabel
              :label="t('participant.columns.pid')"
              tooltip-key="participants.pid"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.pid" />
              <CopyIconButton
                :text="scope.row.pid"
                :label="t('participant.columns.pid')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="display_name"
          min-width="180"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('participant.columns.name')"
              tooltip-key="participants.displayName"
            />
          </template>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.display_name" />
          </template>
        </el-table-column>
        <el-table-column
          prop="type"
          width="140"
        >
          <template #header>
            <TooltipLabel
              :label="t('participant.columns.type')"
              tooltip-key="participants.type"
            />
          </template>
          <template #default="scope">
            <el-tag
              :type="scope.row.type === 'business' ? 'warning' : 'info'"
              size="small"
            >
              {{ labelParticipantType(scope.row.type) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="status"
          width="120"
        >
          <template #header>
            <TooltipLabel
              :label="t('participant.columns.status')"
              tooltip-key="participants.status"
            />
          </template>
          <template #default="scope">
            <el-tag
              :type="participantStatusTagType(scope.row.status)"
              size="small"
            >
              {{ labelParticipantStatus(scope.row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('common.actions')"
          width="140"
        >
          <template #default="scope">
            <el-button
              v-if="scope.row.status === 'active'"
              size="small"
              type="warning"
              :disabled="authStore.isReadOnly"
              data-testid="participants-freeze-btn"
              @click.stop="freeze(scope.row)"
            >
              {{ t('participant.freeze') }}
            </el-button>
            <el-button
              v-else-if="scope.row.status === 'suspended'"
              size="small"
              type="success"
              :disabled="authStore.isReadOnly"
              data-testid="participants-unfreeze-btn"
              @click.stop="unfreeze(scope.row)"
            >
              {{ t('participant.unfreeze') }}
            </el-button>
            <el-tag
              v-else
              type="info"
            >
              {{ t('common.n_a') }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint geoHint">
          {{ t('participant.pager.hint', { count: items.length, perPage }) }}
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
    :title="t('participant.drawer.title')"
    size="45%"
  >
    <div v-if="selected">
      <el-descriptions
        class="geoDescriptions"
        :column="1"
        border
      >
        <el-descriptions-item :label="t('participant.columns.pid')">
          <span class="geoInlineRow">
            <TableCellEllipsis :text="selected.pid" />
            <CopyIconButton
              :text="selected.pid"
              :label="t('participant.columns.pid')"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('participant.drawer.displayName')">
          {{ selected.display_name }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('participant.columns.type')">
          <el-tag
            :type="selected.type === 'business' ? 'warning' : 'info'"
            size="small"
          >
            {{ labelParticipantType(selected.type) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('participant.columns.status')">
          <el-tag
            :type="participantStatusTagType(selected.status)"
            size="small"
          >
            {{ labelParticipantStatus(selected.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item
          v-if="selected.created_at"
          :label="t('participant.drawer.createdAt')"
        >
          {{ selected.created_at }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="selected.meta && Object.keys(selected.meta).length > 0"
          :label="t('participant.drawer.meta')"
        >
          <pre class="json">{{ JSON.stringify(selected.meta, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>{{ t('participant.drawer.relatedData') }}</el-divider>

      <div class="drawer-actions">
        <el-button
          type="primary"
          size="small"
          @click="goTrustlines(selected.pid)"
        >
          {{ t('participant.drawer.viewTrustlinesAsCreditor') }}
        </el-button>
        <el-button
          type="primary"
          size="small"
          @click="goTrustlinesAsDebtor(selected.pid)"
        >
          {{ t('participant.drawer.viewTrustlinesAsDebtor') }}
        </el-button>
        <el-button
          size="small"
          @click="goAuditLog(selected.pid)"
        >
          {{ t('participant.drawer.viewAuditLog') }}
        </el-button>
      </div>

      <el-divider v-if="!isLockedParticipantStatus(selected.status)">
        {{ t('common.actions') }}
      </el-divider>

      <div
        v-if="!isLockedParticipantStatus(selected.status)"
        class="drawer-actions"
      >
        <el-button
          v-if="selected.status === 'active'"
          type="warning"
          size="small"
          :disabled="authStore.isReadOnly"
          @click="freeze(selected)"
        >
          {{ t('participant.drawer.freezeParticipant') }}
        </el-button>
        <el-button
          v-else-if="selected.status === 'suspended'"
          type="success"
          size="small"
          :disabled="authStore.isReadOnly"
          @click="unfreeze(selected)"
        >
          {{ t('participant.drawer.unfreezeParticipant') }}
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
}
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
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
.clickable-table :deep(tr) {
  cursor: pointer;
}
.json {
  margin: 0;
  font-size: var(--geo-font-size-sub);
}
.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
