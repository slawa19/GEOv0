<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import { useDestructiveConfirmation } from '../composables/useDestructiveConfirmation'
import { useParticipantsList } from '../composables/useParticipantsList'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
import { parseAmountNumber, parseAmountStringOrNull } from '../utils/numberFormat'
import { participantLabel } from '../utils/participants'
import { isActiveStatus } from '../utils/status'
import { renderOrDash } from '../utils/valueFormat'
import { useOverlayPositioning } from '../utils/overlayPosition'

type Props = {
  phase: InteractPhase
  state: InteractState

  unit: string
  used?: string | number | null
  currentLimit?: string | number | null
  available?: string | number | null

  /** Optional dropdown data (prefer backend-driven list from Interact Actions API). */
  participants?: ParticipantInfo[]
  trustlines?: TrustlineInfo[]

  /** Optional setters (used by dropdown UX). */
  setFromPid?: (pid: string | null) => void
  setToPid?: (pid: string | null) => void
  selectTrustline?: (fromPid: string, toPid: string) => void

  busy: boolean

  confirmTrustlineCreate: (limit: string) => Promise<void> | void
  confirmTrustlineUpdate: (newLimit: string) => Promise<void> | void
  confirmTrustlineClose: () => Promise<void> | void
  cancel: () => void

  /** Optional anchor (host-relative screen coords) для позиционирования панели рядом с
   *  кликнутым ребром или нодой. При null/undefined применяется CSS default (right/top). */
  anchor?: { x: number; y: number } | null
  /** Host element used as overlay viewport for clamping. */
  hostEl?: HTMLElement | null
}

const props = defineProps<Props>()

const limit = ref('')
const newLimit = ref('')

const selectedTl = computed(() => {
  const from = (props.state.fromPid ?? '').trim()
  const to = (props.state.toPid ?? '').trim()
  if (!from || !to) return null
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  return items.find((tl) => tl.from_pid === from && tl.to_pid === to) ?? null
})

const effectiveData = computed(() => {
  return {
    used: selectedTl.value?.used ?? props.used,
    reverseUsed: selectedTl.value?.reverse_used,
    limit: selectedTl.value?.limit ?? props.currentLimit,
    available: selectedTl.value?.available ?? props.available,
  }
})

const effectiveUsed = computed(() => effectiveData.value.used)
const effectiveReverseUsed = computed(() => effectiveData.value.reverseUsed)
const effectiveLimit = computed(() => effectiveData.value.limit)
const effectiveAvailable = computed(() => effectiveData.value.available)

watch(
  () => props.phase,
  (p) => {
    // Pre-fill edit field when entering edit phase.
    if (p === 'editing-trustline') {
      const cur = effectiveLimit.value
      newLimit.value = cur != null && String(cur).trim() ? String(cur) : ''
    }
    // Reset create field when entering create phase.
    if (p === 'confirm-trustline-create') {
      limit.value = ''
    }
  },
  { immediate: true },
)

// BUGFIX: phase watcher above only runs on phase transitions.
// When switching trustlines via dropdown while staying in editing phase,
// keep the edit field in sync with the newly selected trustline.
watch(
  () => `${props.state.fromPid ?? ''}→${props.state.toPid ?? ''}`,
  () => {
    if (props.phase !== 'editing-trustline') return
    const cur = effectiveLimit.value
    newLimit.value = cur != null && String(cur).trim() ? String(cur) : ''
  },
)

const usedNum = computed(() => {
  const v = parseAmountNumber(effectiveUsed.value)
  return Number.isFinite(v) ? v : 0
})

const createLimitNormalized = computed(() => parseAmountStringOrNull(limit.value))
const createLimitNum = computed(() => parseAmountNumber(createLimitNormalized.value))

const updateLimitNormalized = computed(() => parseAmountStringOrNull(newLimit.value))
const newLimitNum = computed(() => parseAmountNumber(updateLimitNormalized.value))

const updateLimitTooLow = computed(() => {
  if (updateLimitNormalized.value == null) return false
  if (!Number.isFinite(newLimitNum.value)) return false
  return newLimitNum.value < usedNum.value
})

const closeBlocked = computed(() => {
  const u = parseAmountNumber(effectiveUsed.value)
  const ru = parseAmountNumber(effectiveReverseUsed.value)
  const usedDebt = Number.isFinite(u) && u > 0
  const reverseDebt = Number.isFinite(ru) && ru > 0
  return usedDebt || reverseDebt
})

