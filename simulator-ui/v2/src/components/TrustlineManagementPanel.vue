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
  <div
    v-if="open"
    class="panel"
    data-testid="trustline-panel"
    aria-label="Trustline management panel"
  >
    <div class="panel__title">{{ title }}</div>

    <div v-if="participantsSorted.length" class="panel__row">
      <label class="hud-label" for="tl-from">From</label>
      <select
        id="tl-from"
        class="panel__input"
        :value="state.fromPid ?? ''"
        :disabled="busy"
        aria-label="Trustline from participant"
        @change="props.setFromPid?.(($event.target as HTMLSelectElement).value || null)"
      >
        <option value="">—</option>
        <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
      </select>
    </div>

    <div v-if="participantsSorted.length" class="panel__row">
      <label class="hud-label" for="tl-to">To</label>
      <select
        id="tl-to"
        class="panel__input"
        :value="state.toPid ?? ''"
        :disabled="busy || !state.fromPid"
        aria-label="Trustline to participant"
        @change="props.setToPid?.(($event.target as HTMLSelectElement).value || null)"
      >
        <option value="">—</option>
        <option v-for="p in participantsSorted" :key="p.pid" :value="p.pid">{{ labelFor(p) }}</option>
      </select>
    </div>

    <div v-if="trustlinesSorted.length" class="panel__row">
      <label class="hud-label" for="tl-pick">Existing</label>
      <select
        id="tl-pick"
        class="panel__input"
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

    <div v-if="isPickFrom" class="hud-label" style="margin-top: 6px">Pick From node (canvas) or choose from dropdown.</div>
    <div v-if="isPickTo" class="hud-label" style="margin-top: 6px">Pick To node (canvas) or choose from dropdown.</div>

    <div v-if="isEdit" class="panel__grid">
      <div class="hud-label">Used</div>
      <div class="hud-value mono">{{ effectiveUsed ?? '—' }} {{ unit }}</div>
      <div class="hud-label">Limit</div>
      <div class="hud-value mono">{{ effectiveLimit ?? '—' }} {{ unit }}</div>
      <div class="hud-label">Available</div>
      <div class="hud-value mono">{{ effectiveAvailable ?? '—' }} {{ unit }}</div>
    </div>

    <div v-if="isCreate" class="panel__row">
      <label class="hud-label" for="tl-limit">Limit</label>
      <input
        id="tl-limit"
        v-model="limit"
        class="panel__input mono"
        inputmode="decimal"
        placeholder="0.00"
        :aria-invalid="createValid ? 'false' : 'true'"
      />
      <span class="hud-label">{{ unit }}</span>
    </div>

    <div v-if="isCreate && !createValid && limit.trim()" class="hud-label" style="margin-top: 6px">
      Limit must be greater than 0.
    </div>

    <div v-if="isEdit" class="panel__row">
      <label class="hud-label" for="tl-new-limit">New limit</label>
      <input
        id="tl-new-limit"
        v-model="newLimit"
        class="panel__input mono"
        inputmode="decimal"
        placeholder="0.00"
        :aria-invalid="updateValid ? 'false' : 'true'"
      />
      <span class="hud-label">{{ unit }}</span>
    </div>

    <div v-if="state.error" class="panel__error mono" data-testid="trustline-error">{{ state.error }}</div>

    <div class="panel__actions">
      <button v-if="isCreate" class="btn btn-xs" type="button" :disabled="busy || !createValid" @click="onCreate">Create</button>

      <template v-else>
        <button class="btn btn-xs" type="button" :disabled="busy || !updateValid" @click="onUpdate">Update</button>

        <button
          class="btn btn-xs"
          type="button"
          :disabled="busy"
          data-testid="trustline-close-btn"
          @click="onClose"
        >
          {{ closeArmed ? 'Confirm close' : 'Close TL' }}
        </button>

        <button
          v-if="closeArmed"
          class="btn btn-xs btn-ghost"
          type="button"
          :disabled="busy"
          data-testid="trustline-close-cancel"
          @click="disarmClose"
        >
          Cancel close
        </button>
      </template>

      <button class="btn btn-xs btn-ghost" type="button" :disabled="busy" @click="cancel">Cancel</button>
    </div>
  </div>
</template>

<style scoped>
.panel {
  position: absolute;
  right: 12px;
  top: 110px;
  z-index: 42;
  min-width: 360px;
  max-width: min(560px, calc(100vw - 24px));
  padding: 10px 12px;
  border-radius: 12px;
  background: rgba(15, 23, 42, 0.72);
  border: 1px solid rgba(148, 163, 184, 0.16);
  backdrop-filter: blur(10px);
  pointer-events: auto;
}

.panel__title {
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 8px;
}

.panel__grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 6px 10px;
  margin: 8px 0;
}

.panel__row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 6px 0;
}

.panel__input {
  flex: 1;
  background: rgba(2, 6, 23, 0.15);
  color: rgba(226, 232, 240, 0.92);
  border: 1px solid rgba(148, 163, 184, 0.18);
  border-radius: 10px;
  padding: 7px 10px;
  font-size: 12px;
}

.panel__error {
  margin-top: 8px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(248, 113, 113, 0.35);
  background: rgba(127, 29, 29, 0.14);
  color: rgba(254, 202, 202, 0.9);
}

.panel__actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 10px;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
}
</style>

