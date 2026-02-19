<script setup lang="ts">
import type { CSSProperties } from 'vue'

type HoveredEdge = {
  key: string | null
  fromId: string
  toId: string
  amountText: string
  // BUG-2: Interact Mode extended fields
  trustLimit?: string | number | null
  used?: string | number | null
  available?: string | number | null
  edgeStatus?: string | null
}

type Props = {
  edge: HoveredEdge
  style: CSSProperties
  getNodeName: (id: string) => string | null
  /** When true, show limit/used/available section. */
  interactMode?: boolean
}

defineProps<Props>()
</script>

<template>
  <div v-if="edge.key" class="ds-ov-tooltip" :style="style" aria-label="Edge tooltip">
    <div class="ds-ov-tooltip__title">
      {{ getNodeName(edge.fromId) ?? edge.fromId }} → {{ getNodeName(edge.toId) ?? edge.toId }}
    </div>
    <div class="ds-ov-tooltip__amount">{{ edge.amountText }}</div>

    <!-- BUG-2: Interact Mode extra info -->
    <template v-if="interactMode && (edge.trustLimit != null || edge.used != null || edge.available != null)">
      <div class="ds-ov-tooltip__divider" />
      <div class="ds-ov-tooltip__row">
        <span class="ds-ov-tooltip__label">Limit</span>
        <span class="ds-ov-tooltip__val">{{ edge.trustLimit ?? '—' }}</span>
      </div>
      <div class="ds-ov-tooltip__row">
        <span class="ds-ov-tooltip__label">Used</span>
        <span class="ds-ov-tooltip__val">{{ edge.used ?? '—' }}</span>
      </div>
      <div class="ds-ov-tooltip__row">
        <span class="ds-ov-tooltip__label">Avail</span>
        <span class="ds-ov-tooltip__val">{{ edge.available ?? '—' }}</span>
      </div>
      <div v-if="edge.edgeStatus" class="ds-ov-tooltip__row">
        <span class="ds-ov-tooltip__label">Status</span>
        <span class="ds-ov-tooltip__val">{{ edge.edgeStatus }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.ds-ov-tooltip__divider {
  height: 1px;
  background: rgba(255,255,255,0.15);
  margin: 4px 0;
}

.ds-ov-tooltip__row {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 0.78rem;
  opacity: 0.9;
}

.ds-ov-tooltip__label {
  opacity: 0.65;
}

.ds-ov-tooltip__val {
  font-family: var(--ds-font-mono, monospace);
}
</style>