const createValid = computed(() => {
  // Require From/To selection in create flow.
  const from = (props.state.fromPid ?? '').trim()
  const to = (props.state.toPid ?? '').trim()
  if (!from || !to) return false

  if (createLimitNormalized.value == null) return false
  if (!Number.isFinite(createLimitNum.value)) return false
  return createLimitNum.value >= 0
})

const updateValid = computed(() => {
  if (updateLimitNormalized.value == null) return false
  if (!Number.isFinite(newLimitNum.value)) return false
  return newLimitNum.value >= 0 && newLimitNum.value >= usedNum.value
})

async function onCreate() {
  if (props.busy) return
  if (!createValid.value) return
  // Submit normalized (backend-compatible) amount string.
  await props.confirmTrustlineCreate(createLimitNormalized.value!)
}

async function onUpdate() {
  if (props.busy) return
  if (!updateValid.value) return
  // Submit normalized (backend-compatible) amount string.
  await props.confirmTrustlineUpdate(updateLimitNormalized.value!)
}

async function onClose() {
  if (props.busy) return
  if (closeBlocked.value) return

  void confirmCloseOrArm(async () => {
    await props.confirmTrustlineClose()
  })
}

const { armed: closeArmed, disarm: disarmClose, confirmOrArm: confirmCloseOrArm } = useDestructiveConfirmation({
  disarmOn: [
    // When changing phase, always cancel the confirmation state.
    { source: () => props.phase },
    // When switching selected trustline, cancel the confirmation state.
    { source: () => `${props.state.fromPid ?? ''}→${props.state.toPid ?? ''}` },
    // If Close becomes blocked (used > 0), cancel the confirmation state.
    { source: () => closeBlocked.value, when: (b) => !!b },
    // When the UI becomes busy, cancel the confirmation state.
    { source: () => props.busy, when: (b) => !!b },
  ],
})

const title = computed(() => {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `Trustline: ${from} → ${to}`
  return 'Trustline'
})

const isPickFrom = computed(() => props.phase === 'picking-trustline-from')
const isPickTo = computed(() => props.phase === 'picking-trustline-to')
const isCreate = computed(() => props.phase === 'confirm-trustline-create')
const isEdit = computed(() => props.phase === 'editing-trustline')
const open = computed(() => isPickFrom.value || isPickTo.value || isCreate.value || isEdit.value)

const { participantsSorted, toParticipants } = useParticipantsList<ParticipantInfo>({
  participants: () => props.participants,
  fromParticipantId: () => props.state.fromPid,
  // Payment-target filtering is not required in trustline management.
  availableTargetIds: () => undefined,
})

const trustlinesSorted = computed(() => {
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  return [...items].sort((a, b) => {
    const ka = `${a.from_name || a.from_pid} → ${a.to_name || a.to_pid}`
    const kb = `${b.from_name || b.from_pid} → ${b.to_name || b.to_pid}`
    return ka.localeCompare(kb)
  })
})

const trustlinesForFrom = computed(() => {
  const from = (props.state.fromPid ?? '').trim()
  if (!from) return trustlinesSorted.value
  return trustlinesSorted.value.filter((tl) => (tl.from_pid ?? '').trim() === from)
})

const existingActiveToPidsForFrom = computed(() => {
  const from = (props.state.fromPid ?? '').trim()
  const out = new Set<string>()
  if (!from) return out

  for (const tl of trustlinesSorted.value) {
    if ((tl.from_pid ?? '').trim() !== from) continue
    if (!isActiveStatus(tl.status)) continue
    const to = (tl.to_pid ?? '').trim()
    if (to) out.add(to)
  }
  return out
})

function encodeTlKey(tl: { from_pid?: string | null; to_pid?: string | null }): string {
  return `${encodeURIComponent(tl.from_pid ?? '')}|${encodeURIComponent(tl.to_pid ?? '')}`
}

function onTrustlinePick(key: string) {
  const idx = key.indexOf('|')
  if (idx < 0) return
  const from = decodeURIComponent(key.slice(0, idx))
  const to = decodeURIComponent(key.slice(idx + 1))
  if (!from || !to) return
  props.selectTrustline?.(from, to)
}

/** Dynamic positioning: when anchor is provided (opened from NodeCard or edge click),
 *  place the panel near the anchor instead of the fixed CSS top-right position.
 *  When no anchor, returns {} to let CSS `.ds-ov-panel` defaults apply. */
