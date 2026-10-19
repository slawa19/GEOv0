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
 * 039 slice D: "was -> now" of the total debt, as ONE dictionary sentence whose two figures keep elements of their own
 * (`total-debt-before`, `total-debt-after`). `null` - the total alone. When the pair is shown and what it means is
 * `useSystemBalance` / `debtChange.ts`; here only the words and the two fields.
 */
const debtChangeParts = computed(() => (b.value?.debtBefore == null ? null : templateParts('systemBalance.debtChange')))
</script>

<template>
  <div
    class="system-balance-bar"
    :class="{ 'system-balance-bar--compact': shown === 'debt' }"
    :aria-label="shown === 'metrics' ? t('systemBalance.metricsAria') : t('systemBalance.balanceAria')"
    :data-testid="shown === 'metrics' ? 'system-metrics' : 'system-balance'"
  >
    <HudBar variant="ghost" layout="start" fit class="system-balance-bar__inner">
      <div v-if="showDebt" class="ds-panel ds-ov-metric" data-testid="sbb-total-debt">
        <span class="ds-label">{{ t('systemBalance.totalDebt') }}</span>
        <span v-if="debtChangeParts" class="sbb-change" data-testid="total-debt-change">
          <template v-for="(part, i) in debtChangeParts" :key="i">
            <span v-if="'text' in part" class="sbb-change__word">{{ part.text }}</span>
            <span v-else-if="part.name === 'before'" class="sbb-change__before ds-mono" data-testid="total-debt-before">{{ money(b.debtBefore!) }}</span>
            <span v-else-if="part.name === 'after'" class="ds-value ds-mono" data-testid="total-debt-after">{{ money(b.totalUsed) }} {{ unit }}</span>
          </template>
        </span>
        <span v-else class="ds-value ds-mono">{{ money(b.totalUsed) }} {{ unit }}</span>
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
/* 039 slice D: the words of "was -> now" are quieter than the figures; the pair stays on one line. */
.sbb-change {
  white-space: nowrap;
}

.sbb-change__word {
  opacity: 0.7;
  white-space: pre;
}

.sbb-change__before {
  opacity: 0.85;
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

