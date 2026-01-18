<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter, useRoute } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { formatIsoInTimeZone } from '../utils/datetime'
import { useConfigStore } from '../stores/config'
import { useAuthStore } from '../stores/auth'
import type { Incident } from '../types/domain'
import { t } from '../i18n'
import { toLocationQueryRaw } from '../router/query'

const loading = ref(false)
const error = ref<string | null>(null)

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<Incident[]>([])

const router = useRouter()
const route = useRoute()
const lastAbortTxId = ref<string | null>(null)

const abortingTxId = ref<string | null>(null)

const drawerOpen = ref(false)
const selected = ref<Incident | null>(null)

const configStore = useConfigStore()
const authStore = useAuthStore()
const timeZone = computed(() => String(configStore.config['ui.timezone'] || 'UTC'))

function fmtTs(iso: string | undefined): string {
  if (!iso) return t('common.na')
  return formatIsoInTimeZone(iso, timeZone.value)
}

function fmtAge(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(await api.listIncidents({ page: page.value, per_page: perPage.value }))
    total.value = data.total
    const maxPage = Math.max(1, Math.ceil(total.value / perPage.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return
    }
    items.value = data.items
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('incidents.loadFailed')
  } finally {
    loading.value = false
  }
}

function isOverSla(row: Incident): boolean {
  return row.age_seconds > row.sla_seconds
}

async function forceAbort(row: Incident) {
  if (authStore.isReadOnly) {
    ElMessage.error(t('incidents.readOnlyAbortDisabled'))
    return
  }
  let reason: string
  try {
    reason = await ElMessageBox.prompt(t('incidents.abort.reasonRequired'), t('incidents.abort.title'), {
      confirmButtonText: t('common.abort'),
      cancelButtonText: t('common.cancel'),
      inputPlaceholder: t('incidents.abort.reasonPlaceholder'),
      inputValidator: (v) => (String(v || '').trim().length > 0 ? true : t('common.reasonIsRequired')),
      type: 'warning',
    }).then((r) => r.value)
  } catch {
    return
  }

  abortingTxId.value = row.tx_id
  try {
    assertSuccess(await api.abortTx(row.tx_id, reason))
    ElMessage.success(t('incidents.aborted', { txId: row.tx_id }))
    lastAbortTxId.value = row.tx_id
    drawerOpen.value = false
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    ElMessage.error(msg || t('incidents.abortFailed'))
  } finally {
    abortingTxId.value = null
  }
}

function openRow(row: Incident) {
  selected.value = row
  drawerOpen.value = true
}

function goAudit(txId: string) {
  void router.push({ path: '/audit-log', query: toLocationQueryRaw({ ...route.query, q: txId }) })
}

function goParticipant(pid: string) {
  void router.push({ path: '/participants', query: toLocationQueryRaw({ ...route.query, q: pid }) })
}

function goEquivalent(eq: string) {
  void router.push({ path: '/equivalents', query: toLocationQueryRaw({ ...route.query, q: eq }) })
}

onMounted(() => void load())
watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})

