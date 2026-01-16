<template>
  <el-alert
    v-if="!analyticsEq"
    :title="t('graph.analytics.risk.pickEquivalentTitle')"
    type="info"
    show-icon
    class="mb"
  />

  <GraphAnalyticsTogglesCard
    v-if="analyticsEq"
    v-model="analytics"
    :title="t('graph.analytics.risk.widgetsTitle')"
    :title-tooltip-text="t('graph.analytics.risk.widgetsTooltip')"
    :enabled="analyticsEq"
    :items="riskToggleItems"
  />

  <el-card
    v-if="analyticsEq && analytics.showConcentration"
    shadow="never"
    class="mb"
  >
    <template #header>
      <TooltipLabel
        :label="t('graph.analytics.risk.counterpartyConcentration.title')"
        :tooltip-text="t('graph.analytics.risk.counterpartyConcentration.tooltip')"
      />
    </template>

    <div
      v-if="selectedConcentration.eq"
      class="kpi"
    >
      <div class="kpi__row">
        <span class="geoLabel">{{ t('graph.common.outgoingYouOwe') }}</span>
        <el-tag
          :type="selectedConcentration.outgoing.level.type"
          size="small"
        >
          {{ selectedConcentration.outgoing.level.label }}
        </el-tag>
      </div>
      <div class="metricRows">
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            :label="t('graph.analytics.concentration.top1.label')"
            :tooltip-text="t('graph.analytics.concentration.top1.outgoing.tooltip')"
          />
          <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top1, 0) }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            :label="t('graph.analytics.concentration.top5.label')"
            :tooltip-text="t('graph.analytics.concentration.top5.outgoing.tooltip')"
          />
          <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top5, 0) }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            :label="t('graph.analytics.concentration.hhi.label')"
            :tooltip-text="t('graph.analytics.concentration.hhi.tooltip')"
          />
          <span class="metricRow__value">{{ selectedConcentration.outgoing.hhi.toFixed(2) }}</span>
        </div>
      </div>

      <div
        class="kpi__row"
        style="margin-top: 10px"
      >
        <span class="geoLabel">{{ t('graph.common.incomingOwedToYou') }}</span>
        <el-tag
          :type="selectedConcentration.incoming.level.type"
          size="small"
        >
          {{ selectedConcentration.incoming.level.label }}
        </el-tag>
      </div>
      <div class="metricRows">
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            :label="t('graph.analytics.concentration.top1.label')"
            :tooltip-text="t('graph.analytics.concentration.top1.incoming.tooltip')"
          />
          <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top1, 0) }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            :label="t('graph.analytics.concentration.top5.label')"
            :tooltip-text="t('graph.analytics.concentration.top5.incoming.tooltip')"
          />
          <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top5, 0) }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            :label="t('graph.analytics.concentration.hhi.label')"
            :tooltip-text="t('graph.analytics.concentration.hhi.tooltip')"
          />
          <span class="metricRow__value">{{ selectedConcentration.incoming.hhi.toFixed(2) }}</span>
        </div>
      </div>
    </div>

    <div
      v-else
      class="muted"
    >
      {{ t('common.noData') }}
    </div>
  </el-card>

  <el-card
    v-if="analyticsEq && analytics.showCapacity && selectedCapacity"
    shadow="never"
    class="mb"
  >
    <template #header>
      <TooltipLabel
        :label="t('graph.analytics.risk.trustlineCapacity.title')"
        :tooltip-text="t('graph.analytics.risk.trustlineCapacity.tooltip')"
      />
    </template>

    <div class="capRow">
      <div class="capRow__label">
        <TooltipLabel
          :label="t('graph.analytics.capacity.outgoingUsed')"
          :tooltip-text="t('graph.analytics.risk.trustlineCapacity.outgoingUsedTooltip')"
        />
      </div>
      <el-progress
        :percentage="Math.round((selectedCapacity.out.pct || 0) * 100)"
        :stroke-width="10"
        :show-text="false"
      />
      <div class="capRow__value">
        {{ pct(selectedCapacity.out.pct, 0) }}
      </div>
    </div>

    <div class="capRow">
      <div class="capRow__label">
        <TooltipLabel
          :label="t('graph.analytics.capacity.incomingUsed')"
          :tooltip-text="t('graph.analytics.risk.trustlineCapacity.incomingUsedTooltip')"
        />
      </div>
      <el-progress
        :percentage="Math.round((selectedCapacity.inc.pct || 0) * 100)"
        :stroke-width="10"
        :show-text="false"
      />
      <div class="capRow__value">
        {{ pct(selectedCapacity.inc.pct, 0) }}
      </div>
    </div>

    <div
      v-if="analytics.showBottlenecks"
      class="mb"
      style="margin-top: 10px"
    >
      <el-tag
        type="info"
        size="small"
      >
        <TooltipLabel
          :label="t('graph.analytics.risk.bottlenecksCount', { n: selectedCapacity.bottlenecks.length })"
          :tooltip-text="t('graph.analytics.risk.bottlenecksTooltip')"
        />
      </el-tag>
      <span
        class="muted"
        style="margin-left: 8px"
      >{{ t('graph.analytics.risk.threshold', { threshold }) }}</span>
    </div>

    <el-collapse
      v-if="analytics.showBottlenecks && selectedCapacity.bottlenecks.length"
      accordion
    >
      <el-collapse-item name="bottlenecks">
        <template #title>
          <TooltipLabel
            :label="t('graph.analytics.risk.bottlenecksListTitle')"
            :tooltip-text="t('graph.analytics.risk.bottlenecksListTooltip')"
          />
        </template>
        <el-table
          :data="selectedCapacity.bottlenecks"
          size="small"
          table-layout="fixed"
          class="geoTable"
        >
          <el-table-column
            prop="dir"
            :label="t('graph.analytics.risk.columns.dir')"
            width="70"
          />
          <el-table-column
            prop="other"
            :label="t('graph.analytics.connections.columns.counterparty')"
            min-width="220"
          />
          <el-table-column
            :label="t('graph.analytics.risk.columns.limitUsedAvail')"
            min-width="220"
          >
            <template #default="{ row }">
              {{ money(row.t.limit) }} / {{ money(row.t.used) }} / {{ money(row.t.available) }}
            </template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </el-card>

  <el-card
    v-if="analyticsEq && analytics.showActivity && selectedActivity"
    shadow="never"
  >
    <template #header>
      <TooltipLabel
        :label="t('graph.analytics.activity.title')"
        :tooltip-text="t('graph.analytics.risk.activityTooltip')"
      />
    </template>

    <div class="metricRows">
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.trustlinesCreated')"
          :tooltip-text="t('graph.analytics.activity.trustlinesCreatedTooltip')"
        />
        <span class="metricRow__value">{{ selectedActivity.trustlineCreated[7] }} / {{ selectedActivity.trustlineCreated[30] }} / {{ selectedActivity.trustlineCreated[90] }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.trustlinesClosedNow')"
          :tooltip-text="t('graph.analytics.activity.trustlinesClosedNowTooltip')"
        />
        <span class="metricRow__value">{{ selectedActivity.trustlineClosed[7] }} / {{ selectedActivity.trustlineClosed[30] }} / {{ selectedActivity.trustlineClosed[90] }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.incidentsInitiator')"
          :tooltip-text="t('graph.analytics.activity.incidentsInitiatorTooltip')"
        />
        <span class="metricRow__value">{{ selectedActivity.incidentCount[7] }} / {{ selectedActivity.incidentCount[30] }} / {{ selectedActivity.incidentCount[90] }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.participantOps')"
          :tooltip-text="t('graph.analytics.activity.participantOpsTooltip')"
        />
        <span class="metricRow__value">{{ selectedActivity.participantOps[7] }} / {{ selectedActivity.participantOps[30] }} / {{ selectedActivity.participantOps[90] }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.paymentsCommitted')"
          :tooltip-text="t('graph.analytics.activity.paymentsCommittedTooltip')"
        />
        <span class="metricRow__value">{{ selectedActivity.paymentCommitted[7] }} / {{ selectedActivity.paymentCommitted[30] }} / {{ selectedActivity.paymentCommitted[90] }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.clearingCommitted')"
          :tooltip-text="t('graph.analytics.activity.clearingCommittedTooltip')"
        />
        <span class="metricRow__value">{{ selectedActivity.clearingCommitted[7] }} / {{ selectedActivity.clearingCommitted[30] }} / {{ selectedActivity.clearingCommitted[90] }}</span>
      </div>
    </div>

    <el-alert
      v-if="!selectedActivity.hasTransactions"
      type="warning"
      show-icon
      :title="t('graph.analytics.activity.transactionsUnavailableTitle')"
      :description="t('graph.analytics.activity.transactionsUnavailableDescription')"
      class="mb"
      style="margin-top: 10px"
    />
  </el-card>
</template>

<script setup lang="ts">
import GraphAnalyticsTogglesCard from '../../../ui/GraphAnalyticsTogglesCard.vue'
import type { ToggleKey } from '../../../ui/GraphAnalyticsTogglesCard.vue'
import TooltipLabel from '../../../ui/TooltipLabel.vue'
import { t } from '../../../i18n/en'

type AnalyticsModel = Record<ToggleKey, boolean>

type ToggleItem = {
  key: ToggleKey
  label: string
  tooltipText: string
  requires?: ToggleKey
}

type ConcentrationLevel = { type: string; label: string }

type SelectedConcentration = {
  eq: string | null
  outgoing: { level: ConcentrationLevel; top1: number; top5: number; hhi: number }
  incoming: { level: ConcentrationLevel; top1: number; top5: number; hhi: number }
}

type BottleneckRow = {
  dir: string
  other: string
  t: { limit: string; used: string; available: string }
}

type SelectedCapacity = {
  out: { pct: number }
  inc: { pct: number }
  bottlenecks: BottleneckRow[]
}

type SelectedActivity = {
  trustlineCreated: Record<number, number>
  trustlineClosed: Record<number, number>
  incidentCount: Record<number, number>
  participantOps: Record<number, number>
  paymentCommitted: Record<number, number>
  clearingCommitted: Record<number, number>
  hasTransactions: boolean
}

defineProps<{
  analyticsEq: boolean
  riskToggleItems: ToggleItem[]
  selectedConcentration: SelectedConcentration
  selectedCapacity: SelectedCapacity | null
  selectedActivity: SelectedActivity | null
  threshold: string
  money: (value: string) => string
  pct: (value: number, digits?: number) => string
}>()

const analytics = defineModel<AnalyticsModel>('analytics', { required: true })
</script>
