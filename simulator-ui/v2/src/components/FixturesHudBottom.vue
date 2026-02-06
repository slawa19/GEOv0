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
  <div class="hud-bottom">
    <template v-if="showResetView">
      <button class="btn btn-ghost" type="button" @click="resetView">Reset view</button>
      <div class="hud-divider" />
    </template>

    <div class="pill subtle">
      <span class="label">Quality</span>
      <select v-model="quality" class="select select-compact" aria-label="Quality">
        <option value="low">Low</option>
        <option value="med">Med</option>
        <option value="high">High</option>
      </select>
    </div>

    <div class="pill subtle">
      <span class="label">Labels</span>
      <select v-model="labelsLod" class="select select-compact" aria-label="Labels">
        <option value="off">Off</option>
        <option value="selection">Selection</option>
        <option value="neighbors">Neighbors</option>
      </select>
    </div>

    <div class="pill subtle" aria-label="Note">
      <span class="mono">FX: use real mode (Demo UI / debug)</span>
    </div>
  </div>
</template>

<style scoped>
/* Reuse shared HUD styles (global CSS classes). */
</style>
