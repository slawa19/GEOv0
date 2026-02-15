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
      <div v-if="b.isClean" class="hud-chip" style="gap: 10px">
        <span class="hud-badge" data-tone="ok">Clean</span>
        <span class="hud-label">System is clean — no debts</span>
      </div>

      <div class="hud-chip">
        <span class="hud-label">Total Debt</span>
        <span class="hud-value">{{ fmt(b.totalUsed) }} {{ equivalent }}</span>
      </div>

      <div class="hud-chip">
        <span class="hud-label">Available Capacity</span>
        <span class="hud-value">{{ fmt(b.totalAvailable) }} {{ equivalent }}</span>
      </div>

      <div class="hud-chip">
        <span class="hud-label">Trustlines</span>
        <span class="hud-value">{{ fmt(b.activeTrustlines) }}</span>
      </div>

      <div class="hud-chip">
        <span class="hud-label">Participants</span>
        <span class="hud-value">{{ fmt(b.activeParticipants) }}</span>
      </div>

      <div class="hud-chip" style="gap: 10px">
        <span class="hud-label">Utilization</span>
        <span class="hud-value">{{ utilPct }}%</span>
        <div class="util">
          <div class="util__bar" :style="{ width: utilPct + '%' }" />
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

.util {
  width: 110px;
  height: 6px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.16);
  overflow: hidden;
}

.util__bar {
  height: 100%;
  background: rgba(56, 189, 248, 0.7);
  transition:
    width 280ms ease,
    background-color 280ms ease,
    opacity 280ms ease;
}
</style>

