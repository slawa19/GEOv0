<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ArtifactIndexItem } from '../api/simulatorTypes'
import { SCENE_IDS, SCENES, type SceneId } from '../scenes'
import { useHudDropdownFocus } from '../composables/useHudDropdownFocus'
import { useSimulatorStorage } from '../composables/usePersistedSimulatorPrefs'
import { demoToolsAllowed } from '../utils/demoToolsAllowed'
import HudBar from './common/HudBar.vue'

type Props = {
  apiMode: 'fixtures' | 'real'
  activeSegment: 'sandbox' | 'auto' | 'interact'

  isDemoFixtures: boolean
  /** 028 F-028-47 (C1): the equivalents the API names (`equivalentOptions`). */
  equivalentOptions: readonly string[]

  showResetView: boolean
  resetView: () => void

  // real-mode tools
  runId: string | null
  refreshSnapshot: () => void

  artifacts: ArtifactIndexItem[]
  artifactsLoading: boolean
  refreshArtifacts: () => void
  downloadArtifact: (name: string) => void

  // dev tools (variant B)
  isWebDriver: boolean
  isTestMode: boolean
  isE2eScreenshots: boolean

  isDemoUi: boolean
  isExiting: boolean
  toggleDemoUi: () => void

  /**
   * Analytics overlay surface (spec 007, T705). The state is owned by `useSimulatorApp` because
   * the panel's mount point and its poll gate both read it; the bar only shows and flips it.
   */
  analyticsPanelOpen: boolean
  toggleAnalyticsPanel: () => void

  /**
   * 043 S1: the one "Подробнее" toggle (owned by the root). Off: the bottom row is the legend, the total debt (slot
   * `summary`) and "Reset view". On: the row of service controls above it - EQ, Layout, Scene, Quality, Labels,
   * Refresh, Analytics, Artifacts, Dev Tools and "Inspect graph" (slot `navigator`). Hidden means not rendered.
   */
  detailsOpen?: boolean

  fxDebugEnabled: boolean
  fxBusy: boolean
  runTxOnce: () => void | Promise<void>
  runClearingOnce: () => void | Promise<void>
}

const props = withDefaults(defineProps<Props>(), { detailsOpen: false })

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })
const quality = defineModel<string>('quality', { required: true })
const labelsLod = defineModel<string>('labelsLod', { required: true })
const scene = defineModel<SceneId>('scene', { required: true })

const isDevToolsVisible = computed(() => demoToolsAllowed(props))

const simulatorStorage = useSimulatorStorage()

function readDevToolsOpenForCurrentMode(): boolean {
  if (props.isDemoUi) {
    const persisted = simulatorStorage.readDevtoolsOpenDemo()
    if (persisted != null) return persisted

    // Enter demo → open by default.
    // Persist immediately so it survives reloads until the user toggles manually.
    simulatorStorage.writeDevtoolsOpenDemo(true)
    return true
  }

  const persisted = simulatorStorage.readDevtoolsOpenReal()
  if (persisted != null) return persisted

  return false
}

const devToolsOpen = ref(readDevToolsOpenForCurrentMode())
const artifactsOpen = ref(false)

const {
  detailsRef: artifactsDetailsRef,
  summaryRef: artifactsSummaryRef,
  surfaceRef: artifactsSurfaceRef,
  onToggle: onArtifactsToggle,
  onDetailsKeydown: onArtifactsDetailsKeydown,
} = useHudDropdownFocus(artifactsOpen)

const {
  detailsRef: devToolsDetailsRef,
  summaryRef: devToolsSummaryRef,
  surfaceRef: devToolsSurfaceRef,
  onToggle: onDevToolsDropdownToggle,
  onDetailsKeydown: onDevToolsDetailsKeydown,
} = useHudDropdownFocus(devToolsOpen)

watch(
  () => props.isDemoUi,
  () => {
    // Defensive: if demo flag ever changes without full reload, re-initialize from persisted prefs.
    devToolsOpen.value = readDevToolsOpenForCurrentMode()
  },
)

watch(
  devToolsOpen,
  (val) => {
    // Persist separately for real vs demo.
    if (props.isDemoUi) simulatorStorage.writeDevtoolsOpenDemo(val)
    else simulatorStorage.writeDevtoolsOpenReal(val)
  },
  { flush: 'sync' },
)

function onDevToolsToggle(ev: Event) {
  onDevToolsDropdownToggle(ev)
}

async function onRunTxOnce() {
  if (props.fxBusy) return
  await props.runTxOnce()
}

async function onRunClearingOnce() {
  if (props.fxBusy) return
  await props.runClearingOnce()
}
</script>

