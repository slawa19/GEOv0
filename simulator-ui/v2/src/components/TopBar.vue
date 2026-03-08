<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { SimulatorMode } from '../api/simulatorTypes'
import type { UiThemeId } from '../types/uiPrefs'
import { toLower } from '../utils/stringHelpers'
import { isJwtLike } from '../utils/isJwtLike'

import { useHudDropdownFocus } from '../composables/useHudDropdownFocus'
import { useTopBarContext } from '../composables/useTopBarContext'
import HudBar from './common/HudBar.vue'

const ctx = useTopBarContext()

const emit = defineEmits<{
  (e: 'update:selected-scenario-id', v: string): void
  (e: 'update:desired-mode', v: SimulatorMode): void
  (e: 'update:intensity-percent', v: number): void

  (e: 'go-sandbox'): void
  (e: 'go-auto-run'): void
  (e: 'go-interact'): void

  (e: 'set-ui-theme', v: UiThemeId): void

  (e: 'refresh-scenarios'): void
  (e: 'start-run'): void
  (e: 'pause'): void
  (e: 'resume'): void
  (e: 'stop'): void
  (e: 'apply-intensity'): void

  (e: 'admin-get-runs'): void
  (e: 'admin-stop-runs'): void
  (e: 'admin-attach-run', runId: string): void
  (e: 'admin-stop-run', runId: string): void
}>()

const STALL_THRESHOLD_TICKS = 3

const showRunControls = computed(() => ctx.activeSegment.value === 'auto')

const showTestModeBadge = computed(() => {
  // Show only for humans; Playwright/WebDriver runs shouldn't change screenshots.
  const isWebDriver = typeof navigator !== 'undefined' && navigator.webdriver === true
  return !!ctx.isTestMode.value && !isWebDriver
})

const sseTone = computed<'ok' | 'warn' | 'err' | 'info'>(() => {
  const s = toLower(ctx.sseState.value)
  if (s === 'open') return 'ok'
  if (s === 'reconnecting' || s === 'connecting') return 'warn'
  if (s === 'closed') return 'err'
  return 'info'
})

const runTone = computed<'ok' | 'warn' | 'err' | 'info'>(() => {
  const s = toLower(ctx.runStatus.value?.state)
  if (s === 'running') return 'ok'
  if (s === 'paused') return 'warn'
  if (s === 'error') return 'err'
  return 'info'
})

const canPause = computed(() => ctx.runStatus.value?.state === 'running')
const canResume = computed(() => ctx.runStatus.value?.state === 'paused')
const canStop = computed(() => {
  const s = ctx.runStatus.value?.state
  return s === 'running' || s === 'paused' || s === 'created'
})

const isCapacityStall = computed(() => {
  const ticks = Number(ctx.runStatus.value?.consec_all_rejected_ticks ?? 0)
  return ticks >= STALL_THRESHOLD_TICKS
})

const isRunActive = computed(() => {
  if (!ctx.runId.value) return false
  if (!ctx.runStatus.value) return true
  const s = toLower(ctx.runStatus.value?.state)
  return s === 'running' || s === 'paused' || s === 'created' || s === 'stopping'
})

const canEnterInteract = computed(() => {
  if (ctx.isInteractUi.value) return true  // уже в interact — не блокировать
  // Interact UI can auto-bootstrap a paused demo run on entry.
  // Still require real mode because actions rely on backend state.
  return ctx.apiMode.value === 'real'
})

const interactDisabledTitle = computed(() => {
  if (canEnterInteract.value) return ''
  if (ctx.apiMode.value !== 'real') return 'Real mode only'
  if (!isRunActive.value) return 'Start a run first'
  return ''
})

const successRatePct = computed(() => {
  const a = Number(ctx.runStats.value?.attempts ?? 0)
  const ok = Number(ctx.runStats.value?.committed ?? 0)
  if (a <= 0) return 0
  return Math.round((ok / a) * 100)
})

