<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'
import { useDestructiveConfirmation } from '../composables/useDestructiveConfirmation'
import { useParticipantDisplayId } from '../composables/useParticipantDisplay'
import { useParticipantsList } from '../composables/useParticipantsList'
import type { ParticipantInfo, TrustlineInfo } from '../api/simulatorTypes'
import { amountStepHint } from '../config/equivalentPrecision'
import { parseAmountNumber } from '../utils/numberFormat'
import { participantLabel, participantLabelsOf } from '../utils/participants'
import { isActiveStatus } from '../utils/status'
import { uiLocaleRef } from '../i18n/locale'
import { t } from '../i18n/t'
import { templateParts } from '../i18n/t'
import { amountInputHint, isOverLimit, parseTypedAmount, renderOrDash } from '../utils/valueFormat'
import {
  canActOnTrustlineFigures,
  trustlineFiguresNotice,
  trustlineNoRowNotice,
  type TrustlineFiguresSource,
} from '../composables/interact/trustlinesSourceState'
import OverlaySelect from './common/OverlaySelect.vue'

type Props = {
  phase: InteractPhase
  state: InteractState

  unit: string
  used?: string | number | null
  currentLimit?: string | number | null
  available?: string | number | null

  /**
   * `F-013-7`: чем обоснованы `used`/`currentLimit`/`available`. Родитель берёт их из
   * `interactSelectedLink`, а тот при пустом ответе молча берёт числа из SSE-снапшота.
   * Читать устаревшее число терпимо; ПРАВИТЬ или ЗАКРЫВАТЬ линию по нему — нет.
   *
   * ОБЯЗАТЕЛЬНЫЙ ПРОП, и это главное в нём. Раньше здесь были два НЕОБЯЗАТЕЛЬНЫХ
   * флага со значением по умолчанию «всё хорошо»: fail-open умолчание на fail-closed гарде —
   * вызывающий, забывший их передать, молча получал разрешённую мутацию. Отсутствие
   * основания теперь читается как «оснований нет» и в типах, и в рантайме.
   *
   * Считает его родитель: только там виден НЕСЛИТЫЙ ответ источника. В пропе `trustlines`
   * ниже строка из снапшота неотличима от строки из ответа бэкенда, и считать по ней
   * основание значило бы воспроизвести ту же находку этажом ниже.
   */
  figuresSource: TrustlineFiguresSource

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

const selectedTl = computed(() => {
  const from = (props.state.fromPid ?? '').trim()
  const to = (props.state.toPid ?? '').trim()
  if (!from || !to) return null
  const items = Array.isArray(props.trustlines) ? props.trustlines : []
  return items.find((tl) => tl.from_pid === from && tl.to_pid === to) ?? null
})

/**
 * `F-013-7`: есть ли СУЩЕСТВУЮЩАЯ ЛИНИЯ, по числам которой позволено действовать.
 *
 * Что это СПЕЦИАЛЬНО НЕ блокирует, и случай судится тестом: обновление в полёте, пока прежний
 * ответ ещё на руках, — это устаревание, и гасить панель на каждый опрос нельзя.
 *
 * ЧТО ИЗМЕНИЛОСЬ 2026-09-10 (внешнее ревью 013, находка P2). Раньше сюда входил и `no-row`, то
 * есть ответ «линии у этой пары нет» разрешал Update и Close — над снапшотными числами, которые
 * этот же ответ опроверг. Теперь `no-row` сюда не входит: обновлять и закрывать нечего. СОЗДАНИЕ
 * при этом не затронуто — `createValid` ниже этой величины не читает, и это судит корневой тест
 * RT-013-7d2.
 */
const noExistingLineFigures = computed(() => !canActOnTrustlineFigures(props.figuresSource))

/** Источник не ответил вовсе — про это своё сообщение (`no-row` сюда НЕ попадает). */
const sourceUnavailableText = computed<string | null>(() => trustlineFiguresNotice(props.figuresSource, uiLocaleRef.value))

/**
 * Источник ответил, и линии у пары нет — про это ОТДЕЛЬНОЕ сообщение (`tl-no-trustline` в шаблоне),
 * а не ветка предыдущего: «бэкенд не ответил» и «бэкенд ответил, что линии нет» — разные факты с
 * разными последствиями, и одно сообщение на оба вернуло бы находку `F-013-7` в текст после того,
 * как её убрали из кнопок.
 */
const noTrustlineText = computed<string | null>(() => trustlineNoRowNotice(props.figuresSource, uiLocaleRef.value))

const effectiveData = computed(() => {
  // Fail closed: show nothing rather than presenting the snapshot's numbers as this trustline's.
  if (noExistingLineFigures.value) {
    return { used: null, limit: null, available: null }
  }
  return {
    used: selectedTl.value?.used ?? props.used,
    limit: selectedTl.value?.limit ?? props.currentLimit,
    available: selectedTl.value?.available ?? props.available,
  }
})

const effectiveUsed = computed(() => effectiveData.value.used)
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

const createLimitNormalized = computed(() => parseTypedAmount(limit.value))
const createLimitNum = computed(() => parseAmountNumber(createLimitNormalized.value))

const updateLimitNormalized = computed(() => parseTypedAmount(newLimit.value))
const newLimitNum = computed(() => parseAmountNumber(updateLimitNormalized.value))
// 028 F-028-48: a client hint of the equivalent's step; the server decides (F-028-23).
const limitStepHint = computed(() =>
  amountStepHint(isEdit.value ? updateLimitNormalized.value : createLimitNormalized.value, props.unit))

// 026 (owner В1): a close with the debt this line supports (`used`) is a REQUEST - limit 0, the line closes when
// that debt is repaid. The reverse debt belongs to the other line and does not hold the close.
const closeIsRequest = computed(() => {
  const u = parseAmountNumber(effectiveUsed.value)
  return Number.isFinite(u) && u > 0
})
const closeRequested = computed(() => !noExistingLineFigures.value && !!selectedTl.value?.close_requested_at)

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
  // `F-013-7`: no update of a line whose figures the source did not give. 026 `T2602`: a limit below `used`
  // is accepted (a trust change), so there is no "new limit >= used" floor any more.
  if (noExistingLineFigures.value) return false
  if (updateLimitNormalized.value == null) return false
  if (!Number.isFinite(newLimitNum.value)) return false
  return newLimitNum.value >= 0
})

