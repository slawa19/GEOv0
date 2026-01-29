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
  lastError: string

  refreshScenarios: () => void
  startRun: () => void
}

const props = defineProps<Props>()

const DEFAULT_DEV_ACCESS_TOKEN = String(import.meta.env.VITE_GEO_DEV_ACCESS_TOKEN ?? 'dev-admin-token-change-me').trim()
const isDev = import.meta.env.DEV
const isLocalhost = typeof window !== 'undefined' && ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname)
const isUsingDevToken = computed(() => isDev && isLocalhost && String(props.accessToken ?? '').trim() === DEFAULT_DEV_ACCESS_TOKEN)
const showTokenEditor = ref(false)

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
    <div class="hud-row" style="flex-wrap: wrap">
      <div class="pill">
        <span class="label">Mode</span>
        <span class="mono">REAL</span>
      </div>

      <div class="pill">
        <span class="label">EQ</span>
        <select v-model="eq" class="select" aria-label="Equivalent">
          <option value="UAH">UAH</option>
          <option value="HOUR">HOUR</option>
          <option value="EUR">EUR</option>
        </select>
      </div>

      <div class="field pill">
        <span class="label">Layout</span>
        <select v-model="layoutMode" class="select" aria-label="Layout">
          <option value="admin-force">Organic cloud (links)</option>
          <option value="community-clusters">Community clusters</option>
          <option value="balance-split">Constellations: balance</option>
          <option value="type-split">Constellations: type</option>
          <option value="status-split">Constellations: status</option>
        </select>
      </div>

      <div class="pill">
        <span class="label">API</span>
        <input
          class="select"
          style="width: 160px"
          :value="apiBase"
          aria-label="API base"
          @input="setApiBase(($event.target as HTMLInputElement).value)"
        />
      </div>

      <div class="pill">
        <span class="label">Auth</span>
        <template v-if="isUsingDevToken && !showTokenEditor">
          <span class="mono">dev admin (auto)</span>
          <button class="btn btn-xxs" type="button" @click="showTokenEditor = true">edit</button>
        </template>
        <template v-else>
          <input
            class="select mono"
            style="width: 220px"
            type="password"
            :value="accessToken"
            aria-label="Access token"
            placeholder="JWT (Bearer) or admin token"
            @input="setAccessToken(($event.target as HTMLInputElement).value)"
          />
          <button v-if="isDev && isLocalhost" class="btn btn-xxs" type="button" @click="showTokenEditor = false">
            hide
          </button>
        </template>
      </div>

      <div class="pill">
        <span class="label">Scenario</span>
        <select
          class="select"
          :value="selectedScenarioId"
          aria-label="Scenario"
          @change="setSelectedScenarioId(($event.target as HTMLSelectElement).value)"
        >
          <option value="">— select —</option>
          <option v-for="s in scenarios" :key="s.scenario_id" :value="s.scenario_id">
            {{ s.label ? `${s.label} (${s.scenario_id})` : s.scenario_id }}
          </option>
        </select>
        <button class="btn btn-xxs" type="button" :disabled="loadingScenarios" @click="props.refreshScenarios">
          {{ loadingScenarios ? '…' : '↻' }}
        </button>
      </div>

      <div v-if="!loadingScenarios && scenarios.length === 0" class="pill pill-warn" aria-label="No scenarios">
        <span class="label">Hint</span>
        <span class="mono">
          No scenarios. Graph won’t start in real mode until backend returns items for GET /simulator/scenarios.
          Fix: ensure backend image includes fixtures/simulator (Dockerfile COPY fixtures/...) or upload scenario.json.
        </span>
      </div>

      <div class="pill subtle">
        <span class="label">Run mode</span>
        <select class="select" :value="desiredMode" aria-label="Run mode" @change="setDesiredMode(($event.target as HTMLSelectElement).value)">
          <option value="real">real</option>
          <option value="fixtures">fixtures</option>
        </select>
      </div>

      <div class="pill subtle">
        <span class="label">Intensity</span>
        <input
          class="select"
          style="width: 56px"
          type="number"
          min="0"
          max="100"
          step="1"
          :value="intensityPercent"
          aria-label="Intensity percent"
          @input="setIntensityPercent(($event.target as HTMLInputElement).value)"
        />
      </div>

      <button class="btn" type="button" :disabled="!selectedScenarioId" @click="props.startRun">Start run</button>

      <div class="pill subtle" aria-label="Run status">
        <span class="label">SSE</span>
        <span class="mono">{{ sseState }}</span>
        <span class="label" style="margin-left: 10px">Run</span>
        <span class="mono">{{ runStatus?.state ?? (runId ? '…' : '—') }}</span>
        <span v-if="runId" class="mono" style="opacity: 0.8">{{ short(runId, 22) }}</span>
      </div>

      <div v-if="lastError" class="pill pill-error">
        <span class="label">Error</span>
        <span class="mono">{{ short(lastError, 120) }}</span>
      </div>
    </div>
  </div>
</template>
