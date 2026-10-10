<script setup lang="ts">
import { computed } from 'vue'

import type { ParticipantInfo, SimulatorActionClearingCycle } from '../api/simulatorTypes'
import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import { useParticipantDisplayId } from '../composables/useParticipantDisplay'
import { interactText } from '../i18n/interactStrings'

type Props = {
  phase: InteractPhase
  state: InteractState
  busy: boolean

  equivalent: string

  /** For the NAMES in the result (an id is shown only for a participant this list does not know). */
  participants?: ParticipantInfo[]

  confirmClearing: () => Promise<void> | void
  cancel: () => void
}

const props = defineProps<Props>()

const rootStyle = computed(() => {
  // WM owns geometry. Keep ClearingPanel as a simple content block.
  return {
    position: 'static',
    left: 'auto',
    top: 'auto',
    right: 'auto',
    zIndex: 'auto',
  } as const
})

const rootClass = computed(() => {
  return 'ds-ov-panel ds-panel ds-panel--elevated'
})

const last = computed(() => props.state.lastClearing)
const cycles = computed(() => last.value?.cycles ?? [])
const cyclesCount = computed(() => {
  if (typeof last.value?.cleared_cycles === 'number') return last.value.cleared_cycles
  return cycles.value.length
})

async function onConfirm() {
  if (props.busy) return
  await props.confirmClearing()
}

const isRunning = computed(() => props.phase === 'clearing-running')
// 037 C: `clearing-preview` is the phase after the confirm step. Until the answer comes it waits; then it SHOWS THE RESULT until the
// user closes it; an error (a refusal) leaves it open with the message. No timer ends it.
const isPreview = computed(() => props.phase === 'clearing-preview')
const isConfirm = computed(() => props.phase === 'confirm-clearing')
// The refusal is a state of its own (`state.clearingFailure`), not the transient error toast: the application clears `state.error`
// when the toast expires, and the explanation (with its request reference) must outlive that. "Running" is shown only while a
// request is really in flight (`busy`) - never inferred from the absence of an answer.
const errorText = computed(() => (isPreview.value ? (props.state.clearingFailure ?? props.state.error) : props.state.error))
const waiting = computed(() => isRunning.value || (isPreview.value && !last.value && !errorText.value && props.busy))
const nothingCleared = computed(() => !!last.value && cyclesCount.value === 0 && cycles.value.length === 0)

/** The equivalent of the ANSWER: the one the clearing was run for, not the one selected when it came back. */
const resultUnit = computed(() => String(last.value?.equivalent ?? props.equivalent))
/** Before the run: the equivalent that WILL be run. Once there is a result: the equivalent of that result, whatever is selected now. */
const shownEquivalent = computed(() => (isConfirm.value || !last.value ? props.equivalent : resultUnit.value))

// An id is PRINTED when the list has no name for it (or the name is the id), so it goes through the display rule.
const showPid = useParticipantDisplayId()

function nameOf(pid: string): string {
  const found = (props.participants ?? []).find((p) => p.pid === pid)
  return showPid(String(found?.name ?? '').trim() || pid)
}

/**
 * An edge of the answer runs CREDITOR -> DEBTOR ("from" is whom the debt is owed to, "to" is who owes). So {from: Alice, to: Bob}
 * is "Bob's debt to Alice reduced": the wire arrow is NOT a payment from Alice to Bob.
 */
function edgeLine(edge: { from: string; to: string }, cycle: SimulatorActionClearingCycle): string {
  return interactText('clearingEdgeLine', {
    debtor: nameOf(edge.to), creditor: nameOf(edge.from), amount: cycle.cleared_amount, unit: resultUnit.value,
  })
}

const open = computed(() => {
  return isRunning.value || isPreview.value || isConfirm.value
})

const busyUi = computed(() => props.busy || isRunning.value)
// Close waits only while the answer is awaited; a finished result (or a refusal) can always be closed.
const closeDisabled = computed(() => waiting.value)
</script>