const overSlaCount = computed(() => items.value.filter(isOverSla).length)
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('incidents.title')"
          tooltip-key="nav.incidents"
        />
        <el-tooltip
          placement="top"
          effect="dark"
          :show-after="850"
          popper-class="geoTooltip geoTooltip--menu"
        >
          <template #content>
            <span class="geoTooltipText geoTooltipText--clamp4">
              {{ t('incidents.slaTooltip') }}
            </span>
          </template>
          <el-tag type="warning">
            {{ t('incidents.slaBreaches', { n: overSlaCount }) }}
          </el-tag>
        </el-tooltip>
      </div>
    </template>

    <LoadErrorAlert
      v-if="error"
      :title="error"
      :busy="loading"
      @retry="load"
    />
    <el-alert
      v-else-if="lastAbortTxId"
      type="success"
      show-icon
      class="mb"
      :title="t('incidents.abortCompleted')"
    >
      <template #default>
        <div class="okrow">
          <div>{{ t('incidents.auditEntryAvailableFor', { txId: lastAbortTxId }) }}</div>
          <el-button
            size="small"
            type="primary"
            @click="goAudit(lastAbortTxId)"
          >
            {{ t('incidents.openAuditLog') }}
          </el-button>
        </div>
      </template>
    </el-alert>
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <el-empty
      v-else-if="items.length === 0"
      :description="t('incidents.none')"
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
          prop="tx_id"
          min-width="200"
        >
          <template #header>
            <TooltipLabel
              :label="t('incidents.columns.txId')"
              tooltip-key="incidents.txId"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.tx_id" />
              <CopyIconButton
                :text="scope.row.tx_id"
                :label="t('incidents.columns.txId')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="state"
          width="170"
        >
          <template #header>
            <TooltipLabel
              :label="t('incidents.columns.state')"
              tooltip-key="incidents.state"
            />
          </template>
          <template #default="scope">
            <el-tag
              type="warning"
              size="small"
            >
              {{ scope.row.state }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="initiator_pid"
          min-width="200"
        >
          <template #header>
            <TooltipLabel
              :label="t('incidents.columns.initiator')"
              tooltip-key="incidents.initiator"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.initiator_pid" />
              <CopyIconButton
                :text="scope.row.initiator_pid"
                :label="t('incidents.columns.initiatorPid')"
              />
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="equivalent"
          width="100"
        >
          <template #header>
            <TooltipLabel
              :label="t('incidents.columns.equivalent')"
              tooltip-key="incidents.eq"
            />
          </template>
          <template #default="scope">
            <span>
              {{ scope.row.equivalent }}
            </span>
          </template>
        </el-table-column>
        <el-table-column
          prop="age_seconds"
          width="120"
        >
          <template #header>
            <TooltipLabel
              :label="t('incidents.columns.age')"
              tooltip-key="incidents.age"
            />
          </template>
          <template #default="scope">
            <span :class="{ bad: isOverSla(scope.row) }">{{ fmtAge(scope.row.age_seconds) }}</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="sla_seconds"
          width="120"
        >
          <template #header>
            <TooltipLabel
              :label="t('incidents.columns.sla')"
              tooltip-key="incidents.sla"
            />
          </template>
          <template #default="scope">
            {{ fmtAge(scope.row.sla_seconds) }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('common.actions')"
          width="140"
        >
          <template #default="scope">
            <el-button
              size="small"
              type="danger"
              :loading="abortingTxId === scope.row.tx_id"
              :disabled="authStore.isReadOnly"
              @click.stop="forceAbort(scope.row)"
            >
              {{ t('incidents.forceAbort') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint geoHint">
          {{ t('incidents.pager.hint', { count: items.length, perPage }) }}
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
    :title="t('incidents.drawer.title')"
    size="45%"
  >
    <div v-if="selected">
      <el-descriptions
        class="geoDescriptions"
        :column="1"
        border
      >
        <el-descriptions-item :label="t('incidents.drawer.transactionId')">
          <span class="geoInlineRow">
            <TableCellEllipsis :text="selected.tx_id" />
            <CopyIconButton
              :text="selected.tx_id"
              :label="t('incidents.columns.txId')"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('incidents.columns.state')">
          <el-tag
            type="warning"
            size="small"
          >
            {{ selected.state }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item :label="t('incidents.columns.initiatorPid')">
          <span class="geoInlineRow">
            <el-link
              type="primary"
              @click="goParticipant(selected.initiator_pid)"
            >
              {{ selected.initiator_pid }}
            </el-link>
            <CopyIconButton
              :text="selected.initiator_pid"
              :label="t('incidents.columns.initiatorPid')"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('incidents.columns.equivalent')">
          <el-link
            type="primary"
            @click="goEquivalent(selected.equivalent)"
          >
            {{ selected.equivalent }}
          </el-link>
        </el-descriptions-item>
        <el-descriptions-item :label="t('incidents.columns.age')">
          <span :class="{ bad: isOverSla(selected) }">{{ fmtAge(selected.age_seconds) }}</span>
          <span
            v-if="isOverSla(selected)"
            class="sla-warn"
          > {{ t('incidents.overSla') }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('incidents.columns.sla')">
          {{ fmtAge(selected.sla_seconds) }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="selected.created_at"
          :label="t('incidents.createdAt')"
        >
          {{ fmtTs(selected.created_at) }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>{{ t('incidents.drawer.relatedData') }}</el-divider>

      <div class="drawer-actions">
        <el-button
          type="primary"
          size="small"
          @click="goParticipant(selected.initiator_pid)"
        >
          {{ t('incidents.drawer.viewInitiator') }}
        </el-button>
        <el-button
          size="small"
          @click="goAudit(selected.tx_id)"
        >
          {{ t('incidents.drawer.viewAuditLog') }}
        </el-button>
      </div>

      <el-divider>{{ t('common.actions') }}</el-divider>

      <div class="drawer-actions">
        <el-button
          type="danger"
          :loading="abortingTxId === selected.tx_id"
          @click="forceAbort(selected)"
        >
          {{ t('incidents.drawer.forceAbortTransaction') }}
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
.mb {
  margin-bottom: 12px;
}
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
.okrow {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.pager__hint {
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-sub);
}
.bad {
  color: var(--el-color-danger);
  font-weight: 700;
}
.sla-warn {
  color: var(--el-color-danger);
  font-size: var(--geo-font-size-sub);
}
.clickable-table :deep(tr) {
  cursor: pointer;
}
.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
