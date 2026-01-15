<template>
  <div class="hint">
    Derived from trustlines + debts fixtures (debts are derived from trustline.used).
  </div>

  <el-alert
    v-if="!analyticsEq"
    title="Pick an equivalent (not ALL) to enable analytics cards"
    description="With ALL selected, the Balance table still works, but Rank/Distribution/Counterparty/Risk visualizations are hidden because they are per-equivalent."
    type="info"
    show-icon
    class="mb"
  />

  <GraphAnalyticsTogglesCard
    v-if="analyticsEq"
    v-model="analytics"
    title="Balance widgets"
    title-tooltip-text="Show/hide balance visualizations. These toggles are stored in localStorage for this browser."
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
        :label="`Rank / percentile (${selectedRank.eq})`"
        tooltip-text="Rank is 1..N by net balance; percentile is normalized to 0..100 where 100% is the top net creditor."
      />
    </template>
    <div class="kpi">
      <div class="kpi__value">
        rank {{ selectedRank.rank }}/{{ selectedRank.n }}
      </div>
      <el-progress
        :percentage="Math.round((selectedRank.percentile || 0) * 100)"
        :stroke-width="10"
        :show-text="false"
      />
      <div class="kpi__hint muted">
        Percentile: {{ pct(selectedRank.percentile, 0) }}
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
        :label="`Distribution (${netDistribution.eq})`"
        tooltip-text="Histogram of net balances across all participants for the selected equivalent."
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
    description="No data"
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
      label="Equivalent"
      width="120"
    />
    <el-table-column
      prop="outgoing_limit"
      label="Out limit"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.outgoing_limit) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="outgoing_used"
      label="Out used"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.outgoing_used) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="incoming_limit"
      label="In limit"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.incoming_limit) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="incoming_used"
      label="In used"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.incoming_used) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="total_debt"
      label="Debt"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.total_debt) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="total_credit"
      label="Credit"
      min-width="120"
    >
      <template #default="{ row }">
        {{ money(row.total_credit) }}
      </template>
    </el-table-column>
    <el-table-column
      prop="net"
      label="Net"
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
