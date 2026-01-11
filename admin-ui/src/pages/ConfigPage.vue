<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import { useAuthStore } from '../stores/auth'

type RowKind = 'boolean' | 'number' | 'string' | 'json'
type Row = { key: string; kind: RowKind; value: unknown }

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const route = useRoute()
const filterKey = ref('')

const authStore = useAuthStore()

type ScopeTag = 'runtime' | 'restart' | 'readonly'

function scopeForKey(key: string): ScopeTag[] {
  const out: ScopeTag[] = []
  if (key.startsWith('feature_flags.')) out.push('runtime')
  if (key.startsWith('routing.')) out.push('runtime')
  if (key.startsWith('observability.')) out.push('runtime')
  if (key.startsWith('limits.')) out.push('restart')
  // Prototype rule: clearing.* keys are treated as read-only.
  if (key.startsWith('clearing.')) out.push('readonly')
  return out
}

function isKeyReadOnly(key: string): boolean {
  if (authStore.isReadOnly) return true
  return scopeForKey(key).includes('readonly')
}

const original = ref<Record<string, unknown>>({})
const rows = ref<Row[]>([])

function kindOf(value: unknown): RowKind {
  if (typeof value === 'boolean') return 'boolean'
  if (typeof value === 'number') return 'number'
  if (typeof value === 'string') return 'string'
  return 'json'
}

function toRows(obj: Record<string, unknown>): Row[] {
  return Object.entries(obj)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => {
      const kind = kindOf(value)
      if (kind === 'json') {
        return { key, kind, value: JSON.stringify(value, null, 2) }
      }
      return { key, kind, value }
    })
}

function toObject(rs: Row[]): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const r of rs) out[r.key] = r.value
  return out
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const cfg = assertSuccess(await mockApi.getConfig())
    original.value = { ...cfg }
    rows.value = toRows(cfg)
  } catch (e: any) {
    error.value = e?.message || 'Failed to load config'
  } finally {
    loading.value = false
  }
}

const focusKey = computed(() => {
  const q = route.query.key
  return typeof q === 'string' && q.trim() ? q.trim() : ''
})

const visibleRows = computed(() => {
  const needle = String(filterKey.value || '').trim().toLowerCase()
  if (!needle) return rows.value
  return rows.value.filter((r) => r.key.toLowerCase().includes(needle))
})

const dirtyKeys = computed(() => {
  const now = toObject(rows.value)
  const dirty: string[] = []
  for (const [k, v] of Object.entries(now)) {
    if (original.value[k] !== v) dirty.push(k)
  }
  return dirty
})

async function save() {
  const keys = dirtyKeys.value
  if (keys.length === 0) {
    ElMessage.info('No changes')
    return
  }

  saving.value = true
  try {
    const now = toObject(rows.value)
    const rowByKey = new Map(rows.value.map((r) => [r.key, r] as const))
    const patch: Record<string, unknown> = {}

    for (const k of keys) {
      if (isKeyReadOnly(k)) continue
      const r = rowByKey.get(k)
      if (!r) continue
      if (r.kind === 'json') {
        try {
          patch[k] = JSON.parse(String(r.value))
        } catch {
          ElMessage.error(`Invalid JSON for ${k}`)
          return
        }
      } else {
        patch[k] = now[k]
      }
    }

    assertSuccess(await mockApi.patchConfig(patch))
    ElMessage.success(`Saved (${keys.length} keys)`)
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Save failed')
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())

watch(
  () => route.query.key,
  (v) => {
    if (typeof v === 'string' && v.trim()) filterKey.value = v.trim()
  },
  { immediate: true },
)
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Config</div>
        <div class="hdr__actions">
          <el-input
            v-model="filterKey"
            size="small"
            clearable
            placeholder="Filter by key"
            style="width: 260px"
          />
          <el-tag type="info">dirty: {{ dirtyKeys.length }}</el-tag>
          <el-button :disabled="dirtyKeys.length === 0" :loading="saving" type="primary" @click="save">Save</el-button>
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <div v-else>
      <el-table :data="visibleRows" size="small">
        <el-table-column prop="key" label="key" min-width="320">
          <template #default="scope">
            <span :class="{ focus: focusKey && scope.row.key === focusKey }">{{ scope.row.key }}</span>
          </template>
        </el-table-column>

        <el-table-column label="scope" width="160">
          <template #default="scope">
            <el-tag v-for="t in scopeForKey(scope.row.key)" :key="t" size="small" :type="t === 'readonly' ? 'info' : t === 'restart' ? 'warning' : 'success'" style="margin-right: 6px">
              {{ t }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="value" min-width="320">
          <template #default="scope">
            <el-switch
              v-if="scope.row.kind === 'boolean'"
              v-model="scope.row.value"
              :disabled="isKeyReadOnly(scope.row.key)"
              active-text="true"
              inactive-text="false"
            />
            <el-input-number
              v-else-if="scope.row.kind === 'number'"
              v-model="scope.row.value"
              :disabled="isKeyReadOnly(scope.row.key)"
              controls-position="right"
              style="width: 220px"
            />
            <el-input
              v-else-if="scope.row.kind === 'string'"
              v-model="scope.row.value"
              :disabled="isKeyReadOnly(scope.row.key)"
              size="small"
              placeholder="value"
            />
            <el-input
              v-else
              v-model="scope.row.value"
              :disabled="isKeyReadOnly(scope.row.key)"
              size="small"
              type="textarea"
              :rows="2"
              placeholder="JSON (stringified)"
            />
            <el-tag v-if="isKeyReadOnly(scope.row.key)" size="small" type="info" style="margin-left: 8px">read-only</el-tag>
          </template>
        </el-table-column>
      </el-table>
      <div class="count">Showing {{ visibleRows.length }} / {{ rows.length }} keys</div>
    </div>
  </el-card>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.hdr__actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.mb {
  margin-bottom: 12px;
}
.count {
  margin-top: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.focus {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 2px 6px;
}
</style>
