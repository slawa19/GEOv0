<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import TooltipLabel from '../ui/TooltipLabel.vue'

type Incident = {
  tx_id: string
  state: string
  initiator_pid: string
  equivalent: string
  age_seconds: number
  created_at?: string
  sla_seconds: number
}

const loading = ref(false)
const error = ref<string | null>(null)

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<Incident[]>([])

const router = useRouter()
const lastAbortTxId = ref<string | null>(null)

const abortingTxId = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(await mockApi.listIncidents({ page: page.value, per_page: perPage.value }))
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
    assertSuccess(await mockApi.abortTx(row.tx_id, reason))
    ElMessage.success(`Aborted ${row.tx_id}`)
    lastAbortTxId.value = row.tx_id
  } catch (e: any) {
    ElMessage.error(e?.message || 'Abort failed')
  } finally {
    abortingTxId.value = null
  }
}

function goAudit(txId: string) {
  void router.push({ path: '/audit-log', query: { q: txId } })
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
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Incidents</div>
        <el-tag type="warning">over SLA: {{ overSlaCount }}</el-tag>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
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
          <el-button size="small" type="primary" @click="goAudit(lastAbortTxId)">Open audit log</el-button>
        </div>
      </template>
    </el-alert>
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="items.length === 0" description="No incidents" />

    <div v-else>
      <el-table :data="items" size="small">
        <el-table-column prop="tx_id" min-width="220">
          <template #header><TooltipLabel label="tx_id" tooltip-key="incidents.txId" /></template>
        </el-table-column>
        <el-table-column prop="state" width="200">
          <template #header><TooltipLabel label="state" tooltip-key="incidents.state" /></template>
        </el-table-column>
        <el-table-column prop="initiator_pid" min-width="220">
          <template #header><TooltipLabel label="initiator" tooltip-key="incidents.initiator" /></template>
        </el-table-column>
        <el-table-column prop="equivalent" width="90">
          <template #header><TooltipLabel label="eq" tooltip-key="incidents.eq" /></template>
        </el-table-column>
        <el-table-column prop="age_seconds" width="120">
          <template #header><TooltipLabel label="age" tooltip-key="incidents.age" /></template>
          <template #default="scope">
            <span :class="{ bad: isOverSla(scope.row) }">{{ scope.row.age_seconds }}s</span>
          </template>
        </el-table-column>
        <el-table-column prop="sla_seconds" width="120">
          <template #header><TooltipLabel label="sla" tooltip-key="incidents.sla" /></template>
        </el-table-column>
        <el-table-column label="actions" width="160">
          <template #default="scope">
            <el-button
              size="small"
              type="danger"
              :loading="abortingTxId === scope.row.tx_id"
              @click="forceAbort(scope.row)"
            >
              Force abort
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint">Showing {{ items.length }} / {{ perPage }} on this page</div>
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
</style>
