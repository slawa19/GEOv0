<template>
  <el-alert
    v-if="!analyticsEq"
    :title="t('graph.analytics.summary.pickEquivalentTitle')"
    type="info"
    show-icon
    class="mb"
  />

  <div class="hint">
    {{ t('graph.hint.fixturesFirstDerivedFromDatasets') }}
  </div>

  <GraphAnalyticsTogglesCard
    v-if="analyticsEq"
    v-model="analytics"
    :title="t('graph.analytics.summary.widgetsTitle')"
    :title-tooltip-text="t('graph.analytics.summary.widgetsTooltip')"
    :enabled="analyticsEq"
    :items="summaryToggleItems"
  />

  <div
    v-if="analyticsEq"
    class="summaryGrid"
  >
    <el-card
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          :label="t('graph.analytics.netPosition.title')"
          :tooltip-text="t('graph.analytics.netPosition.tooltip')"
        />
      </template>
      <div
        v-if="selectedRank"
        class="kpi"
      >
        <div class="kpi__value">
          {{ money(selectedRank.net) }} {{ selectedRank.eq }}
        </div>
        <div class="kpi__hint muted">
          {{ t('graph.analytics.netPosition.hint') }}
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
      v-if="analytics.showRank"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          :label="t('graph.analytics.rankPercentile.title')"
          :tooltip-text="t('graph.analytics.rankPercentile.tooltip')"
        />
      </template>
      <div
        v-if="selectedRank"
        class="kpi"
      >
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
      <div
        v-else
        class="muted"
      >
        {{ t('common.noData') }}
      </div>
    </el-card>

    <el-card
      v-if="analytics.showConcentration"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          :label="t('graph.analytics.concentration.title')"
          :tooltip-text="t('graph.analytics.concentration.tooltip')"
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
      v-if="analytics.showCapacity"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          :label="t('graph.analytics.capacity.title')"
          :tooltip-text="t('graph.analytics.capacity.tooltip')"
        />
      </template>
      <div
        v-if="selectedCapacity"
        class="kpi"
      >
        <div class="kpi__row">
          <span class="muted">{{ t('graph.analytics.capacity.outgoingUsed') }}</span>
          <span class="kpi__metric">{{ pct(selectedCapacity.out.pct, 0) }}</span>
        </div>
        <el-progress
          :percentage="Math.round((selectedCapacity.out.pct || 0) * 100)"
          :stroke-width="10"
          :show-text="false"
        />
        <div
          class="kpi__row"
          style="margin-top: 10px"
        >
          <span class="muted">{{ t('graph.analytics.capacity.incomingUsed') }}</span>
          <span class="kpi__metric">{{ pct(selectedCapacity.inc.pct, 0) }}</span>
        </div>
        <el-progress
          :percentage="Math.round((selectedCapacity.inc.pct || 0) * 100)"
          :stroke-width="10"
          :show-text="false"
        />
        <div
          v-if="analytics.showBottlenecks"
          class="kpi__hint muted"
          style="margin-top: 8px"
        >
          {{ t('graph.analytics.bottlenecks.countWithThreshold', { n: selectedCapacity.bottlenecks.length, threshold }) }}
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
      v-if="analytics.showActivity"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          :label="t('graph.analytics.activity.title')"
          :tooltip-text="t('graph.analytics.activity.summaryTooltip')"
        />
      </template>
      <div
        v-if="selectedActivity"
        class="metricRows"
      >
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
      </div>
      <div
        v-else
        class="muted"
      >
        {{ t('common.noData') }}
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import GraphAnalyticsTogglesCard from '../../../ui/GraphAnalyticsTogglesCard.vue'
import type { ToggleKey } from '../../../ui/GraphAnalyticsTogglesCard.vue'
import TooltipLabel from '../../../ui/TooltipLabel.vue'
import { t } from '../../../i18n'

type AnalyticsModel = Record<ToggleKey, boolean>

type SelectedRank = {
  net: string
  eq: string
  rank: number
  n: number
  percentile: number
}

type ConcentrationLevel = { type: string; label: string }

type SelectedConcentration = {
  eq: string | null
  outgoing: { level: ConcentrationLevel; top1: number; top5: number; hhi: number }
  incoming: { level: ConcentrationLevel; top1: number; top5: number; hhi: number }
}

type SelectedCapacity = {
  out: { pct: number }
  inc: { pct: number }
  bottlenecks: unknown[]
}

type SelectedActivity = {
  trustlineCreated: Record<number, number>
  trustlineClosed: Record<number, number>
  incidentCount: Record<number, number>
  participantOps: Record<number, number>
}

type ToggleItem = {
  key: ToggleKey
  label: string
  tooltipText: string
  requires?: ToggleKey
}

defineProps<{
  analyticsEq: boolean
  summaryToggleItems: ToggleItem[]
  selectedRank: SelectedRank | null
  selectedConcentration: SelectedConcentration
  selectedCapacity: SelectedCapacity | null
  selectedActivity: SelectedActivity | null
  threshold: string
  money: (value: string) => string
  pct: (value: number, digits?: number) => string
}>()

const analytics = defineModel<AnalyticsModel>('analytics', { required: true })
</script>
