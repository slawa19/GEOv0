<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import TooltipLabel from '../ui/TooltipLabel.vue'

type AuditLogEntry = {
  id: string
  timestamp: string
  actor_id: string
  actor_role: string
  action: string
  object_type: string
  object_id: string
  reason?: string | null
  before_state?: unknown
  after_state?: unknown
  request_id?: string
  ip_address?: string
}

const loading = ref(false)
const error = ref<string | null>(null)

const route = useRoute()
const q = ref('')

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<AuditLogEntry[]>([])

const visibleItems = computed(() => {
  const needle = String(q.value || '').trim().toLowerCase()
  if (!needle) return items.value
  return items.value.filter((e) => {
    return (
      e.id.toLowerCase().includes(needle) ||
      e.actor_id.toLowerCase().includes(needle) ||
      e.actor_role.toLowerCase().includes(needle) ||
      e.action.toLowerCase().includes(needle) ||
      e.object_type.toLowerCase().includes(needle) ||
      e.object_id.toLowerCase().includes(needle) ||
      String(e.reason || '').toLowerCase().includes(needle)
    )
  })
})

const drawerOpen = ref(false)
const selected = ref<AuditLogEntry | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(await mockApi.listAuditLog({ page: page.value, per_page: perPage.value }))
    total.value = data.total
    const maxPage = Math.max(1, Math.ceil(total.value / perPage.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return
    }
    items.value = data.items
  } catch (e: any) {
    error.value = e?.message || 'Failed to load audit log'
    ElMessage.error(error.value || 'Failed to load audit log')
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

watch(
  () => route.query.q,
  (v) => {
    if (typeof v === 'string') q.value = v
  },
  { immediate: true },
)

watch(q, () => {
  page.value = 1
})
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Audit Log" tooltip-key="nav.auditLog" />
        <el-input v-model="q" size="small" clearable placeholder="Filter (Id/Actor/Action/Object/Reason)" style="width: 320px" />
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="items.length === 0" description="No audit entries" />

    <div v-else>
      <el-table :data="visibleItems" size="small" @row-click="openRow" class="clickable-table geoTable">
        <el-table-column prop="timestamp" width="190">
          <template #header><TooltipLabel label="Timestamp" tooltip-key="audit.timestamp" /></template>
        </el-table-column>
        <el-table-column prop="actor_id" width="160">
          <template #header><TooltipLabel label="Actor" tooltip-key="audit.actor" /></template>
        </el-table-column>
        <el-table-column prop="actor_role" width="120">
          <template #header><TooltipLabel label="Role" tooltip-key="audit.role" /></template>
        </el-table-column>
        <el-table-column prop="action" min-width="180">
          <template #header><TooltipLabel label="Action" tooltip-key="audit.action" /></template>
        </el-table-column>
        <el-table-column prop="object_type" width="140">
          <template #header><TooltipLabel label="Object" tooltip-key="audit.objectType" /></template>
        </el-table-column>
        <el-table-column prop="object_id" min-width="220">
          <template #header><TooltipLabel label="Object ID" tooltip-key="audit.objectId" /></template>
        </el-table-column>
        <el-table-column prop="reason" min-width="160">
          <template #header><TooltipLabel label="Reason" tooltip-key="audit.reason" /></template>
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

  <el-drawer v-model="drawerOpen" title="Audit entry" size="45%">
    <div v-if="selected">
      <el-tabs>
        <el-tab-pane label="Details">
          <el-descriptions :column="1" border>
            <el-descriptions-item label="ID">{{ selected.id }}</el-descriptions-item>
            <el-descriptions-item label="Timestamp">{{ selected.timestamp }}</el-descriptions-item>
            <el-descriptions-item label="Actor">{{ selected.actor_id }} ({{ selected.actor_role }})</el-descriptions-item>
            <el-descriptions-item label="Action">{{ selected.action }}</el-descriptions-item>
            <el-descriptions-item label="Object">{{ selected.object_type }} / {{ selected.object_id }}</el-descriptions-item>
            <el-descriptions-item label="Reason">{{ selected.reason }}</el-descriptions-item>
            <el-descriptions-item label="Request ID">{{ selected.request_id }}</el-descriptions-item>
            <el-descriptions-item label="IP Address">{{ selected.ip_address }}</el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>
        <el-tab-pane label="Before">
          <pre class="json">{{ JSON.stringify(selected.before_state, null, 2) }}</pre>
        </el-tab-pane>
        <el-tab-pane label="After">
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
  font-size: 12px;
}
.json {
  margin: 0;
  font-size: 12px;
}
</style>
