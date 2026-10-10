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
const amountEl = ref<HTMLInputElement | null>(null)

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

const shortestText = computed(() => {
  const e = estimate.value
  if (!e) return null
  if (e.state === 'loading') return interactText('estimateLoading')
  if (e.state === 'failed') return interactText('estimateFailed')
  return e.hops === 1 ? interactText('shortestOneStep') : interactText('shortestSteps', { n: e.hops })
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

// F-037-4: a panel that OPENS already at the confirm step with both parties set was started from a line (the line popup sets the
// debtor as the sender and the creditor as the recipient). Reaching the confirm step by choosing in the panel is not that. Decided
// when the panel opens; a change of either party in the lists ends it (the note is about how the panel was filled, not about the
// fields afterwards).
// The sentence belongs to the PAIR the panel opened with: it is shown while both parties are still that pair, however they were
// changed (a list, or a click on the canvas) - compared with the pair, not tied to the events that change it.
const openedPair = ref<{ from: string; to: string } | null>(null)
watch(
  open,
  (isOpen) => {
    const from = props.state.fromPid
    const to = props.state.toPid
    openedPair.value = isOpen && props.phase === 'confirm-payment' && from && to ? { from, to } : null
    if (openedPair.value) void focusAmountOfLineEntry()
  },
  { immediate: true },
)
// A panel that opens already at the confirm step (from a line) has an EMPTY amount and nothing else to ask first: the focus goes to the
// amount, which also brings it into view on a short screen. Not a send: the amount is empty, `canConfirm` is false, a held key is ignored.
// (Not at the first pass - that moves the focus in `onToSelected` - and never into a field that already holds a sum.)
async function focusAmountOfLineEntry() {
  await nextTick()
  await nextTick() // after the window shell's own first focus
  if (!openedPair.value || amount.value.trim() !== '') return
  const input = amountEl.value
  if (!input) return
  // `preventScroll`: the window may not have been clamped into the screen yet, and a focus that scrolls would scroll the APP ROOT
  // (overflow hidden, taller than a phone screen) and carry the whole window layer off the top. The panel's own body scrolls instead.
  input.focus({ preventScroll: true })
  const body = input.closest('.ds-panel__body') as HTMLElement | null
  if (body) {
    const room = 56 // the sticky row of buttons
    const over = input.getBoundingClientRect().bottom - (body.getBoundingClientRect().bottom - room)
    if (over > 0) body.scrollTop += over
  }
}
const startedFromLine = computed(() => {
  const pair = openedPair.value
  return !!pair && props.state.fromPid === pair.from && props.state.toPid === pair.to
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

// A key held down repeats keydown (`repeat: true`) without a new press: only a FRESH press confirms. Defence in depth for every
// way a held key could reach a send - the amount field (Enter) and the Confirm button (Enter / Space activate a button).
function onAmountEnter(event: KeyboardEvent) {
  if (event.repeat) return
  void onConfirm()
}

function onConfirmKeydown(event: KeyboardEvent) {
  if (event.repeat && (event.key === 'Enter' || event.key === ' ')) event.preventDefault()
}

/** A participant by NAME; the id only when the list has no record of it (never a guess). */
function nameOf(pid: string | null | undefined): string {
  const id = String(pid ?? '').trim()
  if (!id) return ''
  const found = (props.participants ?? []).find((p) => p.pid === id)
  return String(found?.name ?? '').trim() || id
}

function titleText() {
  // A payment of unknown result is FROZEN: the header describes it, not the live fields (an edge or a node card may have set others).
  const frozen = unknownOutcome.value
  if (frozen) return `Manual payment: ${frozen.fromName} → ${frozen.toName}`
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `Manual payment: ${nameOf(from)} → ${nameOf(to)}`
  return 'Manual payment'
}

// Summary of the confirm step: who pays whom, then the sum, from the fields as they are now.
const summaryParties = computed(() => interactText('summaryPays', { from: nameOf(props.state.fromPid), to: nameOf(props.state.toPid) }))
const summaryAmount = computed(() =>
  amountPositive.value && amountNormalized.value != null ? moneyText(amountNormalized.value, props.unit) : interactText('summaryNoAmount'),
)

const { participantsSorted, toParticipants } = useParticipantsList<ParticipantInfo>({
  participants: () => props.participants,
  fromParticipantId: () => props.state.fromPid,
  availableTargetIds: () => dropdownToTargetIds.value,
})

// MP-3 (Phase 2): filter From list by availability of outgoing direct-hop payments.
// For payment A -> B, capacity is consumed on TL B -> A, therefore sender candidates are collected
// from `tl.to_pid` where TL is active and has `available > 0`.
// Capacity of S paying R is limit(R->S) - debt[S->R] + debt[R->S] (docs/ru/02-protocol-spec.md): the creditor S of an active
// line S -> R also pays R by reducing R's debt to S, with no incoming line, so `tl.from_pid` is a candidate where `used > 0`.
const fromParticipants = computed<ParticipantInfo[]>(() => {
  const items = Array.isArray(props.trustlines) ? props.trustlines : []

  // Spec fallback: trustlines are empty/not loaded => no filtering.
  if (items.length === 0) return participantsSorted.value

  const pidsWithOutgoing = new Set<string>()
  for (const tl of items) {
    if (!isActiveStatus(tl.status)) continue

    const available = parseAmountNumber(tl.available)
    if (Number.isFinite(available) && available > 0) {
      const pid = (tl.to_pid ?? '').trim()
      if (pid) pidsWithOutgoing.add(pid)
    }

    const used = parseAmountNumber(tl.used)
    if (Number.isFinite(used) && used > 0) {
      const creditor = (tl.from_pid ?? '').trim()
      if (creditor) pidsWithOutgoing.add(creditor)
    }
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

// 037 B2 - the progression. The recipient list opens by itself ONLY as the continuation of an explicit choice of the sender
// (`selected` of the sender list: the user chose; never a change that came from outside - a refresh, a restored payment, a panel
// started from an edge or a node card). The focus goes INTO that list (its own Escape and arrows work); choosing the recipient
// moves it to the amount and NEVER sends.
//
// Both moves are the FIRST PASS only, and "first pass" is decided by the state BEFORE the user's choice (was there a recipient?),
// captured in `onFromChange` / `onToChange` - never by what the choice left behind: changing the sender to the very recipient
// EMPTIES the recipient, and that is a correction, not a first pass. A correction keeps the focus where the restore put it (on the
// control just used), because at the confirm step the amount already holds a sum and Enter in it confirms.
const toSelect = ref<{ openWithFocus: () => void } | null>(null)
const nextChoice = ref('')
const firstPass = { from: false, to: false }

function onFromSelected(v: string | null) {
  const first = firstPass.from
  firstPass.from = false
  if (!v || !first || unknownOutcome.value || props.busy) return
  nextChoice.value = interactText('nextChoiceRecipient')
  toSelect.value?.openWithFocus()
}

function onToSelected(v: string | null) {
  const first = firstPass.to
  firstPass.to = false
  if (!v || !first || !isConfirm.value) return
  if (amount.value.trim() !== '') return // never take the focus into a field that already holds a sum
  amountEl.value?.focus()
}

function onToOpenChange(isOpen: boolean) {
  if (!isOpen) nextChoice.value = ''
}

function onFromChange(v: string) {
  const pid = v ? v : null
  firstPass.from = pid != null && !props.state.toPid
  props.setFromPid?.(pid)
  // If To is now invalid, clear it.
  if (pid && pid === props.state.toPid) props.setToPid?.(null)
  toSelectionInvalidWarning.value = null
}

function onToChange(v: string) {
  firstPass.to = !!v && !props.state.toPid
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
          @selected="onFromSelected"
        />
      </div>

      <div v-if="participantsSorted.length" class="ds-controls__row ds-controls__row--compact">
        <label id="mp-to-label" class="ds-label" for="mp-to__trigger">
          To
          <span v-if="toListUpdating" class="ds-muted ds-mono"> (updating…)</span>
        </label>
        <OverlaySelect
          id="mp-to"
          ref="toSelect"
          :model-value="state.toPid ?? null"
          :options="toOptions"
          :disabled="busy || !state.fromPid || toKnownEmpty"
          labelledBy="mp-to-label"
          triggerLabel="To participant"
          describedBy="mp-to-help"
          surfaceLabel="To participant options"
          @update:model-value="onToChange($event ?? '')"
          @selected="onToSelected"
          @update:open="onToOpenChange"
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

      <!-- Announces the list the form opened for the user (a screen reader reads a live region, not a focus move alone). -->
      <div class="mp-sr" role="status" aria-live="polite" data-testid="mp-next-choice">{{ nextChoice }}</div>

      <div v-if="isPickFrom" class="ds-help mp-pick-help">
        Pick From node (canvas) or choose from dropdown.
      </div>
      <div v-if="isPickTo" class="ds-help mp-pick-help">Pick To node (canvas) or choose from dropdown.</div>

      <template v-if="isConfirm">
        <!-- Closed by default: the full sentence is long and the confirm step is already the tallest one on a phone. Always in the DOM. -->
        <details v-if="startedFromLine" class="ds-help ds-muted mp-line-direction" data-testid="mp-line-direction">
          <summary>{{ interactText('lineDirectionTitle') }}</summary>
          <div data-testid="mp-line-direction-note">{{ interactText('lineDirectionNote') }}</div>
        </details>

        <div class="ds-row ds-row--space mp-summary" data-testid="mp-summary">
          <div class="ds-label">{{ interactText('summaryTitle') }}</div>
          <div class="ds-value">
            <span data-testid="mp-summary-parties">{{ summaryParties }}</span>
            <span class="ds-muted">·</span>
            <span class="ds-mono" data-testid="mp-summary-amount">{{ summaryAmount }}</span>
          </div>
        </div>

        <div class="ds-row ds-row--space">
          <div class="ds-label">Direct capacity</div>
          <div class="ds-value ds-mono">
            {{ availableCapacity ?? '—' }} {{ unit }}
            <span class="ds-muted" :data-figures-source="figuresSource" data-testid="mp-figures-source">· {{ figuresSourceText }}</span>
          </div>
        </div>

        <!-- The two captions of the chosen recipient, from the server's `payment-targets` answer - NOT from the line figures above. -->
        <template v-if="estimate">
          <div class="ds-row ds-row--space" data-testid="mp-shortest">
            <div class="ds-label">{{ interactText('shortestTitle') }}</div>
            <div class="ds-value ds-mono" data-testid="mp-shortest-value">{{ shortestText }}</div>
          </div>
          <div class="ds-row ds-row--space" data-testid="mp-estimate">
            <div class="ds-label">{{ interactText('estimateTitle') }}</div>
            <div class="ds-value ds-mono">
              <span data-testid="mp-estimate-max">{{ estimateText }}</span>
              <span v-if="estimate.state === 'received' && estimate.maxAvailable != null" class="ds-muted mp-source" data-testid="mp-estimate-source">{{ interactText('estimateSource') }}</span>
            </div>
          </div>
          <div class="ds-help ds-muted" data-testid="mp-route-note">{{ interactText('routeNote') }}</div>
        </template>

        <div class="ds-help ds-muted" data-testid="mp-direct-capacity-help">
          {{ interactText('directCapacityHelp', { hops: paymentTargetsMaxHopsLabel }) }}
        </div>

        <div class="ds-controls__row ds-controls__row--compact">
          <label class="ds-label" for="mp-amount">Amount</label>
          <div class="ds-controls__suffix mp-amount-row">
            <input
              id="mp-amount"
              ref="amountEl"
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
              @keydown.enter.prevent="onAmountEnter"
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
          @keydown="onConfirmKeydown"
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

.mp-line-direction > summary {
  cursor: pointer;
}

.mp-source {
  margin-left: 6px;
}

/* Visually hidden, still read by a screen reader: the live region that announces the list the form opened. */
.mp-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  border: 0;
}

.mp-confirm-help {
  margin: 2px 0 0;
}
</style>






<!--
  037 B1: the container of THIS panel on a phone-sized screen (narrow, or short - a phone held sideways is 390 px high and
  wider than 520, so the width condition alone misses it).

  Unscoped on purpose and aimed at one shell: the window shell (WindowShell.vue, owned by the window manager) positions
  and sizes itself with inline styles and gives a frameless window `contain: layout style`; the panel's own root cannot
  change that from inside (it sets `position: static` itself). Its `data-win-type` is the same ("interact-panel") for the
  payment, trustline and clearing panels, so the only handle on THIS shell is its content: `:has()` on the shell that holds
  this panel. The manager is not edited; it keeps measuring the shell and re-clamping it, now to a height the shell can honour.

  The whole block sits in `@supports selector(:has(*))`: in a browser without `:has()` (before Chrome 105, Safari 15.4,
  Firefox 121) NONE of it applies and the panel keeps the old layout - no half-applied state.

  What it does: bounds the window to the screen (the shell's own `max-height`), lets the body scroll INSIDE the window,
  keeps the Confirm/Cancel row and the result buttons in view while it scrolls, and caps the teleported recipient list
  (it lives inside the shell and inherits these variables) so it cannot grow past the screen.

  Checked by `e2e/p037-b1-panel-container.spec.ts`: that the clearing and trustline windows keep the manager's layout (a
  test, with a positive control). NOT checked by a test: the node card and the edge popup - they are not matched because
  their shells do not contain this panel's `data-testid`; that is by the selector, not measured.
-->
<style>
@supports selector(:has(*)) {
@media (max-width: 520px), (max-height: 520px) {
  .ws-shell:has(> .ws-body > [data-testid='manual-payment-panel']) {
    --mp-sticky-bg: var(--ds-surface-1);
    --ds-ov-dropdown-maxh-vh: 36vh;
    --ds-ov-dropdown-maxh: 240px;
    display: flex;
    flex-direction: column;
  }

  [data-theme='hud'] .ws-shell:has(> .ws-body > [data-testid='manual-payment-panel']) {
    --mp-sticky-bg: var(--ds-surface-2);
  }

  .ws-shell:has(> .ws-body > [data-testid='manual-payment-panel']) > .ws-body {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }

  .ws-shell > .ws-body > [data-testid='manual-payment-panel'] {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }

  .ws-shell > .ws-body > [data-testid='manual-payment-panel'] > .ds-panel__header {
    flex: 0 0 auto;
  }

  .ws-shell > .ws-body > [data-testid='manual-payment-panel'] > .ds-panel__body {
    flex: 1 1 auto;
    min-height: 0;
    overflow-x: hidden;
    overflow-y: auto;
    scroll-padding-bottom: 56px;
  }

  /* The row that sends or closes stays in view while a long step scrolls. */
  .ws-shell > .ws-body > [data-testid='manual-payment-panel'] .mp-actions,
  .ws-shell > .ws-body > [data-testid='manual-payment-panel'] [data-testid='mp-result'] > .ds-row--actions {
    position: sticky;
    bottom: 0;
    z-index: 1;
    background: var(--mp-sticky-bg, var(--ds-surface-1));
  }
}
}
</style>
