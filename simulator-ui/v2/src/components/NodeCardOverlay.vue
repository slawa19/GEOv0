<script setup lang="ts">
import { computed } from 'vue'
import type { GraphNode } from '../types'
import type { TrustlineInfo } from '../api/simulatorTypes'
import { classifyNode, fillOf, type DataPalette } from '../vizMapping'
import { parseAmountNumber } from '../utils/numberFormat'
import { equivalentPrecision } from '../config/equivalentPrecision'
import { canonicalEquivalent, sameEquivalent } from '../config/equivalents'
import { atomsToMoney, formatMoney } from '../utils/money'
import { isOverLimit, renderOrDash } from '../utils/valueFormat'
import { useParticipantDisplayId } from '../composables/useParticipantDisplay'
import { useUiLocale } from '../composables/useUiLocale'
import { participantStatusText, participantTypeText } from '../i18n/statusText'

type NodeEdgeStats = {
  outLimitText: string
  inLimitText: string
}

type Props = {
  node: GraphNode
  edgeStats: NodeEdgeStats | null
  equivalentText: string
  /** 039 slice B: the resolved data colours - the object the canvas paints with (never a second read of the tokens). */
  palette: DataPalette

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
  /**
   * The interact phase (BACKLOG 043-4). Pay and New line start a flow only from `idle` (`useInteractMode`
   * `startPaymentFlowWithFrom` / `startTrustlineFlowWithFrom` return early otherwise), so while another action is open
   * they are `disabled` and the card says why - instead of a button that silently does nothing.
   */
  interactPhase?: string
  onInteractSendPayment?: (fromPid: string) => void
  onInteractNewTrustline?: (fromPid: string) => void
  onInteractEditTrustline?: (fromPid: string, toPid: string) => void
}

const emit = defineEmits<{ close: [] }>()

const props = defineProps<Props>()

// 046 wave 3: every word of the card is the dictionary's (`card.*`), in the language of the interface.
const { t, locale } = useUiLocale()

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

/**
 * 039 slice B: the card asks the SAME classifier as the canvas and the legend, and takes its colour from the same
 * resolved palette - so a participant cannot be one thing on the graph and another in its card.
 */
const look = computed(() => classifyNode(props.node))
const nodeColor = computed(() => fillOf(look.value, props.palette))
/**
 * The role in the legend's words. 046 wave 3: the very entries of the legend (`legend.entry.*`) - one set of words; until
 * wave 3 the card had its own English words («Even overall» beside «even»).
 *
 * 049 S2 (decision 3: the second row is «человек · активен», the state of the balance, the balance): the role words are
 * the STATE OF THE BALANCE only. A freeze and a hub are the participant's status and type - the meta beside them says them
 * already («хаб · заморожен»), so until S2 the row said «заморожен» / «хаб» twice (S1 internal adversarial pass). The meta
 * keeps the word because it is the participant's own data (type and status as the server gives them, `suspended` said
 * «приостановлен» as it is); the swatch keeps the freeze's colour and the canvas's sign - the card's link to the node.
 */
const roleWords = computed(() => t(`legend.entry.${look.value.role}`))

/**
 * 049 S1 (decision 3): the balance of the head, or null when it did not arrive - then no figure, no dash and no unit is
 * drawn, never a zero. A participant whose balance did not arrive at all (no sign, no amount: the role "unknown") says
 * ONE thing: the role's words become «Баланс не получен.» (049 S2: decision 8's text, with its period). Until S1 the head
 * said «неизвестно» beside «— UAH»: two half-messages of one absence (owner frame 18). The swatch stays - the card's link
 * to the node on the canvas (`?` there as well). A known sign without an amount keeps its role's words.
 */