<template>
  <div
    v-if="open"
    :class="rootClass"
    :style="rootStyle"
    data-testid="clearing-panel"
    aria-label="Clearing panel"
    :aria-busy="waiting || busyUi ? 'true' : 'false'"
  >
    <div class="ds-panel__header">
      <div class="ds-h2">
        <span v-if="isConfirm">Run clearing</span>
        <span v-else>{{ interactText('clearingResultTitle') }}</span>
        <span class="ds-muted ds-mono"> (ESC to close)</span>
      </div>
    </div>

    <div class="ds-panel__body ds-stack">
      <div class="ds-label cp-equivalent-row">
        <span>Equivalent:</span>
        <span class="ds-mono" data-testid="clearing-equivalent">{{ shownEquivalent }}</span>
      </div>

      <div v-if="errorText" class="ds-alert ds-alert--err ds-mono" data-testid="clearing-error">{{ errorText }}</div>

      <template v-if="isConfirm">
        <div
          class="ds-help"
          data-testid="clearing-confirm-help"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          <template v-if="busyUi">
            Running clearing… <span class="cp-spinner" aria-hidden="true" />
          </template>
          <template v-else>This will run a clearing cycle in backend.</template>
        </div>

        <div class="ds-row ds-row--actions cp-actions">
          <button class="ds-btn ds-btn--primary" type="button" data-testid="clearing-confirm" :disabled="busyUi" @click="onConfirm">
            {{ busyUi ? 'Running…' : 'Confirm' }}
          </button>
          <button class="ds-btn ds-btn--ghost" type="button" data-testid="clearing-cancel" :disabled="busyUi" @click="cancel">Cancel</button>
        </div>
      </template>

      <template v-else>
        <div
          v-if="waiting"
          class="ds-help"
          data-testid="clearing-running"
          role="status"
          aria-live="polite"
          aria-atomic="true"
        >
          {{ interactText('clearingRunning') }} <span class="cp-spinner" aria-hidden="true" />
        </div>

        <div v-else-if="nothingCleared" class="ds-help" data-testid="clearing-nothing" role="status" aria-live="polite" aria-atomic="true">
          {{ interactText('clearingNothing') }}
        </div>

        <div v-else-if="last" class="ds-stack cp-preview-stack" data-testid="clearing-result" role="status" aria-live="polite" aria-atomic="true">
          <div class="ds-label">
            {{ interactText('clearingCycles') }}: <span class="ds-mono" data-testid="clearing-cycles">{{ cyclesCount }}</span>
          </div>
          <div class="ds-label" data-testid="clearing-total">
            {{ interactText('clearingTotal') }}: <span class="ds-mono">{{ last.total_cleared_amount }} {{ resultUnit }}</span>
          </div>
          <div class="ds-help ds-muted" data-testid="clearing-total-note">{{ interactText('clearingTotalNote') }}</div>

          <div v-for="(c, i) in cycles" :key="i" class="ds-stack cp-cycle" data-testid="clearing-cycle">
            <div class="ds-label" data-testid="clearing-cycle-title">
              {{ interactText('clearingCycle', { n: i + 1, total: cycles.length, amount: c.cleared_amount, unit: resultUnit }) }}
            </div>
            <ul class="cp-edges">
              <li v-for="(e, k) in c.edges" :key="k" class="ds-help ds-mono" data-testid="clearing-edge-line">{{ edgeLine(e, c) }}</li>
            </ul>
          </div>
        </div>
      </template>

      <div v-if="!isConfirm" class="ds-row ds-row--actions cp-actions">
        <button class="ds-btn ds-btn--ghost" type="button" data-testid="clearing-close" :disabled="closeDisabled" @click="cancel">Close</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* UX-1: min-height prevents 1-frame layout jump during loading stub → content growth */
.ds-ov-panel {
  min-height: var(--ds-cp-min-h);
}

.cp-equivalent-row {
  margin-bottom: 2px;
}

.cp-actions {
  justify-content: flex-end;
}

.cp-preview-stack {
  gap: 6px;
}

.cp-cycle {
  gap: 2px;
}

.cp-edges {
  margin: 0;
  padding-left: 18px;
}

.cp-spinner {
  display: inline-block;
  width: var(--ds-cp-spinner-size);
  height: var(--ds-cp-spinner-size);
  margin-left: 6px;
  border-radius: 999px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  animation: cp-spin var(--ds-cp-spinner-spin-dur) linear infinite;
  opacity: 0.7;
  vertical-align: -2px;
}

:global([data-motion='reduced']) .cp-spinner {
  animation: none;
}

@media (prefers-reduced-motion: reduce) {
  .cp-spinner {
    animation: none;
  }
}

@keyframes cp-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

</style>

<!--
  037 C: the container of THIS panel on a phone-sized screen, by the same means as the payment panel (ManualPaymentPanel.vue, B1):
  the window shell is owned by the window manager (inline position and width, `contain: layout style`), and its `data-win-type` is
  shared by three panels, so the one handle on THIS shell is its content, through `:has()`. The manager is not edited.
  A long result (many cycles, many edges) must not push Close off the screen: the window is bounded by the shell's own `max-height`,
  the body scrolls INSIDE it, and the Close row stays in view. The whole block is in `@supports selector(:has(*))`: without
  `:has()` none of it applies and the panel keeps the old layout. Narrow OR short screens (a phone held sideways is wider than 520).
-->
<style>
@supports selector(:has(*)) {
@media (max-width: 520px), (max-height: 520px) {
  .ws-shell:has(> .ws-body > [data-testid='clearing-panel']) {
    --cp-sticky-bg: var(--ds-surface-1);
    display: flex;
    flex-direction: column;
  }

  [data-theme='hud'] .ws-shell:has(> .ws-body > [data-testid='clearing-panel']) {
    --cp-sticky-bg: var(--ds-surface-2);
  }

  .ws-shell:has(> .ws-body > [data-testid='clearing-panel']) > .ws-body {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }

  .ws-shell > .ws-body > [data-testid='clearing-panel'] {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-height: 0;
  }

  .ws-shell > .ws-body > [data-testid='clearing-panel'] > .ds-panel__header {
    flex: 0 0 auto;
  }

  .ws-shell > .ws-body > [data-testid='clearing-panel'] > .ds-panel__body {
    flex: 1 1 auto;
    min-height: 0;
    overflow-x: hidden;
    overflow-y: auto;
    scroll-padding-bottom: 56px;
  }

  /* The row with Confirm/Cancel/Close stays in view while a long result scrolls. */
  .ws-shell > .ws-body > [data-testid='clearing-panel'] .cp-actions {
    position: sticky;
    bottom: 0;
    z-index: 1;
    background: var(--cp-sticky-bg, var(--ds-surface-1));
  }
}
}
</style>
