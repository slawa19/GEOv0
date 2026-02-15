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
  <div v-if="open" class="panel" data-testid="manual-payment-panel" aria-label="Manual payment panel">
    <div class="panel__title">{{ titleText() }}</div>

    <div v-if="participantsSorted.length" class="panel__row">
      <label class="hud-label" for="mp-from">From</label>
      <select
        id="mp-from"
        class="panel__input"
        :value="state.fromPid ?? ''"
        :disabled="busy"
        aria-label="From participant"
        @change="onFromChange(($event.target as HTMLSelectElement).value)"
      >
        <option value="">—</option>
        <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
      </select>
    </div>

    <div v-if="participantsSorted.length" class="panel__row">
      <label class="hud-label" for="mp-to">To</label>
      <select
        id="mp-to"
        class="panel__input"
        :value="state.toPid ?? ''"
        :disabled="busy || !state.fromPid"
        aria-label="To participant"
        @change="onToChange(($event.target as HTMLSelectElement).value)"
      >
        <option value="">—</option>
        <option v-for="p in toParticipants" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
      </select>
    </div>

    <div v-if="isPickFrom" class="hud-label" style="margin: 6px 0 2px">Pick From node (canvas) or choose from dropdown.</div>
    <div v-if="isPickTo" class="hud-label" style="margin: 6px 0 2px">Pick To node (canvas) or choose from dropdown.</div>

    <template v-if="isConfirm">
      <div class="panel__row">
        <div class="hud-label">Available</div>
        <div class="hud-value mono">{{ availableCapacity ?? '—' }} {{ unit }}</div>
      </div>

      <div class="panel__row">
        <label class="hud-label" for="mp-amount">Amount</label>
        <input
          id="mp-amount"
          v-model="amount"
          class="panel__input mono"
          inputmode="decimal"
          placeholder="0.00"
          :aria-invalid="amountValid ? 'false' : 'true'"
        />
        <span class="hud-label">{{ unit }}</span>
      </div>

      <div v-if="exceedsCapacity" class="panel__warn mono" data-testid="manual-payment-capacity-warn">
        Amount exceeds available capacity.
      </div>
    </template>

    <div v-if="state.error" class="panel__error mono" data-testid="manual-payment-error">{{ state.error }}</div>

    <div class="panel__actions">
      <button
        v-if="isConfirm"
        class="btn btn-xs"
        type="button"
        data-testid="manual-payment-confirm"
        :disabled="!canConfirm"
        @click="onConfirm"
      >
        Confirm
      </button>
      <button class="btn btn-xs btn-ghost" type="button" data-testid="manual-payment-cancel" :disabled="busy" @click="cancel">
        Cancel
      </button>
    </div>
  </div>
</template>

<style scoped>
.panel {
  position: absolute;
  right: 12px;
  top: 110px;
  z-index: 42;
  min-width: 340px;
  max-width: min(520px, calc(100vw - 24px));
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
  pointer-events: auto;
}

.panel__title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
}

.panel__row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 6px 0;
}

.panel__input {
  flex: 1;
  background: rgba(2, 6, 23, 0.15);
  color: rgba(226, 232, 240, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 10px;
  padding: 7px 10px;
  font-size: 12px;
}

.panel__error {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(248, 113, 113, 0.35);
  background: rgba(127, 29, 29, 0.14);
  color: rgba(254, 202, 202, 0.9);
}

.panel__warn {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(251, 191, 36, 0.28);
  background: rgba(161, 98, 7, 0.14);
  color: rgba(254, 243, 199, 0.92);
}

.panel__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 10px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
</style>

