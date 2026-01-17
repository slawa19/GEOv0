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
    <div class="navRow__fieldGroup">
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
        size="small"
        :disabled="props.canFind === false"
        @click="onFocusSearch"
      >
        {{ t('graph.navigate.find') }}
      </el-button>
    </div>
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

.navRow__fieldGroup {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.navRow__field {
  flex: 1 1 auto;
  min-width: 0;
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

  .navRow__fieldGroup {
    flex-wrap: wrap;
  }
}
</style>
