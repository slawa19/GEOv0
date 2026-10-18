<script setup lang="ts">
import { computed } from 'vue'
import { interactText } from '../i18n/interactStrings'
import { parseAmountNumber } from '../utils/numberFormat'
import { renderAvailable, renderOrDash } from '../utils/valueFormat'

import { useDestructiveConfirmation } from '../composables/useDestructiveConfirmation'
import { useParticipantDisplayId } from '../composables/useParticipantDisplay'
import {
  canActOnTrustlineFigures,
  trustlineFiguresNotice,
  trustlineFrozenNotice,
  trustlineNoRowNotice,
  type TrustlineFiguresSource,
} from '../composables/interact/trustlinesSourceState'

import type { InteractPhase, InteractState } from '../composables/useInteractMode'

type Props = {
  phase: InteractPhase
  state: InteractState

  unit: string
  used?: string | number | null
  /** Debt in reverse direction (debtor=from_pid, creditor=to_pid); the other line's - it does not hold a close (026). */
  reverseUsed?: string | number | null
  limit?: string | number | null
  available?: string | number | null
  status?: string | null
  /** 026: the creditor asked to close; limit 0 until the debt this line supports is repaid. */
  closeRequestedAt?: string | null

  /** Disable action buttons while interact-mode is busy. */
  busy?: boolean

  /**
   * `F-013-7`: чем обоснованы показанные числа — ответом источника, его молчанием или
   * замороженным ранее ответом. Для просмотра годится любое; закрывать линию по числам, за
   * которые никто не поручился, — нет.
   *
   * ОБЯЗАТЕЛЬНЫЙ: раньше здесь был `sourceUnavailable?: boolean` со значением по умолчанию
   * `false`, то есть забывший вызывающий получал разрешённую мутацию.
   */
  figuresSource: TrustlineFiguresSource

  close: () => void
}

const props = defineProps<Props>()

/**
 * `F-013-7`. Отсутствие основания = оснований нет (`canActOnTrustlineFigures(undefined) === false`):
 * типы ловят забывшего вызывающего на сборке, эта ветка — в рантайме.
 *
 * 2026-09-10 (внешнее ревью 013, находка P2): сюда же попал `no-row`. Закрывать линию, которой по
 * ответу бэкенда не существует, нечего, а `used`/`limit` в этот момент показаны из снапшота —
 * то есть из источника, который тот же ответ опроверг.
 */
const noExistingLine = computed(() => !canActOnTrustlineFigures(props.figuresSource))

/**
 * ЧТО ПОКАЗЫВАТЬ, КОГДА СУЩЕСТВУЮЩЕЙ ЛИНИИ НЕТ (кросс-ревью, P2 — презентационная половина
 * `F-013-7`).
 *
 * Мутирующая половина находки была закрыта здесь раньше, презентационная — только в
 * `TrustlineManagementPanel` (`effectiveData`), и попап остался ТРЕТЬЕЙ копией гарда, закрытой
 * наполовину. На `no-row` он печатал числа снапшота (`Used 12 / Limit 100 / Available 88 /
 * Status active`), полосу утилизации `12%` и фразу «Cannot close: trustline has outstanding debt
 * (used: 12 UAH)» — ПОЛОЖИТЕЛЬНОЕ УТВЕРЖДЕНИЕ о долге линии, про которую бэкенд в том же окне,
 * двумя элементами выше, сообщал, что её не существует. Выключенная кнопка этого не исправляет:
 * предмет программы — не «разрешено ли действие», а «правда ли то, что написано».
 *
 * ПОЧЕМУ ТОТ ЖЕ ПРЕДИКАТ, ЧТО У КНОПКИ, А НЕ `trustlineSourceAnswered`: гасится утверждение
 * о СУЩЕСТВУЮЩЕЙ ЛИНИИ, а это вопрос Б. На `frozen` числа остаются — замороженный ответ описывает
 * линию, которая есть, и окно висит контекстом именно ради них.
 *
 * `null` вместо чисел, а не скрытая сетка: `renderOrDash` печатает «—», и «не знаем» остаётся
 * видимым состоянием экрана, как в панели.
 */
