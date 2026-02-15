<script setup lang="ts">

type Props = {
  showResetView: boolean
  resetView: () => void
}

defineProps<Props>()

const quality = defineModel<string>('quality', { required: true })
const labelsLod = defineModel<string>('labelsLod', { required: true })
</script>

<template>
  <div class="ds-ov-bottom ds-panel ds-ov-bar">
    <template v-if="showResetView">
      <button class="ds-btn ds-btn--ghost" type="button" @click="resetView">Reset view</button>
      <div class="ds-ov-divider" />
    </template>

    <div class="ds-row" style="gap: 6px">
      <span class="ds-label">Quality</span>
      <select v-model="quality" class="ds-select" aria-label="Quality">
        <option value="low">Low</option>
        <option value="med">Med</option>
        <option value="high">High</option>
      </select>
    </div>

    <div class="ds-row" style="gap: 6px">
      <span class="ds-label">Labels</span>
      <select v-model="labelsLod" class="ds-select" aria-label="Labels">
        <option value="off">Off</option>
        <option value="selection">Selection</option>
        <option value="neighbors">Neighbors</option>
      </select>
    </div>

    <div class="ds-label ds-mono ds-muted" aria-label="Note">FX: use real mode (Demo UI / debug)</div>
  </div>
</template>

<style scoped>
/* Reuse shared HUD styles (global CSS classes). */
</style>
