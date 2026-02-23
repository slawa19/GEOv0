<script setup lang="ts">
import { computed } from 'vue'

import type { InteractPhase } from '../composables/useInteractMode'
import { type ActivePanelKey, useActivePanelState } from '../composables/useActivePanelState'

type Props = {
  phase: InteractPhase
  busy: boolean

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

const isIdle = computed(() => String(props.phase ?? '').toLowerCase() === 'idle')

const { activeKey } = useActivePanelState(computed(() => props.phase))

function titleFor(key: ActivePanelKey, idleTitle: string): string {
  if (!isIdle.value && activeKey.value !== key) return 'Cancel current action first'
  return idleTitle
}
</script>

<template>
  <div class="action-bar" aria-label="Interact actions">
    <div class="ds-panel ds-ov-bar">
      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'payment' }"
        type="button"
        :disabled="isDisabled"
        :data-active="activeKey === 'payment' ? '1' : '0'"
        data-testid="actionbar-payment"
        :title="titleFor('payment', 'Send a manual payment')"
        @click="startPaymentFlow"
      >
        Send Payment
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'trustline' }"
        type="button"
        :disabled="isDisabled"
        :data-active="activeKey === 'trustline' ? '1' : '0'"
        data-testid="actionbar-trustline"
        :title="titleFor('trustline', 'Create/update/close a trustline')"
        @click="startTrustlineFlow"
      >
        Manage Trustline
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        :class="{ 'ds-btn--muted': !isIdle && activeKey !== 'clearing' }"
        type="button"
        :disabled="isDisabled"
        :data-active="activeKey === 'clearing' ? '1' : '0'"
        data-testid="actionbar-clearing"
        :title="titleFor('clearing', 'Run clearing')"
        @click="startClearingFlow"
      >
        Run Clearing
      </button>
    </div>
  </div>
</template>

<style scoped>
.ds-btn[data-active='1'] {
  border-color: var(--ds-accent);
  background: var(--ds-accent-surface);
}

.ds-btn--muted {
  opacity: 0.65;
}
</style>

