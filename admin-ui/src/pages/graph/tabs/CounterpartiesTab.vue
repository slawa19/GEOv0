<template>
  <el-alert
    v-if="!analyticsEq"
    :title="t('graph.analytics.counterparties.pickEquivalentTitle')"
    type="info"
    show-icon
    class="mb"
  />

  <div v-else>
    <div class="splitGrid">
      <el-card shadow="never">
        <template #header>
          <TooltipLabel
            :label="t('graph.analytics.counterparties.topCreditorsYouOwe')"
            :tooltip-text="t('graph.analytics.counterparties.topCreditorsTooltip')"
          />
        </template>
        <el-empty
          v-if="selectedCounterpartySplit.creditors.length === 0"
          :description="t('graph.analytics.counterparties.noCreditors')"
        />
        <el-table
          v-else
          :data="selectedCounterpartySplit.creditors.slice(0, 10)"
          size="small"
          table-layout="fixed"
          class="geoTable"
        >
          <el-table-column
            prop="display_name"
            :label="t('graph.analytics.common.participant')"
            min-width="220"
          />
          <el-table-column
            prop="amount"
            :label="t('graph.analytics.common.amount')"
            min-width="120"
          >
            <template #default="{ row }">
              {{ money(row.amount) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="share"
            :label="t('graph.analytics.common.share')"
            min-width="140"
          >
            <template #default="{ row }">
              <div class="shareCell">
                <el-progress
                  :percentage="Math.round((row.share || 0) * 100)"
                  :stroke-width="10"
                  :show-text="false"
                />
                <span class="muted">{{ pct(row.share, 0) }}</span>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <TooltipLabel
            :label="t('graph.analytics.counterparties.topDebtorsOwedToYou')"
            :tooltip-text="t('graph.analytics.counterparties.topDebtorsTooltip')"
          />
        </template>
        <el-empty
          v-if="selectedCounterpartySplit.debtors.length === 0"
          :description="t('graph.analytics.counterparties.noDebtors')"
        />
        <el-table
          v-else
          :data="selectedCounterpartySplit.debtors.slice(0, 10)"
          size="small"
          table-layout="fixed"
          class="geoTable"
        >
          <el-table-column
            prop="display_name"
            :label="t('graph.analytics.common.participant')"
            min-width="220"
          />
          <el-table-column
            prop="amount"
            :label="t('graph.analytics.common.amount')"
            min-width="120"
          >
            <template #default="{ row }">
              {{ money(row.amount) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="share"
            :label="t('graph.analytics.common.share')"
            min-width="140"
          >
            <template #default="{ row }">
              <div class="shareCell">
                <el-progress
                  :percentage="Math.round((row.share || 0) * 100)"
                  :stroke-width="10"
                  :show-text="false"
                />
                <span class="muted">{{ pct(row.share, 0) }}</span>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import TooltipLabel from '../../../ui/TooltipLabel.vue'
import { t } from '../../../i18n'

type CounterpartyRow = {
  display_name: string
  amount: string
  share: number
}

type CounterpartySplit = {
  creditors: CounterpartyRow[]
  debtors: CounterpartyRow[]
}

defineProps<{
  analyticsEq: boolean
  selectedCounterpartySplit: CounterpartySplit
  money: (value: string) => string
  pct: (value: number, digits?: number) => string
}>()
</script>