const balanceText = computed(() => netText(props.node))
const roleText = computed(() => {
  if (balanceText.value !== null || look.value.role !== 'unknown') return roleWords.value
  return t('card.balanceUnavailable')
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
        : node.net_sign === 1
          ? s
          // 039 slice B (P-1): atoms are a MAGNITUDE and the sign was not given - printing them would show a
          // positive balance, that is a role. Unknown stays unknown.
          : null
  if (signed === null) return null

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
  return formatMoney(value, equivalentPrecision(rowEquivalent(tl)))
}

/**
 * The equivalent of a row: its own, else - a row without one (empty after trimming, as the line filter below reads it) -
 * the card's. One answer for the precision and the unit of the row (039 closing review 2026-10-20: a row of blanks was
 * listed after the filter moved to the shared semantics, and must not print its amounts without a unit).
 */
function rowEquivalent(tl: TrustlineInfo): string {
  return canonicalEquivalent(tl.equivalent) || canonicalEquivalent(props.equivalentText)
}

/**
 * 045 S3 round (Codex P2): what is available on a line, with its unit - the accessible description of the row's Edit button
 * and visible while the row holds the keyboard focus (the `title` of the row reaches a pointer only).
 */
function rowAvailableText(tl: TrustlineInfo): string {
  const unit = rowEquivalent(tl)
  const shown = renderOrDash(rowMoney(tl, tl.available))
  // 026 `T2602`: a negative `available` is the debt's excess over a lowered limit - its own sentence, not "available over
  // limit by" (046 wave 3, internal adversarial pass: the row title made that contradiction visible).
  if (isOverLimit(shown)) return t('card.lineOverLimit', { amount: shown.slice(1), unit }).trim()
  return t('card.lineAvailable', { amount: shown, unit }).trim()
}
const rowAvailableId = (tl: TrustlineInfo) => `nco-avail-${tl.from_pid}-${tl.to_pid}`.replace(/[^A-Za-z0-9_-]/g, '_')

// Ids are PRINTED in this card (a name falls back to the id), so they go through the display rule; keys and events keep the full id.
const showPid = useParticipantDisplayId()
const nodeName = computed(() => showPid(String(props.node.name ?? props.node.id)))
/**
 * 045 S3 round (Codex P2): the type and status of the participant - a visible meta line under the name. 046 wave 3: in the
 * words of the interface (the type in the legend's words, the status from its closed set; an unknown value as it came).
 */
const nodeMeta = computed(() => {
  const type = props.node.type ? participantTypeText(props.node.type, locale.value) : '—'
  const status = props.node.status ? participantStatusText(props.node.status, locale.value) : '—'
  return `${type} · ${status}`
})

/** 043-4: why Pay and New line cannot start now, or null when they can. */
const startBlockedReason = computed<string | null>(() => {
  if (props.interactBusy) return t('card.busyReason')
  const phase = String(props.interactPhase ?? 'idle').trim().toLowerCase()
  if (phase && phase !== 'idle') return t('card.flowOpenReason')
  return null
})

/**
 * Trustlines that involve this node (outgoing or incoming), in the equivalent the card is labelled with: a line of another
 * equivalent is never listed under this one (owner's principle: equivalents are not mixed). A row without its own
 * equivalent is the card's, and a card without one restricts nothing - the guards of this site; "without" is empty after
 * trimming (missing, '', blanks), as in `canonicalEquivalent`. The comparison is THE shared `sameEquivalent` (039 target
 * item 9; closing review 2026-10-20 replaced a local copy that judged a row of blanks unlike a row of '').
 */
const nodeTrustlines = computed<TrustlineInfo[]>(() => {
  if (!props.interactMode || !props.interactTrustlines?.length) return []
  const id = props.node.id
  const cardEq = canonicalEquivalent(props.equivalentText)
  return props.interactTrustlines.filter(
    (tl) => (tl.from_pid === id || tl.to_pid === id)
      && (!cardEq || !canonicalEquivalent(tl.equivalent) || sameEquivalent(tl.equivalent, cardEq)),
  )
})

/** OUT trustlines (from -> to is creditor -> debtor: the node is the creditor), sorted by used DESC */
const outTrustlines = computed<TrustlineInfo[]>(() =>
  nodeTrustlines.value
    .filter((tl) => tl.from_pid === props.node.id)
    .sort((a, b) => safeNum(b.used) - safeNum(a.used)),
)

/** IN trustlines (the node is the debtor), sorted by used DESC */
const inTrustlines = computed<TrustlineInfo[]>(() =>
  nodeTrustlines.value
    .filter((tl) => tl.to_pid === props.node.id)
    .sort((a, b) => safeNum(b.used) - safeNum(a.used)),
)
</script>

<template>
  <div class="ds-ov-node-card">
    <div class="ds-panel ds-panel--elevated">
      <div class="ds-panel__body ds-ov-node-card__body">

        <!-- Head, two rows (049 S1, decision 3; one line since 045 S3): the name and the two buttons; then the type and
             status, the role (or the balance state), the balance with its unit. The name is cut by an ellipsis, its whole
             text the title; nothing of the second row is cut - its groups wrap as wholes. -->
        <div class="ds-ov-node-card__identity">
          <div class="ds-ov-node-card__title-row">
            <span class="ds-ov-node-card__name" data-testid="node-card-name" :title="nodeName">{{ nodeName }}</span>
            <div class="ds-ov-node-card__service">
              <button
                v-if="showPinActions"
                class="ds-btn ds-btn--ghost ds-btn--icon ds-ov-node-card__action-btn"
                type="button"
                :aria-label="isPinned ? t('card.unpin') : t('card.pin')"
                :title="isPinned ? t('card.unpin') : t('card.pin')"
                @click="isPinned ? unpin() : pin()"
              >
                {{ isPinned ? '◆' : '◇' }}
              </button>
              <button
                class="ds-btn ds-btn--ghost ds-btn--icon ds-ov-node-card__action-btn"
                type="button"
                :aria-label="t('common.close')"
                :title="t('common.close')"
                aria-keyshortcuts="Escape"
                @click="emit('close')"
              >
                ×
              </button>
            </div>
          </div>
          <div class="ds-ov-node-card__facts">
            <span class="ds-ov-node-card__meta" data-testid="node-card-meta" :title="nodeMeta">{{ nodeMeta }}</span>
            <span class="nco-role" data-testid="node-card-role" :data-role="look.role" :title="roleText">
              <span
                class="ds-data-swatch"
                :class="look.frozen ? 'ds-data-swatch--frozen' : 'ds-data-swatch--role'"
                :style="{ backgroundColor: nodeColor, borderColor: nodeColor }"
                data-testid="node-card-role-swatch"
                aria-hidden="true"
              >{{ look.sign }}</span>
              <span class="nco-role__words">{{ roleText }}</span>
            </span>
            <span v-if="balanceText !== null" class="ds-ov-node-card__money"><span class="ds-ov-node-card__balance" :style="{ color: nodeColor }">{{ balanceText }}</span><template v-if="equivalentText">&nbsp;<span class="ds-ov-node-card__unit">{{ equivalentText }}</span></template></span>
          </div>
        </div>

        <!-- Statistics, one line: «Trusts others», «Trusted by» (the sums of the limits; were Out, In until 046 wave 3). 045 S4:
             no Degree - the group counters «Trusts (N)» / «Trusted by (N)» below already say how many lines there are, from
             the same source as the rows; a second number of the same fact from the snapshot was duplicate state. -->
        <div class="ds-ov-node-card__stats" data-testid="node-card-stats">
          <span class="ds-label" :title="t('card.trustsOthersHint')">{{ t('card.trustsOthers') }}</span>{{ ' ' }}<span class="ds-value ds-mono">{{ edgeStats?.outLimitText ?? '—' }}</span>{{ ' · ' }}<span
            class="ds-label" :title="t('card.trustedByHint')">{{ t('card.trustedBy') }}</span>{{ ' ' }}<span class="ds-value ds-mono">{{ edgeStats?.inLimitText ?? '—' }}</span>
        </div>

        <!-- BUG-1: Interact Mode Trustlines section --------------------------->
        <template v-if="interactMode">
          <!-- Quick-action buttons; 043-4: disabled with the reason on a line while they cannot start. -->
          <div class="nco-interact-actions">
            <button
              class="ds-btn ds-btn--primary ds-btn--sm"
              type="button"
              :disabled="!!startBlockedReason"
              :title="startBlockedReason ?? undefined"
              :aria-describedby="startBlockedReason ? 'node-card-actions-reason' : undefined"
              data-testid="node-card-send-payment"
              @click="onInteractSendPayment?.(node.id)"
            >
              {{ t('card.pay') }}
            </button>
            <button
              class="ds-btn ds-btn--secondary ds-btn--sm"
              type="button"
              :disabled="!!startBlockedReason"
              :title="startBlockedReason ?? undefined"
              :aria-describedby="startBlockedReason ? 'node-card-actions-reason' : undefined"
              data-testid="node-card-new-trustline"
              @click="onInteractNewTrustline?.(node.id)"
            >
              {{ t('card.newLine') }}
            </button>
          </div>
          <div v-if="startBlockedReason" id="node-card-actions-reason" class="ds-help" data-testid="node-card-actions-reason">
            {{ startBlockedReason }}
          </div>

          <!-- Trustlines list: grouped OUT / IN; at most five rows on screen, then it scrolls. -->
          <div v-if="nodeTrustlines.length > 0" class="nco-trustlines">

            <template v-if="outTrustlines.length">
              <div class="nco-tl-group-header" data-testid="node-card-group-out">{{ t('card.trustsGroup', { count: outTrustlines.length }) }}</div>
              <div
                v-for="tl in outTrustlines"
                :key="`${tl.from_pid}→${tl.to_pid}`"
                :class="[
                  'nco-trustline-row',
                  { 'nco-trustline-row--saturated': isSaturatedAvailable(tl.available) },
                ]"
                :title="rowAvailableText(tl)"
              >
                <span class="nco-trustline-row__peer" :title="showPid(tl.to_name)">{{ showPid(tl.to_name) }}</span>
                <span class="nco-trustline-row__amounts ds-mono">{{ rowMoney(tl, tl.used) }}&thinsp;/&thinsp;{{ rowMoney(tl, tl.limit) }}</span>
                <span :id="rowAvailableId(tl)" class="nco-trustline-row__avail-desc" data-testid="node-card-line-available">{{ rowAvailableText(tl) }}</span>
                <button
                  class="ds-btn ds-btn--ghost nco-trustline-row__edit"
                  type="button"
                  :aria-describedby="rowAvailableId(tl)"
                  :disabled="!!interactBusy"
                  :title="t('card.editLine')"
                  :aria-label="t('card.editLine')"
                  @click="onInteractEditTrustline?.(tl.from_pid, tl.to_pid)"
                >{{ t('card.edit') }}</button>
              </div>
            </template>

            <template v-if="inTrustlines.length">
              <div class="nco-tl-group-header" data-testid="node-card-group-in">{{ t('card.trustedByGroup', { count: inTrustlines.length }) }}</div>
              <div
                v-for="tl in inTrustlines"
                :key="`${tl.from_pid}→${tl.to_pid}`"
                :class="[
                  'nco-trustline-row',
                  { 'nco-trustline-row--saturated': isSaturatedAvailable(tl.available) },
                ]"
                :title="rowAvailableText(tl)"
              >
                <span class="nco-trustline-row__peer" :title="showPid(tl.from_name)">{{ showPid(tl.from_name) }}</span>
                <span class="nco-trustline-row__amounts ds-mono">{{ rowMoney(tl, tl.used) }}&thinsp;/&thinsp;{{ rowMoney(tl, tl.limit) }}</span>
                <span :id="rowAvailableId(tl)" class="nco-trustline-row__avail-desc" data-testid="node-card-line-available">{{ rowAvailableText(tl) }}</span>
                <button
                  class="ds-btn ds-btn--ghost nco-trustline-row__edit"
                  type="button"
                  :aria-describedby="rowAvailableId(tl)"
                  :disabled="!!interactBusy"
                  :title="t('card.editLineFrom', { name: showPid(tl.from_name || tl.from_pid) })"
                  :aria-label="t('card.editLineFrom', { name: showPid(tl.from_name || tl.from_pid) })"
                  @click="onInteractEditTrustline?.(tl.from_pid, tl.to_pid)"
                >{{ t('card.edit') }}</button>
              </div>
            </template>

          </div>
          <div v-else-if="trustlinesLoading" class="nco-trustlines__empty ds-help">
            {{ t('card.loading') }}
          </div>
          <div v-else class="nco-trustlines__empty ds-help">
            {{ t('card.none') }}
          </div>
        </template>

      </div>
    </div>
  </div>
