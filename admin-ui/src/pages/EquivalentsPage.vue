<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'

type Equivalent = { code: string; precision: number; description: string; is_active: boolean }
type UsageCounts = { trustlines: number; incidents: number }

const loading = ref(false)
const error = ref<string | null>(null)

const includeInactive = ref(false)
const items = ref<Equivalent[]>([])

const router = useRouter()
const authStore = useAuthStore()

const createOpen = ref(false)
const editOpen = ref(false)
const editing = ref<Equivalent | null>(null)

const createForm = reactive({ code: '', precision: 2, description: '', is_active: true })
const editForm = reactive({ precision: 2, description: '' })

const usageByCode = reactive<Record<string, UsageCounts | undefined>>({})
const usageLoadingByCode = reactive<Record<string, boolean | undefined>>({})

async function warmUsage(code: string) {
  const key = String(code || '').trim().toUpperCase()
  if (!key) return
  if (usageByCode[key]) return
  if (usageLoadingByCode[key]) return

  usageLoadingByCode[key] = true
  try {
    const usage = assertSuccess(await mockApi.getEquivalentUsage(key))
    usageByCode[key] = { trustlines: usage.trustlines, incidents: usage.incidents }
  } catch {
    // best-effort only
  } finally {
    usageLoadingByCode[key] = false
  }
}

function onCellMouseEnter(row: Equivalent) {
  void warmUsage(row.code)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(await mockApi.listEquivalents({ include_inactive: includeInactive.value }))
    items.value = data.items
  } catch (e: any) {
    error.value = e?.message || 'Failed to load equivalents'
    ElMessage.error(error.value || 'Failed to load equivalents')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  createForm.code = ''
  createForm.precision = 2
  createForm.description = ''
  createForm.is_active = true
  createOpen.value = true
}

function openEdit(row: Equivalent) {
  editing.value = row
  editForm.precision = row.precision
  editForm.description = row.description
  editOpen.value = true
}

async function createEq() {
  try {
    const created = assertSuccess(
      await mockApi.createEquivalent({
        code: createForm.code,
        precision: Number(createForm.precision),
        description: createForm.description,
        is_active: Boolean(createForm.is_active),
      }),
    ).created
    ElMessage.success(`Created ${created.code}`)
    createOpen.value = false
    includeInactive.value = true
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Create failed')
  }
}

async function saveEdit() {
  if (!editing.value) return
  try {
    const updated = assertSuccess(
      await mockApi.updateEquivalent(editing.value.code, {
        precision: Number(editForm.precision),
        description: editForm.description,
      }),
    ).updated
    ElMessage.success(`Updated ${updated.code}`)
    editOpen.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Update failed')
  }
}

async function setActive(row: Equivalent, next: boolean) {
  let reason: string
  try {
    reason = await ElMessageBox.prompt('Reason (required)', next ? `Activate ${row.code}` : `Deactivate ${row.code}`, {
      confirmButtonText: next ? 'Activate' : 'Deactivate',
      cancelButtonText: 'Cancel',
      inputPlaceholder: 'e.g. enable new equivalent, deprecated unit',
      inputValidator: (v) => (String(v || '').trim().length > 0 ? true : 'reason is required'),
      type: 'warning',
    }).then((r) => r.value)
  } catch {
    return
  }

  try {
    assertSuccess(await mockApi.setEquivalentActive(row.code, next, reason))
    ElMessage.success(next ? `Activated ${row.code}` : `Deactivated ${row.code}`)
    includeInactive.value = true
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Update failed')
  }
}

async function deleteEq(row: Equivalent) {
  let usageLine = ''
  try {
    const usage = assertSuccess(await mockApi.getEquivalentUsage(row.code))
    usageLine = `Used by ${usage.trustlines} trustlines and ${usage.incidents} incidents.`
  } catch {
    usageLine = ''
  }

  let reason: string
  try {
    reason = await ElMessageBox.prompt(
      [usageLine, 'This permanently deletes the equivalent. This cannot be undone.', '', 'Reason (required)']
        .filter(Boolean)
        .join('\n'),
      `Delete ${row.code}`,
      {
        confirmButtonText: 'Delete',
        cancelButtonText: 'Cancel',
        inputPlaceholder: 'e.g. cleanup unused unit',
        inputValidator: (v) => (String(v || '').trim().length > 0 ? true : 'reason is required'),
        type: 'warning',
      },
    ).then((r) => r.value)
  } catch {
    return
  }

  try {
    assertSuccess(await mockApi.deleteEquivalent(row.code, reason))
    ElMessage.success(`Deleted ${row.code}`)
    includeInactive.value = true
    await load()
  } catch (e: any) {
    const t = e?.details?.trustlines
    const i = e?.details?.incidents
    if (typeof t === 'number' || typeof i === 'number') {
      ElMessage.error(`${e?.message || 'Delete failed'} (trustlines: ${t ?? 0}, incidents: ${i ?? 0})`)
    } else {
      ElMessage.error(e?.message || 'Delete failed')
    }
  }
}