<template>
  <div class="ds-ov-bottom">
    <!-- 043 S1: the service row, while Details is on; above the main row, which therefore never moves. -->
    <HudBar v-if="props.detailsOpen" variant="ghost" layout="between" aria-label="Bottom bar" data-testid="bottom-details-row">
      <div class="hud-bar__left" aria-label="View settings">
        <div class="ds-row bb-row" aria-label="Equivalent">
        <span class="ds-label">EQ</span>
        <template v-if="props.isDemoFixtures">
          <span class="ds-value ds-mono">UAH</span>
        </template>
        <template v-else>
          <select v-model="eq" class="ds-select" aria-label="Equivalent">
            <option v-for="code in props.equivalentOptions" :key="code" :value="code">{{ code }}</option>
          </select>
        </template>
      </div>

      <div class="ds-row bb-row" aria-label="Layout">
        <span class="ds-label">Layout</span>
        <select v-model="layoutMode" class="ds-select" aria-label="Layout">
          <option value="admin-force">Organic cloud</option>
          <option value="community-clusters">Clusters</option>
          <option value="balance-split">Balance</option>
          <option value="type-split">Type</option>
          <option value="status-split">Status</option>
        </select>
      </div>

      <div v-if="apiMode !== 'real'" class="ds-row bb-row" aria-label="Scene">
        <span class="ds-label">Scene</span>
        <select v-model="scene" class="ds-select" aria-label="Scene">
          <option v-for="id in SCENE_IDS" :key="id" :value="id">{{ SCENES[id].label }}</option>
        </select>
      </div>

      <div class="ds-row bb-row" aria-label="Quality">
        <span class="ds-label">Quality</span>
        <select v-model="quality" class="ds-select" aria-label="Quality">
          <option value="low">Low</option>
          <option value="med">Med</option>
          <option value="high">High</option>
        </select>
      </div>

      <div class="ds-row bb-row" aria-label="Labels">
        <span class="ds-label">Labels</span>
        <select v-model="labelsLod" class="ds-select" aria-label="Labels">
          <option value="off">Off</option>
          <option value="selection">Selection</option>
          <option value="neighbors">Neighbors</option>
        </select>
      </div>
    </div>

    <div class="hud-bar__right" aria-label="Tools">
      <div v-if="apiMode === 'real'" class="ds-ov-btn-group" aria-label="Run tools">
        <button
          class="ds-btn ds-btn--ghost ds-btn--sm"
          type="button"
          :disabled="!runId"
          @click="refreshSnapshot"
        >
          Refresh
        </button>

        <button
          class="ds-btn ds-btn--ghost ds-btn--sm"
          type="button"
          data-testid="bottombar-analytics-toggle"
          :aria-pressed="analyticsPanelOpen ? 'true' : 'false'"
          @click="toggleAnalyticsPanel"
        >
          Analytics
        </button>

        <details
          v-if="activeSegment === 'auto'"
          ref="artifactsDetailsRef"
          :open="artifactsOpen"
          class="ds-ov-details bb-details"
          aria-label="Artifacts"
          @keydown.capture="onArtifactsDetailsKeydown"
          @toggle="onArtifactsToggle"
        >
          <summary ref="artifactsSummaryRef" class="ds-panel ds-ov-metric ds-row bb-summary">
            <span class="ds-badge ds-badge--info">Artifacts</span>
            <span class="ds-value bb-fade-85">{{ artifacts.length || 0 }}</span>
          </summary>
          <div
            ref="artifactsSurfaceRef"
            class="ds-panel ds-ov-surface ds-ov-dropdown ds-ov-dropdown--up ds-ov-dropdown--right"
            tabindex="-1"
          >
            <div class="ds-stack bb-stack">
              <div class="ds-row bb-row">
                <button
                  class="ds-btn ds-btn--secondary ds-btn--sm"
                  type="button"
                  :disabled="!runId || artifactsLoading"
                  :title="!runId ? 'Start a run first' : 'Refresh artifacts'"
                  @click="refreshArtifacts"
                >
                  {{ artifactsLoading ? 'Loading…' : 'Refresh' }}
                </button>
                <div v-if="!runId" class="ds-label bb-fade-85">Start a run first</div>
                <div v-else-if="!artifacts.length" class="ds-label bb-fade-85">No artifacts</div>
              </div>
              <div v-if="artifacts.length" class="ds-row bb-row">
                <button
                  v-for="a in artifacts"
                  :key="a.name"
                  class="ds-btn ds-btn--secondary ds-btn--sm"
                  type="button"
                  @click="downloadArtifact(a.name)"
                >
                  {{ a.name }}
                </button>
              </div>
            </div>
          </div>
        </details>

        <details
          v-if="isDevToolsVisible"
          ref="devToolsDetailsRef"
          :open="devToolsOpen"
          @keydown.capture="onDevToolsDetailsKeydown"
          @toggle="onDevToolsToggle"
          class="ds-ov-details bb-details"
          aria-label="Dev tools"
        >
          <summary ref="devToolsSummaryRef" class="ds-panel ds-ov-metric ds-row bb-summary">
            <span class="ds-badge ds-badge--warn">Dev</span>
            <span class="ds-label bb-fade-90">Tools</span>
          </summary>
          <div
            ref="devToolsSurfaceRef"
            class="ds-panel ds-ov-surface ds-ov-dropdown ds-ov-dropdown--up ds-ov-dropdown--right ds-ov-dropdown--fit-content bb-devtools-dropdown"
            tabindex="-1"
          >
            <div class="ds-row bb-row bb-devtools-row">
              <button
                class="ds-btn ds-btn--secondary ds-btn--sm"
                type="button"
                :disabled="isDemoUi && isExiting"
                @click="toggleDemoUi"
              >
                {{ isDemoUi ? (isExiting ? 'Exiting…' : 'Exit Demo') : 'Enter Demo' }}
              </button>

              <template v-if="isDemoUi && fxDebugEnabled">
                <button
                  class="ds-btn ds-btn--secondary ds-btn--sm"
                  type="button"
                  :disabled="fxBusy"
                  @click="onRunTxOnce"
                >
                  Single Tx
                </button>
                <button
                  class="ds-btn ds-btn--secondary ds-btn--sm"
                  type="button"
                  :disabled="fxBusy"
                  @click="onRunClearingOnce"
                >
                  Run Clearing
                </button>
              </template>
            </div>
          </div>
        </details>
      </div>

      <div v-if="$slots.navigator" class="bb-navigator"><slot name="navigator" /></div>
    </div>
    </HudBar>

    <!-- 043 S1: the main row - always on screen. 039 slice B: the legend is part of this measured bar. -->
    <HudBar variant="ghost" layout="between" class="bb-main-row">
      <div v-if="$slots.legend" class="ds-ov-bottom__legend bb-main-legend"><slot name="legend" /></div>
      <div class="hud-bar__right bb-main-right">
        <slot name="summary" />
        <button
          v-if="showResetView"
          class="ds-btn ds-btn--secondary ds-btn--sm"
          type="button"
          @click="resetView"
        >
          Reset view
        </button>
      </div>
    </HudBar>
  </div>
