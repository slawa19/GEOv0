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

const isCapacityStall = computed(() => {
  const ticks = Number(props.runStatus?.consec_all_rejected_ticks ?? 0)
  return ticks >= 3
})

const isRunActive = computed(() => {
  if (!props.runId) return false
  // Optimistic: when runId exists but runStatus hasn't arrived yet, treat it as active.
  if (!props.runStatus) return true
  const s = String(props.runStatus?.state ?? '').toLowerCase()
  return s === 'running' || s === 'paused' || s === 'created' || s === 'stopping'
})

const successRatePct = computed(() => {
  const a = Number(props.runStats?.attempts ?? 0)
  const ok = Number(props.runStats?.committed ?? 0)
  if (a <= 0) return 0
  return Math.round((ok / a) * 100)
})

const stopSummary = computed(() => {
  const st = props.runStatus
  if (!st) return ''
  const src = String((st as any).stop_source ?? '').trim()
  const reason = String((st as any).stop_reason ?? '').trim()
  if (!src && !reason) return ''
  return `${src || 'unknown'}${reason ? `: ${reason}` : ''}`
})

// Show the actual mode from runStatus if running, otherwise show desired mode
const currentMode = computed(() => {
  const actualMode = props.runStatus?.mode
  if (actualMode) return actualMode
  return props.desiredMode
})

const modeBadgeText = computed(() => {
  return currentMode.value === 'fixtures' ? 'SANDBOX' : 'REAL'
})

const modeBadgeTone = computed<'ok' | 'warn' | 'info'>(() => {
  return currentMode.value === 'fixtures' ? 'warn' : 'info'
})

const modeTitle = computed(() => {
  if (currentMode.value === 'fixtures') {
    return 'Sandbox (topology-only): no DB enrichment (no balances/debts-based viz)'
  }
  return 'Real: uses DB enrichment (balances/debts-based viz when available)'
})

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })

const emit = defineEmits<{
  // Use kebab-case event names so listeners work reliably in templates.
  (e: 'update:selected-scenario-id', v: string): void
  (e: 'update:desired-mode', v: SimulatorMode): void
  (e: 'update:intensity-percent', v: number): void
}>()

function setSelectedScenarioId(v: string) {
  emit('update:selected-scenario-id', v)
}

function setDesiredMode(v: string) {
  emit('update:desired-mode', (v === 'fixtures' ? 'fixtures' : 'real') as SimulatorMode)
}

function setIntensityPercent(v: string) {
  const n = Number(v)
  if (!Number.isFinite(n)) return
  emit('update:intensity-percent', Math.max(0, Math.min(100, Math.round(n))))
}

function short(s: string, n: number) {
  if (s.length <= n) return s
  return `${s.slice(0, n - 1)}…`
}
</script>

