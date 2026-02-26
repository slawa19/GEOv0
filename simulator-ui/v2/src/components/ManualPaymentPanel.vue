<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import { useParticipantsList } from '../composables/useParticipantsList'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
import { parseAmountNumber, parseAmountStringOrNull } from '../utils/numberFormat'
import { participantLabel } from '../utils/participants'
import { useOverlayPositioning } from '../utils/overlayPosition'
import { isActiveStatus } from '../utils/status'

type Props = {
  phase: InteractPhase
  state: InteractState

  unit: string
  availableCapacity?: string | null

  // MUST MP-0: tri-state trustlines wiring from parent.
  // NOTE: logic/UI will be implemented in follow-up tasks (MP-1/MP-2/MP-6).
  trustlinesLoading: boolean
  /**
   * Payment targets for filtering the To dropdown (tri-state).
   * Invariant: `undefined` should be used only while `trustlinesLoading=true`.
   */
  paymentToTargetIds: Set<string> | undefined
  trustlines?: TrustlineInfo[]

  /** Optional dropdown data (prefer backend-driven list from Interact Actions API). */
  participants?: ParticipantInfo[]

  /** Optional setters (used by dropdown UX). */
  setFromPid?: (pid: string | null) => void
  setToPid?: (pid: string | null) => void

  busy: boolean
  canSendPayment?: boolean

  confirmPayment: (amount: string) => Promise<void> | void
  cancel: () => void

  /** Optional anchor для позиционирования рядом с источником открытия.
   *  При null/undefined применяется CSS default (right: 12px, top: 110px). */
  anchor?: { x: number; y: number } | null
  /** Host element used as overlay viewport for clamping. */
  hostEl?: HTMLElement | null
}

const props = defineProps<Props>()

const anchorPositionStyle = useOverlayPositioning(
  () => props.anchor,
  () => props.hostEl,
  { w: 360, h: 420 },
)

const amount = ref('')

watch(
  () => props.phase,
  (p) => {
    // UX: entering confirm step should not keep stale input from a previous payment.
    if (p === 'confirm-payment') amount.value = ''
  },
  { immediate: true },
)

const amountNum = computed(() => {
  const normalized = parseAmountStringOrNull(amount.value)
  if (normalized == null) return NaN
  return parseAmountNumber(normalized)
})

const amountValid = computed(() => amountNum.value > 0)

const availableNormalized = computed(() => parseAmountStringOrNull(props.availableCapacity))

const availableNum = computed(() => {
  if (availableNormalized.value == null) return NaN
  return parseAmountNumber(availableNormalized.value)
})

const exceedsCapacity = computed(() => {
  if (!Number.isFinite(availableNum.value)) return false
  if (!Number.isFinite(amountNum.value)) return false
  return amountNum.value > availableNum.value
})

const confirmDisabledReason = computed<string | null>(() => {
  // Spec: do not show reason while busy.
  if (props.busy) return null

  const rawTrimmed = amount.value.trim()
  if (!rawTrimmed) return 'Enter a positive amount.'

  const normalized = parseAmountStringOrNull(amount.value)
  if (normalized == null) return "Invalid amount format. Use digits and '.' for decimals."

  const n = parseAmountNumber(normalized)
  if (!(n > 0)) return 'Enter a positive amount.'

  if (exceedsCapacity.value) {
    // `availableCapacity` is a backend-sourced string; keep original formatting in UI message.
    return `Amount exceeds available capacity (max: ${props.availableCapacity ?? '—'} ${props.unit}).`
  }

  const from = (props.state.fromPid ?? '').trim()
  const to = (props.state.toPid ?? '').trim()
  if (from && to && props.canSendPayment === false) {
    return 'No direct routes available between selected participants (direct trustlines only).'
  }

  return null
})

const canConfirm = computed(() => {
  if (props.busy) return false
  return confirmDisabledReason.value == null
})

const isPickFrom = computed(() => props.phase === 'picking-payment-from')
const isPickTo = computed(() => props.phase === 'picking-payment-to')
const isConfirm = computed(() => props.phase === 'confirm-payment')
const open = computed(() => isPickFrom.value || isPickTo.value || isConfirm.value)