// IMPORTANT: panelSize.w must match CSS max-width of .ds-ov-panel (560px).
// Using the smaller min-width (320px) causes clamping to underestimate the right edge
// → panel overflows the screen by up to 240px when rendered at max-width.
const anchorPositionStyle = useOverlayPositioning(
  () => props.anchor,
  () => props.hostEl,
  { w: 560, h: 340 },
)

const newLimitInput = ref<HTMLInputElement | null>(null)

defineExpose({
  focusNewLimit: () => {
    newLimitInput.value?.focus()
    newLimitInput.value?.select()
  },
})
</script>

<template>
  <div v-if="open" class="ds-ov-panel ds-panel ds-panel--elevated" :style="anchorPositionStyle" data-testid="trustline-panel" aria-label="Trustline management panel">
    <div class="ds-panel__header">
      <div class="ds-h2">
        {{ title }}
        <span class="ds-muted ds-mono"> (ESC to close)</span>
      </div>
    </div>

    <div class="ds-panel__body ds-stack">
      <div v-if="participantsSorted.length && isPickFrom" class="ds-controls__row">
        <label class="ds-label" for="tl-from">From</label>
        <select
          id="tl-from"
          class="ds-select"
          :value="state.fromPid ?? ''"
          :disabled="busy"
          aria-label="Trustline from participant"
          @change="props.setFromPid?.(($event.target as HTMLSelectElement).value || null)"
        >
          <option value="">—</option>
          <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ participantLabel(p) }}</option>
        </select>
      </div>

      <div v-if="participantsSorted.length && (isPickTo || isCreate)" class="ds-controls__row">
        <label class="ds-label" for="tl-to">To</label>
        <select
          id="tl-to"
          class="ds-select"
          :value="state.toPid ?? ''"
          :disabled="busy || !state.fromPid"
          :title="!state.fromPid && !busy ? 'Select \'From\' participant first' : undefined"
          aria-label="Trustline to participant"
          @change="props.setToPid?.(($event.target as HTMLSelectElement).value || null)"
        >
          <option value="">—</option>
          <option v-for="p in toParticipants" :key="p.pid" :value="p.pid">
            {{ participantLabel(p) + (isCreate && existingActiveToPidsForFrom.has((p.pid ?? '').trim()) ? ' (exists)' : '') }}
          </option>
        </select>
      </div>

      <div v-if="(isCreate || isEdit) && trustlinesSorted.length" class="ds-controls__row">
        <label class="ds-label" for="tl-pick">Existing</label>
        <select
          id="tl-pick"
          class="ds-select"
          :value="state.fromPid && state.toPid
            ? encodeTlKey({ from_pid: state.fromPid, to_pid: state.toPid })
            : ''"
          :disabled="busy"
          title="Available only in edit/create mode"
          aria-label="Pick existing trustline"
          @change="onTrustlinePick(($event.target as HTMLSelectElement).value)"
        >
          <option value="">—</option>
          <option v-for="tl in trustlinesForFrom" :key="encodeTlKey(tl)" :value="encodeTlKey(tl)">
            {{ (tl.from_name || tl.from_pid) + ' → ' + (tl.to_name || tl.to_pid) }}
          </option>
        </select>
      </div>

      <div v-if="isPickFrom" class="ds-help tl-pick-help">Pick From node (canvas) or choose from dropdown.</div>
      <div v-if="isPickTo" class="ds-help tl-pick-help">Pick To node (canvas) or choose from dropdown.</div>

      <div v-if="isEdit" class="tl-stats" aria-label="Trustline stats">
        <div class="tl-stats__item">
          <div class="ds-label">Used</div>
          <div class="ds-value ds-mono">{{ renderOrDash(effectiveUsed) }} {{ unit }}</div>
        </div>
        <div class="tl-stats__item">
          <div class="ds-label">Limit</div>
          <div class="ds-value ds-mono">{{ renderOrDash(effectiveLimit) }} {{ unit }}</div>
        </div>
        <div class="tl-stats__item">
          <div class="ds-label">Available</div>
          <div class="ds-value ds-mono">{{ renderOrDash(effectiveAvailable) }} {{ unit }}</div>
        </div>
      </div>

      <div v-if="isCreate" class="ds-controls__row">
        <label class="ds-label" for="tl-limit">Limit</label>
        <div class="ds-row tl-input-row">
          <input
            id="tl-limit"
            v-model="limit"
            class="ds-input ds-mono tl-limit-input"
            type="text"
            inputmode="decimal"
            autocomplete="off"
            autocapitalize="off"
            autocorrect="off"
            spellcheck="false"
            placeholder="0.00"
            :aria-invalid="limit.trim() && !createValid ? 'true' : 'false'"
          />
          <span class="ds-label ds-muted">{{ unit }}</span>
        </div>
      </div>

      <div v-if="isCreate && limit.trim() && createLimitNormalized === null" class="ds-help tl-pick-help">
        Invalid amount format. Use digits and '.' for decimals.
      </div>

      <div v-if="isEdit" class="ds-controls__row">
        <label class="ds-label" for="tl-new-limit">New limit</label>
        <div class="ds-row tl-input-row">
          <input
            id="tl-new-limit"
            ref="newLimitInput"
            v-model="newLimit"
            class="ds-input ds-mono tl-limit-input"
            type="text"
            inputmode="decimal"
            autocomplete="off"
            autocapitalize="off"
            autocorrect="off"
            spellcheck="false"
            placeholder="0.00"
            :aria-invalid="newLimit.trim() && !updateValid ? 'true' : 'false'"
          />
          <span class="ds-label ds-muted">{{ unit }}</span>
        </div>
      </div>

      <div v-if="isEdit && newLimit.trim() && updateLimitNormalized === null" class="ds-help tl-pick-help">
        Invalid amount format. Use digits and '.' for decimals.
      </div>

      <div v-if="isEdit && updateLimitTooLow" class="ds-alert ds-alert--warn ds-mono" data-testid="tl-limit-too-low">
        New limit must be ≥ used ({{ renderOrDash(effectiveUsed) }} {{ unit }}).
      </div>

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="trustline-error">{{ state.error }}</div>

      <div v-if="isEdit && closeBlocked" class="ds-alert ds-alert--warn ds-mono" data-testid="tl-close-blocked">
        Cannot close: trustline has outstanding debt ({{ renderOrDash(effectiveUsed) }} {{ unit }}). Reduce used to 0 first.
      </div>

      <div class="ds-row tl-actions">
        <button v-if="isCreate" class="ds-btn ds-btn--primary" type="button" :disabled="busy || !createValid" @click="onCreate">
          {{ busy ? 'Creating…' : 'Create' }}
        </button>

        <template v-else>
          <button class="ds-btn ds-btn--primary" type="button" :disabled="busy || !updateValid" @click="onUpdate">{{ busy ? 'Updating…' : 'Update' }}</button>

          <button
            class="ds-btn"
            type="button"
            :disabled="busy || closeBlocked"
            data-testid="trustline-close-btn"
            @click="onClose"
          >
            {{ closeArmed ? 'Confirm close' : 'Close TL' }}
          </button>

          <button
            v-if="closeArmed"
            class="ds-btn ds-btn--ghost"
            type="button"
            :disabled="busy"
            data-testid="trustline-close-cancel"
            @click="disarmClose"
          >
            Cancel close
          </button>
        </template>

        <button class="ds-btn ds-btn--ghost" type="button" :disabled="busy" @click="cancel">Cancel</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Compact: shrink panel to content width, cap at viewport */
