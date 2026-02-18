<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'

type Props = {
  phase: InteractPhase
  state: InteractState

  /** Screen-space anchor (relative to app root). When provided, popup positions near it. */
  anchor?: { x: number; y: number } | null

  unit: string
  used?: string | number | null
  limit?: string | number | null
  available?: string | number | null
  status?: string | null

  /** Disable action buttons while interact-mode is busy. */
  busy?: boolean

  close: () => void
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'changeLimit'): void
  (e: 'closeLine'): void
}>()

const open = computed(() => props.phase === 'editing-trustline')

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

const popupStyle = computed(() => {
  // Default (legacy) placement.
  if (!props.anchor) return { left: '12px', bottom: '76px' }

  const pad = 10
  // Best-effort clamp to viewport; in real app `.root` occupies the viewport.
  const w = typeof window !== 'undefined' ? window.innerWidth : 10_000
  const h = typeof window !== 'undefined' ? window.innerHeight : 10_000

  const x = clamp(props.anchor.x + 12, pad, w - pad)
  const y = clamp(props.anchor.y + 12, pad, h - pad)
  return { left: `${x}px`, top: `${y}px` }
})

const title = computed(() => {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `${from} → ${to}`
  return props.state.selectedEdgeKey ?? 'Edge'
})

const closeArmed = ref(false)

function disarmClose() {
  closeArmed.value = false
}

function onCloseLine() {
  if (props.busy) return

  // Destructive confirmation: first click arms, second confirms.
  if (!closeArmed.value) {
    closeArmed.value = true
    return
  }

  closeArmed.value = false
  emit('closeLine')
}

function onInteractEsc(ev: Event) {
  if (!closeArmed.value) return
  disarmClose()
  ev.preventDefault()
}

watch(open, (isOpen) => {
  if (!isOpen) disarmClose()
})

watch(
  () => `${String(props.state.fromPid ?? '')}→${String(props.state.toPid ?? '')}`,
  () => disarmClose(),
)

watch(
  () => props.busy,
  (b) => {
    if (b) disarmClose()
  },
)

onMounted(() => {
  if (typeof window === 'undefined') return
  window.addEventListener('geo:interact-esc', onInteractEsc)
})

onUnmounted(() => {
  if (typeof window === 'undefined') return
  window.removeEventListener('geo:interact-esc', onInteractEsc)
})
</script>

<template>
  <div
    v-if="open"
    class="popup ds-ov-item ds-ov-surface ds-ov-edge-detail"
    data-testid="edge-detail-popup"
    aria-label="Edge detail popup"
    :style="popupStyle"
  >
    <div class="popup__title ds-label">Edge</div>
    <div class="popup__subtitle ds-value ds-mono">{{ title }}</div>

    <div class="popup__grid">
      <div class="ds-label">Used</div>
      <div class="ds-value ds-mono">{{ used ?? '—' }} {{ unit }}</div>
      <div class="ds-label">Limit</div>
      <div class="ds-value ds-mono">{{ limit ?? '—' }} {{ unit }}</div>
      <div class="ds-label">Available</div>
      <div class="ds-value ds-mono">{{ available ?? '—' }} {{ unit }}</div>
      <div class="ds-label">Status</div>
      <div class="ds-value ds-mono">{{ status ?? '—' }}</div>
    </div>

    <div class="popup__actions">
      <button class="ds-btn ds-btn--secondary" style="height: 28px; padding: 0 10px" type="button" :disabled="!!busy" @click="emit('changeLimit')">
        Change limit
      </button>
      <button
        class="ds-btn ds-btn--danger"
        style="height: 28px; padding: 0 10px"
        type="button"
        :disabled="!!busy"
        data-testid="edge-close-line-btn"
        @click="onCloseLine"
      >
        {{ closeArmed ? 'Confirm close' : 'Close line' }}
      </button>

      <button
        v-if="closeArmed"
        class="ds-btn ds-btn--ghost"
        style="height: 28px; padding: 0 10px"
        type="button"
        :disabled="!!busy"
        data-testid="edge-close-line-cancel"
        @click="disarmClose"
      >
        Cancel
      </button>
      <button class="ds-btn ds-btn--ghost" style="height: 28px; padding: 0 10px" type="button" @click="close">Close</button>
    </div>
  </div>
</template>

<style scoped>
.popup {
  position: absolute;
  z-index: 42;
  min-width: 260px;
  max-width: min(460px, calc(100vw - 24px));
  pointer-events: auto;
}

.popup__title {
  opacity: 0.9;
}

.popup__subtitle {
  margin-top: 2px;
  margin-bottom: 8px;
  opacity: 0.95;
}

.popup__grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 10px;
}

.popup__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 10px;
}

</style>

