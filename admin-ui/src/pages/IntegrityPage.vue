<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { assertSuccess } from '../api/envelope'
import { mockApi } from '../api/mockApi'

const loading = ref(false)
const error = ref<string | null>(null)
const status = ref<Record<string, unknown> | null>(null)

const verifyLoading = ref(false)

async function load() {
  loading.value = true
  error.value = null
  try {
    status.value = assertSuccess(await mockApi.integrityStatus())
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
    assertSuccess(await mockApi.integrityVerify())
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
  <el-card>
    <template #header>
      <div class="hdr">
        <div>Integrity</div>
        <el-button :loading="verifyLoading" type="primary" @click="verify">Verify</el-button>
      </div>
    </template>

    <el-alert v-if="error" :title="error" type="error" show-icon class="mb" />
    <el-skeleton v-if="loading" animated :rows="10" />

    <div v-else>
      <el-descriptions :column="2" border>
        <el-descriptions-item label="status">{{ status?.status }}</el-descriptions-item>
        <el-descriptions-item label="last_checked_at">{{ status?.last_checked_at }}</el-descriptions-item>
        <el-descriptions-item label="checks_total">{{ status?.checks_total }}</el-descriptions-item>
        <el-descriptions-item label="checks_failed">{{ status?.checks_failed }}</el-descriptions-item>
      </el-descriptions>

      <el-divider />

      <div class="sub">Raw payload</div>
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
