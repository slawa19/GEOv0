<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useRouter } from 'vue-router'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'

type Equivalent = { code: string; precision: number; description: string; is_active: boolean }
type UsageCounts = { trustlines?: number; incidents?: number; debts?: number; integrity_checkpoints?: number }

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
    const usage = assertSuccess(await api.getEquivalentUsage(key))
    usageByCode[key] = {
      trustlines: Number((usage as any).trustlines ?? 0),
      incidents: typeof (usage as any).incidents === 'number' ? Number((usage as any).incidents) : undefined,
      debts: typeof (usage as any).debts === 'number' ? Number((usage as any).debts) : undefined,
      integrity_checkpoints:
        typeof (usage as any).integrity_checkpoints === 'number' ? Number((usage as any).integrity_checkpoints) : undefined,
    }
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
    const data = assertSuccess(await api.listEquivalents({ include_inactive: includeInactive.value }))
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
      await api.createEquivalent({
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
      await api.updateEquivalent(editing.value.code, {
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
    assertSuccess(await api.setEquivalentActive(row.code, next, reason))
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
    const usage = assertSuccess(await api.getEquivalentUsage(row.code))
    const tl = Number((usage as any).trustlines ?? 0)
    const inc = (usage as any).incidents
    const debts = (usage as any).debts
    const ic = (usage as any).integrity_checkpoints
    const parts: string[] = []
    parts.push(`${tl} trustlines`)
    if (typeof inc === 'number') parts.push(`${inc} incidents`)
    if (typeof debts === 'number') parts.push(`${debts} debts`)
    if (typeof ic === 'number') parts.push(`${ic} integrity_checkpoints`)
    usageLine = parts.length ? `Used by ${parts.join(', ')}.` : ''
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
    assertSuccess(await api.deleteEquivalent(row.code, reason))
    ElMessage.success(`Deleted ${row.code}`)
    includeInactive.value = true
    await load()
  } catch (e: any) {
    const t = e?.details?.trustlines
    const i = e?.details?.incidents
    const d = e?.details?.debts
    const ic = e?.details?.integrity_checkpoints
    if ([t, i, d, ic].some((v) => typeof v === 'number')) {
      ElMessage.error(
        `${e?.message || 'Delete failed'} (trustlines: ${t ?? 0}, incidents: ${i ?? 0}, debts: ${d ?? 0}, integrity_checkpoints: ${ic ?? 0})`,
      )
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
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          label="Equivalents"
          tooltip-key="nav.equivalents"
        />
        <div class="hdr__actions">
          <el-button
            :disabled="authStore.isReadOnly"
            type="primary"
            @click="openCreate"
          >
            Create
          </el-button>
          <el-switch
            v-model="includeInactive"
            active-text="Include inactive"
          />
          <el-tag type="info">
            Active: {{ activeCount }}
          </el-tag>
        </div>
      </div>
    </template>

    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      class="mb"
    />
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <el-empty
      v-else-if="items.length === 0"
      description="No equivalents"
    />

    <div v-else>
      <el-table
        :data="items"
        size="small"
        table-layout="fixed"
        class="geoTable"
        @cell-mouse-enter="onCellMouseEnter"
      >
        <el-table-column
          prop="code"
          label="Code"
          width="200"
        >
          <template #default="scope">
            <div class="code">
              <div class="code__main">
                {{ scope.row.code }}
              </div>
              <div
                v-if="usageByCode[scope.row.code]"
                class="code__sub"
              >
                <template v-if="typeof usageByCode[scope.row.code]!.debts === 'number' || typeof usageByCode[scope.row.code]!.integrity_checkpoints === 'number'">
                  Used by {{ usageByCode[scope.row.code]!.trustlines ?? 0 }} TL / {{ usageByCode[scope.row.code]!.debts ?? 0 }} Debts / {{ usageByCode[scope.row.code]!.integrity_checkpoints ?? 0 }} IC
                </template>
                <template v-else>
                  Used by {{ usageByCode[scope.row.code]!.trustlines ?? 0 }} TL / {{ usageByCode[scope.row.code]!.incidents ?? 0 }} Inc
                </template>
              </div>
              <div
                v-else-if="usageLoadingByCode[scope.row.code]"
                class="code__sub"
              >
                Loading usage…
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          prop="precision"
          label="Precision"
          width="90"
          align="center"
          header-align="center"
        />
        <el-table-column
          prop="description"
          label="Description"
          min-width="280"
          show-overflow-tooltip
        />
        <el-table-column
          prop="is_active"
          label="Active"
          width="90"
          align="center"
          header-align="center"
        >
          <template #default="scope">
            <el-tag
              v-if="scope.row.is_active"
              type="success"
            >
              yes
            </el-tag>
            <el-tag
              v-else
              type="info"
            >
              no
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="Actions"
          width="280"
        >
          <template #default="scope">
            <el-button
              size="small"
              :disabled="authStore.isReadOnly"
              @click="openEdit(scope.row)"
            >
              Edit
            </el-button>
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
            <el-button
              size="small"
              @click="goAudit(scope.row)"
            >
              Audit
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-card>

  <el-dialog
    v-model="createOpen"
    title="Create Equivalent"
    width="520"
  >
    <el-form label-width="120">
      <el-form-item label="Code">
        <el-input
          v-model="createForm.code"
          placeholder="e.g. UAH"
          style="width: 200px"
        />
      </el-form-item>
      <el-form-item label="Precision">
        <el-input-number
          v-model="createForm.precision"
          :min="0"
          :max="18"
        />
      </el-form-item>
      <el-form-item label="Description">
        <el-input
          v-model="createForm.description"
          placeholder="Human description"
        />
      </el-form-item>
      <el-form-item label="Active">
        <el-switch v-model="createForm.is_active" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="createOpen = false">
        Cancel
      </el-button>
      <el-button
        type="primary"
        :disabled="authStore.isReadOnly"
        @click="createEq"
      >
        Create
      </el-button>
    </template>
  </el-dialog>

  <el-dialog
    v-model="editOpen"
    title="Edit Equivalent"
    width="520"
  >
    <div
      v-if="editing"
      class="muted"
    >
      Editing: {{ editing.code }}
    </div>
    <el-form label-width="120">
      <el-form-item label="Precision">
        <el-input-number
          v-model="editForm.precision"
          :min="0"
          :max="18"
        />
      </el-form-item>
      <el-form-item label="Description">
        <el-input v-model="editForm.description" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="editOpen = false">
        Cancel
      </el-button>
      <el-button
        type="primary"
        :disabled="authStore.isReadOnly"
        @click="saveEdit"
      >
        Save
      </el-button>
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
