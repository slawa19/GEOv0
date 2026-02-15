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
  <div class="ds-ov-node-card" :style="style">
    <div class="ds-row ds-row--space" style="margin-bottom: 10px">
      <div class="ds-h2">{{ node.name ?? node.id }}</div>
      <div v-if="showPinActions" class="ds-row">
        <button v-if="!isPinned" class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" @click="pin">
          Pin
        </button>
        <button v-else class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" @click="unpin">
          Unpin
        </button>
      </div>
    </div>

    <div class="ds-two" style="gap: 8px 12px">
      <div class="ds-row" style="gap: 6px; align-items: baseline">
        <span class="ds-label">Type</span>
        <span class="ds-value">{{ node.type ?? '—' }}</span>
      </div>

      <div class="ds-row" style="gap: 6px; align-items: baseline">
        <span class="ds-label">Out</span>
        <span class="ds-value ds-mono">{{ edgeStats?.outLimitText ?? '—' }}</span>
        <span class="ds-label ds-muted">{{ equivalentText }}</span>
      </div>

      <div class="ds-row" style="gap: 6px; align-items: baseline">
        <span class="ds-label">Status</span>
        <span class="ds-value">{{ node.status ?? '—' }}</span>
      </div>

      <div class="ds-row" style="gap: 6px; align-items: baseline">
        <span class="ds-label">In</span>
        <span class="ds-value ds-mono">{{ edgeStats?.inLimitText ?? '—' }}</span>
        <span class="ds-label ds-muted">{{ equivalentText }}</span>
      </div>

      <div class="ds-row" style="gap: 6px; align-items: baseline">
        <span class="ds-label">Net</span>
        <span class="ds-value ds-mono">{{ netText(node) ?? '—' }}</span>
      </div>

      <div class="ds-row" style="gap: 6px; align-items: baseline">
        <span class="ds-label">Degree</span>
        <span class="ds-value ds-mono">{{ edgeStats?.degree ?? '—' }}</span>
      </div>
    </div>
  </div>
</template>
