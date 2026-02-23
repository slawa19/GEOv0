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
  font-size: 0.7rem;
  opacity: 0.45;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 4px;
}

.interact-history__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.interact-history__item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 0.78rem;
  opacity: 0.7;
  transition: opacity 0.3s;
}

.interact-history__item--latest {
  opacity: 1;
}

.interact-history__icon {
  flex-shrink: 0;
  font-size: 0.75rem;
  min-width: 1.2em;
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
  font-size: 0.7rem;
  opacity: 0.5;
}
</style>
