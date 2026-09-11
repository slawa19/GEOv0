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
        <span class="metricRow__value">{{ activityCounts(selectedActivity.paymentCommitted, selectedActivity.windows, selectedActivity.payments) }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          :label="t('graph.analytics.activity.clearingCommitted')"
          :tooltip-text="t('graph.analytics.activity.clearingCommittedTooltip')"
        />
        <span class="metricRow__value">{{ activityCounts(selectedActivity.clearingCommitted, selectedActivity.windows, selectedActivity.clearings) }}</span>
      </div>
    </div>

    <!--
      F-013-1 / T1302, corrected by the CROSS review (F-013-R5). What used to stand here was a
      v-if / v-else-if / v-else-if chain of three notices, written out once here and once in
      GraphAnalyticsDrawer.vue. It read as three mutually exclusive statements, which was only ever
      true while a doubt blanked every cell of the collection; once the doubt became per window, a
      cut collection with one clouded window printed "≥" in the surviving cells while the middle
      branch swallowed the sentence explaining "≥". The decision of which sentences apply now
      lives in `activityNotices`, where it can be tested, and both cards render whatever it returns.
    -->
    <el-alert
      v-for="notice in activityNotices(selectedActivity)"
      :key="notice.kind"
      :type="notice.type"
      show-icon
      :title="t(notice.titleKey)"
      :description="t(notice.descriptionKey)"
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
import { activityCounts, activityNotices, type CountConfidence } from '../graphPageHelpers'

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
  // F-013-1 / T1302, reshaped by F-013-R1/R2 and narrowed by the EXTERNAL review of 013. One
  // confidence per COUNTER, sitting beside the counter it governs, because the flat flags it
  // replaces were a single vocabulary shared by two branches that mean different things by it -
  // and the branches got merged.
  //
  //   payments   -> paymentCommitted
  //   clearings  -> clearingCommitted
  //   incidents  -> incidentCount
  //   auditLog   -> participantOps
  //
  // `payments` and `clearings` are two views of ONE collection (`transactions`): they agree about
  // what the response carried and whether it was cut, and differ about which windows hold a row
  // that could not be placed against this participant. They were a single `transactions` object
  // until an unattributable payment was found erasing a clearing zero the client had counted
  // exactly - hence the split, and hence the per-window list rather than a flag.
  //
  // `snapshotIncidents` is deliberately separate from `incidents`: the incident RATIO row is fed by
  // the graph snapshot's incident collection on every branch, while the incident COUNTER can come
  // from the metrics endpoint, which measures it server-side and owes the snapshot nothing.
  payments: CountConfidence
  clearings: CountConfidence
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
