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
        <span class="metricRow__value">{{ activityCounts(selectedActivity.incidentCount, selectedActivity.windows, selectedActivity.incidents) }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.participantOps')"
          :tooltip-text="t('graph.analytics.activity.participantOpsTooltip')"
        />
        <span class="metricRow__value">{{ activityCounts(selectedActivity.participantOps, selectedActivity.windows, selectedActivity.auditLog) }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.paymentsCommitted')"
          :tooltip-text="t('graph.analytics.activity.paymentsCommittedTooltip')"
        />
        <span class="metricRow__value">{{ activityCounts(selectedActivity.paymentCommitted, selectedActivity.windows, selectedActivity.transactions) }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.clearingCommitted')"
          :tooltip-text="t('graph.analytics.activity.clearingCommittedTooltip')"
        />
        <span class="metricRow__value">{{ activityCounts(selectedActivity.clearingCommitted, selectedActivity.windows, selectedActivity.transactions) }}</span>
      </div>
    </div>

    <!--
      F-013-1 / T1302. Three mutually exclusive statements about the same collection, and
      the order matters: "we were told nothing" outranks "we were told something we cannot
      use", which outranks "what we were told is a lower bound". Only the last of them
      leaves a printed number standing.
    -->
    <el-alert
      v-if="!selectedActivity.transactions.known"
      type="warning"
      show-icon
      :title="t('graph.analytics.activity.transactionsNotIncludedTitle')"
      :description="t('graph.analytics.activity.transactionsNotIncludedDescription')"
      class="mb"
      style="margin-top: 10px"
    />
    <el-alert
      v-else-if="selectedActivity.transactions.incomplete"
      type="warning"
      show-icon
      :title="t('graph.analytics.activity.transactionsUnattributableTitle')"
      :description="t('graph.analytics.activity.transactionsUnattributableDescription')"
      class="mb"
      style="margin-top: 10px"
    />
    <el-alert
      v-else-if="selectedActivity.transactions.lowerBound"
      type="info"
      show-icon
      :title="t('graph.analytics.activity.transactionsTruncatedTitle')"
      :description="t('graph.analytics.activity.transactionsTruncatedDescription')"
      class="mb"
      style="margin-top: 10px"
    />
    <!--
      F-013-R2. `incidents` and `audit_log` are governed by the same `include` and this client asks
      for neither, so the two counters above them are silent in real mode. Silence with no
      explanation is its own trap - the row goes blank and the operator is left to guess whether
      that is a fault - so it is said out loud, independently of the transactions notice because
      the two collections fail independently.
    -->
    <el-alert
      v-if="!selectedActivity.incidents.known || !selectedActivity.auditLog.known"
      type="warning"
      show-icon
      :title="t('graph.analytics.activity.snapshotCollectionsNotIncludedTitle')"
      :description="t('graph.analytics.activity.snapshotCollectionsNotIncludedDescription')"
      class="mb"
      style="margin-top: 10px"
    />
  </el-card>
</template>

<script setup lang="ts">
import GraphAnalyticsTogglesCard from '../../../ui/GraphAnalyticsTogglesCard.vue'
import type { ToggleKey } from '../../../ui/GraphAnalyticsTogglesCard.vue'
import TooltipLabel from '../../../ui/TooltipLabel.vue'
import { t } from '../../../i18n'
import { activityCounts, type CountConfidence } from '../graphPageHelpers'

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
  windows: number[]
  trustlineCreated: Record<number, number>
  trustlineClosed: Record<number, number>
  incidentCount: Record<number, number>
  participantOps: Record<number, number>
  paymentCommitted: Record<number, number>
  clearingCommitted: Record<number, number>
  // F-013-1 / T1302, reshaped by F-013-R1/R2. One confidence per COLLECTION, sitting beside the
  // counters derived from it, because the flat flags it replaces were a single vocabulary shared
  // by two branches that mean different things by it - and the branches got merged.
  //
  //   transactions -> paymentCommitted, clearingCommitted
  //   incidents    -> incidentCount
  //   auditLog     -> participantOps
  //
  // `snapshotIncidents` is deliberately separate from `incidents`: the incident RATIO row is fed by
  // the graph snapshot's incident collection on every branch, while the incident COUNTER can come
  // from the metrics endpoint, which measures it server-side and owes the snapshot nothing.
  transactions: CountConfidence
  incidents: CountConfidence
  auditLog: CountConfidence
  snapshotIncidents: CountConfidence
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
