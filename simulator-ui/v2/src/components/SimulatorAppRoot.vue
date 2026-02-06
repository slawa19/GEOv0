<script setup lang="ts">
import FixturesHudBottom from './FixturesHudBottom.vue'
import FixturesHudTop from './FixturesHudTop.vue'
import RealHudBottom from './RealHudBottom.vue'
import RealHudTop from './RealHudTop.vue'
import DemoHudBottom from './DemoHudBottom.vue'
import EdgeTooltip from './EdgeTooltip.vue'
import LabelsOverlayLayers from './LabelsOverlayLayers.vue'
import NodeCardOverlay from './NodeCardOverlay.vue'
import DevPerfOverlay from './DevPerfOverlay.vue'
import FxDebugPanel from './FxDebugPanel.vue'

import { useSimulatorApp } from '../composables/useSimulatorApp'

const app = useSimulatorApp()

const {
  apiMode,

  // flags
  isDemoFixtures,
  isDemoUi,
  isTestMode,
  isWebDriver,

  // real mode
  real,
  realActions,

  // state + prefs
  state,
  eq,
  scene,
  layoutMode,
  quality,
  labelsLod,

  effectiveEq,

  // env
  gpuAccelLikely,

  // refs
  hostEl,
  canvasEl,
  fxCanvasEl,
  dragPreviewEl,

  // derived ui
  overlayLabelScale,
  showResetView,

  // dev / diagnostics
  showPerfOverlay,
  perf,
  fxDebug,

  // selection + overlays
  isNodeCardOpen,
  hoveredEdge,
  clearHoveredEdge,
  edgeTooltipStyle,
  selectedNode,
  nodeCardStyle,
  selectedNodeEdgeStats,

  // pinning
  dragToPin,
  isSelectedPinned,
  pinSelectedNode,
  unpinSelectedNode,

  // handlers
  onCanvasClick,
  onCanvasDblClick,
  onCanvasPointerDown,
  onCanvasPointerMove,
  onCanvasPointerUp,
  onCanvasWheel,

  // labels
  labelNodes,
  floatingLabelsViewFx,
  worldToCssTranslateNoScale,

  // helpers for template
  getNodeById,
  resetView,
} = app

function formatDemoActionError(e: any): string {
  const msg = String(e?.message ?? e)
  const body = typeof e?.bodyText === 'string' && e.bodyText.trim() ? `\n${e.bodyText.trim()}` : ''
  return `${msg}${body}`
}

const demoRunTxOnce = async () => {
  if (fxDebug.busy.value) return
  real.lastError = ''
  try {
    await fxDebug.runTxOnce()
  } catch (e: any) {
    real.lastError = formatDemoActionError(e)
  }
}

const demoRunClearingOnce = async () => {
  if (fxDebug.busy.value) return
  real.lastError = ''
  try {
    await fxDebug.runClearingOnce()
  } catch (e: any) {
    real.lastError = formatDemoActionError(e)
  }
}

function setQueryAndReload(mut: (sp: URLSearchParams) => void) {
  if (typeof window === 'undefined') return
  const url = new URL(window.location.href)
  mut(url.searchParams)
  // Setting href ensures full re-init (important when switching between pipelines).
  window.location.href = url.toString()
}

function forceDbEnrichedPreviewOnNextLoad() {
  // Avoid a transient topology-only preview snapshot right after a full reload.
  // The real-mode boot flow shows a scenario preview until an active run is discovered.
  // If persisted desiredMode is "fixtures", the preview looks "non-enriched" for a few seconds.
  try {
    localStorage.setItem('geo.sim.v2.desiredMode', 'real')
  } catch {
    // ignore
  }
}

function clearFxDebugRunOnNextLoad() {
  // FX Debug may autostart a fixtures run and persist its runId.
  // After exiting Demo UI we want to return to the normal real UI preview (DB-enriched),
  // not a topology-only fixtures run snapshot.
  try {
    const isFxDebugRun = localStorage.getItem('geo.sim.v2.fxDebugRun') === '1'
    if (!isFxDebugRun) return
    localStorage.removeItem('geo.sim.v2.fxDebugRun')
    localStorage.setItem('geo.sim.v2.runId', '')
  } catch {
    // ignore
  }
}

function enterDemoUi() {
  forceDbEnrichedPreviewOnNextLoad()
  setQueryAndReload((sp) => {
    sp.set('mode', 'real')
    sp.set('ui', 'demo')
    sp.set('debug', '1')
  })
}

