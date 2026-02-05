<script setup lang="ts">
import type { CSSProperties } from 'vue'
import type { GraphNode } from '../types'

type NodeEdgeStats = {
  outLimitText: string
  inLimitText: string
  degree: number
}

type Props = {
  node: GraphNode
  style: CSSProperties
  edgeStats: NodeEdgeStats | null
  equivalentText: string

  showPinActions: boolean
  isPinned: boolean
  pin: () => void
  unpin: () => void
}

defineProps<Props>()

function netText(node: GraphNode): string | null {
  const major = node.net_balance
  if (major != null && String(major).trim() !== '') return String(major)

  const atoms = node.net_balance_atoms
  if (atoms == null) return null

  const s = String(atoms)
  // Back-compat: older fixtures used signed atoms already.
  if (s.startsWith('-')) return s

  const sign = node.net_sign
  if (sign === -1) return `-${s}`
  if (sign === 0) return '0'
  return s
}
</script>

<template>
  <div class="node-card" :style="style">
    <div class="node-header">
      <div class="node-title">{{ node.name ?? node.id }}</div>
      <div v-if="showPinActions" class="node-actions">
        <button v-if="!isPinned" class="btn btn-ghost btn-xxs" type="button" @click="pin">Pin</button>
        <button v-else class="btn btn-ghost btn-xxs" type="button" @click="unpin">Unpin</button>
      </div>
    </div>

    <div class="node-grid">
      <div class="node-item">
        <span class="k">Type</span>
        <span class="v">{{ node.type ?? '—' }}</span>
      </div>
      <div class="node-item">
        <span class="k">Out</span>
        <span class="v mono">{{ edgeStats?.outLimitText ?? '—' }}</span>
        <span class="v">{{ equivalentText }}</span>
      </div>

      <div class="node-item">
        <span class="k">Status</span>
        <span class="v">{{ node.status ?? '—' }}</span>
      </div>
      <div class="node-item">
        <span class="k">In</span>
        <span class="v mono">{{ edgeStats?.inLimitText ?? '—' }}</span>
        <span class="v">{{ equivalentText }}</span>
      </div>

      <div class="node-item">
        <span class="k">Net</span>
        <span class="v mono">{{ netText(node) ?? '—' }}</span>
      </div>
      <div class="node-item">
        <span class="k">Degree</span>
        <span class="v mono">{{ edgeStats?.degree ?? '—' }}</span>
      </div>
    </div>
  </div>
</template>
