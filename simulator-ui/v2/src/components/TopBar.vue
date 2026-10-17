<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { normalizeUiThemeId, type UiThemeId } from '../types/uiPrefs'
import { toLower } from '../utils/stringHelpers'
import { isJwtLike } from '../utils/isJwtLike'

import { useHudDropdownFocus } from '../composables/useHudDropdownFocus'
import { useTopBarContext } from '../composables/useTopBarContext'
import HudBar from './common/HudBar.vue'

const ctx = useTopBarContext()

/**
 * 043 S1: `detailsOpen` is the one "Подробнее" toggle (owned and remembered by the root). Off: the first row only -
 * the network's name and state as text, the protocol actions (slot `actions`) and the toggle itself. On: the row of
 * service controls below it (mode, theme, scenario and run controls, statuses, Admin) and the capacity-stall alert.
 * Hidden means NOT RENDERED (`v-if`), never hidden by CSS: a hidden control must not stay in the tab order.
 */
const props = withDefaults(defineProps<{ detailsOpen?: boolean }>(), { detailsOpen: false })

const emit = defineEmits<{
  (e: 'toggle-details'): void

  (e: 'update:selected-scenario-id', v: string): void
  (e: 'update:intensity-percent', v: number): void

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

/** 043 S2: one screen - the scenario and run controls (behind "Подробнее") exist wherever the source is the server. */
const showRunControls = computed(() => ctx.apiMode.value === 'real')

/**
 * 043 S2 (decisions 1 and 3): "Поток" in the first row - pause (⏸) a living run, resume (▶) a paused one. Rendered only
 * for a run in one of those two states: there is nothing to pause or resume otherwise (no run, a run being started or
 * stopped, an ended one - Start lives under "Подробнее"). `aria-pressed` says whether the flow is on.
 */
const flowState = computed<'running' | 'paused' | null>(() => {
  if (ctx.apiMode.value !== 'real') return null
  const s = toLower(ctx.runStatus.value?.state)
  return s === 'running' || s === 'paused' ? s : null
})
function onFlowToggle() {
  if (flowState.value === 'running') emit('pause')
  else if (flowState.value === 'paused') emit('resume')
}

/**
 * 043 S2 (review B1): the name of a scenario as the API gives it - `name` (the decoder, `simulatorContracts.ts`); `label`
 * is a UI-only compatibility field the REST decoder never fills. Empty when neither is set: the caller shows the id.
 */
function scenarioTitle(s: { name?: string | null; label?: string } | undefined): string {
  return s?.name?.trim() || s?.label?.trim() || ''
}

/** The network on screen, as text: the label of the scenario of the run (or of the selected one), its id otherwise. */
const networkName = computed(() => {
  if (ctx.apiMode.value !== 'real') return 'офлайн-демо'
  const id = String(ctx.runStatus.value?.scenario_id ?? '').trim() || String(ctx.selectedScenarioId.value ?? '').trim()
  if (!id) return '—'
  const s = ctx.scenarios.value.find((x) => x.scenario_id === id)
  return scenarioTitle(s) || id
})

/** The state of the network as words (decision 1: "точка состояния (текст)"); the tone colours the dot only. */
const networkState = computed<{ text: string; tone: 'ok' | 'warn' | 'err' | 'info' }>(() => {
  if (ctx.apiMode.value !== 'real') return { text: 'снимок без сервера', tone: 'info' }
  const s = toLower(ctx.runStatus.value?.state)
  if (s === 'running') return { text: 'живёт', tone: 'ok' }
  if (s === 'paused') return { text: 'на паузе', tone: 'warn' }
  if (s === 'created') return { text: 'запускается', tone: 'info' }
  if (s === 'stopping') return { text: 'останавливается', tone: 'warn' }
  if (s === 'stopped') return { text: 'остановлена', tone: 'info' }
  if (s === 'error') return { text: 'ошибка', tone: 'err' }
  return { text: ctx.runId.value ? '…' : 'нет прогона', tone: 'info' }
})

function onToggleDetails() {
  emit('toggle-details')
}

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
  emit('set-ui-theme', normalizeUiThemeId(v))
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
      <!-- 043 S1: the first row - always on screen. -->
      <HudBar variant="ghost" layout="between" class="tb-main-row">
        <div class="hud-bar__left tb-identity">
          <span class="ds-panel ds-ov-metric tb-network-name" data-testid="network-name">
            <span class="ds-label">GEO</span>
            <span class="ds-value">{{ networkName }}</span>
          </span>
          <span class="ds-panel ds-ov-metric tb-network-state" data-testid="network-state">
            <span :class="['ds-badge', `ds-badge--${networkState.tone}`, 'tb-state-dot']"><span class="ds-dot" aria-hidden="true" /></span>
            <span class="ds-value">{{ networkState.text }}</span>
          </span>
          <button
            v-if="flowState"
            type="button"
            class="ds-btn ds-btn--secondary ds-btn--sm tb-flow-toggle"
            data-testid="flow-toggle"
            :aria-pressed="flowState === 'running' ? 'true' : 'false'"
            :title="flowState === 'running' ? 'Поставить автоматические платежи на паузу' : 'Возобновить автоматические платежи'"
            @click="onFlowToggle"
          >
            <span aria-hidden="true">{{ flowState === 'running' ? '⏸' : '▶' }}</span> Поток
          </button>
        </div>

        <div class="hud-bar__center tb-actions">
          <slot name="actions" />
        </div>

        <div class="hud-bar__right">
          <button
            type="button"
            class="ds-btn ds-btn--secondary ds-btn--sm tb-details-toggle"
            data-testid="details-toggle"
            :aria-expanded="props.detailsOpen ? 'true' : 'false'"
            @click="onToggleDetails"
          >
            Подробнее <span aria-hidden="true">{{ props.detailsOpen ? '▴' : '▾' }}</span>
          </button>
        </div>
      </HudBar>

      <HudBar v-if="props.detailsOpen" variant="ghost" layout="between" aria-label="Top bar" data-testid="top-details-row">
        <div class="hud-bar__left" aria-label="Mode">
          <div class="ds-row tb-mode-row">
            <div class="ds-row tb-theme-row" aria-label="Theme">
              <span class="ds-label">Theme</span>
              <select
                class="ds-select tb-select-28"
                :value="ctx.uiTheme.value"
                aria-label="UI theme"
                @change="onThemeChange(($event.target as HTMLSelectElement).value)"
              >
                <!-- 039 B3 (owner's decision R1): two themes; shadcn and library are frozen (tokens kept, not offered). -->
                <option value="saas">SaaS (subtle)</option>
                <option value="hud">HUD (sci-fi)</option>
              </select>
            </div>

            <span v-if="showTestModeBadge" class="ds-badge ds-badge--warn">TEST MODE</span>
          </div>
        </div>

        <!-- 043 S1: named only when it holds the run controls (Auto-Run); an empty div with a name is an axe violation (aria-prohibited-attr). -->
        <div class="hud-bar__center" :aria-label="showRunControls ? 'Run controls' : undefined">
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
                {{ scenarioTitle(s) ? `${scenarioTitle(s)} (${s.scenario_id})` : s.scenario_id }}
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
            <span
              :class="['ds-badge', `ds-badge--${runTone}`]"
              aria-label="Run state"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
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

      <div
        v-if="ctx.lastError.value"
        class="ds-alert ds-alert--err"
        aria-label="Error"
        role="alert"
        aria-atomic="true"
      >
        <span class="ds-alert__icon">✕</span>
        <span class="ds-label">Error</span>
        <span class="ds-value ds-mono tb-fade-92" :title="String(ctx.lastError.value)">{{ short(String(ctx.lastError.value), 160) }}</span>
      </div>

      <div v-if="props.detailsOpen && isCapacityStall" class="ds-alert ds-alert--warn" aria-label="Capacity stall">
        <span class="ds-alert__icon">!</span>
        <span class="ds-label">Stall</span>
        <span class="ds-value ds-mono tb-fade-92">All payments rejected — network capacity exhausted. Waiting for clearing to free capacity.</span>
      </div>

      <!-- Slot for HUD elements that stack below the TopBar rows (043 S1: the full metrics, while Details is on). -->
      <slot />

    </div>
    <!-- 043 S1: the one onboarding hint, laid under the stack and out of its measured height. -->
    <slot name="hint" />
  </div>
</template>

<style scoped>
.tb-identity {
  gap: var(--ds-space-2, 8px);
}

.tb-network-name,
.tb-network-state {
  display: inline-flex;
  align-items: center;
  gap: var(--ds-space-2, 8px);
  white-space: nowrap;
}

.tb-state-dot {
  padding: 0;
  border: 0;
  background: transparent;
}

.tb-actions {
  justify-content: center;
}

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
