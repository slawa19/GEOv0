<script setup lang="ts">
import { computed } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import { useOverlayPositioning } from '../utils/overlayPosition'

type Props = {
  phase: InteractPhase
  state: InteractState
  busy: boolean

  equivalent: string

  confirmClearing: () => Promise<void> | void
  cancel: () => void

  /** Optional anchor для позиционирования рядом с источником открытия.
   *  При null/undefined применяется CSS default (right: 12px, top: 110px). */
  anchor?: { x: number; y: number } | null
  /** Host element used as overlay viewport for clamping. */
  hostEl?: HTMLElement | null
}

const props = defineProps<Props>()

const anchorPositionStyle = useOverlayPositioning(
  () => props.anchor,
  () => props.hostEl,
  { w: 360, h: 280 },
)

const last = computed(() => props.state.lastClearing)
const cycles = computed(() => last.value?.cycles ?? [])
const cyclesCount = computed(() => {
  if (typeof last.value?.cleared_cycles === 'number') return last.value.cleared_cycles
  return cycles.value.length
})

async function onConfirm() {
  if (props.busy) return
  await props.confirmClearing()
}

const isRunning = computed(() => props.phase === 'clearing-running')
const isPreview = computed(() => props.phase === 'clearing-preview')
const isConfirm = computed(() => props.phase === 'confirm-clearing')

const busyUi = computed(() => props.busy || isRunning.value)
</script>

<template>
  <div
    v-if="phase === 'confirm-clearing' || phase === 'clearing-preview' || phase === 'clearing-running'"
    class="ds-ov-panel ds-panel ds-panel--elevated"
    :style="anchorPositionStyle"
    data-testid="clearing-panel"
    aria-label="Clearing panel"
  >
    <div class="ds-panel__header">
      <div class="ds-h2">
        <span v-if="isConfirm">Run clearing</span>
        <span v-else-if="isPreview">Clearing preview</span>
        <span v-else>Clearing running</span>
        <span class="ds-muted ds-mono"> (ESC to close)</span>
      </div>
    </div>

    <div class="ds-panel__body ds-stack">
      <div class="ds-label cp-equivalent-row">
        <span>Equivalent:</span>
        <span class="ds-mono">{{ equivalent }}</span>
      </div>

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="clearing-error">{{ state.error }}</div>

      <template v-if="isConfirm">
        <div class="ds-help" data-testid="clearing-confirm-help">
          <template v-if="busyUi">
            Running clearing… <span class="cp-spinner" aria-hidden="true" />
          </template>
          <template v-else>This will run a clearing cycle in backend.</template>
        </div>

        <div class="ds-row cp-actions">
          <button class="ds-btn ds-btn--primary" type="button" :disabled="busyUi" @click="onConfirm">
            {{ busyUi ? 'Running…' : 'Confirm' }}
          </button>
          <button class="ds-btn ds-btn--ghost" type="button" :disabled="busyUi" @click="cancel">Cancel</button>
        </div>
      </template>

      <template v-else-if="isPreview">
        <div v-if="!last" class="ds-help" data-testid="clearing-preview-loading">
          Preparing preview… <span class="cp-spinner" aria-hidden="true" />
        </div>
        <div v-else class="ds-stack cp-preview-stack">
          <div class="ds-label">
            Cycles: <span class="ds-mono">{{ cyclesCount }}</span>
          </div>
          <div class="ds-label">
            Total cleared: <span class="ds-mono">{{ last.total_cleared_amount }} {{ equivalent }}</span>
          </div>

          <ol v-if="cycles.length" class="ds-mono cp-cycles">
            <li v-for="(c, i) in cycles" :key="i">
              {{ c.cleared_amount }} {{ equivalent }} · edges: {{ c.edges.length }}
            </li>
          </ol>
        </div>
      </template>

      <template v-else>
        <div class="ds-help">Running…</div>
      </template>

      <div v-if="!isConfirm" class="ds-row cp-actions">
        <button class="ds-btn ds-btn--ghost" type="button" :disabled="busyUi" @click="cancel">Close</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cp-equivalent-row {
  margin-bottom: 2px;
}

.cp-actions {
  justify-content: flex-end;
}

.cp-preview-stack {
  gap: 6px;
}

.cp-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-left: 6px;
  border-radius: 999px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  animation: cp-spin 0.9s linear infinite;
  opacity: 0.7;
  vertical-align: -2px;
}

@keyframes cp-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.cp-cycles {
  margin: 8px 0 0;
  padding-left: 18px;
  max-height: 140px;
  overflow: auto;
}
</style>


