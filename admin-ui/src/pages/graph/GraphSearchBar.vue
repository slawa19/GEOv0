<script setup lang="ts">
import TooltipLabel from '../../ui/TooltipLabel.vue'
import { t } from '../../i18n'

type ParticipantSuggestion = {
  value: string
  pid: string
}

type FetchSuggestionsFn = (query: string, cb: (results: ParticipantSuggestion[]) => void) => void

const searchQuery = defineModel<string>('searchQuery', { required: true })
const focusPid = defineModel<string>('focusPid', { required: true })

defineProps<{
  canFind?: boolean
  /** The autocomplete calls this with a callback; it is a function by the contract of that component. */
  fetchSuggestions: FetchSuggestionsFn
}>()

const emit = defineEmits<{ focusSearch: [] }>()

const onSelect = (s: ParticipantSuggestion) => {
  focusPid.value = String(s?.pid || '')
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
      v-model="searchQuery"
      :fetch-suggestions="fetchSuggestions"
      :placeholder="t('graph.search.placeholder')"
      size="small"
      clearable
      class="navRow__field"
      data-testid="graph-search-input"
      @select="onSelect"
      @keyup.enter="emit('focusSearch')"
    />
    <el-button
      class="navRow__button"
      size="small"
      :disabled="canFind === false"
      @click="emit('focusSearch')"
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
  min-width: 0;
}

.navRow__label {
  width: var(--geo-nav-label-w, 84px);
  flex: 0 0 auto;
}

.navRow__field {
  flex: 1 1 320px;
  width: auto;
  max-width: 420px;
  min-width: 0;
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