</template>

<style scoped>
/*
  045 S3 (decision 3): the card on the one scale of the windows - the head and the statistics are one line each, a row of a
  line is 24 px (the smallest pointer target the stand allows, `surfaces.qa.ts`; the spec's 20 px would put the Edit button
  under it), at most five rows are on screen and the list scrolls. Sizes are the tokens of the compact density.
*/
.nco-interact-actions {
  display: flex;
  gap: var(--ds-space-2);
}

.nco-interact-actions > .ds-btn {
  flex: 1 1 0;
}

/* 039 slice B: the role - the swatch the legend shows, and the same words. 049 S1: in the head's second row. */
.nco-role {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  /* Not the muted colour of the meta line: the role is information, and `--ds-text-3` fails contrast in a theme. */
  color: var(--ds-text-2);
  font-size: var(--ds-typo-label-font-size);
  white-space: nowrap;
}

/* 049 S1: the second row is not cut - the role keeps its words whole; only a single group wider than the whole card (an
   unknown status as it came) is cut, and its title holds it. */
.nco-role__words {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* Trustlines list: five rows of 24 px and one group heading of 16 px on screen, then it scrolls. */
.nco-trustlines {
  max-height: calc(5 * 24px + 16px);
  overflow-y: auto;
}

/* Group heading: «Trusts (N)» / «Trusted by (N)» (meta size). */
.nco-tl-group-header {
  font-size: var(--ds-typo-section-label-font-size);
  line-height: 16px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--ds-text-3);
}

