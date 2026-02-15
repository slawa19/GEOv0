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
    <div class="action-bar__inner">
      <button
        class="btn btn-xs"
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
        class="btn btn-xs"
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
        class="btn btn-xs"
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
.action-bar__inner {
  display: inline-flex;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 14px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
}

.btn[data-active='1'] {
  border-color: rgba(56, 189, 248, 0.45);
  background: rgba(56, 189, 248, 0.12);
}
</style>

