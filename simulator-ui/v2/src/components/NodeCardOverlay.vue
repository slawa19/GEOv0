<script setup lang="ts">
import { computed } from 'vue'
import type { CSSProperties } from 'vue'
import type { GraphNode } from '../types'
import { VIZ_MAPPING } from '../vizMapping'

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

const props = defineProps<Props>()

const nodeColor = computed(() => {
  const key = String(props.node.viz_color_key ?? 'unknown')
  return VIZ_MAPPING.node.color[key]?.fill ?? VIZ_MAPPING.node.color.unknown.fill
})

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
      <div class="ds-panel__body ds-ov-node-card__body">

        <!-- Service actions (Pin / Close) ---------------------------------->
        <!-- Positioned absolutely; should not consume layout height. -->
        <div class="ds-ov-node-card__service">
          <button
            v-if="showPinActions"
            class="ds-btn ds-btn--ghost ds-btn--icon ds-ov-node-card__action-btn"
            type="button"
            :aria-label="isPinned ? 'Unpin' : 'Pin'"
            :title="isPinned ? 'Unpin' : 'Pin'"
            @click="isPinned ? unpin() : pin()"
          >
            {{ isPinned ? '◆' : '◇' }}
          </button>
          <button
            class="ds-btn ds-btn--ghost ds-btn--icon ds-ov-node-card__action-btn"
            type="button"
            aria-label="Close"
            title="Close"
            @click="emit('close')"
          >
            ×
          </button>
        </div>

        <!-- Identity + Balance (merged) ------------------------------------>
        <div class="ds-ov-node-card__identity">
          <div class="ds-node-card__avatar ds-ov-node-card__avatar">
            {{ String(node.name ?? node.id).slice(0, 2).toUpperCase() }}
          </div>
          <div class="ds-node-card__info">
            <div class="ds-node-card__name">{{ node.name ?? node.id }}</div>
            <div class="ds-ov-node-card__meta-row">
              <span class="ds-node-card__meta">{{ node.type ?? '—' }} · {{ node.status ?? '—' }}</span>
              <span class="ds-ov-node-card__balance" :style="{ color: nodeColor }">{{ netText(node) ?? '—' }}</span>
            </div>
          </div>
        </div>

        <!-- Separator -------------------------------------------------------->
        <hr class="ds-ov-node-card__divider" />

        <!-- Stats (only unique fields: Out, In, Degree) ---------------------->
        <div class="ds-ov-node-card__stats">
          <div class="ds-ov-node-card__stat">
            <span class="ds-label">Out</span>
            <span class="ds-value ds-mono">{{ edgeStats?.outLimitText ?? '—' }}</span>
          </div>
          <div class="ds-ov-node-card__stat">
            <span class="ds-label">In</span>
            <span class="ds-value ds-mono">{{ edgeStats?.inLimitText ?? '—' }}</span>
          </div>
          <div class="ds-ov-node-card__stat">
            <span class="ds-label">Degree</span>
            <span class="ds-value ds-mono">{{ edgeStats?.degree ?? '—' }}</span>
          </div>
        </div>

      </div>
    </div>
  </div>
</template>
