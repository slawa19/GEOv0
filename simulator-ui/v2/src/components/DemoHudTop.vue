<script setup lang="ts">
import { SCENE_IDS, SCENES, type SceneId } from '../scenes'

type Props = {
  isDemoFixtures: boolean
  isTestMode: boolean
  isWebDriver: boolean

  // diagnostics (helps confirm demo is reading the expected fixtures)
  effectiveEq?: string
  generatedAt?: string
  sourcePath?: string
  eventsPath?: string

  nodesCount?: number
  linksCount?: number
}

defineProps<Props>()

const isDev = import.meta.env.DEV

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })
const scene = defineModel<SceneId>('scene', { required: true })
</script>

<template>
  <!-- Minimal top HUD (controls + small status) -->
  <div class="ds-ov-top">
    <div class="ds-ov-row">
      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">EQ</span>
        <template v-if="isDemoFixtures">
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

      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">Layout</span>
        <select v-model="layoutMode" class="ds-select" aria-label="Layout">
          <option value="admin-force">Organic cloud (links)</option>
          <option value="community-clusters">Community clusters</option>
          <option value="balance-split">Constellations: balance</option>
          <option value="type-split">Constellations: type</option>
          <option value="status-split">Constellations: status</option>
        </select>
      </div>

      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">Scene</span>
        <select v-model="scene" class="ds-select" aria-label="Scene">
          <option v-for="id in SCENE_IDS" :key="id" :value="id">{{ SCENES[id].label }}</option>
        </select>
      </div>

      <div v-if="nodesCount != null && linksCount != null" class="ds-panel ds-ov-metric" style="opacity: 0.9" aria-label="Stats">
        <span class="ds-value ds-mono">Nodes {{ nodesCount }} | Links {{ linksCount }}</span>
      </div>

      <div
        v-if="isDev && !isWebDriver && (sourcePath || eventsPath || effectiveEq || generatedAt)"
        class="ds-panel ds-ov-metric"
        style="opacity: 0.9"
        aria-label="Diagnostics"
      >
        <span class="ds-value ds-mono">
          <template v-if="effectiveEq">EQ* {{ effectiveEq }}</template>
          <template v-if="generatedAt"> | {{ generatedAt }}</template>
          <template v-if="sourcePath"> | {{ sourcePath }}</template>
          <template v-if="eventsPath"> | {{ eventsPath }}</template>
        </span>
      </div>

      <div v-if="isTestMode && !isWebDriver" class="ds-panel ds-ov-metric" style="opacity: 0.9" aria-label="Mode">
        <span class="ds-value ds-mono">TEST MODE</span>
      </div>
    </div>
  </div>
</template>
