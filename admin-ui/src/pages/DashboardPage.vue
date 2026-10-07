<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { describeError } from '../api/describeError'
import { useEquivalentPrecision } from '../composables/useEquivalentPrecision'
import TooltipLabel from '../ui/TooltipLabel.vue'
import TableCellEllipsis from '../ui/TableCellEllipsis.vue'
import ListState from '../ui/ListState.vue'
import { formatTs } from '../utils/datetime'
import type { AuditLogEntry, LiquiditySummary } from '../types/domain'
import { t } from '../i18n'
import { labelParticipantType } from '../i18n/labels'
import { toLocationQueryRaw } from '../router/query'
import { labelParticipantStatus, normalizeParticipantStatusKey } from '../ui/participantStatus'

// 032 S5 (F-3, owner decision 2026-10-07): the Dashboard is the participant counters, one row per equivalent,
// a warning about integrity holds and the latest audit rows. The API/DB/migrations cards (the header already
// polls health, `stores/health.ts`) and the bottlenecks card were removed.

const router = useRouter()
const route = useRoute()

const auditLoading = ref(false)
const auditError = ref<string | null>(null)
const auditItems = ref<AuditLogEntry[]>([])

const participantsStatsLoading = ref(false)
const participantsStatsError = ref<string | null>(null)
const participantsByStatus = ref(new Map<string, number>())
const participantsByType = ref(new Map<string, number>())

function normKey(v: unknown): string {
  return normalizeParticipantStatusKey(v)
}

async function loadParticipantStats() {
  participantsStatsLoading.value = true
  participantsStatsError.value = null
  try {
    const stats = await api.participantsStats()

    const byStatus = new Map<string, number>()
    for (const [k, v] of Object.entries(stats.participants_by_status || {})) {
      const key = normKey(k) || 'unknown'
      byStatus.set(key, v)
    }

    const byType = new Map<string, number>()
    for (const [k, v] of Object.entries(stats.participants_by_type || {})) {
      const key = String(k || '').trim().toLowerCase() || 'unknown'
      byType.set(key, v)
    }

    participantsByStatus.value = byStatus
    participantsByType.value = byType
  } catch (e: unknown) {
    participantsStatsError.value = describeError(e, 'dashboard.participantsStatsLoadFailed').text
  } finally {
    participantsStatsLoading.value = false
  }
}

async function loadAudit() {
  auditLoading.value = true
  auditError.value = null
  try {
    const page = await api.listAuditLog({ page: 1, per_page: 10 })
    auditItems.value = page.items
  } catch (e: unknown) {
    auditError.value = describeError(e, 'auditLog.loadFailed').text
  } finally {
    auditLoading.value = false
  }
}

// D-3: precision comes from the catalogue WITH the inactive equivalents - a stopped equivalent's lines and debts
// still exist, and its row prints its own sums at its own precision, not a dash.
const { equivalents, money, loadEquivalentPrecision } = useEquivalentPrecision()

type EquivalentRow = { code: string; isActive: boolean; summary: LiquiditySummary | null; error: string | null }

const equivalentsLoading = ref(false)
const equivalentsError = ref<string | null>(null)
const equivalentRows = ref<EquivalentRow[]>([])

// One request per equivalent: the server sums money only within one equivalent (028 F-028-37), so the rows are
// never added up across equivalents here either.
async function loadEquivalentRows() {
  equivalentsLoading.value = true
  equivalentsError.value = null
  try {
    await loadEquivalentPrecision()
    const codes = equivalents.value.map((e) => ({ code: String(e.code), isActive: Boolean(e.is_active) }))
    equivalentRows.value = await Promise.all(
      codes.map(async ({ code, isActive }) => {
        try {
          return { code, isActive, summary: await api.liquiditySummary({ equivalent: code }), error: null }
        } catch (e: unknown) {
          return { code, isActive, summary: null, error: describeError(e, 'dashboard.equivalents.loadFailed').text }
        }
      }),
    )
  } catch (e: unknown) {
    equivalentsError.value = describeError(e, 'dashboard.equivalents.loadFailed').text
    equivalentRows.value = []
  } finally {
    equivalentsLoading.value = false
  }
}

function rowMoney(row: EquivalentRow, value: string | null | undefined): string {
  if (!row.summary || value === null || value === undefined) return '—'
  return money(value, row.code)
}

// F-4: the equivalents on an integrity hold (`GET /integrity/summary`); clearing them is the Integrity screen's.
const heldEquivalents = ref<string[]>([])
const holdsError = ref<string | null>(null)

async function loadHolds() {
  holdsError.value = null
  try {
    const res = await api.integritySummary()
    heldEquivalents.value = res.equivalents.filter((e) => e.hold).map((e) => e.equivalent)
  } catch (e: unknown) {
    holdsError.value = describeError(e, 'dashboard.holds.loadFailed').text
    heldEquivalents.value = []
  }
}

function go(path: string) {
  void router.push({ path, query: toLocationQueryRaw({ ...route.query }) })
}

