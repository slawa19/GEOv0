<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import { formatIsoInTimeZone } from '../utils/datetime'
import TooltipLabel from '../ui/TooltipLabel.vue'
import { useConfigStore } from '../stores/config'
import { debounce } from '../utils/debounce'
import type { Trustline } from '../types/domain'

const router = useRouter()
const route = useRoute()

const loading = ref(false)
const error = ref<string | null>(null)

const equivalent = ref('')
const creditor = ref('')
const debtor = ref('')
const status = ref('')
const threshold = ref('0.10')

const page = ref(1)
const perPage = ref(20)
const total = ref(0)
const items = ref<Trustline[]>([])

const drawerOpen = ref(false)
const selected = ref<Trustline | null>(null)

const configStore = useConfigStore()
const timeZone = computed(() => String(configStore.config['ui.timezone'] || 'UTC'))

function isBottleneck(row: Trustline): boolean {
  return isRatioBelowThreshold({ numerator: row.available, denominator: row.limit, threshold: threshold.value })
}

function money(v: string): string {
  return formatDecimalFixed(v, 2)
}

function fmtTs(iso: string): string {
  return formatIsoInTimeZone(iso, timeZone.value)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const data = assertSuccess(
      await api.listTrustlines({
        page: page.value,
        per_page: perPage.value,
        equivalent: equivalent.value || undefined,
        creditor: creditor.value || undefined,
        debtor: debtor.value || undefined,
        status: status.value || undefined,
      }),
    )
    total.value = data.total
    const maxPage = Math.max(1, Math.ceil(total.value / perPage.value))
    if (page.value > maxPage) {
      page.value = maxPage
      return
    }
    items.value = data.items
  } catch (e: any) {
    error.value = e?.message || 'Failed to load trustlines'
    ElMessage.error(error.value || 'Failed to load trustlines')
  } finally {
    loading.value = false
  }
}

function openRow(row: Trustline) {
  selected.value = row
  drawerOpen.value = true
}

function goParticipant(pid: string) {
  void router.push({ path: '/participants', query: { ...route.query, q: pid } })
}

function goEquivalent(eq: string) {
  void router.push({ path: '/equivalents', query: { ...route.query, q: eq } })
}

onMounted(() => void load())
watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})

const debouncedReload = debounce(() => {
  page.value = 1
  void load()
}, 250)

// NOTE: threshold is a UI-only highlight knob; do not reload the list when it changes.
watch([equivalent, creditor, debtor, status], () => {
  debouncedReload()
})

const statusOptions = computed(() => [
  { label: 'Any', value: '' },
  { label: 'active', value: 'active' },
  { label: 'frozen', value: 'frozen' },
  { label: 'closed', value: 'closed' },
])
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Trustlines" tooltip-key="nav.trustlines" />
        <div class="filters">
          <el-input v-model="equivalent" size="small" placeholder="Equivalent (e.g. UAH)" clearable style="width: 170px" />
          <el-input v-model="creditor" size="small" placeholder="Creditor PID (from)" clearable style="width: 220px" />
          <el-input v-model="debtor" size="small" placeholder="Debtor PID (to)" clearable style="width: 220px" />
          <el-select v-model="status" size="small" style="width: 140px">
            <el-option v-for="o in statusOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-input v-model="threshold" size="small" placeholder="Threshold" style="width: 110px" />
        </div>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="items.length === 0" description="No trustlines match filters" />

    <div v-else>
      <el-table :data="items" size="small" @row-click="openRow" class="geoTable">
        <el-table-column prop="equivalent" width="120">
          <template #header><TooltipLabel label="Equivalent" tooltip-key="trustlines.eq" /></template>
        </el-table-column>
        <el-table-column prop="from" min-width="210">
          <template #header><TooltipLabel label="From" tooltip-key="trustlines.from" /></template>
        </el-table-column>
        <el-table-column prop="to" min-width="210">
          <template #header><TooltipLabel label="To" tooltip-key="trustlines.to" /></template>
        </el-table-column>
        <el-table-column prop="limit" width="120">
          <template #header><TooltipLabel label="Limit" tooltip-key="trustlines.limit" /></template>
          <template #default="scope">{{ money(scope.row.limit) }}</template>
        </el-table-column>
        <el-table-column prop="used" width="120">
          <template #header><TooltipLabel label="Used" tooltip-key="trustlines.used" /></template>
          <template #default="scope">{{ money(scope.row.used) }}</template>
        </el-table-column>
        <el-table-column prop="available" width="120">
          <template #header><TooltipLabel label="Available" tooltip-key="trustlines.available" /></template>
          <template #default="scope">
            <span :class="{ bottleneck: isBottleneck(scope.row) }">{{ money(scope.row.available) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" width="110">
          <template #header><TooltipLabel label="Status" tooltip-key="trustlines.status" /></template>
        </el-table-column>
        <el-table-column prop="created_at" width="190">
          <template #header><TooltipLabel label="Created at" tooltip-key="trustlines.createdAt" /></template>
          <template #default="scope">{{ fmtTs(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint geoHint">Showing {{ items.length }} / {{ perPage }} on this page</div>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="perPage"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          :total="total"
          background
        />
      </div>
    </div>
  </el-card>

  <el-drawer v-model="drawerOpen" title="Trustline details" size="45%">
    <div v-if="selected">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="Equivalent">
          <el-link type="primary" @click="goEquivalent(selected.equivalent)">
            {{ selected.equivalent }}
          </el-link>
        </el-descriptions-item>
        <el-descriptions-item label="From (Creditor)">
          <el-link type="primary" @click="goParticipant(selected.from)">
            {{ selected.from }}
          </el-link>
          <span v-if="selected.from_display_name" class="display-name">
            ({{ selected.from_display_name }})
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="To (Debtor)">
          <el-link type="primary" @click="goParticipant(selected.to)">
            {{ selected.to }}
          </el-link>
          <span v-if="selected.to_display_name" class="display-name">
            ({{ selected.to_display_name }})
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="Limit">{{ money(selected.limit) }}</el-descriptions-item>
        <el-descriptions-item label="Used">{{ money(selected.used) }}</el-descriptions-item>
        <el-descriptions-item label="Available">
          <span :class="{ bottleneck: isBottleneck(selected) }">{{ money(selected.available) }}</span>
          <el-tag v-if="isBottleneck(selected)" type="danger" size="small" style="margin-left: 8px">Bottleneck</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Status">
          <el-tag
            :type="selected.status === 'active' ? 'success' : selected.status === 'frozen' ? 'warning' : 'info'"
            size="small"
          >
            {{ selected.status }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="Created At">{{ fmtTs(selected.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="Policy">
          <pre class="json">{{ JSON.stringify(selected.policy, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>Related participants</el-divider>

      <div class="drawer-actions">
        <el-button type="primary" size="small" @click="goParticipant(selected.from)">
          View creditor (from)
        </el-button>
        <el-button type="primary" size="small" @click="goParticipant(selected.to)">
          View debtor (to)
        </el-button>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.filters {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.mb {
  margin-bottom: 12px;
}
.pager {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
.pager__hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.bottleneck {
  color: var(--el-color-danger);
  font-weight: 700;
}
.json {
  margin: 0;
  font-size: 12px;
}
.display-name {
  color: var(--el-text-color-secondary);
  margin-left: 4px;
}
.drawer-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}
</style>
