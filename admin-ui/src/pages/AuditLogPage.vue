<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { debounce } from '../utils/debounce'
import { t } from '../i18n'
import type { AuditLogEntry } from '../types/domain'

const loading = ref(false)
const error = ref<string | null>(null)

const route = useRoute()
const q = ref('')

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<AuditLogEntry[]>([])

const drawerOpen = ref(false)
const selected = ref<AuditLogEntry | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    // NOTE: audit-log search must be server-side. Client-side filtering of a single loaded page is misleading.
    const data = assertSuccess(await api.listAuditLog({
      page: page.value,
      per_page: perPage.value,
      q: q.value || undefined,
    }))
    total.value = data.total
    const maxPage = Math.max(1, Math.ceil(total.value / perPage.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return
    }
    items.value = data.items
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('auditLog.loadFailed')
  } finally {
    loading.value = false
  }
}

function openRow(row: AuditLogEntry) {
  selected.value = row
  drawerOpen.value = true
}

onMounted(() => void load())
watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})

const debouncedReload = debounce(() => {
  page.value = 1
  void load()
}, 250)

watch(
  () => route.query.q,
  (v) => {
    if (typeof v === 'string') q.value = v
  },
  { immediate: true },
)

watch(q, () => {
  debouncedReload()
})
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('auditLog.title')"
          tooltip-key="nav.auditLog"
        />
        <el-input
          v-model="q"
          size="small"
          clearable
          :placeholder="t('auditLog.filterPlaceholder')"
          style="width: 320px"
        />
      </div>
    </template>

    <LoadErrorAlert
      v-if="error"
      :title="error"
      :busy="loading"
      @retry="load"
    />
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <el-empty
      v-else-if="items.length === 0"
      :description="t('auditLog.none')"
    />

    <div v-else>
      <el-table
        :data="items"
        size="small"
        table-layout="fixed"
        class="clickable-table geoTable"
        @row-click="openRow"
      >
        <el-table-column
          prop="id"
          width="220"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.id')"
              :tooltip-text="t('auditLog.tooltip.id')"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.id" />
              <CopyIconButton
                :text="String(scope.row.id)"
                :label="t('auditLog.auditId')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="timestamp"
          width="180"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.timestamp')"
              tooltip-key="audit.timestamp"
            />
          </template>
        </el-table-column>
        <el-table-column
          prop="actor_id"
          width="150"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.actor')"
              tooltip-key="audit.actor"
            />
          </template>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.actor_id" />
          </template>
        </el-table-column>
        <el-table-column
          prop="actor_role"
          width="140"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.role')"
              tooltip-key="audit.role"
            />
          </template>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.actor_role" />
          </template>
        </el-table-column>
        <el-table-column
          prop="action"
          min-width="240"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.action')"
              tooltip-key="audit.action"
            />
          </template>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.action" />
          </template>
        </el-table-column>
        <el-table-column
          prop="object_type"
          width="120"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.object')"
              tooltip-key="audit.objectType"
            />
          </template>
        </el-table-column>
        <el-table-column
          prop="object_id"
          min-width="280"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.objectId')"
              tooltip-key="audit.objectId"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.object_id" />
              <CopyIconButton
                v-if="scope.row.object_id"
                :text="scope.row.object_id"
                :label="t('auditLog.objectIdCopyLabel')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="reason"
          min-width="200"
          show-overflow-tooltip
        >
          <template #header>
            <TooltipLabel
              :label="t('auditLog.reason')"
              tooltip-key="audit.reason"
            />
          </template>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.reason" />
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint geoHint">
          {{ t('auditLog.pager.hint', { count: items.length, perPage }) }}
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
    :title="t('auditLog.auditEntry')"
    size="45%"
  >
    <div v-if="selected">
      <el-tabs>
        <el-tab-pane :label="t('auditLog.details')">
          <el-descriptions
            class="geoDescriptions"
            :column="1"
            border
          >
            <el-descriptions-item :label="t('auditLog.id')">
              <span class="geoInlineRow">
                <TableCellEllipsis :text="selected.id" />
                <CopyIconButton
                  :text="String(selected.id)"
                  :label="t('auditLog.auditId')"
                />
              </span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.timestamp')">
              {{ selected.timestamp }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.actor')">
              <span class="geoInlineRow">
                <TableCellEllipsis :text="selected.actor_id" />
                <span>({{ selected.actor_role || t('common.na') }})</span>
                <CopyIconButton
                  v-if="selected.actor_id"
                  :text="String(selected.actor_id)"
                  :label="t('auditLog.actorId')"
                />
              </span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.action')">
              {{ selected.action }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.object')">
              <span class="geoInlineRow">
                <span>{{ selected.object_type || t('common.na') }} /</span>
                <TableCellEllipsis :text="selected.object_id" />
                <CopyIconButton
                  v-if="selected.object_id"
                  :text="selected.object_id"
                  :label="t('auditLog.objectIdCopyLabel')"
                />
              </span>
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.reason')">
              {{ selected.reason }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.requestId')">
              {{ selected.request_id }}
            </el-descriptions-item>
            <el-descriptions-item :label="t('auditLog.ipAddress')">
              {{ selected.ip_address }}
            </el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>
        <el-tab-pane :label="t('auditLog.before')">
          <pre class="json">{{ JSON.stringify(selected.before_state, null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane :label="t('auditLog.after')">
          <pre class="json">{{ JSON.stringify(selected.after_state, null, 2) }}</pre>
        </el-tab-pane>
      </el-tabs>
    </div>
  </el-drawer>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.clickable-table :deep(tr) {
  cursor: pointer;
}
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
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
.json {
  margin: 0;
  font-size: var(--geo-font-size-sub);
}
</style>
