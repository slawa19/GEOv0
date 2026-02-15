<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import type { ParticipantInfo } from '../api/simulatorTypes'

type Props = {
  phase: InteractPhase
  state: InteractState

  unit: string
  availableCapacity?: string | null

  /** Optional dropdown data (prefer backend-driven list from Interact Actions API). */
  participants?: ParticipantInfo[]

  /** Optional setters (used by dropdown UX). */
  setFromPid?: (pid: string | null) => void
  setToPid?: (pid: string | null) => void

  busy: boolean
  canSendPayment?: boolean

  confirmPayment: (amount: string) => Promise<void> | void
  cancel: () => void
}

const props = defineProps<Props>()

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
  const v = Number(amount.value)
  return Number.isFinite(v) ? v : NaN
})

const amountValid = computed(() => amountNum.value > 0)

const availableNum = computed(() => {
  const v = Number(props.availableCapacity ?? NaN)
  return Number.isFinite(v) ? v : NaN
})

const exceedsCapacity = computed(() => {
  if (!Number.isFinite(availableNum.value)) return false
  if (!Number.isFinite(amountNum.value)) return false
  return amountNum.value > availableNum.value
})

const canConfirm = computed(() => {
  if (props.busy) return false
  if (!amountValid.value) return false
  if (exceedsCapacity.value) return false
  // Optional additional guard from state-machine.
  if (props.canSendPayment === false) return false
  return true
})

const isPickFrom = computed(() => props.phase === 'picking-payment-from')
const isPickTo = computed(() => props.phase === 'picking-payment-to')
const isConfirm = computed(() => props.phase === 'confirm-payment')
const open = computed(() => isPickFrom.value || isPickTo.value || isConfirm.value)

async function onConfirm() {
  if (!canConfirm.value) return
  await props.confirmPayment(amount.value)
}

function titleText() {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `Manual payment: ${from} → ${to}`
  return 'Manual payment'
}

function labelFor(p: ParticipantInfo): string {
  const pid = String(p.pid ?? '').trim()
  const name = String(p.name ?? '').trim()
  if (!pid) return name || '—'
  if (!name || name === pid) return pid
  return `${name} (${pid})`
}

const participantsSorted = computed(() => {
  const items = Array.isArray(props.participants) ? props.participants : []
  return [...items]
    .filter((p) => String(p?.pid ?? '').trim())
    .sort((a, b) => labelFor(a).localeCompare(labelFor(b)))
})

// UX guard: prevent selecting the same participant as both From and To.
const toParticipants = computed(() => {
  const from = String(props.state.fromPid ?? '').trim()
  if (!from) return participantsSorted.value
  return participantsSorted.value.filter((p) => String(p?.pid ?? '').trim() !== from)
})

function onFromChange(v: string) {
  const pid = v ? v : null
  props.setFromPid?.(pid)
  // If To is now invalid, clear it.
  if (pid && pid === props.state.toPid) props.setToPid?.(null)
}

function onToChange(v: string) {
  props.setToPid?.(v ? v : null)
}
</script>

<template>
  <div v-if="open" class="ds-ov-panel ds-panel ds-panel--elevated" data-testid="manual-payment-panel" aria-label="Manual payment panel">
    <div class="ds-panel__header">
      <div class="ds-h2">{{ titleText() }}</div>
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
          <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
        </select>
      </div>

      <div v-if="participantsSorted.length" class="ds-controls__row">
        <label class="ds-label" for="mp-to">To</label>
        <select
          id="mp-to"
          class="ds-select"
          :value="state.toPid ?? ''"
          :disabled="busy || !state.fromPid"
          aria-label="To participant"
          @change="onToChange(($event.target as HTMLSelectElement).value)"
        >
          <option value="">—</option>
          <option v-for="p in toParticipants" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
        </select>
      </div>

      <div v-if="isPickFrom" class="ds-label ds-muted" style="margin: 6px 0 2px">
        Pick From node (canvas) or choose from dropdown.
      </div>
      <div v-if="isPickTo" class="ds-label ds-muted" style="margin: 6px 0 2px">Pick To node (canvas) or choose from dropdown.</div>

      <template v-if="isConfirm">
        <div class="ds-row ds-row--space">
          <div class="ds-label">Available</div>
          <div class="ds-value ds-mono">{{ availableCapacity ?? '—' }} {{ unit }}</div>
        </div>

        <div class="ds-controls__row">
          <label class="ds-label" for="mp-amount">Amount</label>
          <div class="ds-row" style="flex-wrap: nowrap">
            <input
              id="mp-amount"
              v-model="amount"
              class="ds-input ds-mono"
              style="flex: 1"
              inputmode="decimal"
              placeholder="0.00"
              :aria-invalid="amountValid ? 'false' : 'true'"
            />
            <span class="ds-label ds-muted">{{ unit }}</span>
          </div>
        </div>

        <div v-if="exceedsCapacity" class="ds-alert ds-alert--warn ds-mono" data-testid="manual-payment-capacity-warn">
          Amount exceeds available capacity.
        </div>
      </template>

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="manual-payment-error">{{ state.error }}</div>

      <div class="ds-row" style="justify-content: flex-end">
        <button
          v-if="isConfirm"
          class="ds-btn ds-btn--primary"
          type="button"
          data-testid="manual-payment-confirm"
          :disabled="!canConfirm"
          @click="onConfirm"
        >
          Confirm
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