/**
 * Dropdown-specific tri-state normalization.
 * - unknown only while trustlines are loading
 * - loading=false always yields known Set (possibly empty)
 */
const dropdownToTargetIds = computed<Set<string> | undefined>(() => {
  if (props.trustlinesLoading) return undefined
  return props.paymentToTargetIds ?? new Set<string>()
})

async function onConfirm() {
  if (!canConfirm.value) return

  const normalized = parseAmountStringOrNull(amount.value)
  if (normalized == null) return

  await props.confirmPayment(normalized)
}

function titleText() {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `Manual payment: ${from} → ${to}`
  return 'Manual payment'
}

const { participantsSorted, toParticipants } = useParticipantsList<ParticipantInfo>({
  participants: () => props.participants,
  fromParticipantId: () => props.state.fromPid,
  availableTargetIds: () => dropdownToTargetIds.value,
})

// MP-1b: reset recipient if it becomes unavailable in known-state.
const toSelectionInvalidWarning = ref<string | null>(null)

// MP-6: tri-state UX in To label/help.
const toListUpdating = computed(() => {
  if (!props.state.fromPid) return false
  if (props.phase !== 'picking-payment-to' && props.phase !== 'confirm-payment') return false
  return props.trustlinesLoading
})

const toInlineHelpText = computed<string | null>(() => {
  if (props.phase === 'confirm-payment') return null
  if (!props.state.fromPid) return null

  const targets = dropdownToTargetIds.value
  if (targets === undefined) return 'Routes are updating; the list may include unreachable recipients.'
  if (targets.size === 0) {
    return 'No direct routes available (direct trustlines only). Multi-hop routes may exist but are not shown.'
  }
  return null
})

// UX-9: stable aria-describedby target for To select.
const toAriaHelpText = computed<string>(() => {
  if (props.phase === 'confirm-payment') return ''
  return toSelectionInvalidWarning.value ?? toInlineHelpText.value ?? ''
})

// MP-2: capacity labels for To options.
const capacityByToPid = computed<Map<string, string>>(() => {
  const from = (props.state.fromPid ?? '').trim()
  if (!from) return new Map()

  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  const out = new Map<string, string>()

  // For payment `from -> to`, capacity is defined by trustline `to -> from`.
  // So when From is selected, we look for trustlines with `to_pid === from`.
  for (const tl of items) {
    if ((tl.to_pid ?? '').trim() !== from) continue
    if (!isActiveStatus(tl.status)) continue
    const toPid = (tl.from_pid ?? '').trim()
    if (!toPid) continue
    out.set(toPid, tl.available)
  }

  return out
})

function toOptionLabel(p: ParticipantInfo): string {
  const pid = (p?.pid ?? '').trim()
  const cap = pid ? capacityByToPid.value.get(pid) : undefined
  if (!cap) return `${participantLabel(p)} — …`
  return `${participantLabel(p)} — ${cap} ${props.unit}`
}

watch(
  [() => dropdownToTargetIds.value, () => props.state.toPid],
  ([targets, toPid]) => {
    const to = (toPid ?? '').trim()
    if (!to) return

    // Unknown: do NOT reset.
    if (targets === undefined) return

    if (!targets.has(to)) {
      props.setToPid?.(null)
      toSelectionInvalidWarning.value = 'Selected recipient is no longer available. Please re-select.'
    }
  },
)

function onFromChange(v: string) {
  const pid = v ? v : null
  props.setFromPid?.(pid)
  // If To is now invalid, clear it.
  if (pid && pid === props.state.toPid) props.setToPid?.(null)
  toSelectionInvalidWarning.value = null
}

function onToChange(v: string) {
  props.setToPid?.(v ? v : null)
  toSelectionInvalidWarning.value = null
}
</script>

