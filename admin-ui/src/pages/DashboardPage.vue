<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { ApiException, assertSuccess } from '../api/envelope'
import { api } from '../api'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import type { AuditLogEntry, Incident, Trustline } from '../types/domain'
import { t } from '../i18n'
import { labelParticipantType } from '../i18n/labels'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const error = ref<string | null>(null)

const health = ref<Record<string, unknown> | null>(null)
const healthDb = ref<Record<string, unknown> | null>(null)
const migrations = ref<Record<string, unknown> | null>(null)

const auditLoading = ref(false)
const auditError = ref<string | null>(null)
const auditItems = ref<AuditLogEntry[]>([])

const threshold = ref('0.10')

const bottlenecksLoading = ref(false)
const bottlenecksError = ref<string | null>(null)
const bottleneckItems = ref<Trustline[]>([])

const incidentsLoading = ref(false)
const incidentsError = ref<string | null>(null)
const incidentsOverSla = ref<Incident[]>([])

const participantsStatsLoading = ref(false)
const participantsStatsError = ref<string | null>(null)
const participantsByStatus = ref(new Map<string, number>())
const participantsByType = ref(new Map<string, number>())

function normKey(v: unknown): string {
  return String(v ?? '').trim().toLowerCase()
}

function statusLabel(s: string): string {
  const k = normKey(s)
  if (!k) return t('common.unknown')
  if (k === 'active') return t('participant.status.active')
  if (k === 'suspended') return t('participant.status.suspended')
  if (k === 'left') return t('participant.status.left')
  if (k === 'deleted') return t('participant.status.deleted')
  return k
}

async function loadParticipantStats() {
  participantsStatsLoading.value = true
  participantsStatsError.value = null
  try {
    const perPage = 200
    const maxPages = 5
    const byStatus = new Map<string, number>()
    const byType = new Map<string, number>()

    let seen = 0
    let backendTotal = Number.NaN

    for (let p = 1; p <= maxPages; p++) {
      const page = assertSuccess(await api.listParticipants({ page: p, per_page: perPage }))
      const items = (page.items || []) as Array<{ status?: unknown; type?: unknown }>
      for (const it of items) {
        const st = normKey(it.status) || 'unknown'
        const ty = normKey(it.type) || 'unknown'
        byStatus.set(st, (byStatus.get(st) || 0) + 1)
        byType.set(ty, (byType.get(ty) || 0) + 1)
      }
      seen += items.length
      backendTotal = Number((page as { total?: unknown }).total)
      if ((Number.isFinite(backendTotal) && seen >= backendTotal) || items.length === 0) break
    }

    participantsByStatus.value = byStatus
    participantsByType.value = byType
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    participantsStatsError.value = msg || t('dashboard.participantsStatsLoadFailed')
  } finally {
    participantsStatsLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = null
  try {
    health.value = assertSuccess(await api.health())
    healthDb.value = assertSuccess(await api.healthDb())
    migrations.value = assertSuccess(await api.migrations())
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    error.value = msg || t('dashboard.loadFailed')
    ElMessage.error(error.value || t('dashboard.loadFailed'))
  } finally {
    loading.value = false
  }
}

async function loadAudit() {
  auditLoading.value = true
  auditError.value = null
  try {
    const page = assertSuccess(await api.listAuditLog({ page: 1, per_page: 10 }))
    auditItems.value = page.items
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    auditError.value = msg || t('auditLog.loadFailed')
  } finally {
    auditLoading.value = false
  }
}

function isBottleneck(t: Trustline): boolean {
  return isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: threshold.value })
}

function money(v: string): string {
  return formatDecimalFixed(v, 2)
}

async function loadBottlenecks() {
  bottlenecksLoading.value = true
  bottlenecksError.value = null
  try {
    // TODO(backend): this is intentionally naive for mock/fixtures.
    // In real mode, replace with a thin endpoint like:
    //   GET /admin/trustlines/bottlenecks?threshold=&limit=
    // so we don't fetch hundreds of trustlines and sort client-side.
    // Backend enforces per_page <= 200. Fetch a few pages (bounded) to find bottlenecks.
    const perPage = 200
    const maxPages = 5
    const all: Trustline[] = []

    for (let p = 1; p <= maxPages; p++) {
      const page = assertSuccess(await api.listTrustlines({ page: p, per_page: perPage }))
      all.push(...(page.items as Trustline[]))
      const total = Number((page as { total?: unknown }).total)
      if ((Number.isFinite(total) && all.length >= total) || (page.items || []).length === 0) break
    }

    const candidates = all.filter((t) => t.status === 'active' && isBottleneck(t))
    // naive ranking: lower available first
    candidates.sort((a, b) => {
      const avA = Number(a.available)
      const avB = Number(b.available)
      if (Number.isFinite(avA) && Number.isFinite(avB) && avA !== avB) return avA - avB
      return a.created_at.localeCompare(b.created_at)
    })
    bottleneckItems.value = candidates.slice(0, 10)
  } catch (e: unknown) {
    if (e instanceof ApiException) {
      bottlenecksError.value = `${e.message} (${e.status} ${e.code})`
    } else {
      const msg = e instanceof Error ? e.message : String(e)
      bottlenecksError.value = msg || t('dashboard.bottlenecksLoadFailed')
    }
  } finally {
    bottlenecksLoading.value = false
  }
}