function goAudit(row: Equivalent) {
  void router.push({ path: '/audit-log', query: { code: row.code, q: row.code } })
}

onMounted(() => void load())
watch(includeInactive, () => void load())

const activeCount = computed(() => items.value.filter((e) => e.is_active).length)
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Equivalents" tooltip-key="nav.equivalents" />
        <div class="hdr__actions">
          <el-button :disabled="authStore.isReadOnly" type="primary" @click="openCreate">Create</el-button>
          <el-switch v-model="includeInactive" active-text="Include inactive" />
          <el-tag type="info">active: {{ activeCount }}</el-tag>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="items.length === 0" description="No equivalents" />

    <div v-else>
      <el-table :data="items" size="small" @cell-mouse-enter="onCellMouseEnter">
        <el-table-column prop="code" label="code" width="160">
          <template #default="scope">
            <div class="code">
              <div class="code__main">{{ scope.row.code }}</div>
              <div class="code__sub" v-if="usageByCode[scope.row.code]">
                Used by {{ usageByCode[scope.row.code]!.trustlines }} TL / {{ usageByCode[scope.row.code]!.incidents }} Inc
              </div>
              <div class="code__sub" v-else-if="usageLoadingByCode[scope.row.code]">
                Loading usage…
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="precision" label="precision" width="120" />
        <el-table-column prop="description" label="description" min-width="320" />
        <el-table-column prop="is_active" label="active" width="120">
          <template #default="scope">
            <el-tag v-if="scope.row.is_active" type="success">yes</el-tag>
            <el-tag v-else type="info">no</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="actions" width="330">
          <template #default="scope">
            <el-button size="small" :disabled="authStore.isReadOnly" @click="openEdit(scope.row)">Edit</el-button>
            <el-button
              v-if="scope.row.is_active"
              size="small"
              type="warning"
              :disabled="authStore.isReadOnly"
              @click="setActive(scope.row, false)"
            >
              Deactivate
            </el-button>
            <el-button
              v-else
              size="small"
              type="success"
              :disabled="authStore.isReadOnly"
              @click="setActive(scope.row, true)"
            >
              Activate
            </el-button>
            <el-button
              v-if="!scope.row.is_active"
              size="small"
              type="danger"
              :disabled="authStore.isReadOnly"
              @click="deleteEq(scope.row)"
            >
              Delete
            </el-button>
            <el-button size="small" @click="goAudit(scope.row)">Audit</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-card>

  <el-dialog v-model="createOpen" title="Create equivalent" width="520">
    <el-form label-width="120">
      <el-form-item label="code">
        <el-input v-model="createForm.code" placeholder="e.g. UAH" style="width: 200px" />
      </el-form-item>
      <el-form-item label="precision">
        <el-input-number v-model="createForm.precision" :min="0" :max="18" />
      </el-form-item>
      <el-form-item label="description">
        <el-input v-model="createForm.description" placeholder="Human description" />
      </el-form-item>
      <el-form-item label="active">
        <el-switch v-model="createForm.is_active" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="createOpen = false">Cancel</el-button>
      <el-button type="primary" :disabled="authStore.isReadOnly" @click="createEq">Create</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="editOpen" title="Edit equivalent" width="520">
    <div v-if="editing" class="muted">Editing: {{ editing.code }}</div>
    <el-form label-width="120">
      <el-form-item label="precision">
        <el-input-number v-model="editForm.precision" :min="0" :max="18" />
      </el-form-item>
      <el-form-item label="description">
        <el-input v-model="editForm.description" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editOpen = false">Cancel</el-button>
      <el-button type="primary" :disabled="authStore.isReadOnly" @click="saveEdit">Save</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.hdr__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.mb {
  margin-bottom: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-bottom: 10px;
}

.code {
  line-height: 1.15;
}

.code__main {
  font-weight: 600;
}

.code__sub {
  margin-top: 2px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
</style>
