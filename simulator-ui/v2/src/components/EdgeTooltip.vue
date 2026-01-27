<script setup lang="ts">
import type { CSSProperties } from 'vue'

type HoveredEdge = {
  key: string
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
  <div v-if="edge.key" class="edge-tooltip" :style="style" aria-label="Edge tooltip">
    <div class="edge-tooltip-title">{{ getNodeName(edge.fromId) ?? edge.fromId }} → {{ getNodeName(edge.toId) ?? edge.toId }}</div>
    <div class="edge-tooltip-amount mono">{{ edge.amountText }}</div>
  </div>
</template>
