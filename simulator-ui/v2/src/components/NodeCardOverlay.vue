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

const emit = defineEmits<{ close: [] }>()

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
    <div class="ds-panel ds-panel--elevated" role="dialog" aria-label="Node details">
      <div class="ds-panel__header">
        <div class="ds-h2">Node</div>
        <div class="ds-row">
          <div v-if="showPinActions" class="ds-row">
            <button v-if="!isPinned" class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" @click="pin">
              Pin
            </button>
            <button v-else class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" @click="unpin">
              Unpin
            </button>
          </div>

          <button
            class="ds-btn ds-btn--ghost ds-btn--icon"
            type="button"
            aria-label="Close"
            title="Close"
            @click="emit('close')"
          >
            ✕
          </button>
        </div>
      </div>

      <div class="ds-panel__body ds-stack" style="gap: 12px">
        <div class="ds-node-card" role="group" aria-label="Node identity">
          <div class="ds-node-card__avatar">{{ String(node.name ?? node.id).slice(0, 2).toUpperCase() }}</div>
          <div class="ds-node-card__info">
            <div class="ds-node-card__name">{{ node.name ?? node.id }}</div>
            <div class="ds-node-card__meta">{{ node.type ?? '—' }} • {{ node.status ?? '—' }}</div>
          </div>
          <div class="ds-node-card__balance">{{ netText(node) ?? '—' }}</div>
        </div>

        <div class="ds-two" style="gap: 8px 12px">
          <div class="ds-row" style="gap: 6px; align-items: baseline">
            <span class="ds-label">Type</span>
            <span class="ds-value">{{ node.type ?? '—' }}</span>
          </div>

          <div class="ds-row" style="gap: 6px; align-items: baseline">
            <span class="ds-label">Status</span>
            <span class="ds-value">{{ node.status ?? '—' }}</span>
          </div>

          <div class="ds-row" style="gap: 6px; align-items: baseline">
            <span class="ds-label">Out</span>
            <span class="ds-value ds-mono">{{ edgeStats?.outLimitText ?? '—' }}</span>
            <span class="ds-label ds-muted">{{ equivalentText }}</span>
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
    </div>
  </div>
</template>
