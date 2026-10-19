<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, toRaw, watch } from 'vue'
import { normalizeUiThemeId, type UiThemeId } from '../types/uiPrefs'
import type { ArtifactIndexItem } from '../api/simulatorTypes'
import { toLower } from '../utils/stringHelpers'

import { useHudDropdownFocus } from '../composables/useHudDropdownFocus'
import { isAdminViewer, useTopBarContext } from '../composables/useTopBarContext'
import { useUiLocale } from '../composables/useUiLocale'
import { runStopText } from '../i18n/statusText'
import HudBar from './common/HudBar.vue'
import LanguageSwitch from './LanguageSwitch.vue'

const ctx = useTopBarContext()
const { t } = useUiLocale()

/**
 * 043 S1: `detailsOpen` is the one "Подробнее" toggle (owned and remembered by the root). Off: the first row only -
 * the network's name and state as text, the protocol actions (slot `actions`) and the toggle itself. On: the row of
 * service controls below it (theme, language, scenario and run controls, statuses, Admin). The capacity-stall alert
 * is not behind it since 048 S1 (below).
 * Hidden means NOT RENDERED (`v-if`), never hidden by CSS: a hidden control must not stay in the tab order.
 *
 * 048 S1 (decisions 1, 2, 4, 5): three addressees, three existing entries. The visitor: the first view and
 * "Подробнее". The administrator (an admin token): "Admin ▾" holds the runs, the run files (`runFiles`, moved from the
 * bottom bar), the refresh of the scenario catalogue and the analytics dock's toggle. The developer (`debug`, the
 * `?debug=1` of the root): the SSE badge. The capacity-stall alert is on the first view.
 */
type RunFiles = {
  runId: string | null
  /** The run's file index as last read (`real.artifacts`); a read replaces the array, a failed read leaves it. */
  items: readonly ArtifactIndexItem[]
  loading: boolean
  /** Resolves with the index array a successful read of the current attachment installed, or `null`. */
  refresh: () => Promise<readonly ArtifactIndexItem[] | null>
  download: (name: string) => void
}

const props = withDefaults(
  defineProps<{
    detailsOpen?: boolean
    /** 048 S1: `?debug=1` (read by `useSimulatorApp`). Off: no SSE badge. */
    debug?: boolean
    /** 048 S1: the analytics dock (spec 007) is the administrator's: its toggle is in "Admin ▾". */
    analyticsOpen?: boolean
    /** 048 S1: the run files ("Artifacts") in "Admin ▾". Absent: no such section. */
    runFiles?: RunFiles
  }>(),
  { detailsOpen: false, debug: false, analyticsOpen: false, runFiles: undefined },
)

