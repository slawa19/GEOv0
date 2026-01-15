<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { ApiException, assertSuccess } from '../api/envelope'
import { api } from '../api'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import type { Incident, Trustline } from '../types/domain'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const error = ref<string | null>(null)

const health = ref<Record<string, unknown> | null>(null)
const healthDb = ref<Record<string, unknown> | null>(null)
const migrations = ref<Record<string, unknown> | null>(null)

const auditLoading = ref(false)
const auditError = ref<string | null>(null)
const auditItems = ref<any[]>([])

const threshold = ref('0.10')

const bottlenecksLoading = ref(false)
const bottlenecksError = ref<string | null>(null)
const bottleneckItems = ref<Trustline[]>([])

const incidentsLoading = ref(false)
const incidentsError = ref<string | null>(null)
const incidentsOverSla = ref<Incident[]>([])

async function load() {
  loading.value = true
  error.value = null
  try {
    health.value = assertSuccess(await api.health())
    healthDb.value = assertSuccess(await api.healthDb())
    migrations.value = assertSuccess(await api.migrations())
  } catch (e: any) {
    error.value = e?.message || 'Failed to load'
    ElMessage.error(error.value || 'Failed to load')
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
  } catch (e: any) {
    auditError.value = e?.message || 'Failed to load audit log'
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
      const total = Number((page as any).total)
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
  } catch (e: any) {
    if (e instanceof ApiException) {
      bottlenecksError.value = `${e.message} (${e.status} ${e.code})`
    } else {
      bottlenecksError.value = e?.message || 'Failed to load trustline bottlenecks'
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
  } catch (e: any) {
    if (e instanceof ApiException) {
      incidentsError.value = `${e.message} (${e.status} ${e.code})`
    } else {
      incidentsError.value = e?.message || 'Failed to load incidents'
    }
  } finally {
    incidentsLoading.value = false
  }
}

function go(path: string) {
  void router.push({ path, query: { ...route.query } })
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
})

const statusText = computed(() => String(health.value?.status ?? 'unknown'))

const migrationsCurrent = computed(() => String((migrations.value as any)?.current_revision ?? '—'))
const migrationsHead = computed(() => String((migrations.value as any)?.head_revision ?? '—'))
const migrationsUpToDateLabel = computed(() => {
  const m: any = migrations.value
  if (!m) return 'unknown'
  const cur = m?.current_revision
  const head = m?.head_revision
  if (!cur && !head) return 'unknown'
  return m?.is_up_to_date ? 'yes' : 'no'
})
</script>

