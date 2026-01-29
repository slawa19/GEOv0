<script setup lang="ts">
import type { ArtifactIndexItem, RunStatus } from '../api/simulatorTypes'

type Props = {
  showResetView: boolean
  runId: string | null
  runStatus: RunStatus | null
  sseState: string

  intensityPercent: number
  setIntensity: () => void

  pause: () => void
  resume: () => void
  stop: () => void

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

function canPause() {
  return props.runStatus?.state === 'running'
}

function canResume() {
  return props.runStatus?.state === 'paused'
}

function canStop() {
  const s = props.runStatus?.state
  return s === 'running' || s === 'paused' || s === 'created'
}
</script>

<template>
  <div class="hud-bottom">
    <button class="btn" type="button" :disabled="!runId || !canPause()" @click="pause">Pause</button>
    <button class="btn" type="button" :disabled="!runId || !canResume()" @click="resume">Resume</button>
    <button class="btn btn-ghost" type="button" :disabled="!runId || !canStop()" @click="stop">Stop</button>

    <div class="hud-divider" />

    <button class="btn btn-ghost" type="button" :disabled="!runId" @click="refreshSnapshot">Refresh snapshot</button>

    <div class="pill subtle">
      <span class="label">Intensity</span>
      <span class="mono">{{ intensityPercent }}%</span>
      <button class="btn btn-xxs" type="button" :disabled="!runId" @click="setIntensity">Apply</button>
    </div>

    <div class="hud-divider" />

    <details class="pill subtle" style="padding: 6px 10px">
      <summary class="label" style="cursor: pointer">Artifacts</summary>
      <div style="margin-top: 8px; display: flex; gap: 10px; align-items: flex-start; flex-wrap: wrap">
        <button class="btn btn-xxs" type="button" :disabled="!runId || artifactsLoading" @click="refreshArtifacts">
          {{ artifactsLoading ? 'Loading…' : 'Refresh' }}
        </button>
        <div v-if="!artifacts.length" class="mono" style="opacity: 0.8">No artifacts</div>
        <div v-else style="display: flex; gap: 8px; flex-wrap: wrap">
          <button v-for="a in artifacts" :key="a.name" class="btn btn-xxs" type="button" @click="downloadArtifact(a.name)">
            {{ a.name }}
          </button>
        </div>
      </div>
    </details>

    <template v-if="showResetView">
      <div class="hud-divider" />
      <button class="btn btn-ghost" type="button" @click="resetView">Reset view</button>
    </template>

    <div class="hud-divider" />

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

    <div class="pill subtle" aria-label="Connection">
      <span class="mono">{{ sseState }}</span>
    </div>
  </div>
</template>