<template>
  <div v-if="open" class="ds-ov-panel ds-panel ds-panel--elevated" :style="anchorPositionStyle" data-testid="manual-payment-panel" aria-label="Manual payment panel">
    <div class="ds-panel__header">
      <div class="ds-h2">
        {{ titleText() }}
        <span class="ds-muted ds-mono"> (ESC to close)</span>
      </div>
    </div>

    <div class="ds-panel__body ds-stack">
      <div v-if="participantsSorted.length" class="ds-controls__row">
        <label class="ds-label" for="mp-from">From</label>
        <select
          id="mp-from"
          class="ds-select"
          :value="state.fromPid ?? ''"
          :disabled="busy"
          aria-label="From participant"
          @change="onFromChange(($event.target as HTMLSelectElement).value)"
        >
          <option value="">—</option>
          <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ participantLabel(p) }}</option>
        </select>
      </div>

      <div v-if="participantsSorted.length" class="ds-controls__row">
        <label class="ds-label" for="mp-to">
          To
          <span v-if="toListUpdating" class="ds-muted ds-mono"> (updating…)</span>
        </label>
        <select
          id="mp-to"
          class="ds-select"
          :value="state.toPid ?? ''"
          :disabled="busy || !state.fromPid"
          aria-label="To participant"
          aria-describedby="mp-to-help"
          @change="onToChange(($event.target as HTMLSelectElement).value)"
        >
          <option value="">—</option>
          <option v-for="p in toParticipants" :key="p.pid" :value="p.pid">{{ toOptionLabel(p) }}</option>
        </select>
      </div>

      <div
        v-if="!isConfirm && state.fromPid && toSelectionInvalidWarning"
        class="ds-alert ds-alert--warn ds-mono"
        data-testid="manual-payment-to-invalid-warn"
      >
        {{ toSelectionInvalidWarning }}
      </div>

      <div
        id="mp-to-help"
        class="ds-help mp-to-help"
        data-testid="manual-payment-to-help"
        :style="{ display: toAriaHelpText ? 'block' : 'none' }"
      >
        {{ toAriaHelpText }}
      </div>

      <div v-if="isPickFrom" class="ds-help mp-pick-help">
        Pick From node (canvas) or choose from dropdown.
      </div>
      <div v-if="isPickTo" class="ds-help mp-pick-help">Pick To node (canvas) or choose from dropdown.</div>

      <template v-if="isConfirm">
        <div class="ds-row ds-row--space">
          <div class="ds-label">Available</div>
          <div class="ds-value ds-mono">{{ availableCapacity ?? '—' }} {{ unit }}</div>
        </div>

        <div class="ds-controls__row">
          <label class="ds-label" for="mp-amount">Amount</label>
          <div class="ds-row mp-amount-row">
            <input
              id="mp-amount"
              v-model="amount"
              class="ds-input ds-mono mp-amount-input"
              type="text"
              inputmode="decimal"
              autocomplete="off"
              autocapitalize="off"
              autocorrect="off"
              spellcheck="false"
              placeholder="0.00"
              :aria-invalid="amount.trim() && !amountValid ? 'true' : 'false'"
              aria-describedby="mp-amount-help"
              @keydown.enter.prevent="onConfirm"
            />
            <span class="ds-label ds-muted">{{ unit }}</span>
          </div>
        </div>
      </template>

        <div
          v-if="isConfirm"
          id="mp-amount-help"
          class="ds-help mp-confirm-help"
          data-testid="mp-amount-help"
          :style="{ display: confirmDisabledReason ? 'block' : 'none' }"
        >
          {{ confirmDisabledReason ?? '' }}
        </div>

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="manual-payment-error">{{ state.error }}</div>

      <div class="ds-row mp-actions">
        <button
          v-if="isConfirm"
          class="ds-btn ds-btn--primary"
          type="button"
          data-testid="manual-payment-confirm"
          :disabled="!canConfirm"
          @click="onConfirm"
        >
          {{ busy ? 'Sending…' : 'Confirm' }}
        </button>
        <button
          class="ds-btn ds-btn--ghost"
          type="button"
          data-testid="manual-payment-cancel"
          :disabled="busy"
          @click="cancel"
        >
          Cancel
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mp-pick-help {
  margin: 6px 0 2px;
}

.mp-to-help {
  margin: 4px 0 0;
}

.mp-amount-row {
  flex-wrap: nowrap;
}

.mp-amount-input {
  flex: 1;
}

.mp-actions {
  justify-content: flex-end;
}

.mp-confirm-help {
  margin: 2px 0 0;
}
</style>