<template>
  <div>
    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <el-row :gutter="12" class="mb">
      <el-col :span="8">
        <el-card class="geoCard">
          <template #header>
            <TooltipLabel label="API" tooltip-key="dashboard.api" />
          </template>
          <el-skeleton v-if="loading" animated :rows="3" />
          <div v-else>
            <div><span class="geoLabel">Status:</span> {{ statusText }}</div>
            <div><span class="geoLabel">Version:</span> {{ health?.version }}</div>
            <div><span class="geoLabel">Uptime:</span> {{ health?.uptime_seconds }}s</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card class="geoCard">
          <template #header>
            <TooltipLabel label="DB" tooltip-key="dashboard.db" />
          </template>
          <el-skeleton v-if="loading" animated :rows="3" />
          <div v-else>
            <div><span class="geoLabel">Status:</span> {{ healthDb?.status }}</div>
            <div><span class="geoLabel">Reachable:</span> {{ (healthDb?.db as any)?.reachable }}</div>
            <div><span class="geoLabel">Latency:</span> {{ (healthDb?.db as any)?.latency_ms }}ms</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card class="geoCard">
          <template #header>
            <TooltipLabel label="Migrations" tooltip-key="dashboard.migrations" />
          </template>
          <el-skeleton v-if="loading" animated :rows="3" />
          <div v-else>
            <div><span class="geoLabel">Up to date:</span> {{ migrationsUpToDateLabel }}</div>
            <div><span class="geoLabel">Current:</span> {{ migrationsCurrent }}</div>
            <div><span class="geoLabel">Head:</span> {{ migrationsHead }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="mb">
      <el-col :span="12">
        <el-card class="geoCard">
          <template #header>
            <div class="hdr">
              <TooltipLabel label="Trustline bottlenecks" tooltip-key="dashboard.bottlenecks" />
              <div class="hdr__right">
                <el-input v-model="threshold" size="small" style="width: 120px" placeholder="0.10" />
                <el-button size="small" @click="goTrustlinesWithThreshold()">View all</el-button>
              </div>
            </div>
          </template>

          <el-alert v-if="bottlenecksError" :title="bottlenecksError" type="warning" show-icon class="mb" />
          <el-skeleton v-if="bottlenecksLoading" animated :rows="6" />

          <el-empty v-else-if="bottleneckItems.length === 0" description="No bottlenecks under threshold" />

          <el-table v-else :data="bottleneckItems" size="small" height="360" table-layout="fixed" class="geoTable">
            <el-table-column prop="equivalent" label="Equivalent" width="110" />
            <el-table-column prop="from" label="From" min-width="180" show-overflow-tooltip>
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.from" />
              </template>
            </el-table-column>
            <el-table-column prop="to" label="To" min-width="180" show-overflow-tooltip>
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.to" />
              </template>
            </el-table-column>
            <el-table-column prop="limit" label="Limit" width="110">
              <template #default="scope">{{ money(scope.row.limit) }}</template>
            </el-table-column>
            <el-table-column prop="available" label="Available" width="110">
              <template #default="scope">
                <span class="bad">{{ money(scope.row.available) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="Status" width="100" />
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card class="geoCard">
          <template #header>
            <div class="hdr">
              <TooltipLabel label="Incidents over SLA" tooltip-key="dashboard.incidentsOverSla" />
              <div class="hdr__right">
                <el-button size="small" @click="go('/incidents')">View all</el-button>
              </div>
            </div>
          </template>

          <el-alert v-if="incidentsError" :title="incidentsError" type="warning" show-icon class="mb" />
          <el-skeleton v-if="incidentsLoading" animated :rows="6" />

          <el-empty v-else-if="incidentsOverSla.length === 0" description="No incidents over SLA" />

          <el-table v-else :data="incidentsOverSla" size="small" height="360" table-layout="fixed" class="geoTable">
            <el-table-column prop="tx_id" label="Tx ID" min-width="200" show-overflow-tooltip>
              <template #default="scope">
                <TableCellEllipsis :text="scope.row.tx_id" />
              </template>
            </el-table-column>
            <el-table-column prop="state" label="State" width="180" />
            <el-table-column prop="equivalent" label="Equivalent" width="110" />
            <el-table-column prop="age_seconds" label="Age (s)" width="110">
              <template #default="scope">
                <span class="bad">{{ scope.row.age_seconds }}s</span>
              </template>
            </el-table-column>
            <el-table-column prop="sla_seconds" label="SLA (s)" width="90" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="geoCard">
      <template #header>
        <div class="hdr">
          <TooltipLabel label="Recent Audit Log" tooltip-key="dashboard.recentAudit" />
          <el-button size="small" @click="go('/audit-log')">View all</el-button>
        </div>
      </template>

      <el-alert v-if="auditError" :title="auditError" type="warning" show-icon class="mb" />
      <el-skeleton v-if="auditLoading" animated :rows="6" />

      <el-table v-else :data="auditItems" size="small" height="360" table-layout="fixed" class="geoTable">
        <el-table-column prop="timestamp" label="Timestamp" width="200" show-overflow-tooltip />
        <el-table-column prop="actor_id" label="Actor" width="110" show-overflow-tooltip>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.actor_id" />
          </template>
        </el-table-column>
        <el-table-column prop="actor_role" label="Role" width="130" show-overflow-tooltip>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.actor_role" />
          </template>
        </el-table-column>
        <el-table-column prop="action" label="Action" min-width="280" show-overflow-tooltip>
          <template #default="scope">
            <TableCellEllipsis :text="scope.row.action" />
          </template>
        </el-table-column>
        <el-table-column prop="object_type" label="Object" width="140" show-overflow-tooltip />
        <el-table-column prop="object_id" label="Object ID" min-width="360" show-overflow-tooltip>
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

.bad {
  color: var(--el-color-danger);
  font-weight: 700;
}
</style>
