<script setup lang="ts">
import type { ScenarioSummary, SimulatorMode, RunStatus } from '../api/simulatorTypes'
import { computed, ref } from 'vue'

type Props = {
  apiBase: string
  accessToken: string

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

const DEFAULT_DEV_ACCESS_TOKEN = String(import.meta.env.VITE_GEO_DEV_ACCESS_TOKEN ?? 'dev-admin-token-change-me').trim()
const isDev = import.meta.env.DEV
const isLocalhost = typeof window !== 'undefined' && ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname)
const isUsingDevToken = computed(() => isDev && isLocalhost && String(props.accessToken ?? '').trim() === DEFAULT_DEV_ACCESS_TOKEN)
const showTokenEditor = ref(false)

const readyHint = computed<string>(() => {
  if (props.runId) return ''
  if (!String(props.accessToken ?? '').trim()) return 'Set Auth token'
  if (!String(props.selectedScenarioId ?? '').trim()) return 'Choose Scenario'
  return 'Ready — click Start'
})

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

const runSummary = computed(() => {
  const st = String(props.runStatus?.state ?? '')
  if (!st) return ''
  if (st !== 'stopped' && st !== 'error') return ''
  const ok = props.runStats.committed
  const a = props.runStats.attempts
  const rej = props.runStats.rejected
  const err = props.runStats.errors
  return `Done — ok ${ok}/${a} (${successRatePct.value}%), rej ${rej}, err ${err}`
})

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })

const emit = defineEmits<{
  (e: 'update:apiBase', v: string): void
  (e: 'update:accessToken', v: string): void
  (e: 'update:selectedScenarioId', v: string): void
  (e: 'update:desiredMode', v: SimulatorMode): void
  (e: 'update:intensityPercent', v: number): void
}>()

function setApiBase(v: string) {
  emit('update:apiBase', v)
}

function setAccessToken(v: string) {
  emit('update:accessToken', v)
}

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

          <details class="hud-chip hud-chip-advanced">
            <summary class="hud-label" style="cursor: pointer; list-style: none">
              <span class="hud-badge" data-tone="info">⚙</span>
              <span style="margin-left: 6px">Advanced</span>
            </summary>

            <div class="hudbar" style="margin-top: 8px">
              <div class="hud-chip subtle">
                <span class="hud-label">API</span>
                <input
                  class="hud-input"
                  :value="apiBase"
                  aria-label="API base"
                  @input="setApiBase(($event.target as HTMLInputElement).value)"
                />
              </div>

              <div class="hud-chip subtle">
                <span class="hud-label">Auth</span>
                <template v-if="isUsingDevToken && !showTokenEditor">
                  <span class="hud-value mono">dev admin</span>
                  <button class="btn btn-xxs" type="button" @click="showTokenEditor = true">Edit</button>
                </template>
                <template v-else>
                  <input
                    class="hud-input mono"
                    style="width: 22ch"
                    type="password"
                    :value="accessToken"
                    aria-label="Access token"
                    placeholder="token"
                    @input="setAccessToken(($event.target as HTMLInputElement).value)"
                  />
                  <button v-if="isDev && isLocalhost" class="btn btn-xxs" type="button" @click="showTokenEditor = false">Hide</button>
                </template>
              </div>

              <div class="hud-chip subtle">
                <span class="hud-label">Run mode</span>
                <select class="hud-select" :value="desiredMode" aria-label="Run mode" @change="setDesiredMode(($event.target as HTMLSelectElement).value)">
                  <option value="real">real</option>
                  <option value="fixtures">fixtures</option>
                </select>
              </div>

              <div class="hud-chip subtle">
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
            </div>
          </details>

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
            <span v-if="runStats.rejected" class="hud-label">rej {{ runStats.rejected }}</span>
            <span v-if="runStats.errors" class="hud-label">err {{ runStats.errors }}</span>
          </span>
          <span v-if="runId" class="hud-chip subtle" style="gap: 6px">
            <span class="hud-label">ID</span>
            <span class="hud-value mono" style="opacity: 0.9">{{ short(runId, 18) }}</span>
          </span>
        </div>
      </div>

      <div v-if="!lastError && (readyHint || runSummary)" class="hud-alert" data-tone="info" aria-label="Status">
        <span class="hud-label">Status</span>
        <div class="hud-alert__msg">
          <span class="mono">{{ runSummary || readyHint }}</span>
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
