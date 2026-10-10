<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import type {
  InteractPhase,
  InteractState,
  ManualPaymentOutcome,
  PaymentTargetEstimate,
} from '../composables/useInteractMode'
import { useParticipantsList } from '../composables/useParticipantsList'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
import { amountStepHint, equivalentPrecision } from '../config/equivalentPrecision'
import { interactText } from '../i18n/interactStrings'
import { compareMoney, formatMoney } from '../utils/money'
import { parseAmountNumber, parseAmountStringOrNull } from '../utils/numberFormat'
import { participantLabel } from '../utils/participants'
import { isActiveStatus } from '../utils/status'
import OverlaySelect from './common/OverlaySelect.vue'

type Props = {
  phase: InteractPhase
  state: InteractState

  unit: string
  availableCapacity?: string | null

  // MUST MP-0: tri-state trustlines wiring from parent.
  // NOTE: logic/UI will be implemented in follow-up tasks (MP-1/MP-2/MP-6).
  trustlinesLoading: boolean

  /** Phase 2.5: tri-state wiring for backend payment-targets fetch. */
  paymentTargetsLoading: boolean

  /** Phase 2.5: current max_hops policy used for backend payment-targets (6 default, 8 deep). */
  paymentTargetsMaxHops?: number

  /** Best-effort error signal: last payment-targets refresh failure (if any). */
  paymentTargetsLastError?: string | null
  /**
   * Payment targets for filtering the To dropdown (tri-state).
   * Invariant: `undefined` should be used only while routes are loading
   * (`trustlinesLoading=true` OR `paymentTargetsLoading=true`).
   */
  paymentToTargetIds: Set<string> | undefined
  trustlines?: TrustlineInfo[]

  /** Best-effort error signal: last trustlines refresh failure (if any). */
  trustlinesLastError?: string | null

  /** Optional dropdown data (prefer backend-driven list from Interact Actions API). */
  participants?: ParticipantInfo[]

  /** Optional setters (used by dropdown UX). */
  setFromPid?: (pid: string | null) => void
  setToPid?: (pid: string | null) => void

  busy: boolean
  canSendPayment?: boolean

  confirmPayment: (amount: string) => Promise<void> | void
  cancel: () => void

  /**
   * 037 A2 (F-037-1): where the trustlines the numbers come from stand. `answered` = the server answered; anything else
   * means the figures of this panel are the snapshot's, which nobody has confirmed. When the parent does not say, the
   * state is derived from `trustlinesLoading` / `trustlinesLastError`.
   */
  trustlinesState?: 'answered' | 'loading' | 'failed' | 'never-asked'
  /** The committed payment (result screen) or the unknown result (banner with the repeat action). */
  paymentOutcome?: ManualPaymentOutcome | null
  /** Repeat the payment whose result is unknown, under the same key. */
  retryPayment?: () => Promise<void> | void
  /** Leave the result screen for the recipient step. */
  dismissPaymentResult?: () => void
  /** Give up the unresolved payment (explicit, two steps in this panel). */
  discardUnresolvedPayment?: () => void
  /** The server's estimate for the chosen recipient (shortest path in steps, estimated maximum). */
  targetEstimate?: PaymentTargetEstimate | null
}

const props = defineProps<Props>()

const rootStyle = computed(() => {
  // WM owns geometry. Keep ManualPaymentPanel as a simple content block.
  return {
    position: 'static',
    left: 'auto',
    top: 'auto',
    right: 'auto',
    zIndex: 'auto',
  } as const
})

const rootClass = computed(() => {
  return 'ds-ov-panel ds-ov-panel--compact ds-panel ds-panel--elevated'
})

const amount = ref('')

const amountNormalized = computed(() => parseAmountStringOrNull(amount.value))

watch(
  () => props.phase,
  (p) => {
    // UX: entering confirm step should not keep stale input from a previous payment.
    if (p === 'confirm-payment') amount.value = ''
  },
  { immediate: true },
)

/** Exact, no `Number`: `compareMoney` (037 F-037-3). */
const amountPositive = computed(() => amountNormalized.value != null && compareMoney(amountNormalized.value, '0') === 1)

const amountValid = computed(() => amountPositive.value)

const availableNormalized = computed(() => parseAmountStringOrNull(props.availableCapacity))

