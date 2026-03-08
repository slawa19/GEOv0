<script setup lang="ts">
/**
 * BUG-5: Interact Mode History Log
 * Displays the last N user actions in a compact inline list.
 */
import { computed } from 'vue'
import type { InteractHistoryEntry } from '../composables/interact/useInteractHistory'

const props = withDefaults(
  defineProps<{
    entries: InteractHistoryEntry[]
    /** Max visible entries (default 8). */
    maxVisible?: number
  }>(),
  { maxVisible: 8 },
)

const visible = computed(() => {
  // Show most recent entries at the top
  const e = props.entries
  return e.slice(Math.max(0, e.length - props.maxVisible)).reverse()
})
</script>

<template>
  <div v-if="entries.length > 0" class="interact-history" aria-label="Action history">
    <div class="interact-history__header ds-label">Recent actions</div>
    <ul class="interact-history__list">
      <li
        v-for="(entry, i) in visible"
        :key="entry.id"
        class="interact-history__item"
        :class="{ 'interact-history__item--latest': i === 0 }"
      >
        <span class="interact-history__icon" aria-hidden="true">{{ entry.icon }}</span>
        <span class="interact-history__text">{{ entry.text }}</span>
        <span class="interact-history__time ds-mono">{{ entry.timeText }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.interact-history {
  pointer-events: none;
  user-select: none;
}

.interact-history__header {
  font-size: var(--ds-typo-section-label-font-size);
  opacity: var(--ds-ihl-header-opacity);
  text-transform: uppercase;
  letter-spacing: var(--ds-typo-section-label-letter-spacing);
  margin-bottom: var(--ds-ihl-header-margin-bottom);
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
  flex: 1 1 auto;
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
