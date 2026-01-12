<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { api } from '../api'
import TooltipLabel from '../ui/TooltipLabel.vue'

const loading = ref(false)
const error = ref<string | null>(null)
const status = ref<Record<string, unknown> | null>(null)

const verifyLoading = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    status.value = assertSuccess(await api.integrityStatus())
  } catch (e: any) {
    error.value = e?.message || 'Failed to load integrity status'
  } finally {
    loading.value = false
  }
}

async function verify() {
  try {
    await ElMessageBox.confirm(
      'Start integrity verification now? (mocked action)',
      'Confirm',
      {
        type: 'warning',
        confirmButtonText: 'Start',
        cancelButtonText: 'Cancel',
      },
    )
  } catch {
    return
  }

  verifyLoading.value = true
  try {
    assertSuccess(await api.integrityVerify())
    ElMessage.success('Integrity verify started')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || 'Failed to start integrity verify')
  } finally {
    verifyLoading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <el-card class="geoCard">
    <template #header>
      <div class="hdr">
        <TooltipLabel label="Integrity" tooltip-key="nav.integrity" />
        <el-button :loading="verifyLoading" type="primary" @click="verify">Verify</el-button>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <div v-else>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="Status">{{ status?.status }}</el-descriptions-item>
        <el-descriptions-item label="Last Check">{{ status?.last_check }}</el-descriptions-item>
        <el-descriptions-item label="Alerts">{{ (status as any)?.alerts?.length ?? 0 }}</el-descriptions-item>
        <el-descriptions-item label="Equivalents">{{ Object.keys(((status as any)?.equivalents as any) || {}).length }}</el-descriptions-item>
      </el-descriptions>

      <el-divider />

      <div class="sub">Raw Payload</div>
      <pre class="json">{{ JSON.stringify(status, null, 2) }}</pre>
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
.sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.json {
  margin: 0;
  font-size: 12px;
}
</style>
