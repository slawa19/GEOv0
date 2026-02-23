<script setup lang="ts">
import { computed } from 'vue'

import { placeOverlayNearAnchor } from '../utils/overlayPosition'
import { renderOrDash } from '../utils/valueFormat'

import { useDestructiveConfirmation } from '../composables/useDestructiveConfirmation'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'

type Props = {
  phase: InteractPhase
  state: InteractState

  /** Host element used as the overlay viewport (for clamping). */
  hostEl?: HTMLElement | null

  // Anchor is stored in FSM state: `state.edgeAnchor` (host-relative screen coordinates).

  unit: string
  used?: string | number | null
  limit?: string | number | null
  available?: string | number | null
  status?: string | null

  /** Disable action buttons while interact-mode is busy. */
  busy?: boolean

  /** When true, the popup is forced hidden (parent shows TrustlineManagementPanel instead). */
  forceHidden?: boolean

  close: () => void
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'changeLimit'): void
  (e: 'closeLine'): void
}>()

// UX: show the small EDGE popup as a standalone quick-info overlay when
// the user clicks on an edge on the canvas. Requires phase = editing-trustline
// and an edge anchor (set by edge click).
// The popup is hidden when the full TrustlineManagementPanel is shown instead
// (i.e. when the user came from NodeCard ✏️ or ActionBar or clicked "Change Limit").
// This is controlled by the parent via the `forceHidden` prop.
const open = computed(() => {
  if (props.forceHidden) return false
  if (props.phase !== 'editing-trustline') return false
  if (!props.state.edgeAnchor) return false
  return true
})

const popupStyle = computed(() => {
  let anchor = props.state.edgeAnchor
  // Default (legacy) placement.
  if (!anchor) return { left: '12px', bottom: '76px' }

  const MIN_POPUP_W = 260
  const MIN_POPUP_H = 140

  const rect = props.hostEl?.getBoundingClientRect()

  // Safety: tolerate mixed coordinate systems.
  // `edgeAnchor` is expected to be host-relative (clientToScreen), but some callers
  // may still pass viewport-based coords (clientX/clientY). When we have host bounds,
  // detect that case and convert to host-relative.
  if (rect && rect.width > 0 && rect.height > 0) {
    const withinHost = anchor.x >= 0 && anchor.y >= 0 && anchor.x <= rect.width && anchor.y <= rect.height
    const withinViewportRect =
      anchor.x >= rect.left && anchor.y >= rect.top && anchor.x <= rect.right && anchor.y <= rect.bottom
    if (!withinHost && withinViewportRect) {
      anchor = { x: anchor.x - rect.left, y: anchor.y - rect.top }
    }
  }

  return placeOverlayNearAnchor({
    anchor,
    overlaySize: { w: MIN_POPUP_W, h: MIN_POPUP_H },
    viewport: rect ? { w: rect.width, h: rect.height } : undefined,
  })
})

const title = computed(() => {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `${from} → ${to}`
  return props.state.selectedEdgeKey ?? 'Edge'
})

const { armed: closeArmed, disarm: disarmClose, confirmOrArm: confirmCloseOrArm } = useDestructiveConfirmation({
  disarmOn: [
    // When popup closes (including forceHidden), cancel the confirmation state.
    { source: open, when: (isOpen) => !isOpen },
    // When switching the selected trustline, cancel the confirmation state.
    { source: () => `${String(props.state.fromPid ?? '')}→${String(props.state.toPid ?? '')}` },
    // When the UI becomes busy, cancel the confirmation state.
    { source: () => props.busy, when: (b) => !!b },
  ],
})

function onCloseLine() {
  if (props.busy) return
  void confirmCloseOrArm(() => emit('closeLine'))
}
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
      <div class="ds-value ds-mono">{{ renderOrDash(used) }} {{ unit }}</div>
      <div class="ds-label">Limit</div>
      <div class="ds-value ds-mono">{{ renderOrDash(limit) }} {{ unit }}</div>
      <div class="ds-label">Available</div>
      <div class="ds-value ds-mono">{{ renderOrDash(available) }} {{ unit }}</div>
      <div class="ds-label">Status</div>
      <div class="ds-value ds-mono">{{ renderOrDash(status) }}</div>
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
  z-index: var(--ds-z-panel, 42);
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
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
  margin-top: 10px;
}

</style>

