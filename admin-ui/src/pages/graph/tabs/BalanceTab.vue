<template>
  <div class="hint">
    {{ t('graph.hint.balanceDerivedFromTrustlinesDebts') }}
  </div>

  <el-alert
    v-if="!analyticsEq"
    :title="t('graph.analytics.balance.pickEquivalentTitle')"
    :description="t('graph.analytics.balance.pickEquivalentDescription')"
    type="info"
    show-icon
    class="mb"
  />

  <GraphAnalyticsTogglesCard
    v-if="analyticsEq"
    v-model="analytics"
    :title="t('graph.analytics.balance.widgetsTitle')"
    :title-tooltip-text="t('graph.analytics.balance.widgetsTooltip')"
    :enabled="analyticsEq"
    :items="balanceToggleItems"
  />

  <el-card
    v-if="analytics.showRank && selectedRank"
    shadow="never"
    class="mb"
  >
    <template #header>
      <TooltipLabel
        :label="t('graph.analytics.balance.rankPercentileWithEq', { eq: selectedRank.eq })"
        :tooltip-text="t('graph.analytics.balance.rankPercentileTooltip')"
      />
    </template>
    <div class="kpi">
      <div class="kpi__value">
        {{ t('graph.analytics.common.rank') }} {{ selectedRank.rank }}/{{ selectedRank.n }}
      </div>
      <el-progress
        :percentage="Math.round((selectedRank.percentile || 0) * 100)"
        :stroke-width="10"
        :show-text="false"
      />
      <div class="kpi__hint muted">
        {{ t('graph.analytics.common.percentile') }}: {{ pct(selectedRank.percentile, 0) }}
      </div>
    </div>
  </el-card>

  <el-card
    v-if="analytics.showDistribution && netDistribution"
    shadow="never"
    class="mb"
  >
    <template #header>
      <TooltipLabel
        :label="t('graph.analytics.balance.distributionWithEq', { eq: netDistribution.eq })"
        :tooltip-text="t('graph.analytics.balance.distributionTooltip')"
      />
    </template>
    <div class="hist">
      <div
        v-for="(b, i) in netDistribution.bins"
        :key="i"
        class="hist__bar"
        :style="{ height: ((b.count / Math.max(1, Math.max(...netDistribution.bins.map(x => x.count)))) * 48).toFixed(0) + 'px' }"
        :title="String(b.count)"
      />
    </div>
    <div class="hist__labels muted">
      <span>{{ money(atomsToDecimal(netDistribution.min, precisionByEq.get(netDistribution.eq) ?? 2)) }}</span>
      <span>{{ money(atomsToDecimal(netDistribution.max, precisionByEq.get(netDistribution.eq) ?? 2)) }}</span>
    </div>
  </el-card>

  <el-empty
    v-if="selectedBalanceRows.length === 0"
    :description="t('common.noData')"
  />
  <el-table
    v-else
    :data="selectedBalanceRows"
    size="small"
    table-layout="fixed"
    class="geoTable"
  >
    <el-table-column
      prop="equivalent"
      :label="t('trustlines.equivalent')"
      width="120"
    />
    <el-table-column
      prop="outgoing_limit"
      :label="t('graph.analytics.balance.columns.outLimit')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.outgoing_limit) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="outgoing_used"
      :label="t('graph.analytics.balance.columns.outUsed')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.outgoing_used) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="incoming_limit"
      :label="t('graph.analytics.balance.columns.inLimit')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.incoming_limit) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="incoming_used"
      :label="t('graph.analytics.balance.columns.inUsed')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.incoming_used) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="total_debt"
      :label="t('graph.analytics.balance.columns.debt')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.total_debt) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="total_credit"
      :label="t('graph.analytics.balance.columns.credit')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.total_credit) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="net"
      :label="t('graph.analytics.balance.columns.net')"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.net) }}
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
import GraphAnalyticsTogglesCard from '../../../ui/GraphAnalyticsTogglesCard.vue'
import type { ToggleKey } from '../../../ui/GraphAnalyticsTogglesCard.vue'
import TooltipLabel from '../../../ui/TooltipLabel.vue'
import { t } from '../../../i18n'

type AnalyticsModel = Record<ToggleKey, boolean>

type ToggleItem = {
  key: ToggleKey
  label: string
  tooltipText: string
  requires?: ToggleKey
}

type SelectedRank = {
  net: string
  eq: string
  rank: number
  n: number
  percentile: number
}

type NetDistribution = {
  eq: string
  bins: Array<{ count: number }>
  min: bigint
  max: bigint
}

type BalanceRow = {
  equivalent: string
  outgoing_limit: string
  outgoing_used: string
  incoming_limit: string
  incoming_used: string
  total_debt: string
  total_credit: string
  net: string
}

defineProps<{
  analyticsEq: boolean
  balanceToggleItems: ToggleItem[]
  selectedRank: SelectedRank | null
  netDistribution: NetDistribution | null
  precisionByEq: Map<string, number>
  selectedBalanceRows: BalanceRow[]
  atomsToDecimal: (atoms: bigint, precision: number) => string
  money: (value: string) => string
  pct: (value: number, digits?: number) => string
}>()

const analytics = defineModel<AnalyticsModel>('analytics', { required: true })
</script>
