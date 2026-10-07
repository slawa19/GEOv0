<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { describeError } from '../api/describeError'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import ListState from '../ui/ListState.vue'
import { promptReason } from '../ui/promptReason'
import { debounce } from '../utils/debounce'
import { formatTs } from '../utils/datetime'
import { DEBOUNCE_SEARCH_MS } from '../constants/timing'
import { t } from '../i18n'
import { labelParticipantType } from '../i18n/labels'
import type { Participant } from '../types/domain'
import { toLocationQueryRaw } from '../router/query'
import { usePagedList } from '../composables/usePagedList'
import { useRouteQueryFilters } from '../composables/useRouteQueryFilters'
import { useBusyKeys } from '../composables/useBusyKeys'
import {
  isLockedParticipantStatus,
  labelParticipantStatus,
  participantStatusOptions,
  participantStatusTagType,
} from '../ui/participantStatus'

const router = useRouter()
const route = useRoute()

const q = ref('')
const status = ref<string>('')
const type = ref<string>('')

const { page, perPage, total, items, loading, error, reload, reloadFromFirstPage } = usePagedList<Participant>(
  ({ page: requestPage, perPage: requestPerPage }) =>
    api.listParticipants({
      page: requestPage,
      per_page: requestPerPage,
      status: status.value || undefined,
      type: type.value || undefined,
      q: q.value || undefined,
    }),
  { errorKey: 'participant.loadFailed' },
)

const drawerOpen = ref(false)
const selected = ref<Participant | null>(null)
let pageActive = true

const debouncedReload = debounce(reloadFromFirstPage, DEBOUNCE_SEARCH_MS)

const lowerTrimmed = (raw: string) => raw.trim().toLowerCase()
const { applyRoute } = useRouteQueryFilters({
  route,
  router,
  path: '/participants',
  filters: {
    q: { model: q },
    status: { model: status, fromQuery: lowerTrimmed },
    type: { model: type, fromQuery: lowerTrimmed },
  },
  onRouteChange: reloadFromFirstPage,
  onUserChange: debouncedReload,
})

// The two operator actions are one flow that differs in the call and the words.
const STATUS_ACTIONS = {
  freeze: {
    call: (pid: string, reason: string) => api.freezeParticipant(pid, reason),
    titleKey: 'participant.prompt.freezeTitle',
    done: 'participant.frozen',
    failed: 'participant.freezeFailed',
  },
  unfreeze: {
    call: (pid: string, reason: string) => api.unfreezeParticipant(pid, reason),
    titleKey: 'participant.prompt.unfreezeTitle',
    done: 'participant.unfrozen',
    failed: 'participant.unfreezeFailed',
  },
} as const

// A participant's state change runs once at a time: the buttons stay busy until the answer is in.
const busy = useBusyKeys()

function changeStatus(row: Participant, action: keyof typeof STATUS_ACTIONS) {
  return busy.run(row.pid, () => runStatusChange(row, action))
}

async function runStatusChange(row: Participant, action: keyof typeof STATUS_ACTIONS) {
  const words = STATUS_ACTIONS[action]
  const reason = await promptReason(
    t(words.titleKey, { pid: row.pid }),
    '',
    'common.confirm',
    'participant.prompt.reasonPlaceholder',
  )
  if (!reason || !pageActive) return
  try {
    const result = await words.call(row.pid, reason)
    if (!pageActive) return
    const updated = { ...row, status: result.status }
    const index = items.value.findIndex((item) => item.pid === row.pid)
    if (index >= 0) items.value[index] = updated
    if (selected.value?.pid === row.pid) selected.value = updated
    ElMessage.success(t(words.done, { pid: row.pid }))
    await reload()
  } catch (e: unknown) {
    if (!pageActive) return
    ElMessage.error(describeError(e, words.failed).text)
  }
}

const freeze = (row: Participant) => changeStatus(row, 'freeze')
const unfreeze = (row: Participant) => changeStatus(row, 'unfreeze')

function openRow(row: Participant) {
  selected.value = row
  drawerOpen.value = true
}

function goTrustlines(pid: string) {
  void router.push({
    path: '/trustlines',
    query: toLocationQueryRaw({ creditor: pid, debtor: undefined }),
  })
}

function goTrustlinesAsDebtor(pid: string) {
  void router.push({
    path: '/trustlines',
    query: toLocationQueryRaw({ creditor: undefined, debtor: pid }),
  })
}

function goAuditLog(pid: string) {
  void router.push({ path: '/audit-log', query: toLocationQueryRaw({ q: pid }) })
}

onMounted(() => {
  applyRoute()
  void reload()
})

onBeforeUnmount(() => {
  pageActive = false
  debouncedReload.cancel()
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

    <ListState
      :error="error"
      :loading="loading"
      :empty="items.length === 0"
      :empty-text="t('participant.none')"
      @retry="reload"
    >
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
              data-testid="participants-freeze-btn"
              :loading="busy.has(scope.row.pid)"
              @click.stop="freeze(scope.row)"
            >
              {{ t('participant.freeze') }}
            </el-button>
            <el-button
              v-else-if="scope.row.status === 'suspended'"
              size="small"
              type="success"
              data-testid="participants-unfreeze-btn"
              :loading="busy.has(scope.row.pid)"
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
    </ListState>
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
          {{ formatTs(selected.created_at) }}
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
          @click="freeze(selected)"
        >
          {{ t('participant.drawer.freezeParticipant') }}
        </el-button>
        <el-button
          v-else-if="selected.status === 'suspended'"
          type="success"
          size="small"
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
