<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import TooltipLabel from '../ui/TooltipLabel.vue'
import { useAuthStore } from '../stores/auth'

type Participant = { pid: string; display_name: string; type: string; status: string }

const loading = ref(false)
const error = ref<string | null>(null)

const authStore = useAuthStore()

const q = ref('')
const status = ref<string>('')

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<Participant[]>([])

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(
      await mockApi.listParticipants({
        page: page.value,
        per_page: perPage.value,
        status: status.value || undefined,
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

onMounted(() => void load())

watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})
watch([q, status], () => {
  page.value = 1
  void load()
})

const statusOptions = computed(() => [
  { label: 'Any', value: '' },
  { label: 'active', value: 'active' },
  { label: 'frozen', value: 'frozen' },
  { label: 'banned', value: 'banned' },
])
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Participants</div>
        <div class="filters">
          <el-input v-model="q" size="small" placeholder="Search PID / name" clearable style="width: 240px" />
          <el-select v-model="status" size="small" style="width: 140px">
            <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="items.length === 0" description="No participants" />

    <div v-else>
      <el-table :data="items" size="small">
        <el-table-column prop="pid" min-width="240">
          <template #header><TooltipLabel label="PID" tooltip-key="participants.pid" /></template>
        </el-table-column>
        <el-table-column prop="display_name" min-width="200">
          <template #header><TooltipLabel label="Name" tooltip-key="participants.displayName" /></template>
        </el-table-column>
        <el-table-column prop="type" width="140">
          <template #header><TooltipLabel label="Type" tooltip-key="participants.type" /></template>
        </el-table-column>
        <el-table-column prop="status" width="120">
          <template #header><TooltipLabel label="Status" tooltip-key="participants.status" /></template>
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
</style>
