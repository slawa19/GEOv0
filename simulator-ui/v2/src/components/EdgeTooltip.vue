<script setup lang="ts">
import type { CSSProperties } from 'vue'

type HoveredEdge = {
  key: string | null
  fromId: string
  toId: string
  amountText: string
}

type Props = {
  edge: HoveredEdge
  style: CSSProperties
  getNodeName: (id: string) => string | null
}

defineProps<Props>()
</script>

<template>
  <div v-if="edge.key" class="ds-ov-tooltip" :style="style" aria-label="Edge tooltip">
    <div class="ds-ov-tooltip__title">
      {{ getNodeName(edge.fromId) ?? edge.fromId }} → {{ getNodeName(edge.toId) ?? edge.toId }}
    </div>
    <div class="ds-ov-tooltip__amount">{{ edge.amountText }}</div>
  </div>
</template>
