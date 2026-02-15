<script setup lang="ts">
import type { ArtifactIndexItem, RunStatus } from '../api/simulatorTypes'

type Props = {
  showResetView: boolean
  runId: string | null
  runStatus: RunStatus | null
  sseState: string

  refreshSnapshot: () => void

  artifacts: ArtifactIndexItem[]
  artifactsLoading: boolean
  refreshArtifacts: () => void
  downloadArtifact: (name: string) => void

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
      <button class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" :disabled="!runId" @click="refreshSnapshot">Refresh</button>
      <button v-if="showResetView" class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" @click="resetView">Reset view</button>
    </div>

    <details class="ds-panel ds-ov-metric ds-ov-details" style="opacity: 0.92" aria-label="Artifacts">
      <summary class="ds-row" style="gap: 8px; cursor: pointer">
        <span class="ds-badge ds-badge--info">Artifacts</span>
        <span class="ds-value" style="opacity: 0.85">{{ artifacts.length || 0 }}</span>
      </summary>
      <div class="ds-stack" style="margin-top: 8px; gap: 8px">
        <div class="ds-row" style="gap: 6px">
          <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" :disabled="!runId || artifactsLoading" @click="refreshArtifacts">
            {{ artifactsLoading ? 'Loading…' : 'Refresh' }}
          </button>
          <div v-if="!artifacts.length" class="ds-label" style="opacity: 0.85">No artifacts</div>
        </div>
        <div v-if="artifacts.length" class="ds-row" style="gap: 6px">
          <button
            v-for="a in artifacts"
            :key="a.name"
            class="ds-btn ds-btn--secondary"
            style="height: 28px; padding: 0 10px"
            type="button"
            @click="downloadArtifact(a.name)"
          >
            {{ a.name }}
          </button>
        </div>
      </div>
    </details>

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
