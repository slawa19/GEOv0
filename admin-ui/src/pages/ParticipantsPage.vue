<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import TooltipLabel from '../ui/TooltipLabel.vue'
import { useAuthStore } from '../stores/auth'

type Participant = { pid: string; display_name: string; type: string; status: string; created_at?: string; meta?: Record<string, unknown> }

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

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(
      await mockApi.listParticipants({
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
  } catch (e: any) {
    error.value = e?.message || 'Failed to load participants'
    ElMessage.error(error.value || 'Failed to load participants')
  } finally {
    loading.value = false
  }
}

async function promptReason(title: string): Promise<string | null> {
  try {
    const r = await ElMessageBox.prompt('Reason (required)', title, {
      confirmButtonText: 'Confirm',
      cancelButtonText: 'Cancel',
      inputPlaceholder: 'e.g. suspicious activity, compliance hold',
      inputValidator: (v) => (String(v || '').trim().length > 0 ? true : 'reason is required'),
      type: 'warning',
    })
    return r.value
  } catch {
    return null
  }
}

async function freeze(row: Participant) {
  const reason = await promptReason(`Freeze ${row.pid}`)
  if (!reason) return
  try {
    assertSuccess(await mockApi.freezeParticipant(row.pid, reason))
    ElMessage.success(`Frozen ${row.pid}`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Freeze failed')
  }
}

async function unfreeze(row: Participant) {
  const reason = await promptReason(`Unfreeze ${row.pid}`)
  if (!reason) return
  try {
    assertSuccess(await mockApi.unfreezeParticipant(row.pid, reason))
    ElMessage.success(`Unfrozen ${row.pid}`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Unfreeze failed')
  }
}

function openRow(row: Participant) {
  selected.value = row
  drawerOpen.value = true
}

function goTrustlines(pid: string) {
  void router.push({ path: '/trustlines', query: { ...route.query, creditor: pid } })
}

function goTrustlinesAsDebtor(pid: string) {
  void router.push({ path: '/trustlines', query: { ...route.query, debtor: pid } })
}

function goAuditLog(pid: string) {
  void router.push({ path: '/audit-log', query: { ...route.query, q: pid } })
}

onMounted(() => void load())

watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})
watch([q, status, type], () => {
  page.value = 1
  void load()
})

const statusOptions = computed(() => [
  { label: 'Any status', value: '' },
  { label: 'active', value: 'active' },
  { label: 'frozen', value: 'frozen' },
  { label: 'banned', value: 'banned' },
])

const typeOptions = computed(() => [
  { label: 'Any type', value: '' },
  { label: 'person', value: 'person' },
  { label: 'organization', value: 'organization' },
])
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Participants" tooltip-key="nav.participants" />
        <div class="filters">
          <el-input v-model="q" size="small" placeholder="Search PID / name" clearable style="width: 240px" />
          <el-select v-model="type" size="small" style="width: 140px" placeholder="Type">
            <el-option v-for="o in typeOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-select v-model="status" size="small" style="width: 140px" placeholder="Status">
            <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="items.length === 0" description="No participants" />

    <div v-else>
      <el-table :data="items" size="small" @row-click="openRow" class="clickable-table">
        <el-table-column prop="pid" min-width="240">
          <template #header><TooltipLabel label="PID" tooltip-key="participants.pid" /></template>
        </el-table-column>
        <el-table-column prop="display_name" min-width="200">
          <template #header><TooltipLabel label="Name" tooltip-key="participants.displayName" /></template>
        </el-table-column>
        <el-table-column prop="type" width="140">
          <template #header><TooltipLabel label="Type" tooltip-key="participants.type" /></template>
          <template #default="scope">
            <el-tag :type="scope.row.type === 'organization' ? 'warning' : 'info'" size="small">
              {{ scope.row.type }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" width="120">
          <template #header><TooltipLabel label="Status" tooltip-key="participants.status" /></template>
          <template #default="scope">
            <el-tag
              :type="scope.row.status === 'active' ? 'success' : scope.row.status === 'frozen' ? 'warning' : 'danger'"
              size="small"
            >
              {{ scope.row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="actions" width="160">
          <template #default="scope">
            <el-button
              v-if="scope.row.status === 'active'"
              size="small"
              type="warning"
              :disabled="authStore.isReadOnly"
              @click.stop="freeze(scope.row)"
            >
              Freeze
            </el-button>
            <el-button
              v-else-if="scope.row.status === 'frozen'"
              size="small"
              type="success"
              :disabled="authStore.isReadOnly"
              @click.stop="unfreeze(scope.row)"
            >
              Unfreeze
            </el-button>
            <el-tag v-else type="info">n/a</el-tag>
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

  <el-drawer v-model="drawerOpen" title="Participant details" size="45%">
    <div v-if="selected">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="PID">{{ selected.pid }}</el-descriptions-item>
        <el-descriptions-item label="Display Name">{{ selected.display_name }}</el-descriptions-item>
        <el-descriptions-item label="Type">
          <el-tag :type="selected.type === 'organization' ? 'warning' : 'info'" size="small">
            {{ selected.type }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Status">
          <el-tag
            :type="selected.status === 'active' ? 'success' : selected.status === 'frozen' ? 'warning' : 'danger'"
            size="small"
          >
            {{ selected.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item v-if="selected.created_at" label="Created At">
          {{ selected.created_at }}
        </el-descriptions-item>
        <el-descriptions-item v-if="selected.meta && Object.keys(selected.meta).length > 0" label="Meta">
          <pre class="json">{{ JSON.stringify(selected.meta, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>Related data</el-divider>

      <div class="drawer-actions">
        <el-button type="primary" size="small" @click="goTrustlines(selected.pid)">
          View trustlines as creditor
        </el-button>
        <el-button type="primary" size="small" @click="goTrustlinesAsDebtor(selected.pid)">
          View trustlines as debtor
        </el-button>
        <el-button size="small" @click="goAuditLog(selected.pid)">
          View audit log
        </el-button>
      </div>

      <el-divider v-if="selected.status !== 'banned'">Actions</el-divider>

      <div v-if="selected.status !== 'banned'" class="drawer-actions">
        <el-button
          v-if="selected.status === 'active'"
          type="warning"
          :disabled="authStore.isReadOnly"
          @click="freeze(selected)"
        >
          Freeze participant
        </el-button>
        <el-button
          v-else-if="selected.status === 'frozen'"
          type="success"
          :disabled="authStore.isReadOnly"
          @click="unfreeze(selected)"
        >
          Unfreeze participant
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
  font-size: 12px;
}
.clickable-table :deep(tr) {
  cursor: pointer;
}
.json {
  margin: 0;
  font-size: 12px;
}
.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