<template>
  <div class="ds-ov-top">
    <div class="ds-ov-top-grid">
      <div class="ds-ov-controls" aria-label="Controls">
        <div class="ds-panel ds-ov-bar">
          <span :class="['ds-badge', `ds-badge--${modeBadgeTone}`]" :title="modeTitle">{{ modeBadgeText }}</span>

          <div class="ds-panel ds-ov-metric">
            <span class="ds-label">EQ</span>
            <select v-model="eq" class="ds-select" aria-label="Equivalent">
              <option value="UAH">UAH</option>
              <option value="HOUR">HOUR</option>
              <option value="EUR">EUR</option>
            </select>
          </div>

          <div class="ds-panel ds-ov-metric">
            <span class="ds-label">Layout</span>
            <select v-model="layoutMode" class="ds-select" aria-label="Layout">
              <option value="admin-force">Organic cloud</option>
              <option value="community-clusters">Clusters</option>
              <option value="balance-split">Balance</option>
              <option value="type-split">Type</option>
              <option value="status-split">Status</option>
            </select>
          </div>

          <div class="ds-panel ds-ov-bar" aria-label="Scenario controls">
            <span class="ds-label">Scenario</span>
            <select
              class="ds-select"
              :value="selectedScenarioId"
              aria-label="Scenario"
              @change="setSelectedScenarioId(($event.target as HTMLSelectElement).value)"
            >
              <option value="">— select —</option>
              <option v-for="s in scenarios" :key="s.scenario_id" :value="s.scenario_id">
                {{ s.label ? `${s.label} (${s.scenario_id})` : s.scenario_id }}
              </option>
            </select>
            <button
              class="ds-btn ds-btn--icon"
              style="height: 28px; width: 28px"
              type="button"
              :disabled="loadingScenarios"
              aria-label="Refresh scenarios"
              @click="props.refreshScenarios"
            >
              ↻
            </button>
            <button
              class="ds-btn ds-btn--primary"
              style="height: 28px; padding: 0 10px"
              type="button"
              :disabled="isRunActive || !selectedScenarioId"
              @click="props.startRun"
            >
              {{ runId && !isRunActive ? 'New run' : 'Start' }}
            </button>
            <button class="ds-btn" style="height: 28px; padding: 0 10px" type="button" :disabled="!runId || !canPause" @click="props.pause">
              Pause
            </button>
            <button class="ds-btn" style="height: 28px; padding: 0 10px" type="button" :disabled="!runId || !canResume" @click="props.resume">
              Resume
            </button>
            <button
              class="ds-btn ds-btn--ghost"
              style="height: 28px; padding: 0 10px"
              type="button"
              :disabled="!runId || !canStop"
              @click="props.stop"
            >
              Stop
            </button>
          </div>

          <div class="ds-panel ds-ov-metric">
            <span class="ds-label">Mode</span>
            <select
              class="ds-select"
              :value="desiredMode"
              :disabled="isRunActive"
              aria-label="Run mode"
              :title="modeTitle"
              @change="setDesiredMode(($event.target as HTMLSelectElement).value)"
            >
              <option value="real">real (DB)</option>
              <option value="fixtures">sandbox (topology-only)</option>
            </select>
          </div>

          <div class="ds-panel ds-ov-bar" aria-label="Intensity">
            <span class="ds-label">Intensity</span>
            <input
              class="ds-input"
              style="width: 6ch; height: 28px"
              type="number"
              min="0"
              max="100"
              step="1"
              :value="intensityPercent"
              aria-label="Intensity percent"
              @input="setIntensityPercent(($event.target as HTMLInputElement).value)"
            />
            <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" :disabled="!isRunActive" @click="props.applyIntensity">
              Apply
            </button>
          </div>

          <div v-if="!loadingScenarios && scenarios.length === 0" class="ds-panel ds-ov-bar" aria-label="No scenarios" style="opacity: 0.92">
            <span class="ds-badge ds-badge--warn">No scenarios</span>
            <span class="ds-label">Backend must return GET /simulator/scenarios</span>
          </div>
        </div>
      </div>

      <div class="ds-ov-status" aria-label="Run status">
        <div class="ds-panel ds-ov-bar" style="justify-content: flex-end">
          <span :class="['ds-badge', `ds-badge--${sseTone}`]">SSE {{ sseState }}</span>
          <span :class="['ds-badge', `ds-badge--${runTone}`]">Run {{ runStatus?.state ?? (runId ? '…' : '—') }}</span>

          <div v-if="runStatus?.state === 'stopped' && stopSummary" class="ds-panel ds-ov-metric" style="opacity: 0.92" aria-label="Stop reason">
            <span class="ds-label">Stop</span>
            <span class="ds-value ds-mono" style="opacity: 0.9">{{ short(stopSummary, 64) }}</span>
          </div>

          <div class="ds-panel ds-ov-metric" style="opacity: 0.92" aria-label="Stats">
            <span class="ds-label">Tx</span>
            <span class="ds-value">ok {{ runStats.committed }}</span>
            <span class="ds-label">/</span>
            <span class="ds-value">{{ runStats.attempts }}</span>
            <span class="ds-label">SR</span>
            <span class="ds-value">{{ successRatePct }}%</span>
            <span class="ds-label">rej</span>
            <span class="ds-value">{{ runStats.rejected }}</span>
            <span class="ds-label">err</span>
            <span class="ds-value">{{ runStats.errors }}</span>
          </div>

          <div v-if="runId" class="ds-panel ds-ov-metric" style="opacity: 0.92">
            <span class="ds-label">ID</span>
            <span class="ds-value ds-mono" style="opacity: 0.9">{{ short(runId, 18) }}</span>
          </div>
        </div>
      </div>

      <div v-if="lastError" class="ds-alert ds-alert--err ds-ov-full" aria-label="Error">
        <span class="ds-alert__icon">✕</span>
        <span class="ds-label">Error</span>
        <span class="ds-value ds-mono" style="opacity: 0.92">{{ short(lastError, 160) }}</span>
      </div>

      <div v-if="isCapacityStall" class="ds-alert ds-alert--warn ds-ov-full" aria-label="Capacity stall">
        <span class="ds-alert__icon">!</span>
        <span class="ds-label">Stall</span>
        <span class="ds-value ds-mono" style="opacity: 0.92">All payments rejected — network capacity exhausted. Waiting for clearing to free capacity.</span>
      </div>
    </div>
  </div>
</template>