const exceedsCapacity = computed(() => {
  if (amountNormalized.value == null || availableNormalized.value == null) return false
  return compareMoney(amountNormalized.value, availableNormalized.value) === 1
})

/** Where the figures of this panel come from (037 F-037-1). */
const figuresSource = computed<'server' | 'loading' | 'failed' | 'snapshot'>(() => {
  const given = props.trustlinesState
  if (given === 'answered') return 'server'
  if (given === 'loading') return 'loading'
  if (given === 'failed') return 'failed'
  if (given === 'never-asked') return 'snapshot'
  if (props.trustlinesLoading) return 'loading'
  if (props.trustlinesLastError) return 'failed'
  return 'server'
})

const figuresSourceText = computed(() => {
  switch (figuresSource.value) {
    case 'server':
      return interactText('sourceServer')
    case 'loading':
      return interactText('sourceLoading')
    case 'failed':
      return interactText('sourceFailed')
    default:
      return interactText('sourceSnapshot')
  }
})

/** A number is shown plainly only when the server's answer stands behind it. */
const figuresConfirmed = computed(() => figuresSource.value === 'server')

const estimate = computed(() => props.targetEstimate ?? null)

const estimateMax = computed(() => {
  const e = estimate.value
  return e && e.state === 'received' ? e.maxAvailable : null
})

const exceedsEstimate = computed(() => {
  if (estimateMax.value == null || amountNormalized.value == null) return false
  return compareMoney(amountNormalized.value, estimateMax.value) === 1
})

const estimateText = computed(() => {
  const e = estimate.value
  if (!e) return null
  if (e.state === 'loading') return interactText('estimateLoading')
  if (e.state === 'failed') return interactText('estimateFailed')
  if (e.maxAvailable == null) return interactText('estimateNotEstimated')
  return `${formatMoney(e.maxAvailable, equivalentPrecision(props.unit))} ${props.unit}`
})

const outcome = computed(() => props.paymentOutcome ?? null)
const success = computed(() => (outcome.value && outcome.value.kind === 'success' ? outcome.value : null))

// The result replaces the controls the user was on: move the focus onto it so a keyboard or screen-reader user lands on
// the payment id instead of on nothing (the element that held the focus is gone).
const resultEl = ref<HTMLElement | null>(null)
watch(success, async (now) => {
  if (!now) return
  await nextTick()
  resultEl.value?.focus()
})
const unknownOutcome = computed(() => (outcome.value && outcome.value.kind === 'unknown' ? outcome.value : null))

// A route without a step has nothing to show.
const routes = computed(() => (success.value ? success.value.routes.filter((r) => r.hops.length > 0) : []))

// Giving up an unresolved payment is two deliberate steps; leaving it (Esc, Cancel, a closed panel) never does it.
const confirmingDiscard = ref(false)
watch(unknownOutcome, (now) => {
  if (!now) confirmingDiscard.value = false
})

function moneyText(amount: string, unit: string): string {
  return `${formatMoney(amount, equivalentPrecision(unit))} ${unit}`
}

function routeChain(hops: Array<{ fromName: string; toName: string }>): string {
  if (!hops.length) return ''
  return [hops[0].fromName, ...hops.map((h) => h.toName)].join(' → ')
}

const confirmInlineWarning = computed<string | null>(() => {
  if (props.busy) return null
  // 028 F-028-48: a client hint of the equivalent's step; the server decides (F-028-23).
  const stepHint = amountStepHint(amountNormalized.value, props.unit)
  if (stepHint) return stepHint
  // 037: once the server has estimated the maximum for this recipient, THAT is what the amount is weighed against;
  // the direct line is only one of the ways the payment can go. Non-blocking either way - the server decides.
  if (estimateMax.value != null) {
    return exceedsEstimate.value
      ? interactText('estimateExceeded', { max: formatMoney(estimateMax.value, equivalentPrecision(props.unit)), unit: props.unit })
      : null
  }
  if (!exceedsCapacity.value) return null
  // Non-blocking warning: allow confirm, but set expectations.
  return `Amount may exceed direct trustline capacity (${props.availableCapacity ?? '—'} ${props.unit}). Multi-hop may still succeed; backend will validate.`
})