async function loadIncidents() {
  incidentsLoading.value = true
  incidentsError.value = null
  try {
    const page = assertSuccess(await api.listIncidents({ page: 1, per_page: 200 }))
    const all = page.items as Incident[]
    const over = all.filter((i) => i.age_seconds > i.sla_seconds)
    over.sort((a, b) => b.age_seconds - a.age_seconds)
    incidentsOverSla.value = over.slice(0, 10)
  } catch (e: unknown) {
    if (e instanceof ApiException) {
      incidentsError.value = `${e.message} (${e.status} ${e.code})`
    } else {
      const msg = e instanceof Error ? e.message : String(e)
      incidentsError.value = msg || t('incidents.loadFailed')
    }
  } finally {
    incidentsLoading.value = false
  }
}

function go(path: string) {
  void router.push({ path, query: { ...route.query } })
}

function goParticipantsWithFilter(filter: { status?: string; type?: string }) {
  const q = { ...route.query } as Record<string, unknown>
  if (filter.status) q.status = filter.status
  else delete q.status
  if (filter.type) q.type = filter.type
  else delete q.type
  void router.push({ path: '/participants', query: q })
}

function goTrustlinesWithThreshold() {
  const t = String(threshold.value || '').trim()
  void router.push({ path: '/trustlines', query: { ...route.query, ...(t ? { threshold: t } : {}) } })
}

onMounted(() => {
  void load()
  void loadAudit()
  void loadBottlenecks()
  void loadIncidents()
  void loadParticipantStats()
})

const statusText = computed(() => String(health.value?.status ?? t('common.unknown')))

const migrationsCurrent = computed(() => {
  const m = migrations.value
  if (!m) return '—'
  const rec = m as Record<string, unknown>
  return String(rec.current_revision ?? '—')
})
const migrationsHead = computed(() => {
  const m = migrations.value
  if (!m) return '—'
  const rec = m as Record<string, unknown>
  return String(rec.head_revision ?? '—')
})
const migrationsUpToDateLabel = computed(() => {
  const m = migrations.value
  if (!m) return t('common.unknown')
  const rec = m as Record<string, unknown>
  const cur = rec.current_revision
  const head = rec.head_revision
  if (!cur && !head) return t('common.unknown')
  return rec.is_up_to_date ? t('common.yes') : t('common.no')
})

const healthDbInfo = computed(() => {
  const db = healthDb.value?.db
  return db && typeof db === 'object' ? (db as Record<string, unknown>) : null
})

const statusRows = computed(() => {
  const order = ['active', 'suspended', 'left', 'deleted', 'unknown']
  const m = participantsByStatus.value
  const extra = [...m.keys()].filter((k) => !order.includes(k)).sort()
  return [...order, ...extra]
    .filter((k) => (m.get(k) || 0) > 0)
    .map((k) => ({ key: k, label: statusLabel(k), count: m.get(k) || 0 }))
})

const typeRows = computed(() => {
  const order = ['person', 'business', 'hub', 'unknown']
  const m = participantsByType.value
  const extra = [...m.keys()].filter((k) => !order.includes(k)).sort()
  return [...order, ...extra]
    .filter((k) => (m.get(k) || 0) > 0)
    .map((k) => ({ key: k, label: k === 'unknown' ? t('common.unknown') : labelParticipantType(k), count: m.get(k) || 0 }))
})
</script>

