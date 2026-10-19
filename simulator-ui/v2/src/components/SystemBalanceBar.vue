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
 * 039 slice D: the total debt just before the last debt-changing event ("was"), as ONE dictionary sentence whose figure
 * keeps an element of its own (`total-debt-before`); the current total ("now") is the bar's figure (`total-debt-after`
 * while a "was" is shown). `null` - the total alone. When it is shown and what it means is `useSystemBalance` /
 * `debtChange.ts`; here only the words and the fields.
 *
 * The "was" sits ABOVE the bar, out of its flow: written inline it widened the row by 95-115 px and the main row of the
 * bottom bar wrapped to a second line in Russian at 1366x768 (42 -> 80 px, measured 2026-10-19) - the canvas would
 * have jumped with every payment. Out of the flow the bar keeps its size with and without a "was".
 */
const debtBeforeParts = computed(() => (b.value?.debtBefore == null ? null : templateParts('systemBalance.debtBefore')))
</script>

<template>
  <div
    class="system-balance-bar"
    :class="{ 'system-balance-bar--compact': shown === 'debt' }"
    :aria-label="shown === 'metrics' ? t('systemBalance.metricsAria') : t('systemBalance.balanceAria')"
    :data-testid="shown === 'metrics' ? 'system-metrics' : 'system-balance'"
  >
    <div v-if="showDebt && debtBeforeParts" class="ds-panel sbb-was" data-testid="total-debt-change">
      <template v-for="(part, i) in debtBeforeParts" :key="i">
        <span v-if="'text' in part" class="sbb-was__word">{{ part.text }}</span>
        <span v-else-if="part.name === 'before'" class="ds-mono" data-testid="total-debt-before">{{ money(b.debtBefore!) }}</span>
        <span v-else-if="part.name === 'unit'" class="ds-mono">{{ unit }}</span>
      </template>
    </div>
    <HudBar variant="ghost" layout="start" fit class="system-balance-bar__inner">
      <div v-if="showDebt" class="ds-panel ds-ov-metric" data-testid="sbb-total-debt">
        <span class="ds-label">{{ t('systemBalance.totalDebt') }}</span>
        <span class="ds-value ds-mono" :data-testid="debtBeforeParts ? 'total-debt-after' : undefined">{{ money(b.totalUsed) }} {{ unit }}</span>
      </div>

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
/* 039 slice D: the "was" of the total debt, right-aligned just above the bar and out of its flow (see the script). */
.system-balance-bar {
  position: relative;
}

.sbb-was {
  position: absolute;
  right: 0;
  bottom: calc(100% + 2px);
  padding: 1px 8px;
  white-space: nowrap;
  font-size: 0.85em;
  opacity: 0.85;
  pointer-events: none;
}

.sbb-was__word {
  white-space: pre;
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