async function onCreate() {
  if (props.busy) return
  // 045 S2: Create is on screen from the first step; it creates only on the create step.
  if (!isCreate.value || !createValid.value) return
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
  // `F-013-7`: `closeBlocked` is computed from `effectiveUsed`, which is null while the source is
  // unavailable -- i.e. it reads as "no outstanding debt" precisely when we do not know.
  if (noExistingLineFigures.value) return
  if (closeRequested.value) return

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
    // `F-013-7`: if the ground for the mutation goes away, an armed close must not survive it.
    { source: () => noExistingLineFigures.value, when: (b) => !!b },
    // When the UI becomes busy, cancel the confirmation state.
    { source: () => props.busy, when: (b) => !!b },
  ],
})

// Ids are PRINTED in this panel, so they go through the display rule; values, keys and state keep the full id.
const showPid = useParticipantDisplayId()

const title = computed(() => {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return t('trustline.titlePair', { from: showPid(from), to: showPid(to) })
  return t('trustline.title')
})

const isPickFrom = computed(() => props.phase === 'picking-trustline-from')
const isPickTo = computed(() => props.phase === 'picking-trustline-to')
const isCreate = computed(() => props.phase === 'confirm-trustline-create')
const isEdit = computed(() => props.phase === 'editing-trustline')
const open = computed(() => {
  return isPickFrom.value || isPickTo.value || isCreate.value || isEdit.value
})

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

