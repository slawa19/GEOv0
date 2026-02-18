<script setup lang="ts">
import { computed } from 'vue'
import type { RunStatus, ScenarioSummary, SimulatorMode } from '../api/simulatorTypes'

type UiThemeId = 'hud' | 'shadcn' | 'saas' | 'library'

type Props = {
  apiMode: 'fixtures' | 'real'
  isInteractUi: boolean

  /** True when UI runs in automation/deterministic mode (VITE_TEST_MODE=1). */
  isTestMode?: boolean

  /** UI theme id (mapped to data-theme). */
  uiTheme: UiThemeId

  /** Switch theme without reload; should update URL/localStorage. */
  setUiTheme: (v: UiThemeId) => void

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

  goSandbox: () => void
  goAutoRun: () => void
  goInteract: () => void
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:selected-scenario-id', v: string): void
  (e: 'update:desired-mode', v: SimulatorMode): void
  (e: 'update:intensity-percent', v: number): void
}>()

const activeSegment = computed<'sandbox' | 'auto' | 'interact'>(() => {
  if (props.apiMode !== 'real') return 'sandbox'
  return props.isInteractUi ? 'interact' : 'auto'
})

const showRunControls = computed(() => activeSegment.value === 'auto')

const showTestModeBadge = computed(() => {
  // Show only for humans; Playwright/WebDriver runs shouldn't change screenshots.
  const isWebDriver = typeof navigator !== 'undefined' && (navigator as any).webdriver === true
  return !!props.isTestMode && !isWebDriver
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

const isCapacityStall = computed(() => {
  const ticks = Number(props.runStatus?.consec_all_rejected_ticks ?? 0)
  return ticks >= 3
})

const isRunActive = computed(() => {
  if (!props.runId) return false
  if (!props.runStatus) return true
  const s = String(props.runStatus?.state ?? '').toLowerCase()
  return s === 'running' || s === 'paused' || s === 'created' || s === 'stopping'
})

const canEnterInteract = computed(() => {
  if (props.isInteractUi) return true  // уже в interact — не блокировать
  return props.apiMode === 'real' && isRunActive.value
})

const successRatePct = computed(() => {
  const a = Number(props.runStats?.attempts ?? 0)
  const ok = Number(props.runStats?.committed ?? 0)
  if (a <= 0) return 0
  return Math.round((ok / a) * 100)
})

const txCompact = computed(() => {
  const ok = Number(props.runStats?.committed ?? 0)
  const a = Number(props.runStats?.attempts ?? 0)
  return `ok ${ok}/${a} · ${successRatePct.value}%`
})

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

function onThemeChange(v: string) {
  // Normalize at caller if needed.
  props.setUiTheme((v as UiThemeId) || 'hud')
}

const stopSummary = computed(() => {
  const st = props.runStatus
  if (!st) return ''
  const src = String(st.stop_source ?? '').trim()
  const reason = String(st.stop_reason ?? '').trim()
  if (!src && !reason) return ''
  return `${src || 'unknown'}${reason ? `: ${reason}` : ''}`
})
</script>

<template>
  <div class="ds-ov-top">
    <div class="ds-ov-top-stack">
      <div class="ds-panel ds-ov-bar ds-ov-topbar" aria-label="Top bar">
        <div class="ds-ov-topbar__left" aria-label="Mode">
          <div class="ds-row" style="gap: 10px; flex-wrap: wrap; align-items: center">
            <div class="ds-segmented" role="group" aria-label="Simulator mode">
            <button
              type="button"
              class="ds-segment"
              :data-active="activeSegment === 'sandbox' ? '1' : '0'"
              @click="props.goSandbox"
            >
              Sandbox
            </button>
            <button
              type="button"
              class="ds-segment"
              :data-active="activeSegment === 'auto' ? '1' : '0'"
              @click="props.goAutoRun"
            >
              Auto-Run
            </button>
            <button
              type="button"
              class="ds-segment"
              :data-active="activeSegment === 'interact' ? '1' : '0'"
              :disabled="!canEnterInteract"
              :title="!canEnterInteract ? 'Start a run first' : ''"
              @click="props.goInteract"
            >
              Interact
            </button>
            </div>

            <div class="ds-row" style="gap: 6px; align-items: center" aria-label="Theme">
              <span class="ds-label">Theme</span>
              <select
                class="ds-select"
                style="height: 28px"
                :value="uiTheme"
                aria-label="UI theme"
                @change="onThemeChange(($event.target as HTMLSelectElement).value)"
              >
                <option value="shadcn">shadcn/ui (dark)</option>
                <option value="saas">SaaS (subtle)</option>
                <option value="library">Library (Naive-like)</option>
                <option value="hud">HUD (sci-fi)</option>
              </select>
            </div>

            <span v-if="showTestModeBadge" class="ds-badge ds-badge--warn">TEST MODE</span>
          </div>
        </div>

        <div class="ds-ov-topbar__center" aria-label="Run controls">
          <div v-if="showRunControls" class="ds-row" style="gap: 8px; flex-wrap: wrap">
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
              v-if="!isRunActive"
              class="ds-btn ds-btn--primary"
              style="height: 28px; padding: 0 10px"
              type="button"
              :disabled="!selectedScenarioId"
              @click="props.startRun"
            >
              Start
            </button>
            <button
              v-if="canPause"
              class="ds-btn"
              style="height: 28px; padding: 0 10px"
              type="button"
              @click="props.pause"
            >
              Pause
            </button>
            <button
              v-if="canResume"
              class="ds-btn"
              style="height: 28px; padding: 0 10px"
              type="button"
              @click="props.resume"
            >
              Resume
            </button>
            <button
              v-if="canStop"
              class="ds-btn ds-btn--ghost"
              style="height: 28px; padding: 0 10px"
              type="button"
              @click="props.stop"
            >
              Stop
            </button>

            <details class="ds-panel ds-ov-metric ds-ov-details" aria-label="Advanced">
              <summary class="ds-row" style="gap: 8px; cursor: pointer" aria-label="Advanced settings">
                <span class="ds-badge ds-badge--info">⚙</span>
                <span class="ds-label">Advanced</span>
              </summary>
              <div class="ds-stack" style="margin-top: 8px; gap: 8px">
                <div class="ds-row" style="gap: 6px">
                  <span class="ds-label">Pipeline</span>
                  <select
                    class="ds-select"
                    :value="desiredMode"
                    :disabled="isRunActive"
                    aria-label="Run pipeline"
                    @change="setDesiredMode(($event.target as HTMLSelectElement).value)"
                  >
                    <option value="real">real (DB)</option>
                    <option value="fixtures">sandbox (topology-only)</option>
                  </select>
                </div>

                <div class="ds-row" style="gap: 6px" aria-label="Intensity">
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
                  <button
                    class="ds-btn ds-btn--secondary"
                    style="height: 28px; padding: 0 10px"
                    type="button"
                    :disabled="!isRunActive"
                    @click="props.applyIntensity"
                  >
                    Apply
                  </button>
                </div>
              </div>
            </details>

            <div v-if="!loadingScenarios && scenarios.length === 0" class="ds-panel ds-ov-bar" aria-label="No scenarios" style="opacity: 0.92">
              <span class="ds-badge ds-badge--warn">No scenarios</span>
              <span class="ds-label">Backend must return GET /simulator/scenarios</span>
            </div>
          </div>
        </div>

        <div class="ds-ov-topbar__right" aria-label="Status">
          <div v-if="apiMode === 'real'" class="ds-row" style="gap: 8px; justify-content: flex-end; flex-wrap: wrap">
            <span :class="['ds-badge', `ds-badge--${sseTone}`]" aria-label="SSE">
              <span class="ds-dot" aria-hidden="true" /> SSE
            </span>
            <span :class="['ds-badge', `ds-badge--${runTone}`]" aria-label="Run state">
              <span class="ds-dot" aria-hidden="true" /> Run {{ runStatus?.state ?? (runId ? '…' : '—') }}
            </span>

            <div v-if="runStatus?.state === 'stopped' && stopSummary" class="ds-panel ds-ov-metric" style="opacity: 0.92" aria-label="Stop reason">
              <span class="ds-label">Stop</span>
              <span class="ds-value ds-mono" style="opacity: 0.9">{{ short(stopSummary, 64) }}</span>
            </div>

            <span v-if="runId" class="ds-badge ds-badge--info" aria-label="TX">
              TX {{ txCompact }}
            </span>
          </div>
        </div>
      </div>

      <div v-if="lastError" class="ds-alert ds-alert--err" aria-label="Error">
        <span class="ds-alert__icon">✕</span>
        <span class="ds-label">Error</span>
        <span class="ds-value ds-mono" style="opacity: 0.92">{{ short(String(lastError), 160) }}</span>
      </div>

      <div v-if="isCapacityStall" class="ds-alert ds-alert--warn" aria-label="Capacity stall">
        <span class="ds-alert__icon">!</span>
        <span class="ds-label">Stall</span>
        <span class="ds-value ds-mono" style="opacity: 0.92">All payments rejected — network capacity exhausted. Waiting for clearing to free capacity.</span>
      </div>
    </div>
  </div>
</template>
