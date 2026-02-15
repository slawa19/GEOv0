<script setup lang="ts">
import { computed, unref, type ComputedRef } from 'vue'

import type { SystemBalance } from '../composables/useSystemBalance'

type Props = {
  /** Accepts either a raw SystemBalance object or a computed ref (as returned by useSystemBalance(...).balance). */
  balance: SystemBalance | ComputedRef<SystemBalance>
  equivalent: string
}

const props = defineProps<Props>()

const b = computed<SystemBalance>(() => unref(props.balance))

const utilPct = computed(() => {
  const u = Number(b.value?.utilization ?? 0)
  if (!Number.isFinite(u)) return 0
  return Math.max(0, Math.min(100, Math.round(u * 100)))
})

function fmt(n: number): string {
  const v = Number(n ?? 0)
  if (!Number.isFinite(v)) return '0'
  return v.toLocaleString(undefined, { maximumFractionDigits: 0 })
}
</script>

<template>
  <div class="system-balance-bar" aria-label="System balance">
    <div class="system-balance-bar__inner">
      <div v-if="b.isClean" class="ds-panel ds-ov-metric">
        <span class="ds-badge ds-badge--ok">Clean</span>
        <span class="ds-label">System is clean — no debts</span>
      </div>

      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">Total Debt</span>
        <span class="ds-value ds-mono">{{ fmt(b.totalUsed) }} {{ equivalent }}</span>
      </div>

      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">Available Capacity</span>
        <span class="ds-value ds-mono">{{ fmt(b.totalAvailable) }} {{ equivalent }}</span>
      </div>

      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">Trustlines</span>
        <span class="ds-value ds-mono">{{ fmt(b.activeTrustlines) }}</span>
      </div>

      <div class="ds-panel ds-ov-metric">
        <span class="ds-label">Participants</span>
        <span class="ds-value ds-mono">{{ fmt(b.activeParticipants) }}</span>
      </div>

      <div class="ds-panel ds-ov-metric" style="gap: 10px">
        <span class="ds-label">Utilization</span>
        <span class="ds-value ds-mono">{{ utilPct }}%</span>
        <div class="ds-progress__track" style="width: 110px; height: 6px">
          <div class="ds-progress__bar" :style="{ width: utilPct + '%' }" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.system-balance-bar__inner {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  justify-content: center;
  pointer-events: auto;
}

.ds-progress__bar {
  transition:
    width 280ms ease,
    background-color 280ms ease,
    opacity 280ms ease;
}
</style>