const figures = computed(() => {
  if (noExistingLine.value) {
    return { used: null, limit: null, available: null, status: null }
  }
  return {
    used: props.used ?? null,
    limit: props.limit ?? null,
    available: props.available ?? null,
    status: props.status ?? null,
  }
})
/** Источник не ответил вовсе. */
const sourceUnavailableText = computed<string | null>(() => trustlineFiguresNotice(props.figuresSource))
/** Источник ответил, и линии у пары нет — другой факт, другие последствия, своё сообщение. */
const noTrustlineText = computed<string | null>(() => trustlineNoRowNotice(props.figuresSource))
/**
 * Показана ЗАМОРОЖЕННАЯ копия прежнего ответа (`keepAlive`). Действовать по ней позволено — это
 * устаревание, а не отсутствие, — но выдавать её за живое состояние нельзя: окно в этот момент
 * может показывать вообще не ту пару, что живое interact-состояние.
 *
 * Сообщение живёт только здесь, а не в `TrustlineManagementPanel`: `frozen` возникает
 * исключительно в режиме `keepAlive` окна edge-detail (`SimulatorAppRoot.wmEdgeDetailFiguresSource`),
 * и панель этого основания не получает никогда.
 */
const frozenText = computed<string | null>(() => trustlineFrozenNotice(props.figuresSource))

const emit = defineEmits<{
  (e: 'changeLimit'): void
  (e: 'closeLine'): void
  (e: 'sendPayment'): void
}>()

const popupStyle = computed(() =>
  // WM owns geometry. Keep EdgeDetailPopup as a simple content block.
  ({
    position: 'static',
    left: 'auto',
    top: 'auto',
    right: 'auto',
    zIndex: 'auto',
  }) as const,
)

// Ids are PRINTED here, so they go through the display rule; `state` keeps the full ids.
const showPid = useParticipantDisplayId()

const title = computed(() => {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `${showPid(from)} → ${showPid(to)}`
  return props.state.selectedEdgeKey ?? 'Edge'
})

// ED-3 polish: make the payment button label contextual to reduce direction confusion.
// Spec: prefer display name when available; fallback is pid.
// In this component we only have access to InteractState.{fromPid}, so use it as best-effort.
const sendPaymentFromLabel = computed(() => {
  const pid = (props.state.fromPid ?? '').trim()
  return pid ? showPid(pid) : 'sender'
})

// 026 (owner В1): a close with the supported debt (`used`) is a request; the reverse debt is the other line's.
const closeIsRequest = computed(() => {
  const u = parseAmountNumber(figures.value.used)
  return Number.isFinite(u) && u > 0
})
const closeRequested = computed(() => !noExistingLine.value && !!props.closeRequestedAt)

const utilizationPct = computed<number | null>(() => {
  // Полоса — то же утверждение в графическом виде: «занято 12 из 100» у линии, которой нет.
  const u = parseAmountNumber(figures.value.used)
  const l = parseAmountNumber(figures.value.limit)
  if (!Number.isFinite(u) || !Number.isFinite(l)) return null
  if (l <= 0) return null
  const raw = Math.round((u / l) * 100)
  if (!Number.isFinite(raw)) return null
  return Math.max(0, Math.min(100, raw))
})

const utilizationColor = computed(() => {
  const p = utilizationPct.value
  if (p == null) return 'var(--ds-border)'
  if (p >= 85) return 'var(--ds-err)'
  if (p >= 60) return 'var(--ds-warn)'
  return 'var(--ds-ok)'
})

const utilizationLabel = computed(() => {
  const p = utilizationPct.value
  return p == null ? '—%' : `${p}%`
})

const utilizationAriaValueText = computed(() => {
  // A11y: distinguish unknown utilization from 0%.
  if (utilizationPct.value == null) return 'unknown'
  return utilizationLabel.value
})