function goParticipantsWithFilter(filter: { status?: string; type?: string }) {
  const q = { ...route.query } as Record<string, unknown>
  if (filter.status) q.status = filter.status
  else delete q.status
  if (filter.type) q.type = filter.type
  else delete q.type
  void router.push({ path: '/participants', query: toLocationQueryRaw(q) })
}

// Four independent requests, one per card: each loader owns its own loading and error state and never throws, so a
// card that fails shows its failure and does not hold back, blank or replace the others.
onMounted(() => {
  void Promise.all([loadAudit(), loadEquivalentRows(), loadHolds(), loadParticipantStats()])
})

const statusRows = computed(() => {
  const order = ['active', 'suspended', 'left', 'deleted', 'unknown']
  const m = participantsByStatus.value
  const extra = [...m.keys()].filter((k) => !order.includes(k)).sort()
  return [...order, ...extra]
    .filter((k) => (m.get(k) || 0) > 0)
    .map((k) => ({ key: k, label: labelParticipantStatus(k) || t('common.unknown'), count: m.get(k) || 0 }))
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
      v-if="heldEquivalents.length"
      type="error"
      show-icon
      :closable="false"
      class="mb"
      data-testid="dashboard-holds"
      :title="t('dashboard.holds.title', { codes: heldEquivalents.join(', ') })"
    >
      <template #default>
        <div class="holdsBody">
          <span>{{ t('dashboard.holds.text') }}</span>
          <el-button
            size="small"
            type="primary"
            data-testid="dashboard-holds-open"
            @click="go('/integrity')"
          >
            {{ t('dashboard.holds.open') }}
          </el-button>
        </div>
      </template>
    </el-alert>
    <el-alert
      v-else-if="holdsError"
      type="warning"
      show-icon
      :closable="false"
      class="mb"
      :title="holdsError"
    />

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
            :closable="false"
            class="mb"
          >
            <template #default>
              <el-button
                size="small"
                type="primary"
                @click="loadParticipantStats"
              >
                {{ t('common.refresh') }}
              </el-button>
            </template>
          </el-alert>
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

    <el-card
      class="geoCard mb"
      data-testid="dashboard-equivalents"
    >
      <template #header>
        <div class="hdr">
          <TooltipLabel
            :label="t('dashboard.card.equivalents')"
            :tooltip-text="t('dashboard.equivalents.help')"
          />
          <el-button
            size="small"
            @click="go('/equivalents')"
          >
            {{ t('common.viewAll') }}
          </el-button>
        </div>
      </template>

      <el-alert
        v-if="equivalentsError"
        :title="equivalentsError"
        type="warning"
        show-icon
        :closable="false"
        class="mb"
      >
        <template #default>
          <el-button
            size="small"
            type="primary"
            @click="loadEquivalentRows"
          >
            {{ t('common.refresh') }}
          </el-button>
        </template>
      </el-alert>
      <el-skeleton
        v-else-if="equivalentsLoading"
        animated
        :rows="3"
      />
      <el-empty
        v-else-if="equivalentRows.length === 0"
        :description="t('dashboard.equivalents.empty')"
      />
      <el-table
        v-else
        :data="equivalentRows"
        size="small"
        table-layout="fixed"
        class="geoTable"
      >
        <el-table-column
          prop="code"
          :label="t('trustlines.equivalent')"
          width="120"
        />
        <el-table-column
          :label="t('common.status')"
          width="130"
        >
          <template #default="scope">
            <el-tag
              :type="scope.row.isActive ? 'success' : 'info'"
              effect="plain"
              size="small"
            >
              {{ scope.row.isActive ? t('dashboard.equivalents.active') : t('dashboard.equivalents.inactive') }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="t('dashboard.equivalents.activeTrustlines')"
          width="140"
        >
          <template #default="scope">
            {{ scope.row.summary ? scope.row.summary.active_trustlines : '—' }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('dashboard.equivalents.totalLimit')"
          min-width="130"
        >
          <template #default="scope">
            {{ rowMoney(scope.row, scope.row.summary?.total_limit) }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('dashboard.equivalents.totalUsed')"
          min-width="130"
        >
          <template #default="scope">
            {{ rowMoney(scope.row, scope.row.summary?.total_used) }}
          </template>
        </el-table-column>
        <el-table-column
          :label="t('dashboard.equivalents.totalAvailable')"
          min-width="130"
        >
          <template #default="scope">
            {{ rowMoney(scope.row, scope.row.summary?.total_available) }}
          </template>
        </el-table-column>
      </el-table>
      <el-alert
        v-for="row in equivalentRows.filter((r) => r.error)"
        :key="row.code"
        :title="`${row.code}: ${row.error}`"
        type="warning"
        show-icon
        :closable="false"
        class="mt"
      />
    </el-card>

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

      <ListState
        :error="auditError"
        :loading="auditLoading"
        :empty="auditItems.length === 0"
        :empty-text="t('auditLog.none')"
        :skeleton-rows="6"
        @retry="loadAudit"
      >
        <el-table
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
          >
            <template #default="scope">
              {{ formatTs(scope.row.timestamp) }}
            </template>
          </el-table-column>
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
      </ListState>
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

.mt {
  margin-top: 8px;
}

.holdsBody {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.tag {
  cursor: pointer;
}
</style>
