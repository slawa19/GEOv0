<script setup lang="ts">
import { computed } from 'vue'
import TooltipLabel from '../../ui/TooltipLabel.vue'
import { t } from '../../i18n'

type ParticipantSuggestion = {
  value: string
  pid: string
}

type FetchSuggestionsFn = (query: string, cb: (results: ParticipantSuggestion[]) => void) => void

type Props = {
  searchQuery: string
  focusPid: string
  canFind?: boolean
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
        :max-lines="4"
    />
    <el-autocomplete
      v-model="searchQueryModel"
      :fetch-suggestions="fetchSuggestions"
      :placeholder="t('graph.search.placeholder')"
      size="small"
      clearable
      class="navRow__field"
      data-testid="graph-search-input"
      @select="onSelect"
      @keyup.enter="onFocusSearch"
    />
    <el-button
      class="navRow__button"
      size="small"
      :disabled="props.canFind === false"
      @click="onFocusSearch"
    >
      {{ t('graph.navigate.find') }}
    </el-button>
  </div>
</template>

<style scoped>
.toolbarLabel {
  font-size: var(--geo-font-size-label);
  font-weight: var(--geo-font-weight-label);
  color: var(--el-text-color-secondary);
}

.navRow {
  display: flex;
  flex-wrap: nowrap;
  align-items: center;
  gap: 8px;
}

.navRow__label {
  width: var(--geo-nav-label-w, 84px);
  flex: 0 0 auto;
}

.navRow__field {
  width: 320px;
  flex: 0 0 auto;
}

.navRow__button {
  white-space: nowrap;
  flex: 0 0 auto;
}

.navRow__field :deep(.el-input),
.navRow__field :deep(.el-input__wrapper) {
  width: 100%;
}

@media (max-width: 768px) {
  .navRow {
    flex-direction: column;
    align-items: stretch;
  }

  .navRow__label {
    width: auto;
  }

  .navRow__field {
    width: 100% !important;
  }

  .navRow__button {
    align-self: flex-start;
  }
}
</style>
