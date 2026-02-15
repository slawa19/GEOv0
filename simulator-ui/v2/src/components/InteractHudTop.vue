<script setup lang="ts">
import { computed } from 'vue'
import type { RunStatus, ScenarioSummary } from '../api/simulatorTypes'

type Props = {
  loadingScenarios: boolean
  scenarios: ScenarioSummary[]
  selectedScenarioId: string

  /** List of selectable equivalents (provided by app-state/config). */
  equivalents: string[]

  runId: string | null
  runStatus: RunStatus | null
  sseState: string
  lastError: string | null

  refreshScenarios: () => void
  startRun: () => void
  pause: () => void
  resume: () => void
  stop: () => void

  /** Interact-only reset semantics (typically: stop + start). */
  resetScenario?: () => void
}

const props = defineProps<Props>()

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })

const emit = defineEmits<{
  (e: 'update:selected-scenario-id', v: string): void
}>()

function setSelectedScenarioId(v: string) {
  emit('update:selected-scenario-id', v)
}

const sseTone = computed<'ok' | 'warn' | 'err' | 'info'>(() => {
  const s = String(props.sseState ?? '').toLowerCase()
  if (s === 'open') return 'ok'
  if (s === 'reconnecting' || s === 'connecting') return 'warn'
  if (s === 'closed') return 'err'
  return 'info'
})

const runTone = computed<'ok' | 'warn' | 'err' | 'info'>(() => {
  const s = String(props.runStatus?.state ?? '').toLowerCase()
  if (s === 'running') return 'ok'
  if (s === 'paused') return 'warn'
  if (s === 'error') return 'err'
  return 'info'
})

const canPause = computed(() => props.runStatus?.state === 'running')
const canResume = computed(() => props.runStatus?.state === 'paused')
const canStop = computed(() => {
  const s = props.runStatus?.state
  return s === 'running' || s === 'paused' || s === 'created'
})

const isRunActive = computed(() => {
  if (!props.runId) return false
  if (!props.runStatus) return true
  const s = String(props.runStatus?.state ?? '').toLowerCase()
  return s === 'running' || s === 'paused' || s === 'created' || s === 'stopping'
})
</script>

<template>
  <div class="ds-ov-top">
    <div class="ds-ov-top-grid">
      <div class="ds-ov-controls" aria-label="Interact controls">
        <div class="ds-panel ds-ov-bar">
          <span class="ds-badge ds-badge--warn" title="Interact Mode (intensity forced to 0%)">INTERACT MODE</span>

          <div class="ds-row" style="gap: 6px">
            <span class="ds-label">EQ</span>
            <select v-model="eq" class="ds-select" aria-label="Equivalent">
              <option v-for="e in props.equivalents" :key="e" :value="e">{{ e }}</option>
            </select>
          </div>

          <div class="ds-row" style="gap: 6px">
            <span class="ds-label">Layout</span>
            <select v-model="layoutMode" class="ds-select" aria-label="Layout">
              <option value="admin-force">Organic cloud</option>
              <option value="community-clusters">Clusters</option>
              <option value="balance-split">Balance</option>
              <option value="type-split">Type</option>
              <option value="status-split">Status</option>
            </select>
          </div>

          <div class="ds-row" style="gap: 6px">
            <span class="ds-label">Scenario</span>
            <select
              class="ds-select"
              :value="selectedScenarioId"
              aria-label="Scenario"
              @change="setSelectedScenarioId(($event.target as HTMLSelectElement).value)"
            >
              <option value="">— select —</option>
              <option v-for="s in scenarios" :key="s.scenario_id" :value="s.scenario_id">
                {{ s.name ? `${s.name} (${s.scenario_id})` : s.scenario_id }}
              </option>
            </select>
            <button class="ds-btn ds-btn--icon" type="button" :disabled="loadingScenarios" @click="props.refreshScenarios">↻</button>
            <button class="ds-btn ds-btn--primary" type="button" :disabled="isRunActive || !selectedScenarioId" @click="props.startRun">
              {{ runId && !isRunActive ? 'New run' : 'Start' }}
            </button>
            <button class="ds-btn" type="button" :disabled="!runId || !canPause" @click="props.pause">Pause</button>
            <button class="ds-btn" type="button" :disabled="!runId || !canResume" @click="props.resume">Resume</button>
            <button class="ds-btn ds-btn--ghost" type="button" :disabled="!runId || !canStop" @click="props.stop">Stop</button>
            <button
              v-if="props.resetScenario"
              class="ds-btn"
              type="button"
              :disabled="!selectedScenarioId"
              @click="props.resetScenario"
            >
              Reset
            </button>
          </div>
        </div>
      </div>

      <div class="ds-ov-status" aria-label="Interact run status">
        <div class="ds-panel ds-ov-bar" style="justify-content: flex-end">
          <span :class="['ds-badge', `ds-badge--${sseTone}`]">SSE {{ sseState }}</span>
          <span :class="['ds-badge', `ds-badge--${runTone}`]">Run {{ runStatus?.state ?? (runId ? '…' : '—') }}</span>
          <span class="ds-label ds-muted" title="Interact mode forces intensity=0%">Intensity 0%</span>
        </div>
      </div>

      <div v-if="lastError" class="ds-panel ds-ov-bar ds-ov-full" aria-label="Error" style="gap: 10px">
        <span class="ds-badge ds-badge--err">Error</span>
        <div class="ds-label ds-mono" style="max-width: 56ch; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
          {{ String(lastError).slice(0, 160) }}
        </div>
      </div>
    </div>
  </div>
</template>

