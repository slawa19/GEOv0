<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'
import { formatDecimalFixed, isRatioBelowThreshold } from '../utils/decimal'
import { formatIsoInTimeZone } from '../utils/datetime'
import TooltipLabel from '../ui/TooltipLabel.vue'
import { useConfigStore } from '../stores/config'

type Trustline = {
  equivalent: string
  from: string
  to: string
  from_display_name?: string | null
  to_display_name?: string | null
  limit: string
  used: string
  available: string
  status: string
  created_at: string
  policy: Record<string, unknown>
}

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
      await mockApi.listTrustlines({
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

onMounted(() => void load())
watch(page, () => void load())
watch(perPage, () => {
  page.value = 1
  void load()
})
watch([equivalent, creditor, debtor, status, threshold], () => {
  page.value = 1
  void load()
})

const statusOptions = computed(() => [
  { label: 'Any', value: '' },
  { label: 'active', value: 'active' },
  { label: 'frozen', value: 'frozen' },
  { label: 'closed', value: 'closed' },
])
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Trustlines</div>
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
      <el-table :data="items" size="small" @row-click="openRow">
        <el-table-column prop="equivalent" width="90">
          <template #header><TooltipLabel label="eq" tooltip-key="trustlines.eq" /></template>
        </el-table-column>
        <el-table-column prop="from" min-width="210">
          <template #header><TooltipLabel label="from" tooltip-key="trustlines.from" /></template>
        </el-table-column>
        <el-table-column prop="to" min-width="210">
          <template #header><TooltipLabel label="to" tooltip-key="trustlines.to" /></template>
        </el-table-column>
        <el-table-column prop="limit" width="120">
          <template #header><TooltipLabel label="limit" tooltip-key="trustlines.limit" /></template>
          <template #default="scope">{{ money(scope.row.limit) }}</template>
        </el-table-column>
        <el-table-column prop="used" width="120">
          <template #header><TooltipLabel label="used" tooltip-key="trustlines.used" /></template>
          <template #default="scope">{{ money(scope.row.used) }}</template>
        </el-table-column>
        <el-table-column prop="available" width="120">
          <template #header><TooltipLabel label="available" tooltip-key="trustlines.available" /></template>
          <template #default="scope">
            <span :class="{ bottleneck: isBottleneck(scope.row) }">{{ money(scope.row.available) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" width="110">
          <template #header><TooltipLabel label="status" tooltip-key="trustlines.status" /></template>
        </el-table-column>
        <el-table-column prop="created_at" width="190">
          <template #header><TooltipLabel label="created_at" tooltip-key="trustlines.createdAt" /></template>
          <template #default="scope">{{ fmtTs(scope.row.created_at) }}</template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <div class="pager__hint">Showing {{ items.length }} / {{ perPage }} on this page</div>
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

  <el-drawer v-model="drawerOpen" title="Trustline details" size="40%">
    <div v-if="selected">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="equivalent">{{ selected.equivalent }}</el-descriptions-item>
        <el-descriptions-item label="from">{{ selected.from }}</el-descriptions-item>
        <el-descriptions-item label="to">{{ selected.to }}</el-descriptions-item>
        <el-descriptions-item label="limit">{{ money(selected.limit) }}</el-descriptions-item>
        <el-descriptions-item label="used">{{ money(selected.used) }}</el-descriptions-item>
        <el-descriptions-item label="available">{{ money(selected.available) }}</el-descriptions-item>
        <el-descriptions-item label="status">{{ selected.status }}</el-descriptions-item>
        <el-descriptions-item label="created_at">{{ fmtTs(selected.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="policy">
          <pre class="json">{{ JSON.stringify(selected.policy, null, 2) }}</pre>
        </el-descriptions-item>
      </el-descriptions>
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
</style>