// 044 `T4405`: names, told apart where two read the same (`participantLabelsOf`).
const labelByPid = computed(() => participantLabelsOf(props.participants ?? [], showPid))
const labelOf = (participant: { pid?: string | null; name?: string | null }) =>
  labelByPid.value.get((participant.pid ?? '').trim()) ?? participantLabel(participant, showPid)

const fromOptions = computed(() => participantsSorted.value.map((participant) => ({
  value: participant.pid,
  label: labelOf(participant),
})))

const toOptions = computed(() => toParticipants.value.map((participant) => ({
  value: participant.pid,
  label: isCreate.value && existingActiveToPidsForFrom.value.has((participant.pid ?? '').trim())
    ? t('trustline.exists', { label: labelOf(participant) })
    : labelOf(participant),
})))

const trustlineOptions = computed(() => trustlinesForFrom.value.map((trustline) => ({
  value: encodeTlKey(trustline),
  label: `${trustline.from_name || showPid(trustline.from_pid)} → ${trustline.to_name || showPid(trustline.to_pid)}`,
})))

function onFromSelect(value: string | null) {
  props.setFromPid?.(value)
}

function onToSelect(value: string | null) {
  props.setToPid?.(value)
}

function onTrustlineSelect(value: string | null) {
  if (!value) return
  onTrustlinePick(value)
}

const rootStyle = computed(() => {
  // WM owns geometry. Keep TrustlineManagementPanel as a simple content block.
  return {
    position: 'static',
    left: 'auto',
    top: 'auto',
    right: 'auto',
    zIndex: 'auto',
  } as const
})

const rootClass = computed(() => {
  return 'ds-ov-panel ds-ov-panel--compact ds-panel ds-panel--elevated'
})

const newLimitInput = ref<HTMLInputElement | null>(null)

/**
 * 045 S2: the one line of the edit step. 046 (advisor, round 1): one sentence with the unit after each figure -
 * "Used 12.00 UAH · Limit 100.00 UAH · Available 88.00 UAH" (an excess over the limit reads as such). The template is
 * rendered by its parts so that each figure keeps its own element (`tl-stats-used`, `-limit`, `-available`).
 */
const statsParts = computed(() => {
  const available = renderOrDash(effectiveAvailable.value)
  const over = isOverLimit(available)
  const values: Record<string, string> = {
    used: renderOrDash(effectiveUsed.value),
    limit: renderOrDash(effectiveLimit.value),
    available,
    over: over ? available.slice(1) : '',
    unit: props.unit,
  }
  return templateParts(over ? 'trustline.statsOverLimit' : 'trustline.stats').map((part) =>
    'text' in part ? { text: part.text } : { name: part.name, value: values[part.name] ?? '' })
})

/** The hint under a limit the form cannot read: a comma is refused with the limit as it should be typed. */
const createInputHint = computed(() => amountInputHint(limit.value))
const updateInputHint = computed(() => amountInputHint(newLimit.value))

// 045 S2: a disabled primary button says why in its `title` (no dead button). Nothing while busy: the label says it.
const createTitle = computed<string | undefined>(() => {
  if (props.busy || (isCreate.value && createValid.value)) return undefined
  if (!(props.state.fromPid ?? '').trim() || !(props.state.toPid ?? '').trim()) return t('trustline.needsParties')
  if (!limit.value.trim()) return t('trustline.needsLimit')
  return createInputHint.value
})
const lineGroundText = computed(() => sourceUnavailableText.value ?? noTrustlineText.value ?? undefined)
const updateTitle = computed<string | undefined>(() => {
  if (props.busy || updateValid.value) return undefined
  if (noExistingLineFigures.value) return lineGroundText.value
  if (!newLimit.value.trim()) return t('trustline.needsNewLimit')
  return updateInputHint.value
})
const closeTitle = computed<string | undefined>(() => {
  if (props.busy) return undefined
  if (closeRequested.value) return t('trustline.closeAlreadyRequested')
  if (noExistingLineFigures.value) return lineGroundText.value
  return undefined
})

