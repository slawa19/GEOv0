<script setup lang="ts">
import { t } from '../i18n'

type Props = {
  title: string
  busy?: boolean
}

defineProps<Props>()

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <el-alert
    :title="title"
    type="error"
    show-icon
    :closable="false"
    class="mb"
  >
    <template #default>
      <div class="row">
        <div class="hint">
          {{ t('common.refreshHint') }}
        </div>
        <el-button
          size="small"
          type="primary"
          :loading="Boolean(busy)"
          @click="emit('retry')"
        >
          {{ t('common.refresh') }}
        </el-button>
      </div>
    </template>
  </el-alert>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-sub);
}
</style>
