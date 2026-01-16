<script setup lang="ts">
import { computed } from 'vue'
import TooltipLabel from '../../ui/TooltipLabel.vue'
import { t } from '../../i18n/en'

type ParticipantSuggestion = {
  value: string
  pid: string
}

type FetchSuggestionsFn = (query: string, cb: (results: ParticipantSuggestion[]) => void) => void

type Props = {
  searchQuery: string
  focusPid: string
  fetchSuggestions: FetchSuggestionsFn
  onFocusSearch: () => void
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:searchQuery', v: string): void
  (e: 'update:focusPid', v: string): void
}>()

const searchQueryModel = computed({
  get: () => props.searchQuery,
  set: (v) => emit('update:searchQuery', v),
})

const onSelect = (s: ParticipantSuggestion) => {
  emit('update:focusPid', String(s?.pid || ''))
}
</script>

<template>
  <div class="navRow navRow--search">
    <TooltipLabel
      class="toolbarLabel navRow__label"
      :label="t('graph.search.label')"
      tooltip-key="graph.search"
    />
    <el-autocomplete
      v-model="searchQueryModel"
      :fetch-suggestions="fetchSuggestions"
      :placeholder="t('graph.search.placeholder')"
      size="small"
      clearable
      class="navRow__field"
      @select="onSelect"
      @keyup.enter="onFocusSearch"
    />
  </div>
</template>

<style scoped>
.toolbarLabel {
  font-size: var(--geo-font-size-label);
  font-weight: var(--geo-font-weight-label);
  color: var(--el-text-color-secondary);
}

.navRow {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 10px;
  align-items: center;
}

.navRow__label {
  min-width: 76px;
}

.navRow__field :deep(.el-autocomplete),
.navRow__field :deep(.el-input),
.navRow__field :deep(.el-input__wrapper) {
  width: 100%;
}

@media (max-width: 768px) {
  .navRow {
    grid-template-columns: 1fr;
    align-items: start;
  }

  .navRow__label {
    min-width: 0;
  }
}
</style>
