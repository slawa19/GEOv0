<template>
  <el-alert
    v-if="!analyticsEq"
    title="Pick an equivalent (not ALL) to inspect risk metrics."
    type="info"
    show-icon
    class="mb"
  />

  <GraphAnalyticsTogglesCard
    v-if="analyticsEq"
    v-model="analytics"
    title="Risk widgets"
    title-tooltip-text="Show/hide risk-related widgets in this tab. These toggles are stored in localStorage for this browser."
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
        label="Counterparty concentration"
        tooltip-text="Counterparty concentration risk derived from debt shares: top1/top5 and HHI."
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
    v-if="analyticsEq && analytics.showCapacity && selectedCapacity"
    shadow="never"
    class="mb"
  >
    <template #header>
      <TooltipLabel
        label="Trustline capacity"
        tooltip-text="How much of the participant’s trustline limits are already used. Outgoing = used on lines where participant is creditor; incoming = used on lines where participant is debtor."
      />
    </template>

    <div class="capRow">
      <div class="capRow__label">
        <TooltipLabel
          label="Outgoing used"
          tooltip-text="Used / limit aggregated across all outgoing trustlines (participant is creditor)."
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
          label="Incoming used"
          tooltip-text="Used / limit aggregated across all incoming trustlines (participant is debtor)."
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
          :label="`Bottlenecks: ${selectedCapacity.bottlenecks.length}`"
          tooltip-text="Trustlines where available/limit is below the selected threshold."
        />
      </el-tag>
      <span
        class="muted"
        style="margin-left: 8px"
      >threshold {{ threshold }}</span>
    </div>

    <el-collapse
      v-if="analytics.showBottlenecks && selectedCapacity.bottlenecks.length"
      accordion
    >
      <el-collapse-item name="bottlenecks">
        <template #title>
          <TooltipLabel
            label="Bottlenecks list"
            tooltip-text="List of trustlines close to saturation (low available relative to limit)."
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
            label="Dir"
            width="70"
          />
          <el-table-column
            prop="other"
            label="Counterparty"
            min-width="220"
          />
          <el-table-column
            label="Limit / Used / Avail"
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
        label="Activity / churn"
        tooltip-text="Counts by 7/30/90-day windows. Sources: trustlines.created_at, incidents.created_at, audit-log.timestamp, transactions.updated_at."
      />
    </template>

    <div class="metricRows">
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
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          label="Payments committed (7/30/90d)"
          tooltip-text="Count of committed PAYMENT transactions involving this participant (as sender or receiver), by updated_at window."
        />
        <span class="metricRow__value">{{ selectedActivity.paymentCommitted[7] }} / {{ selectedActivity.paymentCommitted[30] }} / {{ selectedActivity.paymentCommitted[90] }}</span>
      </div>
      <div class="metricRow">
        <TooltipLabel
          class="metricRow__label"
          label="Clearing committed (7/30/90d)"
          tooltip-text="Count of committed CLEARING transactions where this participant appears in any cycle edge (or is initiator), by updated_at window."
        />
        <span class="metricRow__value">{{ selectedActivity.clearingCommitted[7] }} / {{ selectedActivity.clearingCommitted[30] }} / {{ selectedActivity.clearingCommitted[90] }}</span>
      </div>
    </div>

    <el-alert
      v-if="!selectedActivity.hasTransactions"
      type="warning"
      show-icon
      title="Transactions-based activity is unavailable in fixtures"
      description="Missing datasets/transactions.json in the current seed. In real mode this must come from API."
      class="mb"
      style="margin-top: 10px"
    />
  </el-card>
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
