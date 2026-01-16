<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import { t } from '../i18n'

type RowKind = 'boolean' | 'number' | 'string' | 'json'
type Row = { key: string; kind: RowKind; value: unknown }

const LOG_LEVEL_OPTIONS = ['CRITICAL', 'ERROR', 'WARNING', 'WARN', 'INFO', 'DEBUG', 'TRACE'] as const

function isLogLevelKey(key: string): boolean {
  return String(key || '').trim().toUpperCase() === 'LOG_LEVEL'
}

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const route = useRoute()
const filterKey = ref('')

const authStore = useAuthStore()

type ScopeTag = 'runtime' | 'restart' | 'readonly'

function scopeForKey(key: string): ScopeTag[] {
  const raw = String(key || '').trim()
  const upper = raw.toUpperCase()
  const out: ScopeTag[] = []

  // Backend currently exposes config keys as env-style UPPER_SNAKE_CASE.
  // Support both formats (env-style + dotted) so Scope doesn't look empty.
  if (raw.startsWith('feature_flags.') || upper.startsWith('FEATURE_FLAGS_')) out.push('runtime')
  if (raw.startsWith('routing.') || upper.startsWith('ROUTING_')) out.push('runtime')
  if (raw.startsWith('observability.') || upper.startsWith('OBSERVABILITY_') || upper.startsWith('METRICS_')) out.push('runtime')
  if (upper === 'LOG_LEVEL') out.push('runtime')
  if (upper.startsWith('RATE_LIMIT_')) out.push('runtime')

  // Background loops are wired on startup in the backend.
  if (upper.startsWith('RECOVERY_')) out.push('restart')
  if (upper.startsWith('INTEGRITY_CHECKPOINT_')) out.push('restart')
  if (raw.startsWith('limits.') || upper.startsWith('LIMITS_')) out.push('restart')

  // Prototype rule: clearing.* keys are treated as read-only.
  if (raw.startsWith('clearing.') || upper.startsWith('CLEARING_')) out.push('readonly')
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
    const cfg = assertSuccess(await api.getConfig())
    original.value = { ...cfg }
    rows.value = toRows(cfg)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('config.loadFailed')
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
  if (authStore.isReadOnly) {
    ElMessage.error(t('common.readOnlyUpdatesDisabled'))
    return
  }
  const keys = dirtyKeys.value
  if (keys.length === 0) {
    ElMessage.info(t('common.noChanges'))
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
          ElMessage.error(t('config.invalidJsonForKey', { key: k }))
          return
        }
      } else {
        patch[k] = now[k]
      }
    }

    assertSuccess(await api.patchConfig(patch))
    ElMessage.success(t('config.savedKeys', { n: keys.length }))
    await load()
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    ElMessage.error(msg || t('config.saveFailed'))
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
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel
          :label="t('config.title')"
          tooltip-key="nav.config"
        />
        <div class="hdr__actions">
          <el-input
            v-model="filterKey"
            size="small"
            clearable
            :placeholder="t('config.filterByKeyPlaceholder')"
            style="width: 260px"
          />
          <el-tag type="info">
            {{ t('common.dirtyCount', { n: dirtyKeys.length }) }}
          </el-tag>
          <el-button
            :disabled="authStore.isReadOnly || dirtyKeys.length === 0"
            :loading="saving"
            type="primary"
            @click="save"
          >
            {{ t('common.save') }}
          </el-button>
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

    <div v-else>
      <el-table
        :data="visibleRows"
        size="small"
        table-layout="fixed"
        class="geoTable"
      >
        <el-table-column
          prop="key"
          :label="t('config.columns.key')"
          width="420"
          show-overflow-tooltip
        >
          <template #default="scope">
            <span :class="{ focus: focusKey && scope.row.key === focusKey }">
              <TableCellEllipsis :text="scope.row.key" />
            </span>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('config.columns.scope')"
          width="140"
        >
          <template #default="scope">
            <template v-if="scopeForKey(scope.row.key).length">
              <el-tag
                v-for="tag in scopeForKey(scope.row.key)"
                :key="tag"
                size="small"
                :type="tag === 'readonly' ? 'info' : tag === 'restart' ? 'warning' : 'success'"
                style="margin-right: 6px"
              >
                {{ tag }}
              </el-tag>
            </template>
            <span
              v-else
              class="geoHint"
            >{{ t('common.na') }}</span>
          </template>
        </el-table-column>

        <el-table-column
          :label="t('common.value')"
          min-width="420"
        >
          <template #default="scope">
            <div class="cfgValueRow">
              <template v-if="scope.row.kind === 'boolean'">
                <span
                  class="cfgBoolLabel"
                  :class="{ 'cfgBoolLabel--active': scope.row.value === false }"
                >{{ t('common.false') }}</span>
                <el-switch
                  v-model="scope.row.value"
                  :disabled="isKeyReadOnly(scope.row.key)"
                />
                <span
                  class="cfgBoolLabel"
                  :class="{ 'cfgBoolLabel--active': scope.row.value === true }"
                >{{ t('common.true') }}</span>
              </template>

              <el-input-number
                v-else-if="scope.row.kind === 'number'"
                v-model="scope.row.value"
                :disabled="isKeyReadOnly(scope.row.key)"
                controls-position="right"
                class="cfgNumber"
                style="width: 160px"
              />

              <template v-else-if="scope.row.kind === 'string'">
                <el-select
                  v-if="isLogLevelKey(scope.row.key)"
                  v-model="scope.row.value"
                  :disabled="isKeyReadOnly(scope.row.key)"
                  filterable
                  allow-create
                  default-first-option
                  class="cfgSelect"
                  :placeholder="t('config.logLevelPlaceholder')"
                >
                  <el-option
                    v-for="opt in LOG_LEVEL_OPTIONS"
                    :key="opt"
                    :label="opt"
                    :value="opt"
                  />
                </el-select>

                <el-input
                  v-else
                  v-model="scope.row.value"
                  :disabled="isKeyReadOnly(scope.row.key)"
                  size="small"
                  :placeholder="t('common.valuePlaceholder')"
                  class="cfgText"
                />
              </template>

              <el-input
                v-else
                v-model="scope.row.value"
                :disabled="isKeyReadOnly(scope.row.key)"
                size="small"
                type="textarea"
                :rows="2"
                :placeholder="t('config.jsonStringifiedPlaceholder')"
                class="cfgJson"
              />

              <el-tag
                v-if="isKeyReadOnly(scope.row.key)"
                size="small"
                type="info"
              >
                {{ t('common.readOnly') }}
              </el-tag>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <div class="count">
        {{ t('config.showingKeys', { shown: visibleRows.length, total: rows.length }) }}
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

.cfgValueRow {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.cfgBoolLabel {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-weight: 500;
}

.cfgBoolLabel--active {
  color: var(--el-text-color-primary);
  font-weight: 700;
}

.cfgNumber {
  width: 160px;
  flex: 0 0 auto;
}

.cfgSelect {
  width: 160px;
}

.cfgText {
  width: 320px;
  max-width: 420px;
  flex: 0 0 auto;
}

.cfgJson {
  flex: 1 1 auto;
  min-width: 260px;
}
.focus {
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 2px 6px;
}
</style>
