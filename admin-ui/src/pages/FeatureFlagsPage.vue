<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'

type FlagRow = { key: string; value: boolean; original: boolean }

const loading = ref(false)
const error = ref<string | null>(null)
const savingKey = ref<string | null>(null)

const rows = ref<FlagRow[]>([])

async function load() {
  loading.value = true
  error.value = null
  try {
    const flags = assertSuccess(await mockApi.getFeatureFlags())
    rows.value = Object.entries(flags)
      .filter(([, v]) => typeof v === 'boolean')
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([k, v]) => ({ key: k, value: v as boolean, original: v as boolean }))
  } catch (e: any) {
    error.value = e?.message || 'Failed to load feature flags'
  } finally {
    loading.value = false
  }
}

const dirtyCount = computed(() => rows.value.filter((r) => r.value !== r.original).length)

const fullMultipathEnabled = computed(() => rows.value.find((r) => r.key === 'full_multipath_enabled')?.value === true)

async function persistRow(row: FlagRow) {
  if (row.value === row.original) return

  const previous = row.original

  try {
    await ElMessageBox.confirm(`Set ${row.key} = ${row.value}?`, 'Confirm', {
      type: 'warning',
      confirmButtonText: 'Apply',
      cancelButtonText: 'Cancel',
    })
  } catch {
    row.value = previous
    return
  }

  savingKey.value = row.key
  try {
    assertSuccess(await mockApi.patchFeatureFlags({ [row.key]: row.value }))
    row.original = row.value
    ElMessage.success('Updated')
  } catch (e: any) {
    row.value = row.original
    ElMessage.error(e?.message || 'Update failed')
  } finally {
    savingKey.value = null
  }
}

onMounted(() => void load())
</script>

<template>
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Feature Flags</div>
        <el-tag type="info">dirty: {{ dirtyCount }}</el-tag>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-alert
      v-else-if="fullMultipathEnabled"
      title="Experimental: full_multipath_enabled is ON"
      type="warning"
      show-icon
      class="mb"
    />
    <el-skeleton v-if="loading" animated :rows="10" />

    <el-empty v-else-if="rows.length === 0" description="No boolean flags in dataset" />

    <div v-else>
      <el-table :data="rows" size="small">
        <el-table-column prop="key" label="flag" min-width="320" />
        <el-table-column label="value" width="180">
          <template #default="scope">
            <el-switch v-model="scope.row.value" @change="persistRow(scope.row)" />
          </template>
        </el-table-column>
        <el-table-column label="status" width="160">
          <template #default="scope">
            <el-tag v-if="savingKey === scope.row.key" type="info">saving...</el-tag>
            <el-tag v-else-if="scope.row.value !== scope.row.original" type="warning">pending</el-tag>
            <el-tag v-else type="success">synced</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-card>
</template>

<style scoped>
.hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.mb {
  margin-bottom: 12px;
}
</style>
