<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { describeError } from '../api/describeError'
import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import ListState from '../ui/ListState.vue'
import { t, te } from '../i18n'
import { useRouteQueryFilters } from '../composables/useRouteQueryFilters'
import { buildPatch, dirtyKeysOf, sectionForKey, toRows, unitHintKey, type Row, type SectionId } from './configModel'

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)

const route = useRoute()
const router = useRouter()
const filterKey = ref('')

const original = ref<Record<string, unknown>>({})
const rows = ref<Row[]>([])

type Section = {
  id: SectionId
  title: string
  rows: Row[]
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const cfg = await api.getConfig()
    original.value = { ...cfg }
    rows.value = toRows(cfg)
  } catch (e: unknown) {
    error.value = describeError(e, 'config.loadFailed').text
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

const dirtyKeys = computed(() => dirtyKeysOf(rows.value, original.value))

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

  const section = sectionForKey(row.key)

  const lines: string[] = []
  const kindKey = `config.helpFallback.kind.${row.kind}`
  if (te(kindKey)) lines.push(t(kindKey as never))

  const sectionKey = `config.helpFallback.section.${section}`
  if (te(sectionKey)) lines.push(t(sectionKey as never))

  const unitKey = unitHintKey(row.key)
  if (unitKey) lines.push(t(unitKey))

  lines.push(t('config.helpFallback.apply'))
  lines.push(t('config.helpFallback.safeDefault'))

  return lines.filter(Boolean).slice(0, 4).join('\n')
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
    mk('rateLimit', 'config.sections.rateLimit'),
    mk('routing', 'config.sections.routing'),
    mk('other', 'config.sections.other'),
  ]
  return ordered.filter((s) => s.rows.length > 0)
})

async function save() {
  const keys = dirtyKeys.value
  if (keys.length === 0) {
    ElMessage.info(t('common.noChanges'))
    return
  }

  const built = buildPatch(rows.value, keys)
  if ('invalidJsonKey' in built) {
    ElMessage.error(t('config.invalidJsonForKey', { key: built.invalidJsonKey }))
    return
  }

  saving.value = true
  try {
    await api.patchConfig(built.patch)
    ElMessage.success(t('config.savedKeys', { n: keys.length }))
    await load()
  } catch (e: unknown) {
    ElMessage.error(describeError(e, 'config.saveFailed').text)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void load()
})

// The key filter is linked in the URL (`/config?key=ROUTING_MAX_HOPS`), which is how other screens point at one key.
const { applyRoute } = useRouteQueryFilters({
  route,
  router,
  path: '/config',
  filters: { key: { model: filterKey } },
})
applyRoute()
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
            :disabled="dirtyKeys.length === 0"
            :loading="saving"
            type="primary"
            @click="save"
          >
            {{ t('common.save') }}
          </el-button>
        </div>
      </div>
    </template>

    <ListState
      :error="error"
      :loading="loading"
      :empty="sections.length === 0"
      @retry="load"
    >
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
                  />
                  <span
                    class="cfgBoolLabel"
                    :class="{ 'cfgBoolLabel--active': scope.row.value === true }"
                  >{{ t('common.true') }}</span>
                </template>

                <el-input-number
                  v-else-if="scope.row.kind === 'number'"
                  v-model="scope.row.value"
                  controls-position="right"
                  class="cfgNumber"
                  style="width: 160px"
                />

                <template v-else-if="scope.row.kind === 'string'">
                  <el-input
                    v-model="scope.row.value"
                    size="small"
                    :placeholder="t('common.valuePlaceholder')"
                    class="cfgText"
                  />
                </template>

                <el-input
                  v-else
                  v-model="scope.row.value"
                  size="small"
                  type="textarea"
                  :rows="2"
                  :placeholder="t('config.jsonStringifiedPlaceholder')"
                  class="cfgJson"
                />
              </div>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <div class="count geoHint">
        {{ t('config.showingKeys', { shown: visibleRows.length, total: rows.length }) }}
      </div>
    </ListState>
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
