<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRoute, useRouter } from 'vue-router'
import { ApiException, assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import TooltipLabel from '../ui/TooltipLabel.vue'

type Trustline = {
  equivalent: string
  from: string
  to: string
  limit: string
  available: string
  used: string
  status: string
  created_at: string
}

type Incident = {
  tx_id: string
  state: string
  initiator_pid: string
  equivalent: string
  age_seconds: number
  sla_seconds: number
  created_at?: string
}

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
    health.value = assertSuccess(await mockApi.health())
    healthDb.value = assertSuccess(await mockApi.healthDb())
    migrations.value = assertSuccess(await mockApi.migrations())
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
    const page = assertSuccess(await mockApi.listAuditLog({ page: 1, per_page: 10 }))
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
    const page = assertSuccess(await mockApi.listTrustlines({ page: 1, per_page: 250 }))
    const all = page.items as Trustline[]

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
    const page = assertSuccess(await mockApi.listIncidents({ page: 1, per_page: 200 }))
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

onMounted(() => {
  void load()
  void loadAudit()
  void loadBottlenecks()
  void loadIncidents()
})

const statusText = computed(() => String(health.value?.status ?? 'unknown'))
</script>

<template>
  <div>
    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />

    <el-row :gutter="12" class="mb">
      <el-col :span="8">
        <el-card>
          <template #header>
            <TooltipLabel label="API" tooltip-key="dashboard.api" />
          </template>
          <el-skeleton v-if="loading" animated :rows="3" />
          <div v-else>
            <div><b>Status:</b> {{ statusText }}</div>
            <div><b>Version:</b> {{ health?.version }}</div>
            <div><b>Uptime:</b> {{ health?.uptime_seconds }}s</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card>
          <template #header>
            <TooltipLabel label="DB" tooltip-key="dashboard.db" />
          </template>
          <el-skeleton v-if="loading" animated :rows="3" />
          <div v-else>
            <div><b>Status:</b> {{ healthDb?.status }}</div>
            <div><b>Reachable:</b> {{ (healthDb?.db as any)?.reachable }}</div>
            <div><b>Latency:</b> {{ (healthDb?.db as any)?.latency_ms }}ms</div>
          </div>
        </el-card>
      </el-col>

      <el-col :span="8">
        <el-card>
          <template #header>
            <TooltipLabel label="Migrations" tooltip-key="dashboard.migrations" />
          </template>
          <el-skeleton v-if="loading" animated :rows="3" />
          <div v-else>
            <div><b>Up to date:</b> {{ migrations?.is_up_to_date }}</div>
            <div><b>Current:</b> {{ migrations?.current }}</div>
            <div><b>Head:</b> {{ migrations?.head }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="mb">
      <el-col :span="12">
        <el-card>
          <template #header>
            <div class="hdr">
              <TooltipLabel label="Trustline bottlenecks" tooltip-key="dashboard.bottlenecks" />
              <div class="hdr__right">
                <el-input v-model="threshold" size="small" style="width: 120px" placeholder="0.10" />
                <el-button size="small" @click="go('/trustlines')">View all</el-button>
              </div>
            </div>
          </template>

          <el-alert v-if="bottlenecksError" :title="bottlenecksError" type="warning" show-icon class="mb" />
          <el-skeleton v-if="bottlenecksLoading" animated :rows="6" />

          <el-empty v-else-if="bottleneckItems.length === 0" description="No bottlenecks under threshold" />

          <el-table v-else :data="bottleneckItems" size="small" height="360">
            <el-table-column prop="equivalent" label="eq" width="80" />
            <el-table-column prop="from" label="from" min-width="180" />
            <el-table-column prop="to" label="to" min-width="180" />
            <el-table-column prop="limit" label="limit" width="110">
              <template #default="scope">{{ money(scope.row.limit) }}</template>
            </el-table-column>
            <el-table-column prop="available" label="avail" width="110">
              <template #default="scope">
                <span class="bad">{{ money(scope.row.available) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="status" label="status" width="100" />
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="12">
        <el-card>
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

          <el-table v-else :data="incidentsOverSla" size="small" height="360">
            <el-table-column prop="tx_id" label="tx_id" min-width="200" />
            <el-table-column prop="state" label="state" width="180" />
            <el-table-column prop="equivalent" label="eq" width="70" />
            <el-table-column prop="age_seconds" label="age" width="110">
              <template #default="scope">
                <span class="bad">{{ scope.row.age_seconds }}s</span>
              </template>
            </el-table-column>
            <el-table-column prop="sla_seconds" label="sla" width="90" />
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card>
      <template #header>
        <div class="hdr">
          <TooltipLabel label="Recent audit log" tooltip-key="dashboard.recentAudit" />
          <el-button size="small" @click="go('/audit-log')">View all</el-button>
        </div>
      </template>

      <el-alert v-if="auditError" :title="auditError" type="warning" show-icon class="mb" />
      <el-skeleton v-if="auditLoading" animated :rows="6" />

      <el-table v-else :data="auditItems" size="small" height="360">
        <el-table-column prop="timestamp" label="timestamp" width="190" />
        <el-table-column prop="actor_id" label="actor" width="160" />
        <el-table-column prop="actor_role" label="role" width="120" />
        <el-table-column prop="action" label="action" />
        <el-table-column prop="object_type" label="object" width="140" />
        <el-table-column prop="object_id" label="object_id" />
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
