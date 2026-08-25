<script setup lang="ts">
import { computed } from 'vue'
import type { GraphNode } from '../types'
import type { TrustlineInfo } from '../api/simulatorTypes'
import { VIZ_MAPPING } from '../vizMapping'
import { parseAmountNumber } from '../utils/numberFormat'
import { equivalentPrecision } from '../config/equivalentPrecision'
import { atomsToMoney, formatMoney } from '../utils/money'

type NodeEdgeStats = {
  outLimitText: string
  inLimitText: string
  degree: number
}

type Props = {
  node: GraphNode
  edgeStats: NodeEdgeStats | null
  equivalentText: string

  showPinActions: boolean
  isPinned: boolean
  pin: () => void
  unpin: () => void

  // BUG-1: Interact Mode extensions
  interactMode?: boolean
  /** All trustlines from useInteractMode — filtered internally by node.id. */
  interactTrustlines?: TrustlineInfo[]
  /** True while trustlines are being fetched from the API. */
  trustlinesLoading?: boolean
  /** True while Interact Mode is busy (disable quick actions). */
  interactBusy?: boolean
  onInteractSendPayment?: (fromPid: string) => void
  onInteractNewTrustline?: (fromPid: string) => void
  onInteractEditTrustline?: (fromPid: string, toPid: string) => void
}

const emit = defineEmits<{ close: [] }>()

const props = defineProps<Props>()

const wrapperStyle = computed(() =>
  // WM owns geometry. Keep NodeCardOverlay as a simple content block.
  ({
    position: 'static',
    left: 'auto',
    top: 'auto',
    right: 'auto',
    zIndex: 'auto',
  }) as const,
)

function safeNum(v: unknown): number {
  // UX-8: use parseAmountNumber() for decimal-like strings (e.g. "1,234.5" must not
  // parse as NaN). This is sort-only, not a guard, but inconsistent parsing would
  // break trustline row ordering when `used` values contain locale separators.
  const n = parseAmountNumber(v)
  return Number.isFinite(n) ? n : 0
}

function isSaturatedAvailable(v: unknown): boolean {
  // NC-3: consider saturated only when `available` is a finite number and <= 0.
  // Unknown/invalid values must NOT be treated as saturated.
  const n = parseAmountNumber(v)
  return Number.isFinite(n) && n <= 0
}

const nodeColor = computed(() => {
  const key = String(props.node.viz_color_key ?? 'unknown')
  return VIZ_MAPPING.node.color[key]?.fill ?? VIZ_MAPPING.node.color.unknown.fill
})

/**
 * Fraction digits the equivalent this card is labelled with declares (012 / `F-012-4`).
 * `equivalentText` is the snapshot's equivalent code (`SimulatorAppRoot.vue:1358`).
 */
const precision = computed(() => equivalentPrecision(props.equivalentText))

/**
 * Signed balance in major units.
 *
 * 012 / `F-012-6`: the atoms branch used to print `net_balance_atoms` verbatim, and atoms
 * are an integer count of 10^-precision units — so on every shipped fixture (0 of 100 nodes
 * carry a major-unit `net_balance`, and `fixtures.ts` does not even read that field) the
 * card showed a balance 10^precision times too large. Both branches now render through the
 * equivalent's precision, so the card cannot show a number that is not an amount of
 * anything.
 */
function netText(node: GraphNode): string | null {
  const major = node.net_balance
  if (major != null && String(major).trim() !== '') return formatMoney(String(major), precision.value)

  const atoms = node.net_balance_atoms
  if (atoms == null) return null

  const s = String(atoms)
  // Back-compat: older fixtures used signed atoms already.
  const signed = s.startsWith('-')
    ? s
    : node.net_sign === -1
      ? `-${s}`
      : node.net_sign === 0
        ? '0'
        : s

  // Unparseable atoms: show what arrived rather than invent a scale for it.
  return atomsToMoney(signed, precision.value) ?? signed
}

/**
 * Money on a trustline row, at the precision that row's own equivalent declares.
 *
 * These three fields (`used`, `limit`, `available`) used to go through `fmtAmt`, which did
 * `parseFloat` and then dropped a `.00` fraction — the same "money as a `number`" defect
 * as `formatAmount2`, on the same card: a UAH limit of `10.00` printed as `10`, and any
 * digit past the double's ~15 significant ones was gone before formatting began. The row
 * carries its own `equivalent`, so it does not have to borrow the card's.
 */
function rowMoney(tl: TrustlineInfo, value: unknown): string {
  return formatMoney(value, equivalentPrecision(tl.equivalent || props.equivalentText))
}

/** Trustlines that involve this node (outgoing or incoming). */
const nodeTrustlines = computed<TrustlineInfo[]>(() => {
  if (!props.interactMode || !props.interactTrustlines?.length) return []
  const id = props.node.id
  return props.interactTrustlines.filter(
    (tl) => tl.from_pid === id || tl.to_pid === id,
  )
})

