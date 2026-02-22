 <script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
import { participantLabel } from '../utils/participants'
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

watch(
  () => props.phase,
  (p) => {
    // Pre-fill edit field when entering edit phase.
    if (p === 'editing-trustline') {
      const cur = props.currentLimit
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
  () => `${String(props.state.fromPid ?? '')}→${String(props.state.toPid ?? '')}`,
  () => {
    if (props.phase !== 'editing-trustline') return
    const cur = props.currentLimit
    newLimit.value = cur != null && String(cur).trim() ? String(cur) : ''
  },
)

const selectedTl = computed(() => {
  const from = String(props.state.fromPid ?? '').trim()
  const to = String(props.state.toPid ?? '').trim()
  if (!from || !to) return null
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  return items.find((tl) => tl.from_pid === from && tl.to_pid === to) ?? null
})

const effectiveData = computed(() => {
  return {
    used: selectedTl.value?.used ?? props.used,
    limit: selectedTl.value?.limit ?? props.currentLimit,
    available: selectedTl.value?.available ?? props.available,
  }
})

const effectiveUsed = computed(() => effectiveData.value.used)
const effectiveLimit = computed(() => effectiveData.value.limit)
const effectiveAvailable = computed(() => effectiveData.value.available)

const usedNum = computed(() => {
  const v = Number(effectiveUsed.value ?? 0)
  return Number.isFinite(v) ? v : 0
})

const limitNum = computed(() => {
  const v = Number(limit.value)
  return Number.isFinite(v) ? v : NaN
})

const newLimitNum = computed(() => {
  const v = Number(newLimit.value)
  return Number.isFinite(v) ? v : NaN
})

// UX: don't allow creating a 0-limit trustline (no practical capacity).
const createValid = computed(() => Number.isFinite(limitNum.value) && limitNum.value > 0)
const updateValid = computed(() =>
  Number.isFinite(newLimitNum.value) &&
  newLimit.value.trim().length > 0 &&
  newLimitNum.value > 0 &&
  newLimitNum.value >= usedNum.value
)

async function onCreate() {
  if (props.busy) return
  if (!createValid.value) return
  await props.confirmTrustlineCreate(limit.value)
}

async function onUpdate() {
  if (props.busy) return
  if (!updateValid.value) return
  await props.confirmTrustlineUpdate(newLimit.value)
}

async function onClose() {
  if (props.busy) return

  // Destructive confirmation: first click arms, second confirms.
  if (!closeArmed.value) {
    closeArmed.value = true
    return
  }

  closeArmed.value = false
  await props.confirmTrustlineClose()
}

const closeArmed = ref(false)

function disarmClose() {
  closeArmed.value = false
}

function onInteractEsc(ev: Event) {
  if (!closeArmed.value) return
  disarmClose()
  // Consume ESC: prevent the global interact cancel (SimulatorAppRoot).
  ev.preventDefault()
}

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

watch(
  () => props.phase,
  () => disarmClose(),
)

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

const participantsSorted = computed(() => {
  const items = Array.isArray(props.participants) ? props.participants : []
  return [...items]
    .filter((p) => String(p?.pid ?? '').trim())
    .sort((a, b) => participantLabel(a).localeCompare(participantLabel(b)))
})

const toParticipants = computed(() => {
  const from = String(props.state.fromPid ?? '').trim()
  if (!from) return participantsSorted.value
  return participantsSorted.value.filter(p => String(p?.pid ?? '').trim() !== from)
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
  const from = String(props.state.fromPid ?? '').trim()
  if (!from) return trustlinesSorted.value
  return trustlinesSorted.value.filter((tl) => String(tl.from_pid ?? '').trim() === from)
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
const anchorPositionStyle = useOverlayPositioning(
  () => props.anchor,
  () => props.hostEl,
  { w: 340, h: 340 },
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
  <Transition name="panel-slide">
  <div v-if="open" class="ds-ov-panel ds-panel ds-panel--elevated" :style="anchorPositionStyle" data-testid="trustline-panel" aria-label="Trustline management panel">
    <div class="ds-panel__header">
      <div class="ds-h2">{{ title }}</div>
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
          <option v-for="p in toParticipants" :key="p.pid" :value="p.pid">{{ participantLabel(p) }}</option>
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

      <div v-if="isPickFrom" class="ds-help" style="margin-top: 6px">Pick From node (canvas) or choose from dropdown.</div>
      <div v-if="isPickTo" class="ds-help" style="margin-top: 6px">Pick To node (canvas) or choose from dropdown.</div>

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
        <div class="ds-row" style="flex-wrap: nowrap">
          <input
            id="tl-limit"
            v-model="limit"
            class="ds-input ds-mono"
            style="width: 125px; flex: none"
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

      <div v-if="isCreate && !createValid && limit.trim()" class="ds-help" style="margin-top: 6px">
        Limit must be greater than 0.
      </div>

      <div v-if="isEdit" class="ds-controls__row">
        <label class="ds-label" for="tl-new-limit">New limit</label>
        <div class="ds-row" style="flex-wrap: nowrap">
          <input
            id="tl-new-limit"
            ref="newLimitInput"
            v-model="newLimit"
            class="ds-input ds-mono"
            style="width: 125px; flex: none"
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

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="trustline-error">{{ state.error }}</div>

      <div class="ds-row" style="justify-content: flex-end">
        <button v-if="isCreate" class="ds-btn ds-btn--primary" type="button" :disabled="busy || !createValid" @click="onCreate">
          {{ busy ? 'Creating…' : 'Create' }}
        </button>

        <template v-else>
          <button class="ds-btn ds-btn--primary" type="button" :disabled="busy || !updateValid" @click="onUpdate">{{ busy ? 'Updating…' : 'Update' }}</button>

          <button
            class="ds-btn"
            type="button"
            :disabled="busy"
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
  </Transition>
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



