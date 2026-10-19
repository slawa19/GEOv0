<script setup lang="ts">
import { computed, type CSSProperties } from 'vue'

import { useParticipantDisplayId } from '../composables/useParticipantDisplay'
import { useUiLocale } from '../composables/useUiLocale'
import { templateParts, type MessageKey } from '../i18n/t'
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
const { t } = useUiLocale()
const availableText = computed(() => {
  const s = renderOrDash(props.edge.available)
  if (s === '—') return t('edgeTooltip.availableUnknown')
  return s.startsWith('-')
    ? t('edgeTooltip.overLimit', { amount: `${s.slice(1)}${unitSuffix.value}` })
    : t('edgeTooltip.available', { amount: `${s}${unitSuffix.value}` })
})
const money = (v: unknown) => {
  const s = renderOrDash(v)
  return s === '—' ? s : `${s}${unitSuffix.value}`
}

// An id is PRINTED when the node has no name, so it goes through the display rule.
const showPid = useParticipantDisplayId()
const fromName = computed(() => showPid(props.getNodeName(props.edge.fromId) ?? props.edge.fromId))
const toName = computed(() => showPid(props.getNodeName(props.edge.toId) ?? props.edge.toId))

/**
 * 046 wave 1: each sentence is ONE template of the dictionary (`edgeTooltip.trust`, `edgeTooltip.debt`); its parts are
 * drawn in order, the names in bold in the first. The amounts are decimal strings as they came (never `Intl`).
 */
type Part = { text: string; bold: boolean }
function sentence(key: MessageKey, boldNames: boolean): Part[] {
  const values: Record<string, string> = {
    from: fromName.value,
    to: toName.value,
    limit: money(props.edge.trustLimit),
    used: money(props.edge.used),
    available: availableText.value,
  }
  return templateParts(key).map((p) => ('text' in p
    ? { text: p.text, bold: false }
    : { text: values[p.name] ?? `{${p.name}}`, bold: boldNames && (p.name === 'from' || p.name === 'to') }))
}
const trustParts = computed(() => sentence('edgeTooltip.trust', true))
const debtParts = computed(() => sentence('edgeTooltip.debt', false))

const edgeTooltipSurface = getOverlaySurfaceDescriptor('edge-tooltip')
</script>

<template>
  <div
    v-if="edge.key"
    class="ds-ov-tooltip"
    :style="style"
    :role="edgeTooltipSurface.a11y?.role"
    :aria-label="edgeTooltipSurface.a11y?.ariaLabel ? t('edgeTooltip.aria') : undefined"
    data-testid="edge-tooltip"
  >
    <div v-if="!hasFigures" class="ds-ov-tooltip__title">
      {{ fromName }} → {{ toName }}
    </div>
    <template v-if="hasFigures">
      <div class="ds-ov-tooltip__amount" data-testid="edge-tooltip-trust">
        <template v-for="(p, i) in trustParts" :key="i"><b v-if="p.bold">{{ p.text }}</b><template v-else>{{ p.text }}</template></template>
      </div>
      <div class="ds-ov-tooltip__amount" data-testid="edge-tooltip-debt">
        <template v-for="(p, i) in debtParts" :key="i">{{ p.text }}</template>
      </div>
    </template>
    <div v-else class="ds-ov-tooltip__amount">{{ edge.amountText }}</div>
    <!-- 048 S1 (decision 4): the line's raw `Status` row is gone - an English word under a Russian sentence. -->
  </div>
</template>