</template>

<style scoped>
/* 043 S1: the rows of this bar are as tight as the top stack's (`--ds-hud-bar-py: 2px` there); nested HudBars inherit
   it. Measured at 1366x768 (layout.qa.ts, R9): the main row with 10 px paddings made the first view 81 % canvas. */
.ds-ov-bottom {
  --ds-hud-bar-py: 2px;
}

.bb-row {
  gap: var(--ds-space-2, 8px);
}

.bb-main-legend {
  margin-bottom: 0;
  min-width: 0;
}

.bb-main-right {
  margin-left: auto;
}

/* "Inspect graph" is a button of the row; its panel opens upwards over the scene and does not grow the measured bar. */
.bb-navigator {
  position: relative;
  min-width: 8.5rem;
  align-self: stretch;
}

.bb-navigator :deep(.graph-navigator) {
  position: absolute;
  right: 0;
  bottom: 0;
  width: max-content;
  max-width: min(520px, calc(100vw - 2 * var(--ds-ov-inset, 12px)));
}

/* Open: the panel floats above the row, so it never covers the row's other controls. */
.bb-navigator :deep(.graph-navigator:has(> details[open])) {
  bottom: calc(100% + var(--ds-space-2, 8px));
}

.bb-details {
  opacity: var(--ds-bb-details-opacity);
}

.bb-summary {
  gap: var(--ds-space-2, 8px);
}

.bb-devtools-dropdown {
  --ds-ov-dropdown-vw-inset: var(--ds-bb-devtools-maxw-inset);
}

.bb-devtools-row {
  flex-wrap: nowrap;
  white-space: nowrap;
}

.bb-stack {
  gap: var(--ds-space-2, 8px);
}

.bb-stack-inner {
  gap: var(--ds-space-2, 8px);
}

.bb-fade-85 {
  opacity: var(--ds-bb-fade-85-opacity);
}

.bb-fade-90 {
  opacity: var(--ds-bb-fade-90-opacity);
}

/* ── Responsive: prevent horizontal overflow on narrow viewports ─────────── */
@media (max-width: 500px) {
  /* Allow rows inside sections to wrap too */
  .bb-row {
    flex-wrap: wrap;
  }

  /* ds-value: no forced nowrap */
  :deep(.ds-value) {
    white-space: normal;
  }
}
</style>
