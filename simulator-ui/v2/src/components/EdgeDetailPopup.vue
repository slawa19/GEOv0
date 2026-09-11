<script setup lang="ts">
import { computed } from 'vue'
import { parseAmountNumber } from '../utils/numberFormat'
import { renderOrDash } from '../utils/valueFormat'

import { useDestructiveConfirmation } from '../composables/useDestructiveConfirmation'
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
  /** Debt in reverse direction (debtor=from_pid, creditor=to_pid). */
  reverseUsed?: string | number | null
  limit?: string | number | null
  available?: string | number | null
  status?: string | null

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

  /** When true, the popup is forced hidden (parent shows TrustlineManagementPanel instead). */
  forceHidden?: boolean

  close: () => void
}

const props = withDefaults(defineProps<Props>(), {
  forceHidden: false,
})

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
    return { used: null, reverseUsed: null, limit: null, available: null, status: null }
  }
  return {
    used: props.used ?? null,
    reverseUsed: props.reverseUsed ?? null,
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

// UX: show the small EDGE popup as a standalone quick-info overlay when
// the user clicks on an edge on the canvas. Requires phase = editing-trustline
// and an edge anchor (set by edge click).
// The popup is hidden when the full TrustlineManagementPanel is shown instead
// (i.e. when the user came from NodeCard ✏️ or ActionBar or clicked "Change Limit").
// This is controlled by the parent via the `forceHidden` prop.
const open = computed(() => {
  if (props.forceHidden) return false
  return true
})

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

const title = computed(() => {
  const from = props.state.fromPid
  const to = props.state.toPid
  if (from && to) return `${from} → ${to}`
  return props.state.selectedEdgeKey ?? 'Edge'
})

// ED-3 polish: make the payment button label contextual to reduce direction confusion.
// Spec: prefer display name when available; fallback is pid.
// In this component we only have access to InteractState.{fromPid}, so use it as best-effort.
const sendPaymentFromLabel = computed(() => {
  const pid = (props.state.fromPid ?? '').trim()
  return pid || 'sender'
})

const closeBlocked = computed(() => {
  const u = parseAmountNumber(figures.value.used)
  const ru = parseAmountNumber(figures.value.reverseUsed)
  const usedDebt = Number.isFinite(u) && u > 0
  const reverseDebt = Number.isFinite(ru) && ru > 0
  return usedDebt || reverseDebt
})

const closeDebtDisplay = computed(() => {
  const u = parseAmountNumber(figures.value.used)
  const ru = parseAmountNumber(figures.value.reverseUsed)
  const usedDebt = Number.isFinite(u) && u > 0
  const reverseDebt = Number.isFinite(ru) && ru > 0

  if (usedDebt && reverseDebt) return `used: ${renderOrDash(figures.value.used)} ${props.unit}, reverse: ${renderOrDash(figures.value.reverseUsed)} ${props.unit}`
  if (usedDebt) return `used: ${renderOrDash(figures.value.used)} ${props.unit}`
  if (reverseDebt) return `reverse: ${renderOrDash(figures.value.reverseUsed)} ${props.unit}`
  return `used: ${renderOrDash(figures.value.used)} ${props.unit}`
})

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
    // When popup closes (including forceHidden), cancel the confirmation state.
    { source: open, when: (isOpen) => !isOpen },
    // When switching the selected trustline, cancel the confirmation state.
    { source: () => `${props.state.fromPid ?? ''}→${props.state.toPid ?? ''}` },
    // When the UI becomes busy, cancel the confirmation state.
    { source: () => props.busy, when: (b) => !!b },
    // ED-1: when Close is blocked (used > 0), disarm any destructive confirmation.
    { source: closeBlocked, when: (b) => !!b },
    // `F-013-7`: если основание для действия пропало, взведённое закрытие не должно его пережить.
    { source: noExistingLine, when: (b) => !!b },
  ],
})

function onCloseLine() {
  if (props.busy) return
  // ОТДЕЛЬНАЯ ПРОВЕРКА, а не расчёт на `closeBlocked`, и это не перестраховка (`F-013-7`).
  // `closeBlocked` означает «есть долг», и вычисляется из чисел; когда чисел нет, оно ложно —
  // то есть каскад разрешил бы закрытие ровно в тот момент, когда мы не знаем, есть ли долг.
  if (noExistingLine.value) return
  if (closeBlocked.value) return
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
      <div class="ds-value ds-mono">{{ renderOrDash(figures.used) }} {{ unit }}</div>
      <div class="ds-label">Limit</div>
      <div class="ds-value ds-mono">{{ renderOrDash(figures.limit) }} {{ unit }}</div>
      <div class="ds-label">Available</div>
      <div class="ds-value ds-mono">{{ renderOrDash(figures.available) }} {{ unit }}</div>
      <div class="ds-label">Status</div>
      <div class="ds-value ds-mono">{{ renderOrDash(figures.status) }}</div>
    </div>

    <!-- ED-2: capacity utilization bar (used/limit), shown under the stats grid. -->
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
    </div>

    <div class="popup__actions">
      <div
        v-if="sourceUnavailableText"
        class="popup__inline-warn ds-label ds-mono"
        data-testid="edge-source-unavailable"
      >
        {{ sourceUnavailableText }} Closing the line is disabled until then.
      </div>
      <div
        v-if="noTrustlineText"
        class="popup__inline-warn ds-label ds-mono"
        data-testid="edge-no-trustline"
      >
        {{ noTrustlineText }}
      </div>
      <div
        v-if="frozenText"
        class="popup__inline-warn ds-label ds-mono"
        data-testid="edge-frozen-figures"
      >
        {{ frozenText }}
      </div>
      <div v-if="closeBlocked" class="popup__inline-warn ds-label ds-mono" data-testid="edge-close-blocked">
        Cannot close: trustline has outstanding debt ({{ closeDebtDisplay }}). Reduce debt to 0 first.
      </div>

      <button
        class="ds-btn ds-btn--secondary ds-btn--sm"
        type="button"
        :disabled="!!busy"
        data-testid="edge-send-payment"
        @click="emit('sendPayment')"
      >
        💸 Pay {{ sendPaymentFromLabel }}
      </button>
      <button class="ds-btn ds-btn--secondary ds-btn--sm" type="button" :disabled="!!busy" @click="emit('changeLimit')">
        Change limit
      </button>
      <button
        class="ds-btn ds-btn--danger ds-btn--sm"
        type="button"
        :disabled="!!busy || closeBlocked || noExistingLine"
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
      <button class="ds-btn ds-btn--ghost ds-btn--sm" type="button" @click="close">Close</button>
    </div>
  </div>
</template>

<style scoped>
.popup {
  position: absolute;
  z-index: var(--ds-z-panel, 42);
  min-width: var(--ds-edp-minw);
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

.popup__util {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
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

.popup__util-pct {
  opacity: 0.9;
  min-width: 3.5ch;
  text-align: right;
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

.popup__inline-warn {
  flex: 1 0 100%;
  padding: var(--ds-edp-warn-pad-y) var(--ds-edp-warn-pad-x);
  border-radius: var(--ds-radius-md);
  border: 1px solid color-mix(in srgb, var(--ds-warn) 35%, transparent);
  background: color-mix(in srgb, var(--ds-warn) 14%, transparent);
  color: color-mix(in srgb, var(--ds-warn) 18%, var(--ds-text-1));
}

</style>

