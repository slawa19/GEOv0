<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { useAuthStore } from '../stores/auth'
import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import LoadErrorAlert from '../ui/LoadErrorAlert.vue'
import { t, te } from '../i18n'

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
const router = useRouter()
const filterKey = ref('')

const authStore = useAuthStore()

type ScopeTag = 'runtime'

function isKeyReadOnly(_key: string): boolean {
  return authStore.isReadOnly
}

const original = ref<Record<string, unknown>>({})
const rows = ref<Row[]>([])

type SectionId =
  | 'featureFlags'
  | 'logging'
  | 'rateLimit'
  | 'routing'
  | 'recovery'
  | 'integrity'
  | 'other'

type Section = {
  id: SectionId
  title: string
  rows: Row[]
}

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

  return rows.value.filter((r) => {
    const key = r.key.toLowerCase()
    const label = configLabel(r.key).toLowerCase()
    return key.includes(needle) || label.includes(needle)
  })
})

const dirtyKeys = computed(() => {
  const now = toObject(rows.value)
  const dirty: string[] = []
  for (const [k, v] of Object.entries(now)) {
    if (original.value[k] !== v) dirty.push(k)
  }
  return dirty
})

function configLabel(key: string): string {
  const k = String(key || '').trim()
  const dictKey = `config.labels.${k}`
  if (te(dictKey)) return t(dictKey as never)
  return k
}

function configTooltipText(key: string): string | undefined {
  const k = String(key || '').trim()
  const dictKey = `config.help.${k}`
  if (te(dictKey)) return t(dictKey as never)
  return undefined
}

function configTooltipTextForRow(row: Row): string {
  const explicit = configTooltipText(row.key)
  if (explicit) return explicit

  const kUpper = String(row.key || '').trim().toUpperCase()
  const section = sectionForKey(row.key)

  const lines: string[] = []
  const kindKey = `config.helpFallback.kind.${row.kind}`
  if (te(kindKey)) lines.push(t(kindKey as never))

  const sectionKey = `config.helpFallback.section.${section}`
  if (te(sectionKey)) lines.push(t(sectionKey as never))

  if (kUpper.endsWith('_SECONDS')) {
    lines.push(t('config.helpFallback.units.seconds'))
  } else if (kUpper.includes('_REQUESTS') || kUpper.endsWith('_COUNT') || kUpper.includes('_MAX_')) {
    lines.push(t('config.helpFallback.units.count'))
  }

  lines.push(t('config.helpFallback.apply'))
  lines.push(t('config.helpFallback.safeDefault'))

  return lines.filter(Boolean).slice(0, 4).join('\n')
}

function appliesForKey(key: string): ScopeTag[] {
  // /admin/config currently only exposes runtime-mutable items.
  // Keep the function for future expansion and UI consistency.
  void key
  return ['runtime']
}

function sectionForKey(key: string): SectionId {
  const k = String(key || '').trim().toUpperCase()
  if (k.startsWith('FEATURE_FLAGS_') || k === 'CLEARING_ENABLED') return 'featureFlags'
  if (k === 'LOG_LEVEL') return 'logging'
  if (k.startsWith('RATE_LIMIT_')) return 'rateLimit'
  if (k.startsWith('ROUTING_')) return 'routing'
  if (k.startsWith('RECOVERY_') || k.startsWith('PAYMENT_TX_')) return 'recovery'
  if (k.startsWith('INTEGRITY_CHECKPOINT_')) return 'integrity'
  return 'other'
}

