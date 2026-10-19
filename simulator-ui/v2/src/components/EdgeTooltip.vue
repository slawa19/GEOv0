<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'

import { useParticipantDisplayId } from '../composables/useParticipantDisplay'
import { getOverlaySurfaceDescriptor } from '../ui-kit/overlaySurfaceCatalog'
import { renderOrDash } from '../utils/valueFormat'

type HoveredEdge = {
  key: string | null
  fromId: string
  toId: string
  amountText: string
  // BUG-2: Interact Mode extended fields
  trustLimit?: string | number | null
  used?: string | number | null
  available?: string | number | null
  /** 043 S1 (review finding 1): the equivalent captured with the figures; the only unit this tooltip prints. */
  equivalent?: string | null
}

type Props = {
  edge: HoveredEdge
  style: CSSProperties
  getNodeName: (id: string) => string | null
}

const props = defineProps<Props>()

/**
 * 043 S1 (decision 1): the line in words and numbers, while watching too - not only in Interact. A trust line runs
 * creditor -> debtor (`from -> to`, AGENTS.md section 8): "from trusts to up to the limit; to owes from the used
 * amount; available". A negative `available` is the debt's excess over a lowered limit (026 T2602), not a payable
 * amount. Without any of the three figures the sentence is not drawn and the bare amount line stays.
 */
const hasFigures = computed(() => props.edge.trustLimit != null || props.edge.used != null || props.edge.available != null)
// 043 S1 (review finding 1): the unit is the one captured with the figures, never the live scene's: a scene replaced
// under a still pointer must not relabel old figures. No captured equivalent - no unit printed.
const unitSuffix = computed(() => (props.edge.equivalent ? ` ${props.edge.equivalent}` : ''))
const availableText = computed(() => {
  const s = renderOrDash(props.edge.available)
  if (s === '—') return 'доступно —'
  return s.startsWith('-') ? `сверх лимита на ${s.slice(1)}${unitSuffix.value}` : `доступно ${s}${unitSuffix.value}`
})
const money = (v: unknown) => {
  const s = renderOrDash(v)
  return s === '—' ? s : `${s}${unitSuffix.value}`
}

// An id is PRINTED when the node has no name, so it goes through the display rule.
const showPid = useParticipantDisplayId()

const edgeTooltipSurface = getOverlaySurfaceDescriptor('edge-tooltip')
</script>

<template>
  <div
    v-if="edge.key"
    class="ds-ov-tooltip"
    :style="style"
    :role="edgeTooltipSurface.a11y?.role"
    :aria-label="edgeTooltipSurface.a11y?.ariaLabel"
  >
    <div v-if="!hasFigures" class="ds-ov-tooltip__title">
      {{ showPid(getNodeName(edge.fromId) ?? edge.fromId) }} → {{ showPid(getNodeName(edge.toId) ?? edge.toId) }}
    </div>
    <template v-if="hasFigures">
      <div class="ds-ov-tooltip__amount" data-testid="edge-tooltip-trust">
        <b>{{ showPid(getNodeName(edge.fromId) ?? edge.fromId) }}</b> доверяет <b>{{ showPid(getNodeName(edge.toId) ?? edge.toId) }}</b>
        до {{ money(edge.trustLimit) }}
      </div>
      <div class="ds-ov-tooltip__amount" data-testid="edge-tooltip-debt">
        долг {{ showPid(getNodeName(edge.toId) ?? edge.toId) }} перед {{ showPid(getNodeName(edge.fromId) ?? edge.fromId) }} — {{ money(edge.used) }} · {{ availableText }}
      </div>
    </template>
    <div v-else class="ds-ov-tooltip__amount">{{ edge.amountText }}</div>
    <!-- 048 S1 (decision 4): the line's raw `Status` row is gone - an English word under a Russian sentence. -->
  </div>
</template>

