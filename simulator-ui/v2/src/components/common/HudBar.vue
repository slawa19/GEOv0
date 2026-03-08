<script setup lang="ts">
import { computed } from 'vue'

/**
 * HudBar — presentational container for HUD toolbars.
 *
 * Slot contract (consumer guidance):
 * - You can render any content into the default slot.
 * - For predictable alignment, consumers may group content into optional sections:
 *   - `.hud-bar__left`   — size-to-content, aligned to start
 *   - `.hud-bar__center` — flexible (grows/shrinks), centered
 *   - `.hud-bar__right`  — size-to-content, aligned to end
 *
 * These section classnames are styled via `:deep()` inside HudBar.
 * Avoid scoped CSS overrides in consumers; prefer structure + `variant/layout/fit` props.
 */
const props = withDefaults(defineProps<{
  variant?: 'solid' | 'ghost'
  layout?: 'between' | 'center' | 'start' | 'end'
  fit?: boolean
}>(), {
  variant: 'solid',
  layout: 'between',
  fit: false
})

const layoutClass = computed(() => {
  return [
    `hud-bar--layout-${props.layout}`,
    `hud-bar--variant-${props.variant}`,
    props.fit ? 'hud-bar--fit' : ''
  ]
})
</script>

<template>
  <div class="hud-bar ds-panel" :class="layoutClass">
    <slot />
  </div>
</template>

<style scoped>
.hud-bar {
  display: flex;
  align-items: center;
  gap: var(--ds-space-3, 12px);
  flex-wrap: wrap; /* responsive flex wrapping */
  width: 100%;
  box-sizing: border-box;
  /* Vertical / horizontal padding driven by CSS variables so ancestor
     contexts (e.g. .ds-ov-top-stack) can tighten them without !important. */
  padding: var(--ds-hud-bar-py, var(--ds-space-2)) var(--ds-hud-bar-px, var(--ds-space-3));
  pointer-events: auto;
}

.hud-bar--variant-ghost {
  /* Frameless override for HUD controls to sit directly on the scene */
  background: transparent !important;
  /* BUG-4: avoid affecting box-model via border width */
  border: none !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
  overflow: visible !important;
  clip-path: none !important;
}

.hud-bar--fit {
  width: auto;
  display: inline-flex;
}

.hud-bar--layout-start { justify-content: flex-start; }
.hud-bar--layout-center { justify-content: center; }
.hud-bar--layout-end { justify-content: flex-end; }
.hud-bar--layout-between { justify-content: space-between; }

/* Slot section alignment classes for consumers (TopBar/BottomBar) */
:deep(.hud-bar__left),
:deep(.hud-bar__center),
:deep(.hud-bar__right) {
  display: flex;
  align-items: center;
  gap: var(--ds-space-2, 8px);
  flex-wrap: wrap;
  min-width: 0;
}

:deep(.hud-bar__left) { flex: 0 0 auto; justify-content: flex-start; }
:deep(.hud-bar__center) { flex: 1 1 auto; justify-content: center; }
:deep(.hud-bar__right) { flex: 0 0 auto; justify-content: flex-end; }

@media (max-width: 500px) {
  :deep(.hud-bar__left),
  :deep(.hud-bar__center),
  :deep(.hud-bar__right) {
    flex: 1 1 100%;
    min-width: 0;
    width: 100%;
  }

  :deep(.ds-select) {
    min-width: 0;
    max-width: 100%;
    flex: 1 1 auto;
  }

  :deep(.ds-btn) {
    min-width: 0;
    flex-shrink: 1;
  }

  :deep(.ds-label) {
    white-space: normal;
    flex-shrink: 1;
  }
}
</style>