const utilizationWidth = computed(() => `${utilizationPct.value ?? 0}%`)

const { armed: closeArmed, disarm: disarmClose, confirmOrArm: confirmCloseOrArm } = useDestructiveConfirmation({
  disarmOn: [
    // When switching the selected trustline, cancel the confirmation state.
    { source: () => `${props.state.fromPid ?? ''}→${props.state.toPid ?? ''}` },
    // When the UI becomes busy, cancel the confirmation state.
    { source: () => props.busy, when: (b) => !!b },
    // `F-013-7`: если основание для действия пропало, взведённое закрытие не должно его пережить.
    { source: noExistingLine, when: (b) => !!b },
  ],
})

function onCloseLine() {
  if (props.busy) return
  // ОТДЕЛЬНАЯ ПРОВЕРКА (`F-013-7`): без чисел линии закрывать нечего — мы не знаем, есть ли она.
  if (noExistingLine.value) return
  if (closeRequested.value) return
  void confirmCloseOrArm(() => emit('closeLine'))
}
</script>

<template>
  <div
    class="popup ds-ov-item ds-ov-surface ds-ov-edge-detail"
    data-testid="edge-detail-popup"
    aria-label="Edge detail popup"
    :style="popupStyle"
  >
    <!-- 045 S3 (decision 3): the pair and its equivalent are the title; the window closes from × (and Escape, handled by
         the window manager). The equivalent is said once, here: the figures below are all of it. -->
    <div class="popup__head">
      <div class="popup__subtitle ds-value ds-mono">{{ title }}</div>
      <span v-if="unit" class="popup__unit" data-testid="edge-unit">{{ unit }}</span>
      <button
        class="ds-btn ds-btn--ghost ds-btn--icon popup__close"
        type="button"
        aria-label="Close"
        title="Close"
        aria-keyshortcuts="Escape"
        @click="close"
      >×</button>
    </div>

    <!-- One line: Used / Limit · avail (the equivalent is in the title); the status ends the bar's line - at 320 px the hud
         monospace does not hold all four on one line (measured 2026-10-18). -->
    <div class="popup__stats" data-testid="edge-stats">
      <span class="popup__k">Used</span>{{ ' ' }}<span data-testid="edge-used">{{ renderOrDash(figures.used) }}</span>{{ ' / ' }}<span
        class="popup__k">Limit</span>{{ ' ' }}<span data-testid="edge-limit">{{ renderOrDash(figures.limit) }}</span>{{ ' · ' }}<span
        class="popup__k">avail</span>{{ ' ' }}<span data-testid="edge-available">{{ renderAvailable(figures.available) }}</span>
    </div>

    <!-- ED-2: capacity utilization bar (used/limit), shown under the figures. -->
    <div class="popup__util" aria-label="Utilization">
      <div
        class="popup__util-bar"
        role="progressbar"
        aria-label="Utilization bar"
        :aria-valuenow="utilizationPct == null ? undefined : utilizationPct"
        :aria-valuetext="utilizationAriaValueText"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <div class="popup__util-fill" :style="{ width: utilizationWidth, background: utilizationColor }" />
      </div>
      <div class="popup__util-pct ds-label ds-mono" data-testid="edge-utilization-pct">{{ utilizationLabel }}</div>
      <span class="popup__status" data-testid="edge-status">{{ renderOrDash(figures.status) }}</span>
    </div>

    <!-- Warnings: a line each, only when there is one. -->
    <div v-if="sourceUnavailableText" class="ds-help popup__warn" data-testid="edge-source-unavailable">
      {{ sourceUnavailableText }} Closing the line is disabled until then.
    </div>
    <div v-if="noTrustlineText" class="ds-help popup__warn" data-testid="edge-no-trustline">
      {{ noTrustlineText }}
    </div>
    <div v-if="frozenText" class="ds-help popup__warn" data-testid="edge-frozen-figures">
      {{ frozenText }}
    </div>
    <div v-if="closeRequested" class="ds-help popup__warn" data-testid="edge-close-requested">
      Close requested: limit stays 0; the line closes when its debt is repaid.
    </div>
    <div v-else-if="closeIsRequest" class="ds-help" data-testid="edge-close-request-note">
      Closing with debt (used: {{ renderOrDash(figures.used) }} {{ unit }}) is a request: limit becomes 0 until repaid.
    </div>

    <!-- F-037-4: why Pay runs against the line's arrow - closed by default, as on the payment panel. Always in the DOM. -->
    <details class="ds-help popup__direction" data-testid="edge-payment-direction">
      <summary>{{ interactText('lineDirectionTitle') }}</summary>
      <div data-testid="edge-payment-direction-note">{{ interactText('lineDirectionNote') }}</div>
    </details>

    <div class="popup__actions">
      <button
        class="ds-btn ds-btn--secondary ds-btn--sm"
        type="button"
        :disabled="!!busy"
        data-testid="edge-send-payment"
        @click="emit('sendPayment')"
      >
        Pay {{ sendPaymentFromLabel }}
      </button>
      <button class="ds-btn ds-btn--secondary ds-btn--sm" type="button" :disabled="!!busy" data-testid="edge-change-limit" @click="emit('changeLimit')">
        Change limit
      </button>
      <button
        class="ds-btn ds-btn--danger ds-btn--sm"
        type="button"
        :disabled="!!busy || closeRequested || noExistingLine"
        data-testid="edge-close-line-btn"
        @click="onCloseLine"
      >
        {{ closeArmed ? 'Confirm close' : 'Close line' }}
      </button>

      <button
        v-if="closeArmed"
        class="ds-btn ds-btn--ghost ds-btn--sm"
        type="button"
        :disabled="!!busy"
        data-testid="edge-close-line-cancel"
        @click="disarmClose"
      >
        Cancel
      </button>
    </div>
  </div>
