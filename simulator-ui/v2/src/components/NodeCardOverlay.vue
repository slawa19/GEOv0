<script setup lang="ts">
import { computed } from 'vue'
import type { CSSProperties } from 'vue'
import type { GraphNode } from '../types'
import type { TrustlineInfo } from '../api/simulatorTypes'
import { VIZ_MAPPING } from '../vizMapping'
import { renderOrDash } from '../utils/valueFormat'

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

  // BUG-1: Interact Mode extensions
  interactMode?: boolean
  /** All trustlines from useInteractMode — filtered internally by node.id. */
  interactTrustlines?: TrustlineInfo[]
  /** True while trustlines are being fetched from the API. */
  trustlinesLoading?: boolean
  /** True while Interact Mode is busy (disable quick actions). */
  interactBusy?: boolean
  onInteractSendPayment?: (fromPid: string) => void
  onInteractNewTrustline?: (fromPid: string) => void
  onInteractEditTrustline?: (fromPid: string, toPid: string) => void
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

/** Trustlines that involve this node (outgoing or incoming). */
const nodeTrustlines = computed<TrustlineInfo[]>(() => {
  if (!props.interactMode || !props.interactTrustlines?.length) return []
  const id = props.node.id
  return props.interactTrustlines.filter(
    (tl) => tl.from_pid === id || tl.to_pid === id,
  )
})
</script>

<template>
  <div class="ds-ov-node-card" :style="style">
    <div class="ds-panel ds-panel--elevated" role="dialog" aria-label="Node details">
      <div class="ds-panel__body ds-ov-node-card__body">

        <!-- Identity + Balance (merged) ------------------------------------>
        <div class="ds-ov-node-card__identity">
          <div class="ds-node-card__avatar ds-ov-node-card__avatar">
            {{ String(node.name ?? node.id).slice(0, 2).toUpperCase() }}
          </div>
          <div class="ds-ov-node-card__text">
            <div class="ds-node-card__name">{{ node.name ?? node.id }}</div>
            <div class="ds-node-card__meta">{{ node.type ?? '—' }} · {{ node.status ?? '—' }}</div>
          </div>
          <div class="ds-ov-node-card__right">
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
            <div class="ds-ov-node-card__balance" :style="{ color: nodeColor }">{{ netText(node) ?? '—' }}</div>
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

        <!-- BUG-1: Interact Mode Trustlines section --------------------------->
        <template v-if="interactMode">
          <hr class="ds-ov-node-card__divider" />

          <!-- Quick-action buttons -->
          <div class="nco-interact-actions">
            <button
              class="ds-btn ds-btn--primary ds-btn--sm"
              type="button"
              :disabled="!!interactBusy"
              @click="onInteractSendPayment?.(node.id)"
            >
              💸 Send Payment
            </button>
            <button
              class="ds-btn ds-btn--secondary ds-btn--sm"
              type="button"
              :disabled="!!interactBusy"
              @click="onInteractNewTrustline?.(node.id)"
            >
              ＋ New Trustline
            </button>
          </div>

          <!-- Trustlines list -->
          <div v-if="nodeTrustlines.length > 0" class="nco-trustlines">
            <div class="nco-trustlines__header ds-label">Trustlines</div>
            <div
              v-for="tl in nodeTrustlines"
              :key="`${tl.from_pid}→${tl.to_pid}`"
              class="nco-trustline-row"
            >
              <span class="nco-trustline-row__dir" aria-hidden="true">
                {{ tl.from_pid === node.id ? '→' : '←' }}
              </span>
              <span class="nco-trustline-row__peer ds-mono">
                {{ tl.from_pid === node.id ? tl.to_name : tl.from_name }}
              </span>
              <span class="nco-trustline-row__amounts ds-mono">
                {{ renderOrDash(tl.used) }}&thinsp;/&thinsp;{{ renderOrDash(tl.limit) }}
                <span class="nco-trustline-row__available ds-text-secondary">(avail:&thinsp;{{ renderOrDash(tl.available) }})</span>
              </span>
              <button
                class="ds-btn ds-btn--ghost ds-btn--icon nco-trustline-row__edit"
                type="button"
                title="Edit trustline"
                aria-label="Edit trustline"
                @click="onInteractEditTrustline?.(tl.from_pid, tl.to_pid)"
              >
                ✏️
              </button>
            </div>
          </div>
          <div v-else-if="trustlinesLoading" class="nco-trustlines__empty ds-label">
            <span class="ds-text-secondary">Loading trustlines…</span>
          </div>
          <div v-else class="nco-trustlines__empty ds-label">
            No trustlines
          </div>
        </template>

      </div>
    </div>
  </div>
</template>

<style scoped>
/* Interact Mode: quick-action buttons */
.nco-interact-actions {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

/* Trustlines list */
.nco-trustlines {
  margin-top: 1px;
}

.nco-trustlines__header {
  font-size: 0.72rem;
  opacity: 0.6;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 3px;
}

.nco-trustline-row {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 1px 0;
  font-size: 0.8rem;
}

.nco-trustline-row__dir {
  flex-shrink: 0;
  width: 1.2em;
  text-align: center;
  opacity: 0.7;
}

.nco-trustline-row__peer {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nco-trustline-row__amounts {
  flex-shrink: 0;
  font-size: 0.75rem;
  opacity: 0.8;
}

.nco-trustline-row__available {
  font-size: 0.7rem;
  opacity: 0.65;
  margin-left: 2px;
}

.nco-trustline-row__edit {
  flex-shrink: 0;
  font-size: 0.75rem;
  padding: 1px 3px;
  line-height: 1;
  opacity: 0.6;
  transition: opacity 0.15s;
}

.nco-trustline-row__edit:hover {
  opacity: 1;
}

.nco-trustlines__empty {
  font-size: 0.75rem;
  opacity: 0.5;
  font-style: italic;
}
</style>
