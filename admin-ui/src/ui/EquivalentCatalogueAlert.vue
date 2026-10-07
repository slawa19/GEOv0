<script setup lang="ts">
import { t } from '../i18n'

/**
 * The catalogue of equivalents (the only source of a money precision) failed to load (033 B, item 3): says so with the
 * request ref, and offers to read it again. Until it answers, the sums on the screen stay '—'.
 */
defineProps<{
  /** Text of the failure, with the request ref (`describeError`). */
  error: string
  busy?: boolean
}>()

const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <el-alert
    data-testid="equivalent-catalogue-error"
    :title="t('money.catalogueLoadFailed', { error })"
    type="error"
    show-icon
    :closable="false"
    class="mb"
  >
    <el-button
      size="small"
      type="primary"
      data-testid="equivalent-catalogue-retry"
      :loading="Boolean(busy)"
      :disabled="Boolean(busy)"
      @click="emit('retry')"
    >
      {{ t('common.retry') }}
    </el-button>
  </el-alert>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}
</style>