defineExpose({
  focusNewLimit: () => {
    newLimitInput.value?.focus()
    newLimitInput.value?.select()
  },
})
</script>

<template>
  <div
    v-if="open"
    :class="rootClass"
    :style="rootStyle"
    data-testid="trustline-panel"
    :aria-label="t('trustline.panelAria')"
    aria-keyshortcuts="Escape"
  >
    <div class="ds-panel__header">
      <div class="ds-h2" :title="t('common.escHint')">{{ title }}</div>
    </div>

    <div class="ds-panel__body ds-stack">
      <!-- 045 S3 (BACKLOG 045-1): From stays on the create step (decision 3: From -> To -> Limit); a change goes through
           `setTrustlineFromPid` of the interact state machine. -->
      <div v-if="participantsSorted.length && (isPickFrom || isCreate)" class="ds-controls__row ds-controls__row--compact">
        <label id="tl-from-label" class="ds-label" for="tl-from__trigger">{{ t('trustline.fromLabel') }}</label>
          <OverlaySelect
          id="tl-from"
            :model-value="state.fromPid ?? null"
            :options="fromOptions"
          :disabled="busy"
              labelledBy="tl-from-label"
              :triggerLabel="t('trustline.fromTrigger')"
              :placeholder="t('common.pickPlaceholder')"
            @update:model-value="onFromSelect"
          />
      </div>

      <div v-if="participantsSorted.length && (isPickTo || isCreate)" class="ds-controls__row ds-controls__row--compact">
        <label id="tl-to-label" class="ds-label" for="tl-to__trigger">{{ t('trustline.toLabel') }}</label>
          <OverlaySelect
          id="tl-to"
            :model-value="state.toPid ?? null"
            :options="toOptions"
          :disabled="busy || !state.fromPid"
              labelledBy="tl-to-label"
              :title="!state.fromPid && !busy ? t('trustline.toNeedsFrom') : undefined"
              :triggerLabel="t('trustline.toTrigger')"
              :surfaceLabel="t('trustline.toSurface')"
              :placeholder="t('common.pickPlaceholder')"
            @update:model-value="onToSelect"
          />
      </div>

      <!-- 046 round 1 (advisor C): choosing an existing line belongs to EDITING one; on the create step this row was an
           empty "Existing —" that explained nothing. -->
      <div v-if="isEdit && trustlinesSorted.length" class="ds-controls__row ds-controls__row--compact">
        <label id="tl-pick-label" class="ds-label" for="tl-pick__trigger">{{ t('trustline.existingLabel') }}</label>
          <OverlaySelect
          id="tl-pick"
            :model-value="state.fromPid && state.toPid
              ? encodeTlKey({ from_pid: state.fromPid, to_pid: state.toPid })
              : null"
            :options="trustlineOptions"
          :disabled="busy"
              labelledBy="tl-pick-label"
              :title="t('trustline.existingTitle')"
              :triggerLabel="t('trustline.existingTrigger')"
            @update:model-value="onTrustlineSelect"
          />
      </div>

      <!-- 045 S2: the figures of the line as one line; values through " · ". -->
      <div v-if="isEdit" class="ds-help tl-stats" :aria-label="t('trustline.statsAria')" data-testid="tl-stats">
        <template v-for="(part, i) in statsParts" :key="i"><template v-if="'text' in part">{{ part.text }}</template><template v-else-if="part.name === 'unit'">{{ part.value }}</template><span
          v-else
          class="ds-mono"
          :data-testid="`tl-stats-${part.name === 'over' ? 'available' : part.name}`"
        >{{ part.value }}</span></template>
      </div>

      <div v-if="isCreate" class="ds-controls__row ds-controls__row--compact">
        <label class="ds-label" for="tl-limit">{{ t('trustline.limitLabel') }}</label>
        <div class="ds-controls__suffix tl-input-row">
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

      <div v-if="isCreate && limit.trim() && createLimitNormalized === null" class="ds-help tl-pick-help" data-testid="tl-input-hint">
        {{ createInputHint }}
      </div>

      <div v-if="isEdit" class="ds-controls__row ds-controls__row--compact">
        <label class="ds-label" for="tl-new-limit">{{ t('trustline.newLimitLabel') }}</label>
        <div class="ds-controls__suffix tl-input-row">
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

      <div v-if="isEdit && newLimit.trim() && updateLimitNormalized === null" class="ds-help tl-pick-help" data-testid="tl-input-hint">
        {{ updateInputHint }}
      </div>
      <div v-else-if="limitStepHint" class="ds-help tl-pick-help" data-testid="tl-step-hint">{{ limitStepHint }}</div>

      <div
        v-if="sourceUnavailableText"
        class="ds-alert ds-alert--warn ds-mono"
        data-testid="tl-source-unavailable"
        role="status"
      >
        {{ sourceUnavailableText }}
      </div>

      <!--
        045 S2: "the backend answered: no line for this pair - nothing to update or close" belongs to EDITING a line. On the
        choice steps there is no pair yet (the window opened with this warning over an empty form), and on the create step
        creating the line is what the window already does.
      -->
      <div
        v-if="isEdit && noTrustlineText"
        class="ds-alert ds-alert--warn ds-mono"
        data-testid="tl-no-trustline"
        role="status"
      >
        {{ noTrustlineText }}
      </div>

      <div v-if="state.error" class="ds-alert ds-alert--err ds-mono" data-testid="trustline-error">{{ state.error }}</div>

      <div v-if="isEdit && closeRequested" class="ds-alert ds-alert--warn ds-mono" data-testid="tl-close-requested">
        {{ t('trustline.closeRequested', { used: renderOrDash(effectiveUsed), unit }) }}
      </div>
      <div v-else-if="isEdit && closeIsRequest" class="ds-help ds-mono" data-testid="tl-close-request-note">
        {{ t('trustline.closeRequestNote', { used: renderOrDash(effectiveUsed), unit }) }}
      </div>

      <!--
        045 S2: [Cancel][Primary] in one row. Create is there from the first step, disabled until the pair and the limit are
        given; editing a line is [Cancel][Close TL][Update]. A disabled button says why in its `title`.
      -->
      <div class="ds-row ds-row--actions tl-actions">
        <button class="ds-btn ds-btn--ghost" type="button" :disabled="busy" data-testid="trustline-cancel" @click="cancel">{{ t('common.cancel') }}</button>

        <template v-if="isEdit">
          <button
            v-if="closeArmed"
            class="ds-btn ds-btn--ghost"
            type="button"
            :disabled="busy"
            data-testid="trustline-close-cancel"
            @click="disarmClose"
          >
            {{ t('trustline.cancelClose') }}
          </button>

          <button
            class="ds-btn"
            type="button"
            :disabled="busy || closeRequested || noExistingLineFigures"
            :title="closeTitle"
            data-testid="trustline-close-btn"
            @click="onClose"
          >
            {{ closeArmed ? t('trustline.confirmClose') : closeIsRequest ? t('trustline.requestClose') : t('trustline.close') }}
          </button>

          <button
            class="ds-btn ds-btn--primary"
            type="button"
            :disabled="busy || !updateValid"
            :title="updateTitle"
            data-testid="trustline-update"
            @click="onUpdate"
          >
            {{ busy ? t('trustline.updating') : t('trustline.update') }}
          </button>
        </template>

        <button
          v-else
          class="ds-btn ds-btn--primary"
          type="button"
          :disabled="busy || !isCreate || !createValid"
          :title="createTitle"
          data-testid="trustline-create"
          @click="onCreate"
        >
          {{ busy ? t('trustline.creating') : t('trustline.create') }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tl-pick-help {
  margin-top: 6px;
}

.tl-actions {
  justify-content: flex-end;
}

@media (max-width: 520px) {
  .tl-stats {
    grid-template-columns: 1fr;
  }
}
</style>


