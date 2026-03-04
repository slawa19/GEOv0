<script setup lang="ts">
import { computed } from 'vue'

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
  padding: var(--ds-space-2) var(--ds-space-3);
  pointer-events: auto;
}

.hud-bar--variant-ghost {
  /* Frameless override for HUD controls to sit directly on the scene */
  background: transparent !important;
  border-color: transparent !important;
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
</style>