<template>
  <div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      class="mb"
    />

    <el-row
      :gutter="12"
      class="mb"
    >
      <el-col :span="8">
        <el-card class="geoCard">
          <template #header>
            <TooltipLabel
              :label="t('dashboard.card.api')"
              tooltip-key="dashboard.api"
            />
          </template>
          <el-skeleton
            v-if="loading"
            animated
            :rows="3"
          />
          <div v-else>
            <div><span class="geoLabel">{{ t('dashboard.field.status') }}:</span> {{ statusText }}</div>
            <div><span class="geoLabel">{{ t('dashboard.field.version') }}:</span> {{ health?.version }}</div>
            <div><span class="geoLabel">{{ t('dashboard.field.uptime') }}:</span> {{ health?.uptime_seconds }}s</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card class="geoCard">
          <template #header>
            <TooltipLabel
              :label="t('dashboard.card.db')"
              tooltip-key="dashboard.db"
            />
          </template>
          <el-skeleton
            v-if="loading"
            animated
            :rows="3"
          />
          <div v-else>
            <div><span class="geoLabel">{{ t('dashboard.field.status') }}:</span> {{ healthDb?.status }}</div>
            <div><span class="geoLabel">{{ t('dashboard.field.reachable') }}:</span> {{ healthDbInfo?.reachable }}</div>
            <div><span class="geoLabel">{{ t('dashboard.field.latency') }}:</span> {{ healthDbInfo?.latency_ms }}ms</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card class="geoCard">
          <template #header>
            <TooltipLabel
              :label="t('dashboard.card.migrations')"
              tooltip-key="dashboard.migrations"
            />
          </template>
          <el-skeleton
            v-if="loading"
            animated
            :rows="3"
          />
          <div v-else>
            <div><span class="geoLabel">{{ t('dashboard.field.upToDate') }}:</span> {{ migrationsUpToDateLabel }}</div>
            <div><span class="geoLabel">{{ t('dashboard.field.current') }}:</span> {{ migrationsCurrent }}</div>
            <div><span class="geoLabel">{{ t('dashboard.field.head') }}:</span> {{ migrationsHead }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row
      :gutter="12"
      class="mb"
    >
      <el-col :span="12">
        <el-card class="geoCard">
          <template #header>
            <div class="hdr">
              <TooltipLabel
                :label="t('dashboard.card.participantsByType')"
                tooltip-key="nav.participants"
              />
              <div class="hdr__right">
                <el-button
                  size="small"
                  @click="go('/participants')"
                >
                  {{ t('common.viewAll') }}
                </el-button>
              </div>
            </div>
          </template>

          <el-alert
            v-if="participantsStatsError"
            :title="participantsStatsError"
            type="warning"
            show-icon
            class="mb"
          />
          <el-skeleton
            v-else-if="participantsStatsLoading"
            animated
            :rows="3"
          />

          <el-empty
            v-else-if="typeRows.length === 0"
            :description="t('dashboard.empty.noParticipantStats')"
          />

          <div
            v-else
            class="tags"
          >
            <el-tooltip
              v-for="r in typeRows"
              :key="r.key"
              placement="top"
              effect="dark"
              :show-after="650"
            >
              <template #content>
                {{ t('dashboard.participants.openFiltered') }}
              </template>
              <el-tag
                class="tag"
                effect="plain"
                @click="goParticipantsWithFilter({ type: r.key === 'unknown' ? '' : r.key })"
              >
                {{ r.label }}: {{ r.count }}
              </el-tag>
            </el-tooltip>
          </div>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card class="geoCard">
          <template #header>
            <div class="hdr">
              <TooltipLabel
                :label="t('dashboard.card.participantsByStatus')"
                tooltip-key="nav.participants"
              />
              <div class="hdr__right">
                <el-button
                  size="small"
                  @click="go('/participants')"
                >
                  {{ t('common.viewAll') }}
                </el-button>
              </div>
            </div>
          </template>

          <el-alert
            v-if="participantsStatsError"
            :title="participantsStatsError"
            type="warning"
            show-icon
            class="mb"
          />
          <el-skeleton
            v-else-if="participantsStatsLoading"
            animated
            :rows="3"
          />

          <el-empty
            v-else-if="statusRows.length === 0"
            :description="t('dashboard.empty.noParticipantStats')"
          />

          <div
            v-else
            class="tags"
          >
            <el-tooltip
              v-for="r in statusRows"
              :key="r.key"
              placement="top"
              effect="dark"
              :show-after="650"
            >
              <template #content>
                {{ t('dashboard.participants.openFiltered') }}
              </template>
              <el-tag
                class="tag"
                effect="plain"
                @click="goParticipantsWithFilter({ status: r.key === 'unknown' ? '' : r.key })"
              >
                {{ r.label }}: {{ r.count }}
              </el-tag>
            </el-tooltip>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row
      :gutter="12"
      class="mb"
    >
      <el-col :span="12">
        <el-card class="geoCard">
          <template #header>
            <div class="hdr">
              <TooltipLabel
                :label="t('dashboard.card.bottlenecks')"
                tooltip-key="dashboard.bottlenecks"
              />
              <div class="hdr__right">
                <el-input
                  v-model="threshold"
                  size="small"
                  style="width: 120px"
                  placeholder="0.10"
                />
                <el-button
                  size="small"
                  @click="goTrustlinesWithThreshold()"
                >
                  {{ t('common.viewAll') }}
                </el-button>
              </div>
            </div>
          </template>

          <el-alert
            v-if="bottlenecksError"
            :title="bottlenecksError"
            type="warning"
            show-icon
            class="mb"
          />
          <el-skeleton
            v-if="bottlenecksLoading"
            animated
            :rows="6"
          />

          <el-empty
            v-else-if="bottleneckItems.length === 0"
            :description="t('dashboard.empty.noBottlenecks')"
          />

          <el-table
            v-else
            :data="bottleneckItems"
            size="small"
            height="360"
            table-layout="fixed"
            class="geoTable"
          >
            <el-table-column
              prop="equivalent"
              :label="t('trustlines.equivalent')"
              width="110"
            />
            <el-table-column
              prop="from"
              :label="t('trustlines.from')"
              min-width="180"
              show-overflow-tooltip
            >
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.from" />
              </template>
            </el-table-column>
            <el-table-column
              prop="to"
              :label="t('trustlines.to')"
              min-width="180"
              show-overflow-tooltip
            >
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.to" />
              </template>
            </el-table-column>
            <el-table-column
              prop="limit"
              :label="t('trustlines.limit')"
              width="110"
            >
              <template #default="scope">
                {{ money(scope.row.limit) }}
              </template>
            </el-table-column>
            <el-table-column
              prop="available"
              :label="t('trustlines.available')"
              width="110"
            >
              <template #default="scope">
                <span class="bad">{{ money(scope.row.available) }}</span>
              </template>
            </el-table-column>
            <el-table-column
              prop="status"
              :label="t('common.status')"
              width="100"
            />
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card class="geoCard">
          <template #header>
            <div class="hdr">
              <TooltipLabel
                :label="t('dashboard.card.incidentsOverSla')"
                tooltip-key="dashboard.incidentsOverSla"
              />
              <div class="hdr__right">
                <el-button
                  size="small"
                  @click="go('/incidents')"
                >
                  {{ t('common.viewAll') }}
                </el-button>
              </div>
            </div>
          </template>

          <el-alert
            v-if="incidentsError"
            :title="incidentsError"
            type="warning"
            show-icon
            class="mb"
          />
          <el-skeleton
            v-if="incidentsLoading"
            animated
            :rows="6"
          />

          <el-empty
            v-else-if="incidentsOverSla.length === 0"
            :description="t('dashboard.empty.noIncidentsOverSla')"
          />

          <el-table
            v-else
            :data="incidentsOverSla"
            size="small"
            height="360"
            table-layout="fixed"
            class="geoTable"
          >
            <el-table-column
              prop="tx_id"
              :label="t('incidents.columns.txId')"
              min-width="200"
              show-overflow-tooltip
            >
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.tx_id" />
              </template>
            </el-table-column>
            <el-table-column
              prop="state"
              :label="t('incidents.columns.state')"
              width="180"
            />
            <el-table-column
              prop="equivalent"
              :label="t('incidents.columns.equivalent')"
              width="110"
            />
            <el-table-column
              prop="age_seconds"
              :label="t('dashboard.incidents.ageSeconds')"
              width="110"
            >
              <template #default="scope">
                <span class="bad">{{ scope.row.age_seconds }}s</span>
              </template>
            </el-table-column>
            <el-table-column
              prop="sla_seconds"
              :label="t('dashboard.incidents.slaSeconds')"
              width="90"
            />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="geoCard">
      <template #header>
        <div class="hdr">
          <TooltipLabel
            :label="t('dashboard.card.recentAudit')"
            tooltip-key="dashboard.recentAudit"
          />
          <el-button
            size="small"
            @click="go('/audit-log')"
          >
            {{ t('common.viewAll') }}
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="auditError"
        :title="auditError"
        type="warning"
        show-icon
        class="mb"
      />
      <el-skeleton
        v-if="auditLoading"
        animated
        :rows="6"
      />

      <el-table
        v-else
        :data="auditItems"
        size="small"
        height="360"
        table-layout="fixed"
        class="geoTable"
      >
        <el-table-column
          prop="timestamp"
          :label="t('auditLog.timestamp')"
          width="200"
          show-overflow-tooltip
        />
        <el-table-column
          prop="actor_id"
          :label="t('auditLog.actor')"
          width="110"
          show-overflow-tooltip
        >
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.actor_id" />
          </template>
        </el-table-column>
        <el-table-column
          prop="actor_role"
          :label="t('auditLog.role')"
          width="130"
          show-overflow-tooltip
        >
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.actor_role" />
          </template>
        </el-table-column>
        <el-table-column
          prop="action"
          :label="t('auditLog.action')"
          min-width="280"
          show-overflow-tooltip
        >
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.action" />
          </template>
        </el-table-column>
        <el-table-column
          prop="object_type"
          :label="t('auditLog.object')"
          width="140"
          show-overflow-tooltip
        />
        <el-table-column
          prop="object_id"
          :label="t('auditLog.objectId')"
          min-width="360"
          show-overflow-tooltip
        >
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.object_id" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}

.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.hdr__right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag {
  cursor: pointer;
}

.bad {
  color: var(--el-color-danger);
  font-weight: 700;
}
</style>
