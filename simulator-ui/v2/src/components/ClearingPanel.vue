<script setup lang="ts">
import { computed } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'

type Props = {
  phase: InteractPhase
  state: InteractState
  busy: boolean

  equivalent: string
  /** System-wide context (from SystemBalanceBar). */
  totalDebt?: number | null

  confirmClearing: () => Promise<void> | void
  cancel: () => void
}

const props = defineProps<Props>()

const last = computed(() => props.state.lastClearing)
const cycles = computed(() => last.value?.cycles ?? [])
const cyclesCount = computed(() => {
  if (typeof last.value?.cleared_cycles === 'number') return last.value.cleared_cycles
  return cycles.value.length
})

function fmtInt(n: unknown): string {
  const v = Number(n ?? 0)
  if (!Number.isFinite(v)) return '0'
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

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
    class="panel"
    data-testid="clearing-panel"
    aria-label="Clearing panel"
  >
    <div class="panel__title">
      <span v-if="isConfirm">Run clearing</span>
      <span v-else-if="isPreview">Clearing preview</span>
      <span v-else>Clearing running</span>
    </div>

    <div class="hud-label" style="margin-bottom: 8px">
      <span>Equivalent:</span>
      <span class="mono">{{ equivalent }}</span>
      <span v-if="totalDebt != null"> · Total Debt: <span class="mono">{{ fmtInt(totalDebt) }} {{ equivalent }}</span></span>
    </div>

    <div v-if="state.error" class="panel__error mono" data-testid="clearing-error">{{ state.error }}</div>

    <template v-if="isConfirm">
      <div class="hud-label" style="margin-bottom: 8px">This will run a clearing cycle in backend.</div>

      <div class="panel__actions">
        <button class="btn btn-xs" type="button" :disabled="busyUi" @click="onConfirm">Confirm</button>
        <button class="btn btn-xs btn-ghost" type="button" :disabled="busyUi" @click="cancel">Cancel</button>
      </div>
    </template>

    <template v-else-if="isPreview">
      <div v-if="!last" class="hud-label">Preparing preview…</div>
      <div v-else>
        <div class="hud-label">Cycles: <span class="mono">{{ cyclesCount }}</span></div>
        <div class="hud-label">Total cleared: <span class="mono">{{ last.total_cleared_amount }} {{ equivalent }}</span></div>

        <ol v-if="cycles.length" class="cycles mono">
          <li v-for="(c, i) in cycles" :key="i">
            {{ c.cleared_amount }} {{ equivalent }} · edges: {{ c.edges.length }}
          </li>
        </ol>
      </div>
    </template>

    <template v-else>
      <div class="hud-label">Running…</div>
    </template>

    <div v-if="!isConfirm" class="panel__actions">
      <button class="btn btn-xs btn-ghost" type="button" :disabled="busyUi" @click="cancel">Close</button>
    </div>
  </div>
</template>

<style scoped>
.panel {
  position: absolute;
  right: 12px;
  top: 110px;
  z-index: 42;
  min-width: 320px;
  max-width: min(520px, calc(100vw - 24px));
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
  pointer-events: auto;
}

.panel__title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
}

.panel__error {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(248, 113, 113, 0.35);
  background: rgba(127, 29, 29, 0.14);
  color: rgba(254, 202, 202, 0.9);
}

.panel__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 10px;
}

.cycles {
  margin: 10px 0 0;
  padding-left: 18px;
  max-height: 140px;
  overflow: auto;
  color: rgba(226, 232, 240, 0.85);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
</style>

