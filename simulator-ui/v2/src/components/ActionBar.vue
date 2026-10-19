<script setup lang="ts">
import { computed } from 'vue'

import type { InteractPhase } from '../composables/useInteractMode'
import { type ActivePanelKey, useActivePanelStateShared } from '../composables/useActivePanelState'
import { useUiLocale } from '../composables/useUiLocale'
import { toLower } from '../utils/stringHelpers'
import HudBar from './common/HudBar.vue'

type Props = {
  phase: InteractPhase
  busy: boolean

  /** Optional: shows a more specific hint when cancel was requested while busy is still true. */
  cancelling?: boolean

  /** Set when backend rejects actions due to feature flags (HTTP 403 ACTIONS_DISABLED). */
  actionsDisabled?: boolean

  /** Optional: disable actions when run is terminal (stopped/error). */
  runTerminal?: boolean

  startPaymentFlow: () => void
  startTrustlineFlow: () => void
  startClearingFlow: () => void
}

const props = defineProps<Props>()
const { t } = useUiLocale()

const isDisabled = computed(() => !!props.busy || !!props.actionsDisabled || !!props.runTerminal)

const isIdle = computed(() => toLower(props.phase) === 'idle')

// Contract: one flow at a time (see Interact Mode user guide).
// When a flow is active (phase != idle), ActionBar must not allow starting any flow.
const isFlowActive = computed(() => !isIdle.value)

const { activeKey } = useActivePanelStateShared(computed(() => props.phase))

/**
 * Why a button is disabled, else what its action results in (048 item 13 (a): the hint says the RESULT, not the name
 * again). 046 wave 1: the dictionary's words. No run (048 S2 hand-over P2-2): a visitor has no Start, so the reason
 * names the way back every viewer has - a reload makes the opening's one attempt again.
 */
function titleFor(_key: ActivePanelKey, idleTitle: string): string {
  // Prioritize in-flight operation hints over the generic "flow active" message.
  if (props.busy && props.cancelling) return t('actions.cancellingTitle')
  if (props.busy) return t('card.busyReason')
  if (isFlowActive.value) return t('card.flowOpenReason')
  if (props.actionsDisabled) return t('actions.disabled')
  if (props.runTerminal) return t('actions.noRun')
  return idleTitle
}

function guardedStart(fn: () => void) {
  // If ActionBar is globally disabled (busy/feature flags/run terminal) or a flow is active,
  // do nothing. Buttons are also disabled in template, but this is a safety guard.
  if (isDisabled.value || isFlowActive.value) return
  fn()
}
</script>

<template>
  <div class="action-bar" :aria-label="t('actions.aria')" data-testid="action-bar">
    <HudBar variant="ghost" layout="start" fit>
      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'payment' }"
        type="button"
        :disabled="isDisabled || isFlowActive"
        :data-active="activeKey === 'payment' ? '1' : '0'"
        data-testid="actionbar-payment"
        :title="titleFor('payment', t('actions.paymentHint'))"
        @click="guardedStart(startPaymentFlow)"
      >
        {{ t('actions.payment') }}
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'trustline' }"
        type="button"
        :disabled="isDisabled || isFlowActive"
        :data-active="activeKey === 'trustline' ? '1' : '0'"
        data-testid="actionbar-trustline"
        :title="titleFor('trustline', t('actions.trustlineHint'))"
        @click="guardedStart(startTrustlineFlow)"
      >
        {{ t('actions.trustline') }}
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'clearing' }"
        type="button"
        :disabled="isDisabled || isFlowActive"
        :data-active="activeKey === 'clearing' ? '1' : '0'"
        data-testid="actionbar-clearing"
        :title="titleFor('clearing', t('actions.clearingHint'))"
        @click="guardedStart(startClearingFlow)"
      >
        {{ t('actions.clearing') }}
      </button>

      <span
        v-if="busy"
        class="action-bar__hint"
        data-testid="actionbar-busy-hint"
        :aria-label="t('actions.busyHintAria')"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {{ cancelling ? t('actions.cancellingHint') : t('card.busyReason') }}
      </span>

      <span
        v-else-if="isFlowActive"
        class="action-bar__hint"
        data-testid="actionbar-locked-hint"
        :aria-label="t('actions.lockedHintAria')"
      >
        {{ t('card.flowOpenReason') }}
      </span>
    </HudBar>
  </div>
</template>

<style scoped>
.ds-btn[data-active='1'] {
  border-color: var(--ds-accent);
  background: var(--ds-accent-surface);
}

.ds-btn--muted {
  opacity: var(--ds-ab-muted-opacity);
}

.action-bar {
  position: relative;
}

/*
  043 S2 (owner's decision on review item B2, 2026-10-17): the busy and locked lines are OUT of the row's layout -
  under it, centred - so the three actions never move when a flow locks them (the line used to join the flex row and
  push the centred group sideways). It does not take pointer input: the buttons' own titles carry the same reason.
*/
.action-bar__hint {
  position: absolute;
  top: calc(100% + 4px);
  left: 50%;
  transform: translateX(-50%);
  white-space: nowrap;
  pointer-events: none;
  opacity: var(--ds-ab-hint-opacity);
  font-size: var(--ds-ab-hint-font-size);
  line-height: var(--ds-ab-hint-line-height);
}

</style>



