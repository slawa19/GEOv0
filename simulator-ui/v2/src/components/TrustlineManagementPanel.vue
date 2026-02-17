<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'

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
      if (cur != null && String(cur).trim()) newLimit.value = String(cur)
    }
    // Reset create field when entering create phase.
    if (p === 'confirm-trustline-create') {
      limit.value = ''
    }
  },
  { immediate: true },
)

const usedNum = computed(() => {
  const v = Number(props.used ?? 0)
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
const updateValid = computed(() => newLimitNum.value >= usedNum.value)

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

function labelFor(p: ParticipantInfo): string {
  const pid = String(p.pid ?? '').trim()
  const name = String(p.name ?? '').trim()
  if (!pid) return name || '—'
  if (!name || name === pid) return pid
  return `${name} (${pid})`
}

const participantsSorted = computed(() => {
  const items = Array.isArray(props.participants) ? props.participants : []
  return [...items]
    .filter((p) => String(p?.pid ?? '').trim())
    .sort((a, b) => labelFor(a).localeCompare(labelFor(b)))
})

const selectedTl = computed(() => {
  const from = String(props.state.fromPid ?? '').trim()
  const to = String(props.state.toPid ?? '').trim()
  if (!from || !to) return null
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  return items.find((tl) => tl.from_pid === from && tl.to_pid === to) ?? null
})

const effectiveUsed = computed(() => selectedTl.value?.used ?? props.used)
const effectiveLimit = computed(() => selectedTl.value?.limit ?? props.currentLimit)
const effectiveAvailable = computed(() => selectedTl.value?.available ?? props.available)

const trustlinesSorted = computed(() => {
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  return [...items].sort((a, b) => {
    const ka = `${a.from_name || a.from_pid} → ${a.to_name || a.to_pid}`
    const kb = `${b.from_name || b.from_pid} → ${b.to_name || b.to_pid}`
    return ka.localeCompare(kb)
  })
})

function encodeTlKey(tl: TrustlineInfo): string {
  return `${String(tl.from_pid ?? '')}→${String(tl.to_pid ?? '')}`
}

function onTrustlinePick(key: string) {
  const s = String(key ?? '')
  const [from, to] = s.split('→')
  if (!from || !to) return
  props.selectTrustline?.(from, to)
}
</script>

<template>
  <div v-if="open" class="ds-ov-panel ds-panel ds-panel--elevated" data-testid="trustline-panel" aria-label="Trustline management panel">
    <div class="ds-panel__header">
      <div class="ds-h2">{{ title }}</div>
    </div>

    <div class="ds-panel__body ds-stack">
      <div v-if="participantsSorted.length" class="ds-controls__row">
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
          <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
        </select>
      </div>

      <div v-if="participantsSorted.length" class="ds-controls__row">
        <label class="ds-label" for="tl-to">To</label>
        <select
          id="tl-to"
          class="ds-select"
          :value="state.toPid ?? ''"
          :disabled="busy || !state.fromPid"
          aria-label="Trustline to participant"
          @change="props.setToPid?.(($event.target as HTMLSelectElement).value || null)"
        >
          <option value="">—</option>
          <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
        </select>
      </div>

      <div v-if="trustlinesSorted.length" class="ds-controls__row">
        <label class="ds-label" for="tl-pick">Existing</label>
        <select
          id="tl-pick"
          class="ds-select"
          :disabled="busy"
          aria-label="Pick existing trustline"
          @change="onTrustlinePick(($event.target as HTMLSelectElement).value)"
        >
          <option value="">—</option>
          <option v-for="tl in trustlinesSorted" :key="encodeTlKey(tl)" :value="encodeTlKey(tl)">
            {{ (tl.from_name || tl.from_pid) + ' → ' + (tl.to_name || tl.to_pid) }}
          </option>
        </select>
      </div>

      <div v-if="isPickFrom" class="ds-help" style="margin-top: 6px">Pick From node (canvas) or choose from dropdown.</div>
      <div v-if="isPickTo" class="ds-help" style="margin-top: 6px">Pick To node (canvas) or choose from dropdown.</div>

      <div v-if="isEdit" class="ds-controls" style="padding: 0">
        <div class="ds-controls__row">
          <div class="ds-label">Used</div>
          <div class="ds-value ds-mono">{{ effectiveUsed ?? '—' }} {{ unit }}</div>
        </div>
        <div class="ds-controls__row">
          <div class="ds-label">Limit</div>
          <div class="ds-value ds-mono">{{ effectiveLimit ?? '—' }} {{ unit }}</div>
        </div>
        <div class="ds-controls__row">
          <div class="ds-label">Available</div>
          <div class="ds-value ds-mono">{{ effectiveAvailable ?? '—' }} {{ unit }}</div>
        </div>
      </div>

      <div v-if="isCreate" class="ds-controls__row">
        <label class="ds-label" for="tl-limit">Limit</label>
        <div class="ds-row" style="flex-wrap: nowrap">
          <input
            id="tl-limit"
            v-model="limit"
            class="ds-input ds-mono"
            style="flex: 1"
            inputmode="decimal"
            placeholder="0.00"
            :aria-invalid="createValid ? 'false' : 'true'"
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
            v-model="newLimit"
            class="ds-input ds-mono"
            style="flex: 1"
            inputmode="decimal"
            placeholder="0.00"
            :aria-invalid="updateValid ? 'false' : 'true'"
          />
          <span class="ds-label ds-muted">{{ unit }}</span>
        </div>
      </div>

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="trustline-error">{{ state.error }}</div>

      <div class="ds-row" style="justify-content: flex-end">
        <button v-if="isCreate" class="ds-btn ds-btn--primary" type="button" :disabled="busy || !createValid" @click="onCreate">
          Create
        </button>

        <template v-else>
          <button class="ds-btn ds-btn--primary" type="button" :disabled="busy || !updateValid" @click="onUpdate">Update</button>

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
</template>