const emit = defineEmits<{
  (e: 'toggle-details'): void
  (e: 'toggle-analytics'): void

  (e: 'update:selected-scenario-id', v: string): void
  (e: 'update:intensity-percent', v: number): void

  (e: 'set-ui-theme', v: UiThemeId): void

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
/** 048 S1: the run's state as the server names it, on the state of the first row (`data-run-state`) - for a machine. */
const flowRunState = computed(() => (ctx.apiMode.value === 'real' ? toLower(ctx.runStatus.value?.state) : ''))

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

/** The text of a scenario's option: "<name> (<id>)", or the id when the API gives no name. */
function scenarioOptionText(s: { scenario_id: string; name?: string | null; label?: string }): string {
  const title = scenarioTitle(s)
  return title ? `${title} (${s.scenario_id})` : s.scenario_id
}

/**
 * 043 S3 (Codex §15, P2): the Scenario select is narrower than its longest option at desktop sizes; the full text of
 * the chosen option is its `title`, so nothing of the name is lost to a pointer or to assistive tech.
 */
const selectedScenarioText = computed(() => {
  const id = String(ctx.selectedScenarioId.value ?? '')
  const s = ctx.scenarios.value.find((x) => x.scenario_id === id)
  return s ? scenarioOptionText(s) : ''
})

/** The network on screen, as text: the label of the scenario of the run (or of the selected one), its id otherwise. */
const networkName = computed(() => {
  if (ctx.apiMode.value !== 'real') return 'офлайн-демо'
  const id = String(ctx.runStatus.value?.scenario_id ?? '').trim() || String(ctx.selectedScenarioId.value ?? '').trim()
  if (!id) return '—'
  const s = ctx.scenarios.value.find((x) => x.scenario_id === id)
  return scenarioTitle(s) || id
})

/**
 * 045 S2 (Codex §15 P2): is the name in the first row cut by its ellipsis? Measured on the element (a resize of the row or a
 * new name re-measures). Only a cut name gets its full form behind "Подробнее" - a name that fits adds no row there.
 */
const networkNameEl = ref<HTMLElement | null>(null)
const networkNameCut = ref(false)
function measureNetworkName() {
  const el = networkNameEl.value
  networkNameCut.value = !!el && el.scrollWidth > el.clientWidth + 0.5
}
let networkNameRo: ResizeObserver | null = null
onMounted(() => {
  measureNetworkName()
  if (typeof ResizeObserver === 'undefined' || !networkNameEl.value) return
  networkNameRo = new ResizeObserver(() => measureNetworkName())
  networkNameRo.observe(networkNameEl.value)
})
onBeforeUnmount(() => {
  networkNameRo?.disconnect()
  networkNameRo = null
})
watch(networkName, () => void nextTick(measureNetworkName))

/**
 * 048 S1 (decision 5; §15 Codex P2, 2026-10-18): a living run (running or paused) whose event stream is lost - SSE
 * `reconnecting` or `closed`; a stream that ends normally is followed by `reconnecting` while REST still says
 * `running`, and no error line is written (`useSimulatorRealMode.ts`, `runSseLoop`) - is "no connection" in the state
 * of the network: the raw SSE badge is the developer's (`?debug=1`), and a green "живёт" over a dead stream hid the
 * loss from the visitor. `connecting` (the first connection of a run) is not a loss.
 */
const streamLost = computed(() => {
  const sse = toLower(ctx.sseState.value)
  return sse === 'reconnecting' || sse === 'closed'
})

/** The state of the network as words (decision 1: "точка состояния (текст)"); the tone colours the dot only. */
const networkState = computed<{ text: string; tone: 'ok' | 'warn' | 'err' | 'info' }>(() => {
  if (ctx.apiMode.value !== 'real') return { text: 'снимок без сервера', tone: 'info' }
  const s = toLower(ctx.runStatus.value?.state)
  if ((s === 'running' || s === 'paused') && streamLost.value) return { text: t('topBar.noConnection'), tone: 'err' }
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

const sseTone = computed<'ok' | 'warn' | 'err' | 'info'>(() => {
  const s = toLower(ctx.sseState.value)
  if (s === 'open') return 'ok'
  if (s === 'reconnecting' || s === 'connecting') return 'warn'
  if (s === 'closed') return 'err'
  return 'info'
})

// 048 S1 (decision 4): no "Run <state>" badge (the state dot of the first row says the same) and no Pause/Resume of the
// service row ("Поток" pauses and resumes; the `pause`/`resume` events are its).
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

/**
 * 046 wave 1: why the run stopped, in the language of the interface - the server's `stop_reason` through its closed set
 * (`runStopText`: a known reason is a sentence, any other value the general text with the value itself). A stop with a
 * source but no reason says nothing readable, so it shows no line. The raw `source: reason` is the line's `title`, for
 * correlation with the server's log (AGENTS.md section 12).
 */
const stopSummary = computed(() => runStopText(ctx.runStatus.value?.stop_reason))
const stopSummaryRaw = computed(() => {
  const st = ctx.runStatus.value
  const src = String(st?.stop_source ?? '').trim()
  const reason = String(st?.stop_reason ?? '').trim()
  return [src, reason].filter(Boolean).join(': ')
})

/**
 * BACKLOG 043-3 (046 wave 1): the scenario select changed the choice under a live run while the run went on - nothing
 * happened. While a run is live (`isRunActive`: running, paused, created, stopping, or a run whose status is not read
 * yet) the select is disabled and says why - in its `title` (a pointer) and in its accessible description (a screen
 * reader); Stop is beside it. Not as a visible line: a line of its own made the service row one line taller for as
 * long as a run lives, which is most of the time (the stand's `empty-scene` counter-check lost a node to it).
 */
const scenarioLocked = computed(() => isRunActive.value)
const scenarioSelectTitle = computed(() => {
  if (!scenarioLocked.value) return selectedScenarioText.value || undefined
  const why = t('topBar.scenarioLocked')
  return selectedScenarioText.value ? `${selectedScenarioText.value} — ${why}` : why
})

/** 046 wave 1: the error line is a start the global limit of runs refused - "Try again" starts again (as Start does). */
const startRetryable = computed(() => !!ctx.startRetryable?.value)

// ============================
// §10: Admin controls
// ============================

/**
 * Admin controls are only visible when the token is an admin token (non-JWT).
 * Participant JWTs should not show admin panel.
 */
const showAdminControls = computed(() => isAdminViewer(ctx.accessToken.value, ctx.apiMode.value))

/**
 * 048 S1 (decision 5): the run files under "Admin ▾". Their number is shown only once the index ON SCREEN has been
 * read: "not read yet" is not "no files". The marker is the index array a SUCCESSFUL read of the current attachment
 * installed - the owner says which (`useSimulatorRealMode.ts` `refreshArtifacts` resolves with it, or with `null` for a
 * failed read, no run, or a read whose attachment was reset while it was in flight). Every reset of the index (a start,
 * an attach - also of the same run again -, a stale run) puts a new empty array, which is not the remembered one.
 * §15 Codex (2026-10-18): the marker was the run id (attaching A again showed "0 / No artifacts"), then "the same run,
 * a different array after the await" - which a failed read across an Attach of the same run also satisfied. Nothing is
 * inferred across the await now.
 */
const runFilesReadIndex = shallowRef<readonly ArtifactIndexItem[] | null>(null)
const runFilesRead = computed(() => {
  const f = props.runFiles
  return !!f?.runId && ((runFilesReadIndex.value !== null && toRaw(f.items) === runFilesReadIndex.value) || f.items.length > 0)
})
async function onRefreshRunFiles() {
  const f = props.runFiles
  if (!f?.runId || f.loading) return
  const installed = await f.refresh()
  if (installed) runFilesReadIndex.value = toRaw(installed)
}

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
const adminPanelOpen = ref(false)

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

function onStartRun() {
  emit('start-run')
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
            <span ref="networkNameEl" class="ds-value tb-network-name__value" :title="networkName || undefined">{{ networkName }}</span>
          </span>
          <!-- 048 S1: the live region of the removed "Run <state>" badge - the state is announced from here. -->
          <span
            class="ds-panel ds-ov-metric tb-network-state"
            data-testid="network-state"
            :data-run-state="flowRunState || undefined"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
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

      <!--
        045 S2 (Codex §15 P2): the first row cuts a long network name (its `title` is for a pointer only). Behind "Подробнее" the
        name of the network on screen - the scenario of the RUN, the selected one only while there is no run (`networkName`;
        not `selectedScenarioText`, which is the selection and may differ) - is shown in full, as text: a keyboard user reaches it
        with the toggle, and the first view gains no focus stop. Only when the first row cuts it: a name that fits adds no row.
      -->
      <HudBar v-if="props.detailsOpen && networkNameCut" variant="ghost" layout="start" class="tb-network-full-row">
        <span class="ds-panel ds-ov-metric tb-network-full" data-testid="details-network-name">
          <span class="ds-label">Network</span>
          <span class="ds-value">{{ networkName }}</span>
        </span>
      </HudBar>

      <HudBar v-if="props.detailsOpen" variant="ghost" layout="between" class="tb-details-row" aria-label="Top bar" data-testid="top-details-row">
        <div class="hud-bar__left" aria-label="Mode">
          <div class="ds-row tb-mode-row">
            <div class="ds-row tb-theme-row" aria-label="Theme">
              <span class="ds-label">Theme</span>
              <select
                class="ds-select tb-select-28"
                :value="ctx.uiTheme.value"
                aria-label="UI theme"
                data-testid="theme-select"
                @change="onThemeChange(($event.target as HTMLSelectElement).value)"
              >
                <!-- 039 B3 (owner's decision R1): two themes; shadcn and library are frozen (tokens kept, not offered). -->
                <option value="saas">SaaS (subtle)</option>
                <option value="hud">HUD (sci-fi)</option>
              </select>
            </div>
            <!-- 046 (decision 2): the language, beside the theme - behind "Подробнее", not on the first view (043: <= 7 stops). -->
            <LanguageSwitch />
            <!-- 048 S1 (decision 4): no TEST MODE badge (the flag `isTestMode` stays: it shapes the stand). -->
          </div>
        </div>

        <!-- 043 S1: named only when it holds the run controls (Auto-Run); an empty div with a name is an axe violation (aria-prohibited-attr). -->
        <div class="hud-bar__center" :aria-label="showRunControls ? 'Run controls' : undefined">
          <div v-if="showRunControls" class="ds-row tb-run-controls">
            <span class="ds-label">Scenario</span>
            <select
              class="ds-select tb-scenario-select"
              :value="ctx.selectedScenarioId.value"
              :title="scenarioSelectTitle"
              :disabled="scenarioLocked"
              :aria-describedby="scenarioLocked ? 'tb-scenario-locked' : undefined"
              data-testid="scenario-select"
              aria-label="Scenario"
              @change="setSelectedScenarioId(($event.target as HTMLSelectElement).value)"
            >
              <option value="">— select —</option>
              <option v-for="s in ctx.scenarios.value" :key="s.scenario_id" :value="s.scenario_id">
                {{ scenarioOptionText(s) }}
              </option>
            </select>
            <!-- 048 S1 (decision 5): the refresh of the scenario catalogue (↻) is in "Admin ▾". -->
            <span v-if="scenarioLocked" id="tb-scenario-locked" class="tb-visually-hidden" data-testid="scenario-locked">{{ t('topBar.scenarioLocked') }}</span>

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
              v-if="canStop"
              class="ds-btn ds-btn--danger ds-btn--sm"
              type="button"
              @click="onStop"
            >
              Stop
            </button>

            <!-- 043 S3 (decision 5): the tempo sits in the row itself; the "Advanced ⚙" menu that held only it is gone. -->
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

            <div v-if="!ctx.loadingScenarios.value && ctx.scenarios.value.length === 0" class="ds-panel ds-ov-bar tb-fade-92" aria-label="No scenarios">
              <span class="ds-badge ds-badge--warn">No scenarios</span>
              <span class="ds-label">Backend must return GET /simulator/scenarios</span>
            </div>
          </div>
        </div>

        <div class="hud-bar__right" aria-label="Status">
          <div v-if="ctx.apiMode.value === 'real'" class="ds-row tb-status-row">
            <!-- 048 S1 (decision 5): the raw SSE badge is the developer's (`?debug=1`). -->
            <span v-if="props.debug" :class="['ds-badge', `ds-badge--${sseTone}`]" aria-label="SSE" data-testid="sse-badge">
              <span class="ds-dot" aria-hidden="true" /> SSE
            </span>

            <div
              v-if="ctx.runStatus.value?.state === 'stopped' && stopSummary"
              class="ds-panel ds-ov-metric tb-fade-92"
              :aria-label="t('topBar.stopReasonAria')"
              data-testid="stop-reason"
              :title="stopSummaryRaw || undefined"
            >
              <span class="ds-label">{{ t('topBar.stopLabel') }}</span>
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
              data-testid="admin-menu"
              aria-label="Admin controls"
              :open="adminPanelOpen"
              @keydown.capture="onAdminDetailsKeydown"
              @toggle="onAdminToggle"
            >
              <summary
                ref="adminSummaryRef"
                class="ds-panel ds-ov-metric tb-admin-summary"
                data-testid="admin-summary"
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

                  <!-- 048 S1 (decision 1): the analytics dock's toggle (from the bottom bar). No refresh of the scenario
                       catalogue anywhere in the UI (the owner's word, 2026-10-19: an edge case the interface ignores): the
                       catalogue is read when the page loads; after an admin uploads a scenario, a reload picks it up. -->
                  <div class="ds-row tb-admin-actions" data-testid="admin-tools">
                    <button
                      class="ds-btn ds-btn--ghost ds-btn--sm"
                      type="button"
                      data-testid="analytics-toggle"
                      :aria-pressed="props.analyticsOpen ? 'true' : 'false'"
                      @click="emit('toggle-analytics')"
                    >
                      Analytics
                    </button>
                  </div>

                  <!-- 048 S1 (decision 5): the run files ("Artifacts", from the bottom bar). -->
                  <div v-if="props.runFiles" class="ds-stack tb-admin-files" role="group" aria-label="Artifacts" data-testid="admin-run-files">
                    <div class="ds-row tb-admin-actions">
                      <span class="ds-badge ds-badge--info">Artifacts</span>
                      <span v-if="runFilesRead" class="ds-value tb-fade-75" data-testid="admin-run-files-count">{{ props.runFiles.items.length }}</span>
                      <button
                        class="ds-btn ds-btn--secondary ds-btn--sm"
                        type="button"
                        aria-label="Refresh artifacts"
                        :disabled="!props.runFiles.runId || props.runFiles.loading"
                        :title="!props.runFiles.runId ? 'Start a run first' : 'Refresh artifacts'"
                        @click="onRefreshRunFiles"
                      >
                        {{ props.runFiles.loading ? 'Loading…' : 'Refresh' }}
                      </button>
                      <span v-if="!props.runFiles.runId" class="ds-label tb-fade-75">Start a run first</span>
                      <span v-else-if="runFilesRead && !props.runFiles.items.length" class="ds-label tb-fade-75">No artifacts</span>
                    </div>
                    <div v-if="props.runFiles.items.length" class="ds-row tb-admin-actions">
                      <button
                        v-for="a in props.runFiles.items"
                        :key="a.name"
                        class="ds-btn ds-btn--secondary ds-btn--sm"
                        type="button"
                        @click="props.runFiles.download(a.name)"
                      >
                        {{ a.name }}
                      </button>
                    </div>
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
        :aria-label="t('topBar.errorAria')"
        role="alert"
        aria-atomic="true"
        data-testid="top-error"
      >
        <span class="ds-alert__icon">✕</span>
        <span class="ds-label">{{ t('topBar.errorLabel') }}</span>
        <span class="ds-value ds-mono tb-fade-92" :title="String(ctx.lastError.value)">{{ short(String(ctx.lastError.value), 160) }}</span>
        <!-- 046 wave 1: a start refused by the limit of runs (409 global_active_limit) can be tried again from here, on the first view. -->
        <button
          v-if="startRetryable"
          type="button"
          class="ds-btn ds-btn--secondary ds-btn--sm tb-retry"
          data-testid="start-retry"
          @click="onStartRun"
        >
          {{ t('topBar.retry') }}
        </button>
      </div>

      <!-- 048 S1 (decision 2): the one diagnosis a visitor gets - on the first view, not behind "Подробнее" (its words come
           from the dictionary with 046 wave 1). Shown only while payments stall: `consec_all_rejected_ticks` >= threshold. -->
      <div v-if="isCapacityStall" class="ds-alert ds-alert--warn" aria-label="Capacity stall" data-testid="capacity-stall">
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
/*
  043 S2 (owner's decision on review item B2, 2026-10-17): the three actions sit in the centre of the row whatever the
  width of the sides - the state text and the flow button change width ("живёт" / "на паузе", ⏸ / ▶) the moment an
  action pauses the run, and with a flex row the centred group moved under the person's pointer. Two equal side tracks
  keep the centre track fixed.
*/
.tb-main-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
}

.tb-main-row > .hud-bar__right {
  justify-self: end;
}

.tb-identity {
  gap: var(--ds-space-2, 8px);
}

/*
  043 S3: the service row keeps its status cluster (SSE, Run, TX, Admin) on the right of its first line. The run
  controls in the middle (scenario, Start/Stop, the tempo that left the "Advanced" menu) take the rest of the line and
  wrap inside themselves, instead of pushing the cluster onto a line of its own: there the Admin summary landed on the
  left, and its right-anchored menu, opened while the summary was still on the right, ended past the left edge (stand,
  `menu-admin` in `saas` at 1366x768: x 8.5 against a pad of 14).
*/
.tb-details-row > .hud-bar__center {
  flex: 1 1 0;
  /*
    Codex §15 (P2 on the rule above, 2026-10-17): a zero basis alone hid the middle's content from the row's own wrapping,
    and its widest unbreakable item (the Scenario select, as wide as its longest option) then spilled over the theme and
    the status cluster. The middle counts its min-content: the row wraps the cluster before anything overlaps, and the
    min-content is kept small by the cap on the select below.
  */
  min-width: min-content;
}

/* The Scenario select at desktop sizes: capped, its text cut by the control; the whole option is its `title`. */
.tb-scenario-select {
  max-width: 18rem;
  min-width: 0;
  text-overflow: ellipsis;
}

/* ...and the tempo wraps as a whole, never between its label, its field and Apply (narrow screens wrap: below). */
@media (min-width: 501px) {
  .tb-details-row .tb-adv-row {
    flex-wrap: nowrap;
    flex-shrink: 0;
  }
}

.tb-network-name,
.tb-network-state {
  display: inline-flex;
  align-items: center;
  gap: var(--ds-space-2, 8px);
  white-space: nowrap;
}

/* 045 S2: the full network name behind "Подробнее" wraps rather than being cut. */
.tb-network-full {
  display: inline-flex;
  align-items: baseline;
  gap: var(--ds-space-2, 8px);
  max-width: 100%;
  white-space: normal;
  overflow-wrap: anywhere;
}

/*
  045 S2 (advisor 2026-10-17): the first row keeps the name, the state and the flow button on one line - a long scenario
  name wrapped the cluster into two (1280x720, both themes: the state went under the name). Like the Scenario select: the
  name gives way, cut by an ellipsis, and the whole name is its `title`. Desktop only; narrow screens wrap as before.
*/
@media (min-width: 501px) {
  .tb-identity {
    flex-wrap: nowrap;
  }

  .tb-network-name {
    flex: 0 1 auto;
    min-width: 0;
  }

  .tb-network-name__value {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .tb-network-state,
  .tb-flow-toggle {
    flex-shrink: 0;
  }
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

/*
  046 wave 1 (CI run 39143700381, stand: empty-scene "SAME source" judged 2 < 3): the language switch beside the theme
  made the left column of the service row about 180 px wider; the run controls in the middle got that much less, and
  with the CI fonts the Scenario label wrapped to a line of its own - the row grew by two lines and covered a node.
  The theme and the language stand one under the other instead: the column is as wide as before the switch (its
  widest row), and as tall as two controls - not taller than the middle's own two lines. Desktop only; narrow screens
  wrap as before.
*/
@media (min-width: 501px) {
  .tb-mode-row {
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: var(--ds-space-1, 4px);
  }

  .tb-mode-row > .tb-theme-row,
  .tb-mode-row > .lang-switch {
    justify-content: space-between;
    flex-wrap: nowrap;
  }
}

.tb-select-28 {
  height: var(--ds-tb-control-h);
}

/* BACKLOG 043-3: the reason the scenario select is disabled - read by a screen reader (`aria-describedby`), not drawn. */
.tb-visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

.tb-retry {
  margin-left: auto;
  flex-shrink: 0;
}

.tb-run-controls {
  gap: var(--ds-space-2, 8px);
  flex-wrap: wrap;
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