/** OUT trustlines (node = debtor), sorted by used DESC */
const outTrustlines = computed<TrustlineInfo[]>(() =>
  nodeTrustlines.value
    .filter((tl) => tl.from_pid === props.node.id)
    .sort((a, b) => safeNum(b.used) - safeNum(a.used)),
)

/** IN trustlines (node = creditor), sorted by used DESC */
const inTrustlines = computed<TrustlineInfo[]>(() =>
  nodeTrustlines.value
    .filter((tl) => tl.to_pid === props.node.id)
    .sort((a, b) => safeNum(b.used) - safeNum(a.used)),
)
</script>

<template>
  <div class="ds-ov-node-card" :style="wrapperStyle">
    <div class="ds-panel ds-panel--elevated">
      <div class="ds-panel__body ds-ov-node-card__body">

        <!-- Identity + Balance (merged) ------------------------------------>
        <div class="ds-ov-node-card__identity">
          <div class="ds-node-card__avatar ds-ov-node-card__avatar">
            {{ String(node.name ?? node.id).slice(0, 2).toUpperCase() }}
          </div>
          <div class="ds-ov-node-card__text">
            <div class="ds-node-card__name">{{ node.name ?? node.id }}</div>
            <div class="ds-node-card__meta">{{ node.type ?? '—' }} · {{ node.status ?? '—' }}</div>
          </div>
          <div class="ds-ov-node-card__right">
            <div class="ds-ov-node-card__service">
              <button
                v-if="showPinActions"
                class="ds-btn ds-btn--ghost ds-btn--icon ds-ov-node-card__action-btn"
                type="button"
                :aria-label="isPinned ? 'Unpin' : 'Pin'"
                :title="isPinned ? 'Unpin' : 'Pin'"
                @click="isPinned ? unpin() : pin()"
              >
                {{ isPinned ? '◆' : '◇' }}
              </button>
              <button
                class="ds-btn ds-btn--ghost ds-btn--icon ds-ov-node-card__action-btn"
                type="button"
                aria-label="Close"
                title="Close"
                @click="emit('close')"
              >
                ×
              </button>
            </div>
            <div class="ds-ov-node-card__balance" :style="{ color: nodeColor }">{{ netText(node) ?? '—' }}</div>
          </div>
        </div>

        <!-- Separator -------------------------------------------------------->
        <hr class="ds-ov-node-card__divider" />

        <!-- Stats (only unique fields: Out, In, Degree) ---------------------->
        <div class="ds-ov-node-card__stats">
          <div class="ds-ov-node-card__stat">
            <span class="ds-label">Out</span>
            <span class="ds-value ds-mono">{{ edgeStats?.outLimitText ?? '—' }}</span>
          </div>
          <div class="ds-ov-node-card__stat">
            <span class="ds-label">In</span>
            <span class="ds-value ds-mono">{{ edgeStats?.inLimitText ?? '—' }}</span>
          </div>
          <div class="ds-ov-node-card__stat">
            <span class="ds-label">Degree</span>
            <span class="ds-value ds-mono">{{ edgeStats?.degree ?? '—' }}</span>
          </div>
        </div>

        <!-- BUG-1: Interact Mode Trustlines section --------------------------->
        <template v-if="interactMode">
          <hr class="ds-ov-node-card__divider" />

          <!-- Quick-action buttons -->
          <div class="nco-interact-actions">
            <button
              class="ds-btn ds-btn--primary ds-btn--sm"
              type="button"
              :disabled="!!interactBusy"
              data-testid="node-card-send-payment"
              @click="onInteractSendPayment?.(node.id)"
            >
              💸 Send Payment
            </button>
            <button
              class="ds-btn ds-btn--secondary ds-btn--sm"
              type="button"
              :disabled="!!interactBusy"
              data-testid="node-card-new-trustline"
              @click="onInteractNewTrustline?.(node.id)"
            >
              ＋ New Trustline
            </button>
          </div>

          <!-- Trustlines list: grouped OUT / IN -->
          <div v-if="nodeTrustlines.length > 0" class="nco-trustlines">

            <!-- OUT group -->
            <div class="nco-tl-group-header">OUT → {{ outTrustlines.length }}</div>
            <div
              v-for="tl in outTrustlines"
              :key="`${tl.from_pid}→${tl.to_pid}`"
              :class="[
                'nco-trustline-row',
                { 'nco-trustline-row--saturated': isSaturatedAvailable(tl.available) },
              ]"
              :title="`avail: ${rowMoney(tl, tl.available)}`"
            >
              <span class="nco-trustline-row__peer ds-mono">{{ tl.to_name }}</span>
              <span class="nco-trustline-row__amounts ds-mono">{{ rowMoney(tl, tl.used) }}&thinsp;/&thinsp;{{ rowMoney(tl, tl.limit) }}</span>
              <span class="nco-trustline-row__avail ds-mono">avail: {{ rowMoney(tl, tl.available) }}</span>
              <button
                class="ds-btn ds-btn--ghost ds-btn--icon nco-trustline-row__edit"
                type="button"
                :disabled="!!interactBusy"
                title="Edit trustline"
                aria-label="Edit trustline"
                @click="onInteractEditTrustline?.(tl.from_pid, tl.to_pid)"
              >✏️</button>
            </div>

            <!-- IN group -->
            <div class="nco-tl-group-header nco-tl-group-header--gap">IN ← {{ inTrustlines.length }}</div>
            <div
              v-for="tl in inTrustlines"
              :key="`${tl.from_pid}→${tl.to_pid}`"
              :class="[
                'nco-trustline-row',
                { 'nco-trustline-row--saturated': isSaturatedAvailable(tl.available) },
              ]"
              :title="`avail: ${rowMoney(tl, tl.available)}`"
            >
              <span class="nco-trustline-row__peer ds-mono">{{ tl.from_name }}</span>
              <span class="nco-trustline-row__amounts ds-mono">{{ rowMoney(tl, tl.used) }}&thinsp;/&thinsp;{{ rowMoney(tl, tl.limit) }}</span>
              <span class="nco-trustline-row__avail ds-mono">avail: {{ rowMoney(tl, tl.available) }}</span>
              <button
                class="ds-btn ds-btn--ghost ds-btn--icon nco-trustline-row__edit"
                type="button"
                :disabled="!!interactBusy"
                :title="`Edit trustline (set by ${tl.from_name || tl.from_pid})`"
                :aria-label="`Edit trustline (set by ${tl.from_name || tl.from_pid})`"
                @click="onInteractEditTrustline?.(tl.from_pid, tl.to_pid)"
              >✏️</button>
            </div>

          </div>
          <div v-else-if="trustlinesLoading" class="nco-trustlines__empty ds-label">
            <span class="ds-text-secondary">Loading trustlines…</span>
          </div>
          <div v-else class="nco-trustlines__empty ds-label">
            No trustlines
          </div>
        </template>

      </div>
    </div>
  </div>
