<script setup lang="ts">
import type { ScenarioSummary, SimulatorMode, RunStatus } from '../api/simulatorTypes'
import { computed } from 'vue'

type Props = {
  loadingScenarios: boolean
  scenarios: ScenarioSummary[]
  selectedScenarioId: string

  desiredMode: SimulatorMode
  intensityPercent: number

  runId: string | null
  runStatus: RunStatus | null

  sseState: string
  lastError: string | null

  refreshScenarios: () => void
  startRun: () => void

  pause: () => void
  resume: () => void
  stop: () => void
  applyIntensity: () => void

  runStats: {
    startedAtMs: number
    attempts: number
    committed: number
    rejected: number
    errors: number
    timeouts: number
    rejectedByCode: Record<string, number>
    errorsByCode: Record<string, number>
  }
}

const props = defineProps<Props>()

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
  const s = String(props.runStatus?.state ?? '').toLowerCase()
  return s === 'running' || s === 'paused' || s === 'created' || s === 'stopping'
})

const successRatePct = computed(() => {
  const a = Number(props.runStats?.attempts ?? 0)
  const ok = Number(props.runStats?.committed ?? 0)
  if (a <= 0) return 0
  return Math.round((ok / a) * 100)
})

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })

const emit = defineEmits<{
  (e: 'update:selectedScenarioId', v: string): void
  (e: 'update:desiredMode', v: SimulatorMode): void
  (e: 'update:intensityPercent', v: number): void
}>()

function setSelectedScenarioId(v: string) {
  emit('update:selectedScenarioId', v)
}

function setDesiredMode(v: string) {
  emit('update:desiredMode', (v === 'fixtures' ? 'fixtures' : 'real') as SimulatorMode)
}

function setIntensityPercent(v: string) {
  const n = Number(v)
  if (!Number.isFinite(n)) return
  emit('update:intensityPercent', Math.max(0, Math.min(100, Math.round(n))))
}

function short(s: string, n: number) {
  if (s.length <= n) return s
  return `${s.slice(0, n - 1)}…`
}
</script>

<template>
  <div class="hud-top">
    <div class="hud-top-grid">
      <div class="hud-controls" aria-label="Controls">
        <div class="hudbar">
          <span class="hud-badge" data-tone="info">REAL</span>

          <div class="hud-chip">
            <span class="hud-label">EQ</span>
            <select v-model="eq" class="hud-select" aria-label="Equivalent">
              <option value="UAH">UAH</option>
              <option value="HOUR">HOUR</option>
              <option value="EUR">EUR</option>
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
                {{ s.label ? `${s.label} (${s.scenario_id})` : s.scenario_id }}
              </option>
            </select>
            <button class="btn btn-xxs" type="button" :disabled="loadingScenarios" @click="props.refreshScenarios">↻</button>
            <button class="btn btn-xs" type="button" :disabled="isRunActive || !selectedScenarioId" @click="props.startRun">
              {{ runId && !isRunActive ? 'New run' : 'Start' }}
            </button>
            <button class="btn btn-xs" type="button" :disabled="!runId || !canPause" @click="props.pause">Pause</button>
            <button class="btn btn-xs" type="button" :disabled="!runId || !canResume" @click="props.resume">Resume</button>
            <button class="btn btn-xs btn-ghost" type="button" :disabled="!runId || !canStop" @click="props.stop">Stop</button>
          </div>

          <div class="hud-chip">
            <span class="hud-label">Mode</span>
            <select class="hud-select" :value="desiredMode" aria-label="Run mode" @change="setDesiredMode(($event.target as HTMLSelectElement).value)">
              <option value="real">real</option>
              <option value="fixtures">fixtures</option>
            </select>
          </div>

          <div class="hud-chip">
            <span class="hud-label">Intensity</span>
            <input
              class="hud-input"
              style="width: 6ch"
              type="number"
              min="0"
              max="100"
              step="1"
              :value="intensityPercent"
              aria-label="Intensity percent"
              @input="setIntensityPercent(($event.target as HTMLInputElement).value)"
            />
            <button class="btn btn-xxs" type="button" :disabled="!runId" @click="props.applyIntensity">Apply</button>
          </div>

          <div v-if="!loadingScenarios && scenarios.length === 0" class="hud-chip" aria-label="No scenarios">
            <span class="hud-badge" data-tone="warn">No scenarios</span>
            <span class="hud-label">Backend must return GET /simulator/scenarios</span>
          </div>
        </div>
      </div>

      <div class="hud-status" aria-label="Run status">
        <div class="hudbar" style="justify-content: flex-end">
          <span class="hud-badge" :data-tone="sseTone">SSE {{ sseState }}</span>
          <span class="hud-badge" :data-tone="runTone">Run {{ runStatus?.state ?? (runId ? '…' : '—') }}</span>
          <span class="hud-chip subtle" style="gap: 8px" aria-label="Stats">
            <span class="hud-label">Tx</span>
            <span class="hud-value">ok {{ runStats.committed }}</span>
            <span class="hud-label">/</span>
            <span class="hud-value">{{ runStats.attempts }}</span>
            <span class="hud-label">SR</span>
            <span class="hud-value">{{ successRatePct }}%</span>
            <span class="hud-label">rej</span>
            <span class="hud-value">{{ runStats.rejected }}</span>
            <span class="hud-label">err</span>
            <span class="hud-value">{{ runStats.errors }}</span>
          </span>
          <span v-if="runId" class="hud-chip subtle" style="gap: 6px">
            <span class="hud-label">ID</span>
            <span class="hud-value mono" style="opacity: 0.9">{{ short(runId, 18) }}</span>
          </span>
        </div>
      </div>

      <div v-if="lastError" class="hud-alert" data-tone="err" aria-label="Error">
        <span class="hud-label">Error</span>
        <div class="hud-alert__msg">
          <span class="mono">{{ short(lastError, 160) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