const sections = computed((): Section[] => {
  const byId = new Map<SectionId, Row[]>()
  for (const r of visibleRows.value) {
    const id = sectionForKey(r.key)
    const arr = byId.get(id) ?? []
    arr.push(r)
    byId.set(id, arr)
  }

  const mk = (id: SectionId, titleKey: string): Section => ({
    id,
    title: t(titleKey),
    rows: (byId.get(id) ?? []).sort((a, b) => a.key.localeCompare(b.key)),
  })

  const ordered: Section[] = [
    mk('featureFlags', 'config.sections.featureFlags'),
    mk('logging', 'config.sections.logging'),
    mk('rateLimit', 'config.sections.rateLimit'),
    mk('routing', 'config.sections.routing'),
    mk('recovery', 'config.sections.recovery'),
    mk('integrity', 'config.sections.integrity'),
    mk('other', 'config.sections.other'),
  ]
  return ordered.filter((s) => s.rows.length > 0)
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

onMounted(() => {
  void load()
})

watch(
  () => route.query.key,
  (v) => {
    filterKey.value = typeof v === 'string' ? v : ''
  },
  { immediate: true },
)

watch(
  filterKey,
  (v) => {
    const key = String(v || '').trim()
    const nextQuery = { ...route.query } as Record<string, any>
    if (key) nextQuery.key = key
    else delete nextQuery.key
    void router.replace({ query: nextQuery as any })
  },
  { flush: 'post' },
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

    <LoadErrorAlert
      v-if="error"
      :title="error"
      :busy="loading"
      @retry="load"
    />
    <el-skeleton
      v-if="loading"
      animated
      :rows="10"
    />

    <div v-else>
      <el-empty
        v-if="sections.length === 0"
        :description="t('common.noData')"
      />

      <template v-else>
        <section
          v-for="section in sections"
          :key="section.id"
          class="cfgSection"
        >
          <div class="cfgSection__title">
            {{ section.title }}
          </div>

          <el-table
            :data="section.rows"
            size="small"
            table-layout="fixed"
            class="geoTable"
            :show-header="sections.indexOf(section) === 0"
          >
            <el-table-column
              :label="t('config.columns.key')"
              min-width="300"
            >
              <template #default="scope">
                <div class="cfgName">
                  <TooltipLabel
                    :label="configLabel(scope.row.key)"
                    :tooltip-text="configTooltipTextForRow(scope.row)"
                  />
                  <div
                    v-if="configLabel(scope.row.key) !== scope.row.key"
                    class="cfgKey geoHint"
                  >
                    <span :class="{ focus: focusKey && scope.row.key === focusKey }">
                      <TableCellEllipsis :text="scope.row.key" />
                    </span>
                  </div>
                </div>
              </template>
            </el-table-column>

            <el-table-column
              :label="t('config.columns.scope')"
              width="160"
            >
              <template #default="scope">
                <template v-if="appliesForKey(scope.row.key).length">
                  <el-tag
                    v-for="tag in appliesForKey(scope.row.key)"
                    :key="tag"
                    size="small"
                    type="success"
                    style="margin-right: 6px"
                  >
                    {{ t(`config.applies.${tag}`) }}
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
              min-width="300"
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
                      popper-class="geoSelectPopper geoSelectPopper--configValue"
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
        </section>

        <div class="count geoHint">
          {{ t('config.showingKeys', { shown: visibleRows.length, total: rows.length }) }}
        </div>
      </template>
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
  font-size: var(--geo-font-size-sub);
}

.cfgSection {
  margin-bottom: 12px;
}
.cfgSection__title {
  font-weight: 700;
  font-size: 14px;
  margin: 4px 0 4px 0;
  color: var(--el-text-color-primary);
}

.cfgName {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.2;
}
.cfgKey {
  margin-top: 1px;
  font-size: 10px;
  opacity: 0.8;
}

.cfgValueRow {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.cfgBoolLabel {
  font-size: 11px;
  color: var(--el-text-color-secondary);
  font-weight: 400;
  width: 32px;
  text-align: center;
  flex: 0 0 32px;
}

.cfgBoolLabel--active {
  color: var(--el-text-color-primary);
  font-weight: 600;
}

.geoTable :deep(.el-table__cell) {
  padding: 4px 0;
}

.geoTable :deep(.el-table__header) th {
  padding: 4px 0;
  background-color: var(--el-fill-color-lighter);
}

.cfgNumber {
  width: 160px;
  flex: 0 0 auto;
}

.cfgSelect {
  width: 160px;
}

/* geoTable enforces label font size; restore standard size for select input and dropdown options */
.cfgSelect :deep(.el-input__wrapper) {
  font-size: var(--el-font-size-base);
}

.cfgSelect :deep(.el-input__inner) {
  font-size: var(--el-font-size-base);
}

:global(.geoSelectPopper--configValue) {
  font-size: var(--el-font-size-base);
}

:global(.geoSelectPopper--configValue .el-select-dropdown__item) {
  font-size: var(--el-font-size-base);
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