</template>

<style scoped>
/* Interact Mode: quick-action buttons */
.nco-interact-actions {
  display: flex;
  gap: var(--ds-nco-actions-gap);
  flex-wrap: wrap;
  margin-bottom: var(--ds-nco-actions-margin-bottom);
}

.nco-interact-actions > .ds-btn {
  flex: 1 1 0;
  min-width: var(--ds-nco-actions-min-btn-width);
}

/* Trustlines list */
.nco-trustlines {
  margin-top: var(--ds-nco-trustlines-margin-top);
  max-height: max(var(--ds-nco-trustlines-min-h), calc(50vh - var(--ds-nco-trustlines-vh-offset)));
  overflow-y: auto;
}

/* Group heading: OUT → N / IN ← N */
.nco-tl-group-header {
  font-size: var(--ds-nco-tl-group-font-size);
  text-transform: uppercase;
  letter-spacing: var(--ds-nco-tl-group-letter-spacing);
  opacity: var(--ds-nco-tl-group-opacity);
  margin-bottom: var(--ds-nco-tl-group-margin-bottom);
}

.nco-tl-group-header--gap {
  margin-top: var(--ds-nco-tl-group-gap-margin-top);
}

/* Grid row: [peer-name] [used/limit] [edit-btn or placeholder] */
.nco-trustline-row {
  display: grid;
  grid-template-columns: minmax(60px, 80px) 1fr auto auto;
  align-items: center;
  gap: var(--ds-nco-row-gap);
  padding: var(--ds-nco-row-padding-y) 0;
  font-size: var(--ds-nco-row-font-size);
}

/* NC-3: "available <= 0" should be visually emphasized (border only). */
.nco-trustline-row--saturated {
  border-left: var(--ds-nco-row-saturated-border-w) solid var(--ds-err);
  padding-left: var(--ds-nco-row-saturated-pad-left);
}

.nco-trustline-row__peer {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nco-trustline-row__amounts {
  font-size: var(--ds-nco-row-amounts-font-size);
  text-align: right;
  opacity: var(--ds-nco-row-amounts-opacity);
  font-variant-numeric: tabular-nums;
}

/* NC-2: available column */
.nco-trustline-row__avail {
  font-size: var(--ds-nco-row-avail-font-size);
  opacity: var(--ds-nco-row-avail-opacity);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.nco-trustline-row__edit {
  font-size: var(--ds-nco-row-edit-font-size);
  padding: var(--ds-nco-row-edit-padding-y) var(--ds-nco-row-edit-padding-x);
  line-height: 1;
  opacity: var(--ds-nco-row-edit-opacity);
  transition: opacity var(--ds-nco-row-edit-dur);
}

.nco-trustline-row__edit:hover {
  opacity: 1;
}

.nco-trustlines__empty {
  font-size: var(--ds-nco-empty-font-size);
  opacity: var(--ds-nco-empty-opacity);
  font-style: italic;
}
</style>