async function exitDemoUi() {
  forceDbEnrichedPreviewOnNextLoad()

  // Best-effort cleanup: if Demo UI auto-started an FX debug run, stop it server-side
  // before doing a full reload, otherwise it may keep running in the background.
  try {
    const isFxDebugRun = localStorage.getItem('geo.sim.v2.fxDebugRun') === '1'
    if (isFxDebugRun) {
      const stopPromise = realActions.stop().catch(() => {
        // ignore
      })
      await Promise.race([stopPromise, new Promise<void>((resolve) => setTimeout(resolve, 3000))])
    }
  } catch {
    // ignore
  }

  clearFxDebugRunOnNextLoad()
  setQueryAndReload((sp) => {
    // Always exit into real full UI.
    sp.set('mode', 'real')
    sp.delete('ui')
    sp.delete('debug')
  })
}

function toggleDemoUi() {
  if (isDemoUi.value) void exitDemoUi()
  else enterDemoUi()
}
</script>

<template>
  <div
    ref="hostEl"
    class="root"
    :data-ready="!state.loading && !state.error && state.snapshot ? '1' : '0'"
    :data-scene="scene"
    :data-layout="layoutMode"
    :data-quality="quality"
    :data-gpu="gpuAccelLikely ? '1' : '0'"
    :data-webdriver="isWebDriver ? '1' : '0'"
    :style="{ '--overlay-scale': String(overlayLabelScale) }"
  >
    <canvas
      ref="canvasEl"
      class="canvas"
      @click="onCanvasClick"
      @dblclick.prevent="onCanvasDblClick"
      @pointerdown="onCanvasPointerDown"
      @pointermove="onCanvasPointerMove"
      @pointerup="onCanvasPointerUp"
      @pointercancel="onCanvasPointerUp"
      @pointerleave="clearHoveredEdge"
      @wheel.prevent="onCanvasWheel"
    />
    <canvas ref="fxCanvasEl" class="canvas canvas-fx" />

    <div ref="dragPreviewEl" class="drag-preview" aria-hidden="true" />

    <EdgeTooltip
      :edge="hoveredEdge"
      :style="edgeTooltipStyle()"
      :get-node-name="(id) => getNodeById(id)?.name ?? null"
    />

    <div v-if="!isTestMode && !isWebDriver" class="demo-ui" :data-enabled="isDemoUi ? '1' : '0'">
      <div class="demo-ui__title">Demo UI</div>
      <div class="demo-ui__row">
        <button class="demo-ui__btn" type="button" @click="toggleDemoUi">
          {{ isDemoUi ? 'Exit' : 'Enter' }}
        </button>

        <div v-if="apiMode === 'real' && isDemoUi" class="demo-ui__chip">
          <span class="demo-ui__label">EQ</span>
          <select v-model="eq" class="demo-ui__select" aria-label="Equivalent">
            <option value="UAH">UAH</option>
            <option value="HOUR">HOUR</option>
            <option value="EUR">EUR</option>
          </select>
        </div>

        <div v-if="apiMode === 'real' && isDemoUi" class="demo-ui__chip">
          <span class="demo-ui__label">Layout</span>
          <select v-model="layoutMode" class="demo-ui__select" aria-label="Layout">
            <option value="admin-force">Organic cloud</option>
            <option value="community-clusters">Clusters</option>
            <option value="balance-split">Balance</option>
            <option value="type-split">Type</option>
            <option value="status-split">Status</option>
          </select>
        </div>

        <div v-if="apiMode === 'real' && isDemoUi" class="demo-ui__chip" aria-label="SSE status">
          <span class="demo-ui__label">SSE</span>
          <span class="demo-ui__value">{{ real.sseState }}</span>
        </div>

        <div v-if="apiMode === 'real' && isDemoUi" class="demo-ui__chip" aria-label="Run status">
          <span class="demo-ui__label">Run</span>
          <span class="demo-ui__value">
            {{ real.runStatus?.state ?? (real.runId ? 'unknown' : 'none') }}
          </span>
        </div>
      </div>

      <div v-if="apiMode === 'real' && isDemoUi && real.lastError" class="demo-ui__error mono">
        {{ real.lastError }}
      </div>
    </div>

    <RealHudTop
      v-if="apiMode === 'real' && !isDemoUi"
      v-model:eq="eq"
      v-model:layoutMode="layoutMode"
      :loading-scenarios="real.loadingScenarios"
      :scenarios="real.scenarios"
      :selected-scenario-id="real.selectedScenarioId"
      :desired-mode="real.desiredMode"
      :intensity-percent="real.intensityPercent"
      :run-id="real.runId"
      :run-status="real.runStatus"
      :sse-state="real.sseState"
      :last-error="real.lastError"
      :refresh-scenarios="realActions.refreshScenarios"
      :start-run="realActions.startRun"
      :pause="realActions.pause"
      :resume="realActions.resume"
      :stop="realActions.stop"
      :apply-intensity="realActions.applyIntensity"
      :run-stats="real.runStats"
      @update:selected-scenario-id="realActions.setSelectedScenarioId"
      @update:desired-mode="realActions.setDesiredMode"
      @update:intensity-percent="realActions.setIntensityPercent"
    />

    <FixturesHudTop
      v-else-if="apiMode !== 'real'"
      v-model:eq="eq"
      v-model:layoutMode="layoutMode"
      v-model:scene="scene"
      :is-demo-fixtures="isDemoFixtures"
      :is-test-mode="isTestMode"
      :is-web-driver="isWebDriver"
      :effective-eq="effectiveEq"
      :source-path="state.sourcePath"
      :generated-at="state.snapshot?.generated_at"
      :nodes-count="state.snapshot?.nodes.length"
      :links-count="state.snapshot?.links.length"
    />

    <NodeCardOverlay
      v-if="isNodeCardOpen && selectedNode && !dragToPin.dragState.active"
      :node="selectedNode"
      :style="nodeCardStyle()"
      :edge-stats="selectedNodeEdgeStats"
      :equivalent-text="state.snapshot?.equivalent ?? ''"
      :show-pin-actions="!isTestMode && !isWebDriver"
      :is-pinned="isSelectedPinned"
      :pin="pinSelectedNode"
      :unpin="unpinSelectedNode"
    />


    <RealHudBottom
      v-if="apiMode === 'real' && !isDemoUi"
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :show-reset-view="showResetView"
      :run-id="real.runId"
      :run-status="real.runStatus"
      :sse-state="real.sseState"
      :refresh-snapshot="realActions.refreshSnapshot"
      :artifacts="real.artifacts"
      :artifacts-loading="real.artifactsLoading"
      :refresh-artifacts="realActions.refreshArtifacts"
      :download-artifact="realActions.downloadArtifact"
      :reset-view="resetView"
    />

    <DemoHudBottom
      v-else-if="apiMode === 'real' && isDemoUi"
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :show-reset-view="showResetView"
      :show-demo-controls="false"
      :busy="fxDebug.busy.value"
      :run-tx-once="demoRunTxOnce"
      :run-clearing-once="demoRunClearingOnce"
      :reset-view="resetView"
    />

    <FixturesHudBottom
      v-else-if="apiMode !== 'real'"
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :show-reset-view="showResetView"
      :reset-view="resetView"
    />

    <FxDebugPanel
      :enabled="apiMode === 'real' && !isDemoUi && fxDebug.enabled.value"
      :is-busy="fxDebug.busy.value"
      :run-tx-once="fxDebug.runTxOnce"
      :run-clearing-once="fxDebug.runClearingOnce"
    />

    <!-- Loading / error overlay (fail-fast, but non-intrusive) -->
    <div v-if="state.loading" class="overlay">Loading…</div>
    <div v-else-if="state.error" class="overlay overlay-error">
      <div class="overlay-title">Error</div>
      <div class="overlay-text mono">{{ state.error }}</div>
    </div>

    <LabelsOverlayLayers
      :label-nodes="labelNodes"
      :floating-labels="floatingLabelsViewFx"
      :world-to-css-translate-no-scale="worldToCssTranslateNoScale"
    />

    <DevPerfOverlay :enabled="showPerfOverlay" :perf="perf" />
  </div>
</template>

<style scoped>
.demo-ui {
  position: absolute;
  left: 12px;
  top: 12px;
  z-index: 41;
  padding: 10px 10px 8px;
  border-radius: 10px;
  background: rgba(10, 12, 16, 0.55);
  border: 1px solid rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(8px);
  color: rgba(255, 255, 255, 0.92);
  user-select: none;
}

.demo-ui__title {
  font-size: 12px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  opacity: 0.8;
  margin-bottom: 6px;
}

.demo-ui__row {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.demo-ui__btn {
  appearance: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.08);
  color: inherit;
  border-radius: 9px;
  padding: 8px 10px;
  font-weight: 700;
  font-size: 13px;
  cursor: pointer;
}

.demo-ui__chip {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  border: 1px solid rgba(255, 255, 255, 0.10);
  background: rgba(255, 255, 255, 0.06);
  border-radius: 9px;
  padding: 6px 8px;
}

.demo-ui__label {
  font-size: 11px;
  opacity: 0.75;
}

.demo-ui__select {
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(0, 0, 0, 0.2);
  color: inherit;
  border-radius: 8px;
  padding: 4px 6px;
  font-weight: 600;
}

.demo-ui__value {
  font-weight: 600;
}
</style>
