<script setup lang="ts">
import { computed } from 'vue'

import type { InteractPhase } from '../composables/useInteractMode'

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

const activeKey = computed<'payment' | 'trustline' | 'clearing' | null>(() => {
  const p = String(props.phase ?? '').toLowerCase()
  if (p.includes('payment')) return 'payment'
  if (p.includes('trustline')) return 'trustline'
  if (p.includes('clearing')) return 'clearing'
  return null
})
</script>

<template>
  <div class="action-bar" aria-label="Interact actions">
    <div class="ds-panel ds-ov-bar">
      <button
        class="ds-btn ds-btn--secondary"
        type="button"
        :disabled="isDisabled"
        :data-active="activeKey === 'payment' ? '1' : '0'"
        data-testid="actionbar-payment"
        title="Send a manual payment"
        @click="startPaymentFlow"
      >
        Send Payment
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        type="button"
        :disabled="isDisabled"
        :data-active="activeKey === 'trustline' ? '1' : '0'"
        data-testid="actionbar-trustline"
        title="Create/update/close a trustline"
        @click="startTrustlineFlow"
      >
        Manage Trustline
      </button>

      <button
        class="ds-btn ds-btn--secondary"
        type="button"
        :disabled="isDisabled"
        :data-active="activeKey === 'clearing' ? '1' : '0'"
        data-testid="actionbar-clearing"
        title="Run clearing"
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
</style>

