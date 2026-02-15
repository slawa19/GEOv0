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
  <div class="hud-top">
    <div class="hud-top-grid">
      <div class="hud-controls" aria-label="Interact controls">
        <div class="hudbar">
          <span class="hud-badge" data-tone="warn" title="Interact Mode (intensity forced to 0%)">INTERACT MODE</span>

          <div class="hud-chip">
            <span class="hud-label">EQ</span>
            <select v-model="eq" class="hud-select" aria-label="Equivalent">
              <option v-for="e in props.equivalents" :key="e" :value="e">{{ e }}</option>
            </select>
          </div>

          <div class="hud-chip">
            <span class="hud-label">Layout</span>
            <select v-model="layoutMode" class="hud-select" aria-label="Layout">
              <option value="admin-force">Organic cloud</option>
              <option value="community-clusters">Clusters</option>
              <option value="balance-split">Balance</option>
              <option value="type-split">Type</option>
              <option value="status-split">Status</option>
            </select>
          </div>

          <div class="hud-chip">
            <span class="hud-label">Scenario</span>
            <select
              class="hud-select"
              :value="selectedScenarioId"
              aria-label="Scenario"
              @change="setSelectedScenarioId(($event.target as HTMLSelectElement).value)"
            >
              <option value="">— select —</option>
              <option v-for="s in scenarios" :key="s.scenario_id" :value="s.scenario_id">
                {{ s.name ? `${s.name} (${s.scenario_id})` : s.scenario_id }}
              </option>
            </select>
            <button class="btn btn-xxs" type="button" :disabled="loadingScenarios" @click="props.refreshScenarios">↻</button>
            <button class="btn btn-xs" type="button" :disabled="isRunActive || !selectedScenarioId" @click="props.startRun">
              {{ runId && !isRunActive ? 'New run' : 'Start' }}
            </button>
            <button class="btn btn-xs" type="button" :disabled="!runId || !canPause" @click="props.pause">Pause</button>
            <button class="btn btn-xs" type="button" :disabled="!runId || !canResume" @click="props.resume">Resume</button>
            <button class="btn btn-xs btn-ghost" type="button" :disabled="!runId || !canStop" @click="props.stop">Stop</button>
            <button
              v-if="props.resetScenario"
              class="btn btn-xs"
              type="button"
              :disabled="!selectedScenarioId"
              @click="props.resetScenario"
            >
              Reset
            </button>
          </div>
        </div>
      </div>

      <div class="hud-status" aria-label="Interact run status">
        <div class="hudbar" style="justify-content: flex-end">
          <span class="hud-badge" :data-tone="sseTone">SSE {{ sseState }}</span>
          <span class="hud-badge" :data-tone="runTone">Run {{ runStatus?.state ?? (runId ? '…' : '—') }}</span>
          <span class="hud-chip subtle" title="Interact mode forces intensity=0%">Intensity 0%</span>
        </div>
      </div>

      <div v-if="lastError" class="hud-alert" data-tone="err" aria-label="Error">
        <span class="hud-label">Error</span>
        <div class="hud-alert__msg">
          <span class="mono">{{ String(lastError).slice(0, 160) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

