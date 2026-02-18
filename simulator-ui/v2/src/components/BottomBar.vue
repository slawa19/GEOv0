<script setup lang="ts">
import { computed } from 'vue'
import type { ArtifactIndexItem } from '../api/simulatorTypes'
import { SCENE_IDS, SCENES, type SceneId } from '../scenes'

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
      <div class="ds-row" style="gap: 6px" aria-label="Equivalent">
        <span class="ds-label">EQ</span>
        <template v-if="props.isDemoFixtures">
          <span class="ds-value ds-mono">UAH</span>
        </template>
        <template v-else>
          <select v-model="eq" class="ds-select" aria-label="Equivalent">
            <option value="UAH">UAH</option>
            <option value="HOUR">HOUR</option>
            <option value="EUR">EUR</option>
          </select>
        </template>
      </div>

      <div class="ds-row" style="gap: 6px" aria-label="Layout">
        <span class="ds-label">Layout</span>
        <select v-model="layoutMode" class="ds-select" aria-label="Layout">
          <option value="admin-force">Organic cloud</option>
          <option value="community-clusters">Clusters</option>
          <option value="balance-split">Balance</option>
          <option value="type-split">Type</option>
          <option value="status-split">Status</option>
        </select>
      </div>

      <div v-if="apiMode !== 'real'" class="ds-row" style="gap: 6px" aria-label="Scene">
        <span class="ds-label">Scene</span>
        <select v-model="scene" class="ds-select" aria-label="Scene">
          <option v-for="id in SCENE_IDS" :key="id" :value="id">{{ SCENES[id].label }}</option>
        </select>
      </div>

      <div class="ds-row" aria-label="Quality" style="gap: 6px">
        <span class="ds-label">Quality</span>
        <select v-model="quality" class="ds-select" aria-label="Quality">
          <option value="low">Low</option>
          <option value="med">Med</option>
          <option value="high">High</option>
        </select>
      </div>

      <div class="ds-row" aria-label="Labels" style="gap: 6px">
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
          class="ds-btn ds-btn--ghost"
          style="height: 28px; padding: 0 10px"
          type="button"
          :disabled="!runId"
          @click="refreshSnapshot"
        >
          Refresh
        </button>

        <button
          v-if="showResetView"
          class="ds-btn ds-btn--ghost"
          style="height: 28px; padding: 0 10px"
          type="button"
          @click="resetView"
        >
          Reset view
        </button>

        <details
          v-if="activeSegment === 'auto'"
          class="ds-panel ds-ov-metric ds-ov-details"
          style="opacity: 0.92"
          aria-label="Artifacts"
        >
          <summary class="ds-row" style="gap: 8px; cursor: pointer">
            <span class="ds-badge ds-badge--info">Artifacts</span>
            <span class="ds-value" style="opacity: 0.85">{{ artifacts.length || 0 }}</span>
          </summary>
          <div class="ds-stack" style="margin-top: 8px; gap: 8px">
            <div class="ds-row" style="gap: 6px">
              <button
                class="ds-btn ds-btn--secondary"
                style="height: 28px; padding: 0 10px"
                type="button"
                :disabled="!runId || artifactsLoading"
                @click="refreshArtifacts"
              >
                {{ artifactsLoading ? 'Loading…' : 'Refresh' }}
              </button>
              <div v-if="!artifacts.length" class="ds-label" style="opacity: 0.85">No artifacts</div>
            </div>
            <div v-if="artifacts.length" class="ds-row" style="gap: 6px">
              <button
                v-for="a in artifacts"
                :key="a.name"
                class="ds-btn ds-btn--secondary"
                style="height: 28px; padding: 0 10px"
                type="button"
                @click="downloadArtifact(a.name)"
              >
                {{ a.name }}
              </button>
            </div>
          </div>
        </details>

        <details
          v-if="isDevToolsVisible"
          class="ds-panel ds-ov-metric ds-ov-details"
          style="opacity: 0.92"
          aria-label="Dev tools"
        >
          <summary class="ds-row" style="gap: 8px; cursor: pointer">
            <span class="ds-badge ds-badge--warn">Dev</span>
            <span class="ds-label" style="opacity: 0.9">Tools</span>
          </summary>
          <div class="ds-stack" style="margin-top: 8px; gap: 8px">
            <div class="ds-row" style="gap: 6px">
              <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" @click="toggleDemoUi">
                {{ isDemoUi ? 'Exit Demo UI' : 'Enter Demo UI' }}
              </button>
            </div>

            <div v-if="fxDebugEnabled" class="ds-stack" style="gap: 8px">
              <div class="ds-row" style="gap: 6px">
                <span class="ds-label">FX Debug</span>
              </div>
              <div class="ds-row" style="gap: 6px">
                <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" :disabled="fxBusy" @click="onRunTxOnce">
                  Single Tx
                </button>
                <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" :disabled="fxBusy" @click="onRunClearingOnce">
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
          class="ds-btn ds-btn--ghost"
          style="height: 28px; padding: 0 10px"
          type="button"
          @click="resetView"
        >
          Reset view
        </button>
      </div>
    </div>
  </div>
</template>
