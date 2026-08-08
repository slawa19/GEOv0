<script setup lang="ts">
import { t } from '../../i18n'
import type { GraphElementOption } from '../../composables/useGraphVisualization'

defineProps<{
  options: GraphElementOption[]
  busy: boolean
  unavailable: boolean
}>()

const selectedKey = defineModel<string>({ required: true })
const emit = defineEmits<{ open: [] }>()
</script>

<template>
  <div class="keyboardNavigator">
    <label
      class="keyboardNavigator__label"
      for="graph-element-select"
    >
      {{ t('graph.keyboard.label') }}
    </label>
    <el-select
      id="graph-element-select"
      v-model="selectedKey"
      filterable
      :disabled="busy || unavailable"
      :placeholder="t('graph.keyboard.placeholder')"
      :aria-label="t('graph.keyboard.label')"
      data-testid="graph-element-select"
      class="keyboardNavigator__select"
    >
      <el-option
        v-for="option in options"
        :key="option.key"
        :label="option.label"
        :value="option.key"
      />
    </el-select>
    <el-button
      :disabled="busy || unavailable || !selectedKey"
      data-testid="graph-element-open"
      @click="emit('open')"
    >
      {{ t('graph.keyboard.openDetails') }}
    </el-button>
  </div>
</template>

<style scoped>
.keyboardNavigator {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.keyboardNavigator__label {
  flex: 0 0 auto;
  color: var(--el-text-color-secondary);
  font-size: var(--geo-font-size-label);
  font-weight: var(--geo-font-weight-label);
}

.keyboardNavigator__select {
  width: min(560px, 100%);
}

@media (max-width: 768px) {
  .keyboardNavigator {
    align-items: stretch;
    flex-direction: column;
  }

  .keyboardNavigator__select {
    width: 100%;
  }
}
</style>
