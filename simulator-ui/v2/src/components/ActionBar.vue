<script setup lang="ts">
import { computed } from 'vue'

import type { InteractPhase } from '../composables/useInteractMode'
import { type ActivePanelKey, useActivePanelStateShared } from '../composables/useActivePanelState'
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

const isDisabled = computed(() => !!props.busy || !!props.actionsDisabled || !!props.runTerminal)

const isIdle = computed(() => toLower(props.phase) === 'idle')

// Contract: one flow at a time (see Interact Mode user guide).
// When a flow is active (phase != idle), ActionBar must not allow starting any flow.
const isFlowActive = computed(() => !isIdle.value)

const { activeKey } = useActivePanelStateShared(computed(() => props.phase))

function titleFor(_key: ActivePanelKey, idleTitle: string): string {
  // Prioritize in-flight operation hints over the generic "flow active" message.
  if (props.busy && props.cancelling) return 'Cancelling… please wait for the operation to finish.'
  if (props.busy) return 'Operation in progress… please wait.'
  if (isFlowActive.value) return 'Cancel current action first (press ESC).'
  if (props.actionsDisabled) return 'Actions are disabled by backend feature flags.'
  if (props.runTerminal) return 'Run is stopped or in error state. Start a run first.'
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
  <div class="action-bar" aria-label="Interact actions">
    <HudBar variant="ghost" layout="start" fit>
      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'payment' }"
        type="button"
        :disabled="isDisabled || isFlowActive"
        :data-active="activeKey === 'payment' ? '1' : '0'"
        data-testid="actionbar-payment"
        :title="titleFor('payment', 'Send a manual payment')"
        @click="guardedStart(startPaymentFlow)"
      >
        Send Payment
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'trustline' }"
        type="button"
        :disabled="isDisabled || isFlowActive"
        :data-active="activeKey === 'trustline' ? '1' : '0'"
        data-testid="actionbar-trustline"
        :title="titleFor('trustline', 'Create/update/close a trustline')"
        @click="guardedStart(startTrustlineFlow)"
      >
        Manage Trustline
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'clearing' }"
        type="button"
        :disabled="isDisabled || isFlowActive"
        :data-active="activeKey === 'clearing' ? '1' : '0'"
        data-testid="actionbar-clearing"
        :title="titleFor('clearing', 'Run clearing')"
        @click="guardedStart(startClearingFlow)"
      >
        Run Clearing
      </button>

      <span
        v-if="busy"
        class="action-bar__hint"
        data-testid="actionbar-busy-hint"
        aria-label="Action Bar busy hint"
      >
        {{ cancelling ? 'Cancelling… please wait.' : 'Operation in progress… please wait.' }}
      </span>

      <span
        v-else-if="isFlowActive"
        class="action-bar__hint"
        data-testid="actionbar-locked-hint"
        aria-label="Action Bar locked hint"
      >
        Cancel current action first (press ESC).
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

.action-bar__hint {
  opacity: var(--ds-ab-hint-opacity);
  font-size: var(--ds-ab-hint-font-size);
  /*
    Responsive safety:
    - never force a single line (can overflow on <500px)
    - allow wrapping/breaking long chunks
    - keep buttons stable; hint can take remaining space or drop to a new row
  */
  order: 10;
  flex: 1 1 auto;
  min-width: 0;
  white-space: normal;
  overflow-wrap: anywhere;
  line-height: var(--ds-ab-hint-line-height);
}

@media (max-width: 500px) {
  .action-bar__hint {
    flex-basis: 100%;
  }
}
</style>