.ds-ov-panel {
  width: fit-content;
  min-width: 0;
  max-width: min(380px, calc(100vw - 24px));
}

/* Compact panel padding override (global --ds-space-4 ≥ 18px is too wide here) */
.ds-ov-panel :deep(.ds-panel__header),
.ds-ov-panel :deep(.ds-panel__body) {
  padding: 12px;
}

/* Select fields: don't stretch to full 1fr column width */
.ds-controls__row .ds-select {
  max-width: 180px;
  min-width: 120px;
}

/* Fix: input+suffix (.ds-row) must not overflow the 1fr grid column.
   Without min-width:0, the flex/grid item keeps its intrinsic size
   and the row becomes wider than the select rows above it. */
.ds-controls__row .ds-row {
  min-width: 0;
}
.ds-controls__row .ds-row .ds-input {
  min-width: 0;
}

.tl-pick-help {
  margin-top: 6px;
}

.tl-input-row {
  flex-wrap: nowrap;
}

.tl-limit-input {
  width: 125px;
  flex: none;
}

.tl-actions {
  justify-content: flex-end;
}

.tl-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--ds-space-2);
  margin-top: 2px;
}

.tl-stats__item {
  min-width: 0;
}

@media (max-width: 520px) {
  .tl-stats {
    grid-template-columns: 1fr;
  }
}
</style>