/* Row: [peer] [used / limit] [Edit]; what is available is in the row's `title`, and in a line of its own while the row
   holds the keyboard focus (045 S3 round, Codex P2); otherwise that line is visually hidden but still describes Edit. */
.nco-trustline-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  column-gap: var(--ds-space-2);
  min-height: 24px;
  font-size: var(--ds-typo-label-font-size);
}

/* NC-3: "available <= 0" should be visually emphasized (border only). */
.nco-trustline-row--saturated {
  border-left: 2px solid var(--ds-err);
  padding-left: 4px;
}

.nco-trustline-row__peer {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nco-trustline-row__amounts {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.nco-trustline-row {
  position: relative;
}

.nco-trustline-row__avail-desc {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

.nco-trustline-row:focus-within .nco-trustline-row__avail-desc {
  position: static;
  width: auto;
  height: auto;
  overflow: visible;
  clip: auto;
  clip-path: none;
  grid-column: 1 / -1;
  grid-row: 2;
  font-size: var(--ds-typo-section-label-font-size);
  color: var(--ds-text-2);
}

.nco-trustline-row__edit {
  --ds-button-height: 24px;
  --ds-button-padding-x: 6px;
  min-width: 24px;
  font-size: var(--ds-typo-section-label-font-size);
}

.nco-trustlines__empty {
  font-style: italic;
}
</style>