const txCompact = computed(() => {
  const ok = Number(ctx.runStats.value?.committed ?? 0)
  const a = Number(ctx.runStats.value?.attempts ?? 0)
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
  emit('set-ui-theme', (v as UiThemeId) || 'hud')
}

const stopSummary = computed(() => {
  const st = ctx.runStatus.value
  if (!st) return ''
  const src = String(st.stop_source ?? '').trim()
  const reason = String(st.stop_reason ?? '').trim()
  if (!src && !reason) return ''
  return `${src || 'unknown'}${reason ? `: ${reason}` : ''}`
})

// ============================
// §10: Admin controls
// ============================

/**
 * Admin controls are only visible when the token is an admin token (non-JWT).
 * Participant JWTs should not show admin panel.
 */
const showAdminControls = computed(() => {
  const t = String(ctx.accessToken.value ?? '').trim()
  return !!t && !isJwtLike(t) && ctx.apiMode.value === 'real'
})

const adminRunsCountLabel = computed(() => {
  if (ctx.adminRunsLoading.value) return '…'
  if (!Array.isArray(ctx.adminRuns.value)) return '—'
  return String(ctx.adminRuns.value.length)
})

const adminHasLoadedRuns = computed(() => Array.isArray(ctx.adminRuns.value))

const adminHasActiveRuns = computed(() => {
  if (!Array.isArray(ctx.adminRuns.value)) return false
  const active = new Set(['running', 'paused', 'created', 'stopping'])
  return ctx.adminRuns.value.some((r) => active.has(toLower(r.state)))
})

const adminStopAllDisabled = computed(() => {
  if (ctx.adminRunsLoading.value) return true
  if (!adminHasLoadedRuns.value) return false
  return !adminHasActiveRuns.value
})

const adminStopAllBtnClass = computed(() => {
  return adminStopAllDisabled.value ? 'ds-btn ds-btn--secondary ds-btn--sm' : 'ds-btn ds-btn--danger ds-btn--sm'
})

/** Toggle state for the admin panel (details element). */
const advancedPanelOpen = ref(false)
const adminPanelOpen = ref(false)

const {
  detailsRef: advancedDetailsRef,
  summaryRef: advancedSummaryRef,
  surfaceRef: advancedSurfaceRef,
  onToggle: onAdvancedToggle,
  onDetailsKeydown: onAdvancedDetailsKeydown,
} = useHudDropdownFocus(advancedPanelOpen)

const {
  detailsRef: adminDetailsRef,
  summaryRef: adminSummaryRef,
  surfaceRef: adminSurfaceRef,
  onToggle: onAdminToggle,
  onDetailsKeydown: onAdminDetailsKeydown,
} = useHudDropdownFocus(adminPanelOpen)

/** Preserve previous UX: open admin panel after a successful runs fetch. */
const shouldOpenAdminPanelAfterLoad = ref(false)

watch(
  () => ctx.adminRuns.value,
  (runs) => {
    if (!shouldOpenAdminPanelAfterLoad.value) return
    if (!Array.isArray(runs)) return
    adminPanelOpen.value = true
    shouldOpenAdminPanelAfterLoad.value = false
  }
)

async function onAdminGetRuns() {
  if (!ctx.adminCanGetRuns.value) return
  if (adminHasLoadedRuns.value) {
    adminPanelOpen.value = true
  } else {
    shouldOpenAdminPanelAfterLoad.value = true
  }
  emit('admin-get-runs')
}

async function onAdminStopRuns() {
  if (!ctx.adminCanStopRuns.value) return
  emit('admin-stop-runs')
}

async function onAdminAttachRun(runId: string) {
  if (!ctx.adminCanAttachRun.value) return
  if (!runId) return
  emit('admin-attach-run', runId)
}

async function onAdminStopRun(runId: string) {
  if (!ctx.adminCanStopRun.value) return
  if (!runId) return
  emit('admin-stop-run', runId)
}

function onGoSandbox() {
  emit('go-sandbox')
}

function onGoAutoRun() {
  emit('go-auto-run')
}

function onGoInteract() {
  emit('go-interact')
}

function onRefreshScenarios() {
  emit('refresh-scenarios')
}

function onStartRun() {
  emit('start-run')
}

function onPause() {
  emit('pause')
}

function onResume() {
  emit('resume')
}

function onStop() {
  emit('stop')
}

function onApplyIntensity() {
  emit('apply-intensity')
}
</script>

<template>
  <div class="ds-ov-top">
    <div class="ds-ov-top-stack">
      <HudBar variant="ghost" layout="between" aria-label="Top bar">
        <div class="hud-bar__left" aria-label="Mode">
          <div class="ds-row tb-mode-row">
            <div class="ds-segmented" role="group" aria-label="Simulator mode">
            <button
              type="button"
              class="ds-segment"
              :data-active="ctx.activeSegment.value === 'sandbox' ? '1' : '0'"
              @click="onGoSandbox"
            >
              Sandbox
            </button>
            <button
              type="button"
              class="ds-segment"
              :data-active="ctx.activeSegment.value === 'auto' ? '1' : '0'"
              @click="onGoAutoRun"
            >
              Auto-Run
            </button>
            <button
              type="button"
              class="ds-segment"
              :data-active="ctx.activeSegment.value === 'interact' ? '1' : '0'"
              :disabled="!canEnterInteract"
              :title="!canEnterInteract ? interactDisabledTitle : ''"
              @click="onGoInteract"
            >
              Interact
            </button>
            </div>

            <div class="ds-row tb-theme-row" aria-label="Theme">
              <span class="ds-label">Theme</span>
              <select
                class="ds-select tb-select-28"
                :value="ctx.uiTheme.value"
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

        <div class="hud-bar__center" aria-label="Run controls">
          <div v-if="showRunControls" class="ds-row tb-run-controls">
            <span class="ds-label">Scenario</span>
            <select
              class="ds-select"
              :value="ctx.selectedScenarioId.value"
              aria-label="Scenario"
              @change="setSelectedScenarioId(($event.target as HTMLSelectElement).value)"
            >
              <option value="">— select —</option>
              <option v-for="s in ctx.scenarios.value" :key="s.scenario_id" :value="s.scenario_id">
                {{ s.label ? `${s.label} (${s.scenario_id})` : s.scenario_id }}
              </option>
            </select>
            <button
              class="ds-btn ds-btn--icon tb-icon-28"
              type="button"
              :disabled="ctx.loadingScenarios.value"
              aria-label="Refresh scenarios"
              @click="onRefreshScenarios"
            >
              ↻
            </button>

            <button
              v-if="!isRunActive"
              class="ds-btn ds-btn--primary ds-btn--sm"
              type="button"
              :disabled="!ctx.selectedScenarioId.value"
              @click="onStartRun"
            >
              Start
            </button>
            <button
              v-if="canPause"
              class="ds-btn ds-btn--sm"
              type="button"
              @click="onPause"
            >
              Pause
            </button>
            <button
              v-if="canResume"
              class="ds-btn ds-btn--sm"
              type="button"
              @click="onResume"
            >
              Resume
            </button>
            <button
              v-if="canStop"
              class="ds-btn ds-btn--danger ds-btn--sm"
              type="button"
              @click="onStop"
            >
              Stop
            </button>

            <details
              ref="advancedDetailsRef"
              class="ds-ov-details"
              aria-label="Advanced"
              :open="advancedPanelOpen"
              @keydown.capture="onAdvancedDetailsKeydown"
              @toggle="onAdvancedToggle"
            >
              <summary
                ref="advancedSummaryRef"
                class="ds-panel ds-ov-metric ds-row tb-adv-summary"
                aria-label="Advanced settings"
              >
                <span class="ds-badge ds-badge--info">⚙</span>
                <span class="ds-label">Advanced</span>
              </summary>

              <div
                ref="advancedSurfaceRef"
                class="ds-panel ds-ov-surface ds-ov-dropdown"
                aria-label="Advanced dropdown"
                tabindex="-1"
              >
                <div class="ds-stack tb-adv-stack">
                  <div class="ds-row tb-adv-row">
                    <span class="ds-label">Pipeline</span>
                    <select
                      class="ds-select"
                      :value="ctx.desiredMode.value"
                      :disabled="isRunActive"
                      aria-label="Run pipeline"
                      @change="setDesiredMode(($event.target as HTMLSelectElement).value)"
                    >
                      <option value="real">real (DB)</option>
                      <option value="fixtures">sandbox (topology-only)</option>
                    </select>
                  </div>

                  <div class="ds-row tb-adv-row" aria-label="Intensity">
                    <span class="ds-label">Intensity</span>
                    <input
                      class="ds-input tb-intensity-input"
                      type="number"
                      min="0"
                      max="100"
                      step="1"
                      :value="ctx.intensityPercent.value"
                      aria-label="Intensity percent"
                      @input="setIntensityPercent(($event.target as HTMLInputElement).value)"
                    />
                    <button
                      class="ds-btn ds-btn--secondary ds-btn--sm"
                      type="button"
                      :disabled="!isRunActive"
                      @click="onApplyIntensity"
                    >
                      Apply
                    </button>
                  </div>
                </div>
              </div>
            </details>

            <div v-if="!ctx.loadingScenarios.value && ctx.scenarios.value.length === 0" class="ds-panel ds-ov-bar tb-fade-92" aria-label="No scenarios">
              <span class="ds-badge ds-badge--warn">No scenarios</span>
              <span class="ds-label">Backend must return GET /simulator/scenarios</span>
            </div>
          </div>
        </div>

        <div class="hud-bar__right" aria-label="Status">
          <div v-if="ctx.apiMode.value === 'real'" class="ds-row tb-status-row">
            <span :class="['ds-badge', `ds-badge--${sseTone}`]" aria-label="SSE">
              <span class="ds-dot" aria-hidden="true" /> SSE
            </span>
            <span :class="['ds-badge', `ds-badge--${runTone}`]" aria-label="Run state">
              <span class="ds-dot" aria-hidden="true" /> Run {{ ctx.runStatus.value?.state ?? (ctx.runId.value ? '…' : '—') }}
            </span>

            <div v-if="ctx.runStatus.value?.state === 'stopped' && stopSummary" class="ds-panel ds-ov-metric tb-fade-92" aria-label="Stop reason">
              <span class="ds-label">Stop</span>
              <span class="ds-value ds-mono tb-fade-90">{{ short(stopSummary, 64) }}</span>
            </div>

            <span v-if="ctx.runId.value" class="ds-badge ds-badge--info" aria-label="TX">
              TX {{ txCompact }}
            </span>

            <!-- §10: Admin controls — admin-only (requires admin token) -->
            <details
              v-if="showAdminControls"
              ref="adminDetailsRef"
              class="ds-ov-details"
              aria-label="Admin controls"
              :open="adminPanelOpen"
              @keydown.capture="onAdminDetailsKeydown"
              @toggle="onAdminToggle"
            >
              <summary
                ref="adminSummaryRef"
                class="ds-panel ds-ov-metric tb-admin-summary"
                aria-label="Admin"
              >
                <span class="ds-badge ds-badge--warn">Admin</span>
                <span class="ds-label tb-fade-75">Runs: {{ adminRunsCountLabel }}</span>
              </summary>

              <div
                ref="adminSurfaceRef"
                class="ds-panel ds-ov-surface ds-ov-dropdown ds-ov-dropdown--right tb-admin-dropdown"
                aria-label="Admin dropdown"
                tabindex="-1"
              >
                <div class="ds-stack tb-admin-stack">
                  <div class="ds-row tb-admin-actions">
                    <button
                      class="ds-btn ds-btn--secondary ds-btn--sm"
                      type="button"
                      :disabled="ctx.adminRunsLoading.value || !ctx.adminCanGetRuns.value"
                      aria-label="Refresh runs list"
                      @click="onAdminGetRuns"
                    >
                      {{ ctx.adminRunsLoading.value ? '…' : 'Refresh' }}
                    </button>
                    <button
                      :class="adminStopAllBtnClass"
                      type="button"
                      aria-label="Stop all runs"
                      :disabled="adminStopAllDisabled || !ctx.adminCanStopRuns.value"
                      :title="adminStopAllDisabled && adminHasLoadedRuns ? 'No running runs' : ''"
                      @click="onAdminStopRuns"
                    >
                      Stop all
                    </button>
                    <span
                      v-if="ctx.adminLastError.value"
                      class="ds-badge ds-badge--err tb-admin-last-error"
                      :title="ctx.adminLastError.value"
                    >
                      {{ short(ctx.adminLastError.value, 40) }}
                    </span>
                  </div>

                  <div v-if="ctx.adminRunsLoading.value" class="ds-label tb-fade-75" aria-label="Runs loading">
                    Loading…
                  </div>

                  <div
                    v-else-if="ctx.adminRuns.value && ctx.adminRuns.value.length > 0"
                    class="ds-stack tb-runs-list"
                    aria-label="Runs list"
                  >
                    <div
                      v-for="run in ctx.adminRuns.value"
                      :key="run.run_id"
                      class="tb-run-item"
                    >
                        <span class="ds-label ds-mono tb-run-id" :title="run.run_id">{{ run.run_id.slice(0, 8) }}</span>
                        <span :class="['ds-badge', 'tb-run-state', toLower(run.state) === 'running' ? 'ds-badge--ok' : 'ds-badge--info']">{{ run.state }}</span>
                        <span class="ds-label tb-run-scenario" :title="String(run.scenario_id ?? '')">{{ short(String(run.scenario_id ?? ''), 22) }}</span>
                        <span class="ds-label ds-mono tb-run-owner" :title="String(run.owner_id ?? '')">{{ short(String(run.owner_id ?? ''), 12) }}</span>

                        <div class="ds-row tb-run-actions">
                          <button
                            v-if="ctx.adminCanAttachRun.value"
                            class="ds-btn ds-btn--secondary tb-run-btn"
                            type="button"
                            aria-label="Attach to selected run"
                            @click="onAdminAttachRun(run.run_id)"
                          >
                            Attach
                          </button>
                          <button
                            v-if="ctx.adminCanStopRun.value"
                            class="ds-btn ds-btn--danger tb-run-btn"
                            type="button"
                            aria-label="Stop selected run"
                            @click="onAdminStopRun(run.run_id)"
                          >
                            Stop
                          </button>
                        </div>
                    </div>
                  </div>

                  <div v-else-if="adminHasLoadedRuns" class="ds-label tb-fade-75" aria-label="Runs empty">
                    No active runs
                  </div>

                  <div v-else class="ds-label tb-fade-75" aria-label="Runs not loaded">
                    Press Refresh to load runs
                  </div>
                </div>
              </div>
            </details>
          </div>
        </div>
      </HudBar>

      <div v-if="ctx.lastError.value" class="ds-alert ds-alert--err" aria-label="Error">
        <span class="ds-alert__icon">✕</span>
        <span class="ds-label">Error</span>
        <span class="ds-value ds-mono tb-fade-92">{{ short(String(ctx.lastError.value), 160) }}</span>
      </div>

      <div v-if="isCapacityStall" class="ds-alert ds-alert--warn" aria-label="Capacity stall">
        <span class="ds-alert__icon">!</span>
        <span class="ds-label">Stall</span>
        <span class="ds-value ds-mono tb-fade-92">All payments rejected — network capacity exhausted. Waiting for clearing to free capacity.</span>
      </div>

      <!-- Slot for HUD elements (SystemBalanceBar, ActionBar) that stack below TopBar rows -->
      <slot />

    </div>
  </div>
</template>

<style scoped>
.tb-mode-row {
  gap: var(--ds-space-3, 12px);
  flex-wrap: wrap;
  align-items: center;
}

.tb-theme-row {
  gap: var(--ds-space-2, 8px);
  align-items: center;
}

.tb-select-28 {
  height: var(--ds-tb-control-h);
}

.tb-run-controls {
  gap: var(--ds-space-2, 8px);
  flex-wrap: wrap;
}

.tb-icon-28 {
  height: var(--ds-tb-icon-size);
  width: var(--ds-tb-icon-size);
}

.tb-adv-summary {
  gap: var(--ds-space-2, 8px);
}

.tb-adv-stack {
  gap: var(--ds-space-2, 8px);
}

.tb-adv-row {
  gap: var(--ds-space-2, 8px);
}

.tb-intensity-input {
  width: var(--ds-tb-intensity-input-w);
  height: var(--ds-tb-control-h);
}

.tb-status-row {
  gap: var(--ds-space-2, 8px);
  justify-content: flex-end;
  flex-wrap: wrap;
}

.tb-admin-summary {
  display: flex;
  gap: var(--ds-space-2, 8px);
  align-items: center;
}

.tb-admin-stack {
  gap: var(--ds-space-2, 8px);
}

.tb-admin-actions {
  gap: var(--ds-space-2, 8px);
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.tb-admin-last-error {
  max-width: var(--ds-tb-admin-last-error-maxw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tb-admin-dropdown {
  --ds-ov-dropdown-minw: var(--ds-tb-admin-dropdown-minw);
  --ds-ov-dropdown-maxw: var(--ds-tb-admin-dropdown-maxw);
  --ds-ov-dropdown-vw-inset: var(--ds-tb-admin-dropdown-vw-inset);
}

.tb-runs-list {
  gap: var(--ds-space-1, 4px);
  max-height: var(--ds-tb-runs-maxh);
  overflow-y: auto;
}

.tb-run-item {
  display: grid;
  grid-template-columns: var(--ds-tb-run-id-col-w) var(--ds-tb-run-state-col-w) 1fr auto auto;
  align-items: center;
  gap: var(--ds-space-2, 8px);
  padding: var(--ds-tb-run-item-pad-y) var(--ds-tb-run-item-pad-x);
  background: var(--ds-surface-1);
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-lg);
  min-width: 0;
}

.tb-run-id {
  font-size: var(--ds-tb-run-id-font-size);
  white-space: nowrap;
}

.tb-run-state {
  font-size: var(--ds-tb-run-state-font-size);
  text-align: center;
  white-space: nowrap;
}

.tb-run-scenario {
  opacity: var(--ds-tb-run-scenario-opacity);
  font-size: var(--ds-tb-run-meta-font-size);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.tb-run-owner {
  opacity: var(--ds-tb-run-owner-opacity);
  font-size: var(--ds-tb-run-meta-font-size);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tb-run-actions {
  gap: var(--ds-space-1, 4px);
  align-items: center;
  flex-shrink: 0;
}

.tb-run-btn {
  height: var(--ds-tb-run-btn-h);
  padding: 0 var(--ds-tb-run-btn-pad-x);
  font-size: var(--ds-tb-run-meta-font-size);
  white-space: nowrap;
}

.tb-fade-75 {
  opacity: var(--ds-tb-fade-75-opacity);
}

.tb-fade-90 {
  opacity: var(--ds-tb-fade-90-opacity);
}

.tb-fade-92 {
  opacity: var(--ds-tb-fade-92-opacity);
}

/* ── Responsive: prevent horizontal overflow on narrow viewports ─────────── */
@media (max-width: 500px) {
  /* Segmented control: allow wrapping inside */
  :deep(.ds-segmented) {
    flex-wrap: wrap;
    min-width: 0;
  }

  /* Each segment button: shrink to fit */
  :deep(.ds-segment) {
    min-width: 0;
    flex-shrink: 1;
  }

  /* Theme row: allow wrapping */
  .tb-theme-row {
    flex-wrap: wrap;
  }

  /* Mode row: already has flex-wrap:wrap - ensure min-width:0 */
  .tb-mode-row {
    min-width: 0;
  }
}
</style>
