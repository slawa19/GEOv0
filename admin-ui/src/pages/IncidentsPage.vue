<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter, useRoute } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import TooltipLabel from '../ui/TooltipLabel.vue'
import CopyIconButton from '../ui/CopyIconButton.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import { formatIsoInTimeZone } from '../utils/datetime'
import { useConfigStore } from '../stores/config'
import { useAuthStore } from '../stores/auth'
import type { Incident } from '../types/domain'

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
  if (!iso) return '—'
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
  } catch (e: any) {
    error.value = e?.message || 'Failed to load incidents'
    ElMessage.error(error.value || 'Failed to load incidents')
  } finally {
    loading.value = false
  }
}

function isOverSla(row: Incident): boolean {
  return row.age_seconds > row.sla_seconds
}

async function forceAbort(row: Incident) {
  if (authStore.isReadOnly) {
    ElMessage.error('Read-only role: abort is disabled')
    return
  }
  let reason: string
  try {
    reason = await ElMessageBox.prompt('Reason for abort (required)', 'Force abort transaction', {
      confirmButtonText: 'Abort',
      cancelButtonText: 'Cancel',
      inputPlaceholder: 'e.g. stuck prepare, manual intervention',
      inputValidator: (v) => (String(v || '').trim().length > 0 ? true : 'reason is required'),
      type: 'warning',
    }).then((r) => r.value)
  } catch {
    return
  }

  abortingTxId.value = row.tx_id
  try {
    assertSuccess(await api.abortTx(row.tx_id, reason))
    ElMessage.success(`Aborted ${row.tx_id}`)
    lastAbortTxId.value = row.tx_id
    drawerOpen.value = false
  } catch (e: any) {
    ElMessage.error(e?.message || 'Abort failed')
  } finally {
    abortingTxId.value = null
  }
}

function openRow(row: Incident) {
  selected.value = row
  drawerOpen.value = true
}

function goAudit(txId: string) {
  void router.push({ path: '/audit-log', query: { ...route.query, q: txId } })
}

function goParticipant(pid: string) {
  void router.push({ path: '/participants', query: { ...route.query, q: pid } })
}

function goEquivalent(eq: string) {
  void router.push({ path: '/equivalents', query: { ...route.query, q: eq } })
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
          label="Incidents"
          tooltip-key="nav.incidents"
        />
        <el-tooltip
          placement="top"
          effect="dark"
          :show-after="850"
          popper-class="geoTooltip geoTooltip--menu"
        >
          <template #content>
            <span class="geoTooltipText geoTooltipText--clamp2">
              Incidents whose Age is greater than SLA (on this page).
            </span>
          </template>
          <el-tag type="warning">
            SLA breaches: {{ overSlaCount }}
          </el-tag>
        </el-tooltip>
      </div>
    </template>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      class="mb"
    />
    <el-alert
      v-else-if="lastAbortTxId"
      type="success"
      show-icon
      class="mb"
      title="Abort completed"
    >
      <template #default>
        <div class="okrow">
          <div>Audit entry should be available for {{ lastAbortTxId }}.</div>
          <el-button
            size="small"
            type="primary"
            @click="goAudit(lastAbortTxId)"
          >
            Open audit log
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
      description="No incidents"
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
              label="Tx ID"
              tooltip-key="incidents.txId"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.tx_id" />
              <CopyIconButton
                :text="scope.row.tx_id"
                label="Tx ID"
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
              label="State"
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
              label="Initiator"
              tooltip-key="incidents.initiator"
            />
          </template>
          <template #default="scope">
            <span class="geoInlineRow">
              <TableCellEllipsis :text="scope.row.initiator_pid" />
              <CopyIconButton
                :text="scope.row.initiator_pid"
                label="Initiator PID"
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
              label="Equivalent"
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
              label="Age"
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
              label="SLA"
              tooltip-key="incidents.sla"
            />
          </template>
          <template #default="scope">
            {{ fmtAge(scope.row.sla_seconds) }}
          </template>
        </el-table-column>
        <el-table-column
          label="Actions"
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
              Force abort
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint geoHint">
          Showing {{ items.length }} / {{ perPage }} on this page
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
    title="Incident details"
    size="45%"
  >
    <div v-if="selected">
      <el-descriptions
        :column="1"
        border
      >
        <el-descriptions-item label="Transaction ID">
          <span class="geoInlineRow">
            <TableCellEllipsis :text="selected.tx_id" />
            <CopyIconButton
              :text="selected.tx_id"
              label="Tx ID"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="State">
          <el-tag
            type="warning"
            size="small"
          >
            {{ selected.state }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Initiator PID">
          <span class="geoInlineRow">
            <el-link
              type="primary"
              @click="goParticipant(selected.initiator_pid)"
            >
              {{ selected.initiator_pid }}
            </el-link>
            <CopyIconButton
              :text="selected.initiator_pid"
              label="Initiator PID"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="Equivalent">
          <el-link
            type="primary"
            @click="goEquivalent(selected.equivalent)"
          >
            {{ selected.equivalent }}
          </el-link>
        </el-descriptions-item>
        <el-descriptions-item label="Age">
          <span :class="{ bad: isOverSla(selected) }">{{ fmtAge(selected.age_seconds) }}</span>
          <span
            v-if="isOverSla(selected)"
            class="sla-warn"
          > (over SLA!)</span>
        </el-descriptions-item>
        <el-descriptions-item label="SLA">
          {{ fmtAge(selected.sla_seconds) }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="selected.created_at"
          label="Created At"
        >
          {{ fmtTs(selected.created_at) }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>Related data</el-divider>

      <div class="drawer-actions">
        <el-button
          type="primary"
          size="small"
          @click="goParticipant(selected.initiator_pid)"
        >
          View initiator participant
        </el-button>
        <el-button
          size="small"
          @click="goAudit(selected.tx_id)"
        >
          View audit log
        </el-button>
      </div>

      <el-divider>Actions</el-divider>

      <div class="drawer-actions">
        <el-button
          type="danger"
          :loading="abortingTxId === selected.tx_id"
          @click="forceAbort(selected)"
        >
          Force abort transaction
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
  font-size: 12px;
}
.bad {
  color: var(--el-color-danger);
  font-weight: 700;
}
.sla-warn {
  color: var(--el-color-danger);
  font-size: 12px;
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
