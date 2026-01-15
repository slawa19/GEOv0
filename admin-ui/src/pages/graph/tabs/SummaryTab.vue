<template>
  <el-alert
    v-if="!analyticsEq"
    title="Pick an equivalent (not ALL) for full analytics."
    type="info"
    show-icon
    class="mb"
  />

  <div class="hint">
    Fixtures-first: derived from trustlines + debts + incidents + audit-log.
  </div>

  <GraphAnalyticsTogglesCard
    v-if="analyticsEq"
    v-model="analytics"
    title="Summary widgets"
    title-tooltip-text="Show/hide summary cards. These toggles are stored in localStorage for this browser."
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
          label="Net position"
          tooltip-text="Net balance in the selected equivalent: total_credit − total_debt (derived from debts fixture)."
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
          credit − debt
        </div>
      </div>
      <div
        v-else
        class="muted"
      >
        No data
      </div>
    </el-card>

    <el-card
      v-if="analytics.showRank"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          label="Rank / percentile"
          tooltip-text="Your position among all participants by net balance for the selected equivalent (1 = top net creditor)."
        />
      </template>
      <div
        v-if="selectedRank"
        class="kpi"
      >
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
      <div
        v-else
        class="muted"
      >
        No data
      </div>
    </el-card>

    <el-card
      v-if="analytics.showConcentration"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          label="Concentration"
          tooltip-text="How concentrated your debts/credits are across counterparties (top1/top5 shares + HHI). Higher = more dependence on a few counterparties."
        />
      </template>
      <div
        v-if="selectedConcentration.eq"
        class="kpi"
      >
        <div class="kpi__row">
          <span class="geoLabel">Outgoing (you owe)</span>
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
              label="Top1 share"
              tooltip-text="Share of total outgoing debt owed to the largest single creditor."
            />
            <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top1, 0) }}</span>
          </div>
          <div class="metricRow">
            <TooltipLabel
              class="metricRow__label"
              label="Top5 share"
              tooltip-text="Share of total outgoing debt owed to the largest 5 creditors combined."
            />
            <span class="metricRow__value">{{ pct(selectedConcentration.outgoing.top5, 0) }}</span>
          </div>
          <div class="metricRow">
            <TooltipLabel
              class="metricRow__label"
              label="HHI"
              tooltip-text="Herfindahl–Hirschman Index = sum of squared counterparty shares. Closer to 1 means more concentrated."
            />
            <span class="metricRow__value">{{ selectedConcentration.outgoing.hhi.toFixed(2) }}</span>
          </div>
        </div>
        <div
          class="kpi__row"
          style="margin-top: 10px"
        >
          <span class="geoLabel">Incoming (owed to you)</span>
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
              label="Top1 share"
              tooltip-text="Share of total incoming credit owed by the largest single debtor."
            />
            <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top1, 0) }}</span>
          </div>
          <div class="metricRow">
            <TooltipLabel
              class="metricRow__label"
              label="Top5 share"
              tooltip-text="Share of total incoming credit owed by the largest 5 debtors combined."
            />
            <span class="metricRow__value">{{ pct(selectedConcentration.incoming.top5, 0) }}</span>
          </div>
          <div class="metricRow">
            <TooltipLabel
              class="metricRow__label"
              label="HHI"
              tooltip-text="Herfindahl–Hirschman Index = sum of squared counterparty shares. Closer to 1 means more concentrated."
            />
            <span class="metricRow__value">{{ selectedConcentration.incoming.hhi.toFixed(2) }}</span>
          </div>
        </div>
      </div>
      <div
        v-else
        class="muted"
      >
        No data
      </div>
    </el-card>

    <el-card
      v-if="analytics.showCapacity"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          label="Capacity"
          tooltip-text="Aggregate trustline capacity around the participant: used% = total_used / total_limit (incoming/outgoing)."
        />
      </template>
      <div
        v-if="selectedCapacity"
        class="kpi"
      >
        <div class="kpi__row">
          <span class="muted">Outgoing used</span>
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
          <span class="muted">Incoming used</span>
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
          Bottlenecks: {{ selectedCapacity.bottlenecks.length }} (threshold {{ threshold }})
        </div>
      </div>
      <div
        v-else
        class="muted"
      >
        No data
      </div>
    </el-card>

    <el-card
      v-if="analytics.showActivity"
      shadow="never"
      class="summaryCard"
    >
      <template #header>
        <TooltipLabel
          label="Activity / churn"
          tooltip-text="Recent changes around the participant in rolling windows (7/30/90 days), based on fixture timestamps."
        />
      </template>
      <div
        v-if="selectedActivity"
        class="metricRows"
      >
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            label="Trustlines created (7/30/90d)"
            tooltip-text="Count of trustlines involving this participant with created_at inside each window."
          />
          <span class="metricRow__value">{{ selectedActivity.trustlineCreated[7] }} / {{ selectedActivity.trustlineCreated[30] }} / {{ selectedActivity.trustlineCreated[90] }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            label="Trustlines closed now (7/30/90d)"
            tooltip-text="Not an event counter: among trustlines created inside each window, how many are currently status=closed."
          />
          <span class="metricRow__value">{{ selectedActivity.trustlineClosed[7] }} / {{ selectedActivity.trustlineClosed[30] }} / {{ selectedActivity.trustlineClosed[90] }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            label="Incidents (initiator, 7/30/90d)"
            tooltip-text="Count of incident records where this participant is the initiator_pid, by created_at window."
          />
          <span class="metricRow__value">{{ selectedActivity.incidentCount[7] }} / {{ selectedActivity.incidentCount[30] }} / {{ selectedActivity.incidentCount[90] }}</span>
        </div>
        <div class="metricRow">
          <TooltipLabel
            class="metricRow__label"
            label="Participant ops (audit-log, 7/30/90d)"
            tooltip-text="Count of audit-log actions starting with PARTICIPANT_* for this pid (object_id), by timestamp window."
          />
          <span class="metricRow__value">{{ selectedActivity.participantOps[7] }} / {{ selectedActivity.participantOps[30] }} / {{ selectedActivity.participantOps[90] }}</span>
        </div>
      </div>
      <div
        v-else
        class="muted"
      >
        No data
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import GraphAnalyticsTogglesCard from '../../../ui/GraphAnalyticsTogglesCard.vue'
import type { ToggleKey } from '../../../ui/GraphAnalyticsTogglesCard.vue'
import TooltipLabel from '../../../ui/TooltipLabel.vue'

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
