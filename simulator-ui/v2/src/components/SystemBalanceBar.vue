<script setup lang="ts">
import { computed, unref, type ComputedRef } from 'vue'

import type { SystemBalance } from '../composables/useSystemBalance'
import { useUiLocale } from '../composables/useUiLocale'
import { equivalentPrecision } from '../config/equivalentPrecision'
import { templateParts } from '../i18n/t'
import { formatMoney } from '../utils/money'
import HudBar from './common/HudBar.vue'

type Props = {
  /** Accepts either a raw SystemBalance object or a computed ref (as returned by useSystemBalance(...).balance). */
  balance: SystemBalance | ComputedRef<SystemBalance>
  /** Fallback only: the unit is the balance's own equivalent (028 F-028-47, B6). */
  equivalent?: string
  /** Legacy alias of `variant: 'debt'`. */
  compact?: boolean
  /**
   * 043 S1. `debt`: the total debt alone - the first view's bottom row. `metrics`: everything else (capacity, trust
   * lines, participants, utilization) - the row of "Подробнее", beside the total debt that stays in the bottom row.
   * `full` (default): all of it. 048 S1 (decision 4): no "Clean" badge - it repeated the total debt of 0.00.
   */
  variant?: 'full' | 'debt' | 'metrics'
}

const props = defineProps<Props>()
const { t } = useUiLocale()

const shown = computed<'full' | 'debt' | 'metrics'>(() => props.variant ?? (props.compact ? 'debt' : 'full'))
const showDebt = computed(() => shown.value !== 'metrics')
const showMetrics = computed(() => shown.value !== 'debt')

const b = computed<SystemBalance>(() => unref(props.balance))

const utilPct = computed(() => {
  const u = Number(b.value?.utilization ?? 0)
  if (!Number.isFinite(u)) return 0
  return Math.max(0, Math.min(100, Math.round(u * 100)))
})

const unit = computed(() => b.value?.equivalent || props.equivalent || '')
// 028 F-028-48: money at the equivalent's precision (was: a float with 0 fraction digits).
const money = (v: string) => formatMoney(v, equivalentPrecision(unit.value))

/**
 * 039 slice D: the "was -> now" of the last clearing, as ONE dictionary sentence whose two figures keep elements of
 * their own (`total-debt-before`, `total-debt-after`); `null` - the total alone. Only a clearing states it (advisor's
 * decision 2026-10-19, option (a)), and its "now" is the clearing's own after-total: payments after it move the bar's
 * figure, not the sentence. When it is shown and what it means is `useSystemBalance` / `debtChange.ts`; here only the
 * words and the fields.
 *
 * The sentence sits ABOVE the bar, out of its flow: written inline the first build widened the row by 95-115 px and
 * the main row of the bottom bar wrapped to a second line in Russian at 1366x768 (42 -> 80 px, measured 2026-10-19).
 * Out of the flow the bar keeps its size with and without it. Above the WHOLE bottom bar, not above this panel (039
 * D-fix, see the style).
 *
 * 049 S1 (decision 1, the advisor's refinement 2026-10-20, option B): the total debt joins it - one column above the right
 * end of the bar, on the control line, every row right-aligned (see the style). A whitespace part right before the unit
 * is a no-break space: the phrase may wrap between its parts, never between an amount and its unit (decision 9).
 */
const NBSP = '\u00a0'
const clearingParts = computed(() => {
  if (b.value?.clearingChange == null) return null
  const parts = templateParts('systemBalance.clearingChange')
  return parts.map((part, i) => {
    const next = parts[i + 1]
    return 'text' in part && next && !('text' in next) && next.name === 'unit' && part.text.trim() === '' ? { text: NBSP } : part
  })
})
</script>