</template>

<style scoped>
/*
  045 S3 (decision 3): the line window on the one scale of the windows - as wide as the shell's preferred width (the token the
  window manager reads), the pair as the title, the figures on one line with the bar under them, warnings as `.ds-help`
  lines, one row of small buttons.
*/
.popup {
  position: absolute;
  z-index: var(--ds-z-panel, 42);
  box-sizing: border-box;
  width: var(--ds-wm-edge-detail-prefw);
  max-width: min(460px, calc(100vw - 24px));
  pointer-events: auto;
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-2);
}

.popup__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ds-space-2);
  min-width: 0;
}

.popup__subtitle {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--ds-typo-panel-heading-font-size);
  font-weight: 600;
}

.popup__unit {
  flex: 0 0 auto;
  margin-right: auto;
  font-size: var(--ds-typo-section-label-font-size);
  color: var(--ds-text-3);
}

.popup__close {
  width: 24px;
  height: 24px;
  flex: 0 0 auto;
}

.popup__stats {
  font-size: var(--ds-typo-label-font-size);
  font-variant-numeric: tabular-nums;
  color: var(--ds-text-1);
}

.popup__k {
  color: var(--ds-text-3);
}

.popup__util {
  display: flex;
  align-items: center;
  gap: 10px;
}

.popup__util-bar {
  flex: 1;
  height: 4px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--ds-border) 45%, transparent);
  overflow: hidden;
}

.popup__util-fill {
  height: 100%;
  border-radius: 999px;
}

.popup__status {
  font-size: var(--ds-typo-label-font-size);
  color: var(--ds-text-1);
  white-space: nowrap;
}

.popup__util-pct {
  opacity: 0.9;
  min-width: 3.5ch;
  text-align: right;
}

/* A warning is a hint line in the warning tone (no box). */
.popup__warn {
  color: color-mix(in srgb, var(--ds-warn) 55%, var(--ds-text-1));
}

.popup__direction > summary {
  cursor: pointer;
}

.popup__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: flex-end;
}
</style>