const confirmDisabledReason = computed<string | null>(() => {
  // Spec: do not show reason while busy.
  if (props.busy) return null

  const rawTrimmed = amount.value.trim()
  if (!rawTrimmed) return 'Enter a positive amount.'

  if (amountNormalized.value == null) return "Invalid amount format. Use digits and '.' for decimals."

  if (!amountPositive.value) return 'Enter a positive amount.'

  // Phase 2.5 multi-hop: exceeding direct capacity must NOT block confirm.
  // It is expressed as a non-blocking warning (see confirmInlineWarning).

  const from = (props.state.fromPid ?? '').trim()
  const to = (props.state.toPid ?? '').trim()
  if (from && to && from === to) {
    return 'You cannot send a payment to yourself.'
  }
  if (from && to && props.canSendPayment === false) {
    return 'Backend reports no payment routes between selected participants.'
  }

  return null
})

const canConfirm = computed(() => {
  if (props.busy) return false
  // An unresolved payment has to be checked or discarded first: no other payment is sent from here.
  if (unknownOutcome.value) return false
  return confirmDisabledReason.value == null
})

const isPickFrom = computed(() => props.phase === 'picking-payment-from')
const isPickTo = computed(() => props.phase === 'picking-payment-to')
const isConfirm = computed(() => props.phase === 'confirm-payment')
const open = computed(() => {
  return isPickFrom.value || isPickTo.value || isConfirm.value
})

// Giving up a payment always starts from the FIRST step: closing the panel (the instance may live on, hidden), opening it again
// or removing it does not carry step two over.
watch(open, () => {
  confirmingDiscard.value = false
})
onBeforeUnmount(() => {
  confirmingDiscard.value = false
})

const routesLoading = computed(() => props.trustlinesLoading || props.paymentTargetsLoading)

const paymentTargetsMaxHopsLabel = computed(() => {
  const n = Number(props.paymentTargetsMaxHops)
  if (!Number.isFinite(n) || !(n > 0)) return '—'
  return String(Math.round(n))
})

/**
 * Dropdown-specific tri-state normalization.
 * - unknown (undefined)  → while routes are loading  OR  payment-targets errored
 * - known-empty (size=0) → backend returned 0 reachable targets, no error
 * - known-nonempty       → backend returned ≥1 reachable targets
 *
 * NOTE (P1-1): on payment-targets error we conservatively return `undefined`
 * (degraded-unknown) rather than a known-empty Set, to prevent incorrectly
 * disabling the To-select.  The toInlineHelpText computed separately surfaces
 * the "Routes update failed; showing fallback data" copy in this case.
 */
const dropdownToTargetIds = computed<Set<string> | undefined>(() => {
  if (routesLoading.value) return undefined

  // Conservative degraded: treat error as unknown so the dropdown stays
  // enabled and falls back to full participant list (see toInlineHelpText).
  if (props.paymentTargetsLastError) return undefined // degraded → unknown

  return props.paymentToTargetIds ?? new Set<string>()
})