<template>
  <div
    class="system-balance-bar"
    :class="{ 'system-balance-bar--compact': shown === 'debt', 'sbb-edge': shown === 'debt' }"
    :aria-label="shown === 'metrics' ? t('systemBalance.metricsAria') : t('systemBalance.balanceAria')"
    :data-testid="shown === 'metrics' ? 'system-metrics' : 'system-balance'"
  >
    <!-- 049 S1 (decision 1, option B): the clearing's "was -> now" and the total debt - in the bottom row (`debt`) this bar
         IS the right-aligned column above the right end of the bottom bar, out of its flow; "Показать всю сеть" stays in the
         bar under it. -->
    <template v-if="showDebt">
      <div v-if="clearingParts && b.clearingChange" class="ds-panel sbb-was" data-testid="total-debt-change">
        <template v-for="(part, i) in clearingParts" :key="i">
          <span v-if="'text' in part" class="sbb-was__word">{{ part.text }}</span>
          <span v-else-if="part.name === 'before'" class="ds-mono sbb-nowrap" data-testid="total-debt-before">{{ money(b.clearingChange.before) }}</span>
          <span v-else-if="part.name === 'after'" class="ds-mono sbb-nowrap" data-testid="total-debt-after">{{ money(b.clearingChange.after) }}</span>
          <span v-else-if="part.name === 'unit'" class="ds-mono sbb-nowrap">{{ unit }}</span>
        </template>
      </div>
      <div class="ds-panel ds-ov-metric sbb-debt" data-testid="sbb-total-debt">
        <span class="ds-label">{{ t('systemBalance.totalDebt') }}</span>
        <span class="ds-value ds-mono sbb-nowrap">{{ money(b.totalUsed) }} {{ unit }}</span>
      </div>
    </template>
    <HudBar v-if="showMetrics" variant="ghost" layout="start" fit class="system-balance-bar__inner">
      <div v-if="showMetrics" class="ds-panel ds-ov-metric" data-testid="sbb-available">
        <span class="ds-label">{{ t('systemBalance.available') }}</span>
        <span class="ds-value ds-mono">{{ money(b.totalAvailable) }} {{ unit }}</span>
      </div>

      <div v-if="showMetrics" class="ds-panel ds-ov-metric" data-testid="sbb-trustlines">
        <span class="ds-label">{{ t('systemBalance.trustlines') }}</span>
        <span class="ds-value ds-mono">{{ b.activeTrustlines }}</span>
      </div>

      <div v-if="showMetrics" class="ds-panel ds-ov-metric" data-testid="sbb-participants">
        <span class="ds-label">{{ t('systemBalance.participants') }}</span>
        <span class="ds-value ds-mono">{{ b.activeParticipants }}</span>
      </div>

      <div v-if="showMetrics" class="ds-panel ds-ov-metric sbb-util" data-testid="sbb-utilization">
        <span class="ds-label">{{ t('systemBalance.utilization') }}</span>
        <span class="ds-value ds-mono">{{ utilPct }}%</span>
        <div class="ds-progress__track sbb-track">
          <div class="ds-progress__bar" :style="{ width: utilPct + '%' }" />
        </div>
      </div>
    </HudBar>
  </div>
</template>

<style scoped>
/*
  049 S1 (decision 1; the advisor's refinement 2026-10-20, option B): the bottom-right edge group is ONE column - the last
  clearing's "was -> now" over the total debt - above the right end of the bottom bar, its right edge on the CONTROL
  LINE (safe-area + `--ds-ov-inset` + the HudBar's padding `--ds-hud-bar-px`: the same line "Показать всю сеть" ends on,
  inside the bar's main row, and the top bar's controls). Every row is right-aligned and the column is as wide as its
  widest row: a longer total grows the plate to the left, the edge stays. Out of the bar's flow: with the plate inside
  the row, the plate and the button one above the other made the bar 75 px and the canvas 81 % of 1366x768, under the
  85 % floor of 043 R9 (measured 2026-10-20); beside each other they had two right edges (F-049-1).
  039 slice D / D-fix: above the WHOLE bottom bar (`.ds-ov-bottom`, its containing block: nothing between it and this
  column is positioned), so nothing of the bar is under it - the legend when the main row wraps, the service row when
  "Подробнее" is on. `z-index: -1` (in the bar's own stacking context): a popover the bar opens upwards ("Inspect
  graph") covers the column rather than the reverse. Not a target: a click there reaches the canvas.
  The stand holds it (`e2e/qa/debt-before-after.qa.ts`, "the pair fits": the edges on the line at 49745.90 and
  1049745.90, no neighbour and no node of the graph under the column, both themes and languages, 1366x768 and 1024x640).
*/
.sbb-edge {
  position: absolute;
  z-index: -1;
  right: var(--ds-hud-bar-px, var(--ds-space-3, 12px));
  bottom: calc(100% + 2px);
  /* A quarter of the screen at most: the clearing's phrase stays on one line at 1366 px and wraps between its parts at
     1024 px (decision 1), so the column does not reach into the graph from the corner. */
  max-width: min(25vw, 100% - 2 * var(--ds-hud-bar-px, var(--ds-space-3, 12px)));
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--ds-ov-stack-gap, 3px);
  pointer-events: none;
}

/*
  With "Подробнее" on, "Inspect graph" stands in the bar's right corner ABOVE the bar: its 42 px button rises from a
  zero-height place in the middle of the 38 px service row (048 S1, `BottomBar.vue` `.bb-navigator`), 23 px over the
  bar's top edge (measured 2026-10-19 at 1366x768 and 1024x640, both themes). The column clears it with a margin.
*/
.ds-ov-bottom:has(.bb-navigator) .sbb-edge {
  bottom: calc(100% + 2px + 2rem);
}

.sbb-was {
  padding: 1px 8px;
  max-width: 100%;
  text-align: right;
  font-size: 0.85em;
  opacity: 0.85;
}

/* Spaces of the phrase are kept; it wraps between its parts (049 decision 1, at narrow widths), never inside a figure. */
.sbb-was__word {
  white-space: pre-wrap;
}

.sbb-nowrap {
  white-space: nowrap;
}

.sbb-util {
  gap: var(--ds-space-3, 12px);
}

.sbb-track {
  width: var(--ds-sbb-track-width);
  height: var(--ds-sbb-track-height);
}

.ds-progress__bar {
  transition:
    width var(--ds-sbb-progress-dur) var(--ds-sbb-progress-ease),
    background-color var(--ds-sbb-progress-dur) var(--ds-sbb-progress-ease),
    opacity var(--ds-sbb-progress-dur) var(--ds-sbb-progress-ease);
}
</style>

