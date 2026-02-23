<script setup lang="ts">
import { computed } from 'vue'
import type { ArtifactIndexItem } from '../api/simulatorTypes'
import { SCENE_IDS, SCENES, type SceneId } from '../scenes'
import { EQUIVALENT_CODES } from '../config/equivalents'

type Props = {
  apiMode: 'fixtures' | 'real'
  activeSegment: 'sandbox' | 'auto' | 'interact'

  isDemoFixtures: boolean

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

  fxDebugEnabled: boolean
  fxBusy: boolean
  runTxOnce: () => void | Promise<void>
  runClearingOnce: () => void | Promise<void>
}

const props = defineProps<Props>()

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })
const quality = defineModel<string>('quality', { required: true })
const labelsLod = defineModel<string>('labelsLod', { required: true })
const scene = defineModel<SceneId>('scene', { required: true })

const isDevToolsVisible = computed(
  () => import.meta.env.DEV && !props.isWebDriver && !props.isTestMode && !props.isE2eScreenshots
)

const allowDemoUi = import.meta.env.VITE_ALLOW_DEMO_UI === 'true'

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
  <div class="ds-ov-bottom ds-panel ds-ov-bar ds-ov-bottombar" aria-label="Bottom bar">
    <div class="ds-ov-bottombar__left" aria-label="View settings">
      <div class="ds-row bb-row" aria-label="Equivalent">
        <span class="ds-label">EQ</span>
        <template v-if="props.isDemoFixtures">
          <span class="ds-value ds-mono">UAH</span>
        </template>
        <template v-else>
          <select v-model="eq" class="ds-select" aria-label="Equivalent">
            <option v-for="code in EQUIVALENT_CODES" :key="code" :value="code">{{ code }}</option>
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

    <div class="ds-ov-bottombar__right" aria-label="Tools">
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
          v-if="showResetView"
          class="ds-btn ds-btn--ghost ds-btn--sm"
          type="button"
          @click="resetView"
        >
          Reset view
        </button>

        <details
          v-if="activeSegment === 'auto'"
          class="ds-panel ds-ov-metric ds-ov-details bb-details"
          aria-label="Artifacts"
        >
          <summary class="ds-row bb-summary">
            <span class="ds-badge ds-badge--info">Artifacts</span>
            <span class="ds-value bb-fade-85">{{ artifacts.length || 0 }}</span>
          </summary>
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
        </details>

        <details
          v-if="isDevToolsVisible || allowDemoUi"
          class="ds-panel ds-ov-metric ds-ov-details bb-details"
          aria-label="Dev tools"
        >
          <summary class="ds-row bb-summary">
            <span class="ds-badge ds-badge--warn">Dev</span>
            <span class="ds-label bb-fade-90">Tools</span>
          </summary>
          <div class="ds-stack bb-stack">
            <div class="ds-row bb-row">
              <button
                class="ds-btn ds-btn--secondary ds-btn--sm"
                type="button"
                :disabled="isDemoUi && isExiting"
                @click="toggleDemoUi"
              >
                {{ isDemoUi ? (isExiting ? 'Exiting…' : 'Exit Demo UI') : 'Enter Demo UI' }}
              </button>
            </div>

            <div v-if="fxDebugEnabled" class="ds-stack bb-stack-inner">
              <div class="ds-row bb-row">
                <span class="ds-label">FX Debug</span>
              </div>
              <div class="ds-row bb-row">
                <button class="ds-btn ds-btn--secondary ds-btn--sm" type="button" :disabled="fxBusy" @click="onRunTxOnce">
                  Single Tx
                </button>
                <button class="ds-btn ds-btn--secondary ds-btn--sm" type="button" :disabled="fxBusy" @click="onRunClearingOnce">
                  Run Clearing
                </button>
              </div>
            </div>
          </div>
        </details>
      </div>

      <div v-else class="ds-ov-btn-group" aria-label="Offline tools">
        <button
          v-if="showResetView"
          class="ds-btn ds-btn--ghost ds-btn--sm"
          type="button"
          @click="resetView"
        >
          Reset view
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bb-row {
  gap: 6px;
}

.bb-details {
  opacity: 0.92;
}

.bb-summary {
  gap: 8px;
}

.bb-stack {
  margin-top: 8px;
  gap: 8px;
}

.bb-stack-inner {
  gap: 8px;
}

.bb-fade-85 {
  opacity: 0.85;
}

.bb-fade-90 {
  opacity: 0.9;
}
</style>
