<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from 'vue'

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

/**
 * BACKLOG 049-10 (advisor 2026-10-21, owner frames 05/06 of 2026-10-20): the busy and the locked line hung absolutely
 * under the actions, centred - over the graph (on the stand a node of the 100-node scene lay under the words at
 * 1366x768). They move (`Teleport`, no new element) into the FIRST ROW of the top bar - the row whose content edges are
 * the control line - into its right group, before "Подробнее": the band of the row is above the scene, so they lie over
 * no node and add no height to the stack. A row of the stack under it (the place of the error line and the stall plaque,
 * spec 049 decision 2) was measured and rejected: the scene is framed under the first row only, so such a row lay over
 * nodes too (1 at 1366x768; 12-18 with "Подробнее" on, where it falls under the service rows) and pushed the window down.
 * Mounted alone - the unit tests - there is no top bar, and the lines stay in the bar.
 */
const rootEl = ref<HTMLElement | null>(null)
const rowSlotEl = shallowRef<HTMLElement | null>(null)
onMounted(() => {
  const row = rootEl.value?.closest('.tb-main-row')
  rowSlotEl.value = (row?.querySelector(':scope > .hud-bar__right') as HTMLElement | null) ?? null
})

function guardedStart(fn: () => void) {
  // If ActionBar is globally disabled (busy/feature flags/run terminal) or a flow is active,
  // do nothing. Buttons are also disabled in template, but this is a safety guard.
  if (isDisabled.value || isFlowActive.value) return
  fn()
}
</script>

<template>
  <div ref="rootEl" class="action-bar" :aria-label="t('actions.aria')" data-testid="action-bar">
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
    </HudBar>

    <Teleport :to="rowSlotEl" :disabled="!rowSlotEl">
      <span
        v-if="busy"
        class="action-bar__hint"
        data-testid="actionbar-busy-hint"
        :aria-label="t('actions.busyHintAria')"
        :title="cancelling ? t('actions.cancellingHint') : t('card.busyReason')"
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
        :title="t('card.flowOpenReason')"
      >
        {{ t('card.flowOpenReason') }}
      </span>
    </Teleport>
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

/*
  043 S2 (owner's decision on review item B2, 2026-10-17): the busy and locked lines are OUT of the actions' row, so the
  three actions never move when a flow locks them (the line used to join the flex row and push the centred group
  sideways).
  BACKLOG 049-10: in the right group of the first row (above), before "Подробнее" (`order`); it gives way to the button
  inside the group's track (`TopBar.vue`, `.tb-main-row > .hud-bar__right`), so it never reaches the actions. It stays ONE
  line in the room it has, cut by an ellipsis, the whole sentence its `title` (spec 049, decision 9): wrapped, it grew the
  first row at 1024x640 with the CI fonts (run 39553532984: 56 -> 61 px). It takes the pointer now - for that `title`; in
  the row it covers no canvas. The stand holds it (`e2e/qa/edge-line.qa.ts`, 049-10).
*/
.action-bar__hint {
  order: -1;
  flex: 0 1 auto;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: var(--ds-ab-hint-opacity);
  font-size: var(--ds-ab-hint-font-size);
  line-height: var(--ds-ab-hint-line-height);
}

</style>



