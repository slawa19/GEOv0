<script setup lang="ts">
/**
 * BUG-5: Interact Mode History Log
 * Displays the last N user actions in a compact inline list. 046 wave 3: a line is drawn from its kept key and data in
 * the language of now (BACKLOG 046-3) - a switch re-renders the lines already shown.
 */
import { computed } from 'vue'
import { historyEntryText, type InteractHistoryEntry } from '../composables/interact/useInteractHistory'
import { useUiLocale } from '../composables/useUiLocale'

const props = withDefaults(
  defineProps<{
    entries: InteractHistoryEntry[]
    /** Max visible entries (default 8). */
    maxVisible?: number
  }>(),
  { maxVisible: 8 },
)
const { t, locale } = useUiLocale()

const visible = computed(() => {
  // Show most recent entries at the top
  const e = props.entries
  return e.slice(Math.max(0, e.length - props.maxVisible)).reverse()
})
</script>

<template>
  <div v-if="entries.length > 0" class="interact-history" :aria-label="t('recentActions.aria')" data-testid="recent-actions">
    <div class="interact-history__header ds-label">{{ t('recentActions.title') }}</div>
    <ul class="interact-history__list">
      <li
        v-for="(entry, i) in visible"
        :key="entry.id"
        class="interact-history__item"
        :class="{ 'interact-history__item--latest': i === 0 }"
      >
        <span class="interact-history__icon" aria-hidden="true">{{ entry.icon }}</span>
        <span class="interact-history__text" :title="historyEntryText(entry, locale)">{{ historyEntryText(entry, locale) }}</span>
        <span class="interact-history__time ds-mono">{{ entry.timeText }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
/*
  049 S1 (decisions 4 and 9): the history stands at the right edge of the screen (on the control line, placed by
  `SimulatorAppRoot.vue`), so its content is aligned to that side - the heading and every line end on the line, a shorter
  line starts further right. A long line is cut by an ellipsis inside a bounded width, its whole text is its `title`.
*/
.interact-history {
  pointer-events: none;
  user-select: none;
  max-width: min(28rem, 45vw);
}

.interact-history__header {
  font-size: var(--ds-typo-section-label-font-size);
  opacity: var(--ds-ihl-header-opacity);
  text-transform: uppercase;
  letter-spacing: var(--ds-typo-section-label-letter-spacing);
  margin-bottom: var(--ds-ihl-header-margin-bottom);
  text-align: right;
}

.interact-history__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ds-ihl-list-gap);
}

.interact-history__item {
  display: flex;
  justify-content: flex-end;
  align-items: baseline;
  gap: var(--ds-ihl-item-gap);
  font-size: var(--ds-ihl-item-font-size);
  opacity: var(--ds-ihl-item-opacity);
  transition: opacity var(--ds-dur-slow);
}

.interact-history__item--latest {
  opacity: 1;
}

.interact-history__icon {
  flex-shrink: 0;
  font-size: var(--ds-ihl-icon-font-size);
  min-width: var(--ds-ihl-icon-minw);
  text-align: center;
}

.interact-history__text {
  flex: 0 1 auto;
  min-width: 0;
  /* The one part of the history a pointer reaches: the `title` of a cut line (decision 9). */
  pointer-events: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.interact-history__time {
  flex-shrink: 0;
  font-size: var(--ds-ihl-time-font-size);
  opacity: var(--ds-ihl-time-opacity);
}
</style>
