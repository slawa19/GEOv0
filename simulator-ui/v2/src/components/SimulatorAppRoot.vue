<script setup lang="ts">
import DemoHudBottom from './DemoHudBottom.vue'
import DemoHudTop from './DemoHudTop.vue'
import RealHudBottom from './RealHudBottom.vue'
import RealHudTop from './RealHudTop.vue'
import EdgeTooltip from './EdgeTooltip.vue'
import LabelsOverlayLayers from './LabelsOverlayLayers.vue'
import NodeCardOverlay from './NodeCardOverlay.vue'

import { useSimulatorApp } from '../composables/useSimulatorApp'

const app = useSimulatorApp()

const {
  apiMode,

  // flags
  isDemoFixtures,
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

  // refs
  hostEl,
  canvasEl,
  fxCanvasEl,
  dragPreviewEl,

  // derived ui
  overlayLabelScale,
  showDemoControls,
  showResetView,

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

  // demo
  playlist,
  canDemoPlay,
  demoPlayLabel,
  runTxOnce,
  runClearingOnce,
  demoStepOnce,
  demoTogglePlay,
  demoReset,

  // labels
  labelNodes,
  floatingLabelsViewFx,
  worldToCssTranslateNoScale,

  // helpers for template
  getNodeById,
  resetView,
} = app
</script>

<template>
  <div
    ref="hostEl"
    class="root"
    :data-ready="!state.loading && !state.error && state.snapshot ? '1' : '0'"
    :data-scene="scene"
    :data-layout="layoutMode"
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

    <RealHudTop
      v-if="apiMode === 'real'"
      v-model:eq="eq"
      v-model:layoutMode="layoutMode"
      :api-base="real.apiBase"
      :access-token="real.accessToken"
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
      @update:apiBase="realActions.setApiBase"
      @update:accessToken="realActions.setAccessToken"
      @update:selectedScenarioId="realActions.setSelectedScenarioId"
      @update:desiredMode="realActions.setDesiredMode"
      @update:intensityPercent="realActions.setIntensityPercent"
    />

    <DemoHudTop
      v-else
      v-model:eq="eq"
      v-model:layoutMode="layoutMode"
      v-model:scene="scene"
      :is-demo-fixtures="isDemoFixtures"
      :is-test-mode="isTestMode"
      :is-web-driver="isWebDriver"
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
      v-if="apiMode === 'real'"
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :show-reset-view="showResetView"
      :run-id="real.runId"
      :run-status="real.runStatus"
      :sse-state="real.sseState"
      :intensity-percent="real.intensityPercent"
      :set-intensity="realActions.applyIntensity"
      :pause="realActions.pause"
      :resume="realActions.resume"
      :stop="realActions.stop"
      :refresh-snapshot="realActions.refreshSnapshot"
      :artifacts="real.artifacts"
      :artifacts-loading="real.artifactsLoading"
      :refresh-artifacts="realActions.refreshArtifacts"
      :download-artifact="realActions.downloadArtifact"
      :reset-view="resetView"
    />

    <DemoHudBottom
      v-else
      v-model:quality="quality"
      v-model:labelsLod="labelsLod"
      :show-reset-view="showResetView"
      :show-demo-controls="showDemoControls"
      :playlist-playing="playlist.playing"
      :can-demo-play="canDemoPlay"
      :demo-play-label="demoPlayLabel"
      :run-tx-once="runTxOnce"
      :run-clearing-once="runClearingOnce"
      :reset-view="resetView"
      :demo-step-once="demoStepOnce"
      :demo-toggle-play="demoTogglePlay"
      :demo-reset="demoReset"
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
  </div>
</template>