async function onConfirm() {
  if (!canConfirm.value) return

  if (amountNormalized.value == null) return

  await props.confirmPayment(amountNormalized.value)
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

// MP-3 (Phase 2): filter From list by availability of outgoing direct-hop payments.
// For payment A -> B, capacity is consumed on TL B -> A, therefore sender candidates are collected
// from `tl.to_pid` where TL is active and has `available > 0`.
const fromParticipants = computed<ParticipantInfo[]>(() => {
  const items = Array.isArray(props.trustlines) ? props.trustlines : []

  // Spec fallback: trustlines are empty/not loaded => no filtering.
  if (items.length === 0) return participantsSorted.value

  const pidsWithOutgoing = new Set<string>()
  for (const tl of items) {
    if (!isActiveStatus(tl.status)) continue

    const available = parseAmountNumber(tl.available)
    if (!Number.isFinite(available)) continue
    if (!(available > 0)) continue

    const pid = (tl.to_pid ?? '').trim()
    if (!pid) continue
    pidsWithOutgoing.add(pid)
  }

  // Spec fallback: no outgoing candidates found => no filtering.
  if (pidsWithOutgoing.size === 0) return participantsSorted.value

  const result = participantsSorted.value.filter((p) => pidsWithOutgoing.has((p?.pid ?? '').trim()))

  // Guarantee that the currently selected fromPid is always present in the list (AC-3).
  // This handles the case where the pre-filled sender has no outgoing TL with available > 0.
  if (props.state.fromPid && !result.find((p) => p.pid === props.state.fromPid)) {
    const current = participantsSorted.value.find((p) => p.pid === props.state.fromPid)
    if (current) result.unshift(current)
  }

  return result
})

// MP-1b: reset recipient if it becomes unavailable in known-state.
const toSelectionInvalidWarning = ref<string | null>(null)

// MP-6: tri-state UX in To label/help.
const toListUpdating = computed(() => {
  if (!props.state.fromPid) return false
  if (props.phase !== 'picking-payment-to' && props.phase !== 'confirm-payment') return false
  return routesLoading.value
})

const toInlineHelpText = computed<string | null>(() => {
  if (props.phase === 'confirm-payment') return null
  if (!props.state.fromPid) return null

  const targets = dropdownToTargetIds.value
  if (targets === undefined) {
    if (routesLoading.value) return 'Routes are updating; the list may include unreachable recipients.'
    if (props.paymentTargetsLastError || props.trustlinesLastError) {
      return 'Routes update failed; showing fallback data. Some recipients may be unreachable.'
    }
    return null
  }
  if (targets.size === 0) {
    return `Backend reports no payment routes from selected sender (max hops: ${paymentTargetsMaxHopsLabel.value}).`
  }

  if (props.trustlinesLastError) {
    return 'Routes update failed; showing fallback data. Some recipients may be missing.'
  }
  return null
})

// UX-10 (Phase 2): disable To-select when known-empty.
const toKnownEmpty = computed(() => {
  const targets = dropdownToTargetIds.value
  return targets !== undefined && targets.size === 0
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
  if (cap == null) return `${participantLabel(p)} — …`
  // 026 `T2602`: a negative `available` is trust excess, not an amount this payment can carry; no amount is shown
  // and the recipient stays selectable (another route may exist).
  if (String(cap).trim().startsWith('-')) return participantLabel(p)
  // 037 F-037-1: a snapshot figure is not offered as a capacity; the Direct capacity row says what it is.
  if (!figuresConfirmed.value) return participantLabel(p)
  return `${participantLabel(p)} — ${cap} ${props.unit}`
}

watch(
  [() => dropdownToTargetIds.value, () => props.state.toPid],
  ([targets, toPid]) => {
    const to = (toPid ?? '').trim()
    if (!to) return

    // Self-payment is always invalid (regardless of routes loading/known state).
    const from = (props.state.fromPid ?? '').trim()
    if (from && to === from) {
      props.setToPid?.(null)
      toSelectionInvalidWarning.value = 'You cannot send a payment to yourself. Please select a different recipient.'
      return
    }

    // Unknown: do NOT reset.
    if (targets === undefined) return

    if (!targets.has(to)) {
      props.setToPid?.(null)
      toSelectionInvalidWarning.value = 'Selected recipient is no longer available. Please re-select.'
    }
  },
)

// UX-14.5: reset inline "To" invalid-selection warning when From changes outside of the dropdown
// (e.g. canvas-driven selection calling setFromPid directly, bypassing onFromChange()).
watch(
  () => props.state.fromPid,
  () => {
    toSelectionInvalidWarning.value = null
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

const fromOptions = computed(() => fromParticipants.value.map((participant) => ({
  value: participant.pid,
  label: participantLabel(participant),
})))

const toOptions = computed(() => toParticipants.value.map((participant) => ({
  value: participant.pid,
  label: toOptionLabel(participant),
})))
</script>

<template>
  <div v-if="open" :class="rootClass" :style="rootStyle" data-testid="manual-payment-panel" aria-label="Manual payment panel">
    <div class="ds-panel__header">
      <div class="ds-h2">
        {{ titleText() }}
        <span class="ds-muted ds-mono"> (ESC to close)</span>
      </div>
    </div>

    <div class="ds-panel__body ds-stack">
      <div v-if="success" ref="resultEl" class="ds-stack" tabindex="-1" role="status" data-testid="mp-result">
        <div class="ds-h2" data-testid="mp-result-title">{{ interactText('resultTitle') }}</div>
        <div class="ds-row ds-row--space">
          <div class="ds-label">{{ interactText('resultPaymentId') }}</div>
          <div class="ds-value ds-mono" data-testid="mp-result-payment-id">{{ success.paymentId }}</div>
        </div>
        <div class="ds-row ds-row--space">
          <div class="ds-label">{{ interactText('resultStatus') }}</div>
          <div class="ds-value ds-mono" data-testid="mp-result-status">{{ success.status }}</div>
        </div>
        <div class="ds-row ds-row--space">
          <div class="ds-label">{{ interactText('resultAmount') }}</div>
          <div class="ds-value ds-mono" data-testid="mp-result-amount">{{ moneyText(success.amount, success.equivalent) }}</div>
        </div>
        <div class="ds-row ds-row--space">
          <div class="ds-label">{{ interactText('resultParties') }}</div>
          <div class="ds-value" data-testid="mp-result-parties">{{ success.fromName }} → {{ success.toName }}</div>
        </div>
        <template v-if="routes.length">
          <div
            v-for="(route, index) in routes"
            :key="index"
            class="ds-stack"
            :data-testid="`mp-result-route-${index + 1}`"
          >
            <div class="ds-label">
              {{ routes.length > 1 ? interactText('resultRoutes', { n: index + 1, total: routes.length }) : interactText('resultRoute') }}:
              <span class="ds-mono" data-testid="mp-result-route-chain">{{ routeChain(route.hops) }}</span>
            </div>
            <div v-for="(hop, hopIndex) in route.hops" :key="hopIndex" class="ds-help ds-mono" data-testid="mp-result-hop">
              {{ interactText('resultRouteStep', { from: hop.fromName, to: hop.toName, amount: formatMoney(hop.amount, equivalentPrecision(success.equivalent)), unit: success.equivalent }) }}
            </div>
          </div>
        </template>
        <div v-else class="ds-help ds-muted" data-testid="mp-result-no-routes">{{ interactText('resultNoRoutes') }}</div>
        <div class="ds-row ds-row--actions">
          <button class="ds-btn ds-btn--primary" type="button" data-testid="mp-result-another" @click="dismissPaymentResult?.()">
            {{ interactText('resultAnother') }}
          </button>
          <button class="ds-btn ds-btn--ghost" type="button" data-testid="mp-result-close" @click="cancel()">
            {{ interactText('resultClose') }}
          </button>
        </div>
      </div>

      <template v-if="!success">
      <template v-if="!unknownOutcome">
      <div v-if="participantsSorted.length" class="ds-controls__row ds-controls__row--compact">
        <label id="mp-from-label" class="ds-label" for="mp-from__trigger">From</label>
        <OverlaySelect
          id="mp-from"
          :model-value="state.fromPid ?? null"
          :options="fromOptions"
          :disabled="busy"
          labelledBy="mp-from-label"
          triggerLabel="From participant"
          @update:model-value="onFromChange($event ?? '')"
        />
      </div>

      <div v-if="participantsSorted.length" class="ds-controls__row ds-controls__row--compact">
        <label id="mp-to-label" class="ds-label" for="mp-to__trigger">
          To
          <span v-if="toListUpdating" class="ds-muted ds-mono"> (updating…)</span>
        </label>
        <OverlaySelect
          id="mp-to"
          :model-value="state.toPid ?? null"
          :options="toOptions"
          :disabled="busy || !state.fromPid || toKnownEmpty"
          labelledBy="mp-to-label"
          triggerLabel="To participant"
          describedBy="mp-to-help"
          surfaceLabel="To participant options"
          @update:model-value="onToChange($event ?? '')"
        />
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
          <div class="ds-label">Direct capacity</div>
          <div class="ds-value ds-mono">
            {{ availableCapacity ?? '—' }} {{ unit }}
            <span class="ds-muted" :data-figures-source="figuresSource" data-testid="mp-figures-source">· {{ figuresSourceText }}</span>
          </div>
        </div>

        <div
          v-if="estimateText"
          class="ds-row ds-row--space"
          data-testid="mp-estimate"
          :title="estimate && estimate.state === 'received' ? interactText('estimateShortestTitle', { n: estimate.hops }) : undefined"
        >
          <div class="ds-label">{{ interactText('estimateTitle') }}</div>
          <div class="ds-value ds-mono">
            <span data-testid="mp-estimate-max">{{ estimateText }}</span>
            <span v-if="estimate && estimate.state === 'received'" class="ds-muted" data-testid="mp-estimate-hops">
              · {{ interactText('estimateShortest', { n: estimate.hops }) }}
            </span>
          </div>
        </div>

        <div class="ds-help ds-muted" data-testid="mp-direct-capacity-help">
          {{ interactText('directCapacityHelp', { hops: paymentTargetsMaxHopsLabel }) }}
        </div>

        <div class="ds-controls__row ds-controls__row--compact">
          <label class="ds-label" for="mp-amount">Amount</label>
          <div class="ds-controls__suffix mp-amount-row">
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

      <!-- Keep stable aria-describedby target (UX-9), but show content only when disabled. -->
      <div v-if="isConfirm" id="mp-amount-help" class="mp-confirm-help">
        <div v-if="confirmDisabledReason" class="ds-help" data-testid="mp-confirm-reason">
          {{ confirmDisabledReason }}
        </div>
        <div v-else-if="confirmInlineWarning" class="ds-help" data-testid="mp-confirm-warning">
          {{ confirmInlineWarning }}
        </div>
      </div>

      </template>

      <div v-if="state.error && !unknownOutcome" class="ds-alert ds-alert--err ds-mono" data-testid="manual-payment-error">{{ state.error }}</div>

      <div v-if="unknownOutcome" class="ds-alert ds-alert--warn ds-stack" data-testid="mp-outcome-unknown">
        <div class="ds-label">{{ interactText('unknownTitle') }}</div>
        <div class="ds-help ds-mono" data-testid="mp-outcome-unknown-intent">
          {{ interactText('unknownFrozen', { amount: unknownOutcome.amount, unit: unknownOutcome.equivalent, from: unknownOutcome.fromName, to: unknownOutcome.toName }) }}
        </div>
        <!-- What the last attempt said (the error text) replaces the generic sentence, so the same thing is not shown twice. -->
        <template v-if="!confirmingDiscard">
          <div class="ds-help" data-testid="mp-outcome-unknown-message">{{ state.error || unknownOutcome.message }}</div>
          <div v-if="unknownOutcome.runMismatch" class="ds-help" data-testid="mp-outcome-other-run">
            {{ interactText('unknownOtherRun') }}
          </div>
          <div v-else class="ds-help ds-muted">{{ interactText('unknownRetryHint') }}</div>
        </template>
        <div v-if="!confirmingDiscard" class="ds-row ds-row--actions">
          <button
            class="ds-btn ds-btn--primary"
            type="button"
            data-testid="mp-retry"
            :disabled="busy || unknownOutcome.runMismatch"
            @click="retryPayment?.()"
          >
            {{ interactText('unknownRetry') }}
          </button>
          <button
            class="ds-btn ds-btn--ghost"
            type="button"
            data-testid="mp-discard"
            :disabled="busy"
            @click="confirmingDiscard = true"
          >
            {{ interactText('unknownDiscard') }}
          </button>
        </div>
        <template v-else>
          <div class="ds-help" data-testid="mp-discard-warning">{{ interactText('unknownDiscardWarning') }}</div>
          <div class="ds-row ds-row--actions">
            <button
              class="ds-btn ds-btn--ghost"
              type="button"
              data-testid="mp-discard-confirm"
              :disabled="busy"
              @click="discardUnresolvedPayment?.()"
            >
              {{ interactText('unknownDiscardConfirm') }}
            </button>
            <button class="ds-btn ds-btn--primary" type="button" data-testid="mp-discard-keep" @click="confirmingDiscard = false">
              {{ interactText('unknownDiscardKeep') }}
            </button>
          </div>
        </template>
      </div>

      <div class="ds-row ds-row--actions mp-actions">
        <button
          v-if="isConfirm && !unknownOutcome"
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
      </template>
    </div>
  </div>
</template>

<style scoped>
/* UX-1: min-height prevents 1-frame layout jump during loading stub → content growth */
.ds-ov-panel {
  min-height: var(--ds-mpp-min-h);
}

.mp-pick-help {
  margin: 6px 0 2px;
}

.mp-to-help {
  margin: 4px 0 0;
}

.mp-actions {
  justify-content: flex-end;
}

.mp-confirm-help {
  margin: 2px 0 0;
}
</style>





