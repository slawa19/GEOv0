<script setup lang="ts">
import type { RunStatus } from '../api/simulatorTypes'

type Props = {
  showResetView: boolean
  runId: string | null
  runStatus: RunStatus | null
  sseState: string

  refreshSnapshot: () => void
  resetView: () => void
}

const props = defineProps<Props>()

const quality = defineModel<string>('quality', { required: true })
const labelsLod = defineModel<string>('labelsLod', { required: true })

function sseTone() {
  const s = String(props.sseState ?? '').toLowerCase()
  if (s === 'open') return 'ok'
  if (s === 'reconnecting' || s === 'connecting') return 'warn'
  if (s === 'closed') return 'err'
  return 'info'
}
</script>

<template>
  <div class="ds-ov-bottom ds-panel ds-ov-bar">
    <div class="ds-ov-btn-group" aria-label="View controls">
      <button class="ds-btn ds-btn--ghost" type="button" :disabled="!runId" @click="refreshSnapshot">Refresh</button>
      <button v-if="showResetView" class="ds-btn ds-btn--ghost" type="button" @click="resetView">Reset view</button>
    </div>

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

    <span :class="['ds-badge', `ds-badge--${sseTone()}`]" aria-label="Connection">SSE {{ sseState }}</span>
  </div>
</template>

