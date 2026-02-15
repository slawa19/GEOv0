<script setup lang="ts">
type Props = {
  showResetView: boolean
  showDemoControls: boolean

  busy?: boolean

  playlistPlaying?: boolean
  canDemoPlay?: boolean
  demoPlayLabel?: string

  runTxOnce: () => void | Promise<void>
  runClearingOnce: () => void | Promise<void>
  resetView: () => void

  demoStepOnce?: () => void
  demoTogglePlay?: () => void
  demoReset?: () => void
}

const props = withDefaults(defineProps<Props>(), {
  busy: false,
  playlistPlaying: false,
  canDemoPlay: false,
  demoPlayLabel: 'Play',
  demoStepOnce: () => {},
  demoTogglePlay: () => {},
  demoReset: () => {},
})

async function onRunTxOnce() {
  if (props.busy) return
  await props.runTxOnce()
}

async function onRunClearingOnce() {
  if (props.busy) return
  await props.runClearingOnce()
}

const quality = defineModel<string>('quality', { required: true })
const labelsLod = defineModel<string>('labelsLod', { required: true })
</script>

<template>
  <!-- Bottom HUD (as per prototypes: minimal buttons) -->
  <div class="ds-ov-bottom ds-panel ds-ov-bar">
    <button class="ds-btn ds-btn--secondary" type="button" :disabled="busy" @click="onRunTxOnce">Single Tx</button>
    <button class="ds-btn ds-btn--secondary" type="button" :disabled="busy" @click="onRunClearingOnce">Run Clearing</button>

    <template v-if="showResetView">
      <div class="ds-ov-divider" />
      <button class="ds-btn ds-btn--ghost" type="button" @click="resetView">Reset view</button>
    </template>

    <template v-if="showDemoControls">
      <div class="ds-ov-divider" />
      <button class="ds-btn ds-btn--ghost" type="button" :disabled="!canDemoPlay || playlistPlaying" @click="demoStepOnce">
        Step
      </button>
      <button class="ds-btn ds-btn--secondary" type="button" :disabled="!canDemoPlay" @click="demoTogglePlay">{{ demoPlayLabel }}</button>
      <button class="ds-btn ds-btn--ghost" type="button" :disabled="!canDemoPlay" @click="demoReset">Reset</button>
    </template>

    <div class="ds-ov-divider" />

    <div class="ds-row" aria-label="Quality" style="gap: 6px">
      <span class="ds-label">Quality</span>
      <select v-model="quality" class="ds-select" aria-label="Quality">
        <option value="low">Low</option>
        <option value="med">Med</option>
        <option value="high">High</option>
      </select>
    </div>

    <div class="ds-row" aria-label="Labels" style="gap: 6px">
      <span class="ds-label">Labels</span>
      <select v-model="labelsLod" class="ds-select" aria-label="Labels">
        <option value="off">Off</option>
        <option value="selection">Selection</option>
        <option value="neighbors">Neighbors</option>
      </select>
    </div>
  </div>
</template>
