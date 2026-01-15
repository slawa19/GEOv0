<script setup lang="ts">
import TooltipLabel from '../../ui/TooltipLabel.vue'
import CopyIconButton from '../../ui/CopyIconButton.vue'
import GraphAnalyticsTogglesCard from '../../ui/GraphAnalyticsTogglesCard.vue'

import type { DrawerTab, SelectedInfo } from '../../composables/useGraphVisualization'
import type { ClearingCycles } from './graphTypes'

type AnalyticsToggles = {
  showRank: boolean
  showDistribution: boolean
  showConcentration: boolean
  showCapacity: boolean
  showBottlenecks: boolean
  showActivity: boolean
}

type AnalyticsToggleKey = keyof AnalyticsToggles

type AnalyticsToggleItem = {
  key: AnalyticsToggleKey
  label: string
  tooltipText: string
  requires?: AnalyticsToggleKey
}

type CounterpartySplitRow = {
  pid: string
  display_name: string
  amount: string
  share: number
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

type NetDist = {
  eq: string
  n: number
  netAtomsByPid: Map<string, bigint>
  sortedPids: string[]
  min: bigint
  max: bigint
  bins: Array<{ from: bigint; to: bigint; count: number }>
}

type SelectedRank = {
  eq: string
  rank: number
  n: number
  percentile: number
  net: string
}

type SelectedConcentration = {
  eq: string | null
  outgoing: { top1: number; top5: number; hhi: number; level: { label: string; type: 'success' | 'warning' | 'danger' } }
  incoming: { top1: number; top5: number; hhi: number; level: { label: string; type: 'success' | 'warning' | 'danger' } }
}

type SelectedCapacity = {
  eq: string | null
  out: { limit: bigint; used: bigint; pct: number }
  inc: { limit: bigint; used: bigint; pct: number }
  bottlenecks: Array<{ dir: 'out' | 'in'; other: string; t: { limit: string; used: string; available: string } }>
}

type SelectedActivity = {
  windows: number[]
  trustlineCreated: Record<number, number>
  trustlineClosed: Record<number, number>
  incidentCount: Record<number, number>
  participantOps: Record<number, number>
  paymentCommitted: Record<number, number>
  clearingCommitted: Record<number, number>
  hasTransactions: boolean
}

type ConnectionRow = {
  direction: 'incoming' | 'outgoing'
  counterparty_pid: string
  counterparty_name: string
  equivalent: string
  status: string
  limit: string
  used: string
  available: string
  bottleneck: boolean
}

type CycleEdge = ClearingCycles['equivalents'][string]['cycles'][number][number]

type Cycle = CycleEdge[]

const open = defineModel<boolean>({ required: true })
const tab = defineModel<DrawerTab>('tab', { required: true })
const eq = defineModel<string>('eq', { required: true })
const analytics = defineModel<AnalyticsToggles>('analytics', { required: true })
const connectionsIncomingPage = defineModel<number>('connectionsIncomingPage', { required: true })
const connectionsOutgoingPage = defineModel<number>('connectionsOutgoingPage', { required: true })

defineProps<{
  selected: SelectedInfo | null
  showIncidents: boolean
  incidentRatioByPid: Map<string, number>

  availableEquivalents: string[]
  analyticsEq: string | null
  threshold: string

  precisionByEq: Map<string, number>
  atomsToDecimal: (atoms: bigint, precision: number) => string

  loadData: () => void
  money: (v: string) => string
  pct: (x: number, digits?: number) => string

  selectedRank: SelectedRank | null
  selectedConcentration: SelectedConcentration
  selectedCapacity: SelectedCapacity | null
  selectedActivity: SelectedActivity | null
  netDistribution: NetDist | null

  selectedBalanceRows: BalanceRow[]
  selectedCounterpartySplit: {
    eq: string | null
    totalDebtAtoms: bigint
    totalCreditAtoms: bigint
    creditors: CounterpartySplitRow[]
    debtors: CounterpartySplitRow[]
  }

  selectedConnectionsIncoming: ConnectionRow[]
  selectedConnectionsOutgoing: ConnectionRow[]
  selectedConnectionsIncomingPaged: ConnectionRow[]
  selectedConnectionsOutgoingPaged: ConnectionRow[]

  connectionsPageSize: number

  onConnectionRowClick: (row: ConnectionRow) => void

  selectedCycles: Cycle[]
  isCycleActive: (c: Cycle) => boolean
  toggleCycleHighlight: (c: Cycle) => void

  summaryToggleItems: AnalyticsToggleItem[]
  balanceToggleItems: AnalyticsToggleItem[]
  riskToggleItems: AnalyticsToggleItem[]
}>()
</script>

<template>
  <el-drawer
    v-model="open"
    title="Details"
    size="40%"
  >
    <div v-if="selected && selected.kind === 'node'">
      <el-descriptions
        :column="1"
        border
      >
        <el-descriptions-item label="PID">
          <span class="geoInlineRow">
            {{ selected.pid }}
            <CopyIconButton
              :text="selected.pid"
              label="PID"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="Display name">
          {{ selected.display_name || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="Status">
          {{ selected.status || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="Type">
          {{ selected.type || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="Degree">
          {{ selected.degree }}
        </el-descriptions-item>
        <el-descriptions-item label="In / out">
          {{ selected.inDegree }} / {{ selected.outDegree }}
        </el-descriptions-item>
        <el-descriptions-item
          v-if="showIncidents"
          label="Incident ratio"
        >
          {{ (incidentRatioByPid.get(selected.pid) || 0).toFixed(2) }}
        </el-descriptions-item>
      </el-descriptions>

      <el-divider>Analytics (fixtures-first)</el-divider>

      <div class="drawerControls">
        <div class="drawerControls__row">
          <div class="ctl">
            <div class="toolbarLabel">
              Equivalent
            </div>
            <el-select
              v-model="eq"
              size="small"
              filterable
              class="ctl__field"
              placeholder="Equivalent"
            >
              <el-option
                v-for="o in availableEquivalents"
                :key="o"
                :label="o"
                :value="o"
              />
            </el-select>
          </div>
          <div class="drawerControls__actions">
            <el-button
              size="small"
              @click="loadData"
            >
              Refresh
            </el-button>
          </div>
        </div>
      </div>

      <el-tabs
        v-model="tab"
        class="drawerTabs"
      >
        <el-tab-pane
          label="Summary"
          name="summary"
        >
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
            :enabled="Boolean(analyticsEq)"
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
        </el-tab-pane>

        <el-tab-pane
          label="Connections"
          name="connections"
        >
          <div class="hint">
            Derived from visible graph edges (incoming/outgoing trustlines).
          </div>

          <el-empty
            v-if="selectedConnectionsIncoming.length + selectedConnectionsOutgoing.length === 0"
            description="No connections in current view"
          />
          <div v-else>
            <el-divider>Incoming (owed to you)</el-divider>
            <div class="tableTop">
              <el-pagination
                v-model:current-page="connectionsIncomingPage"
                :page-size="connectionsPageSize"
                :total="selectedConnectionsIncoming.length"
                small
                background
                layout="prev, pager, next, total"
              />
            </div>
            <el-table
              :data="selectedConnectionsIncomingPaged"
              size="small"
              border
              table-layout="fixed"
              style="width: 100%"
              class="mb clickable-table"
              highlight-current-row
              @row-click="onConnectionRowClick"
            >
              <el-table-column
                label="Counterparty"
                min-width="220"
              >
                <template #default="{ row }">
                  <span class="mono pidLink">{{ row.counterparty_pid }}</span>
                  <span
                    v-if="row.counterparty_name"
                    class="muted"
                  > — {{ row.counterparty_name }}</span>
                </template>
              </el-table-column>
              <el-table-column
                prop="equivalent"
                label="Eq"
                width="80"
              />
              <el-table-column
                prop="status"
                label="Status"
                width="90"
              />
              <el-table-column
                label="Available"
                width="120"
              >
                <template #default="{ row }">
                  {{ money(row.available) }}
                </template>
              </el-table-column>
              <el-table-column
                label="Used"
                width="120"
              >
                <template #default="{ row }">
                  {{ money(row.used) }}
                </template>
              </el-table-column>
              <el-table-column
                label="Limit"
                width="120"
              >
                <template #default="{ row }">
                  {{ money(row.limit) }}
                </template>
              </el-table-column>
            </el-table>

            <el-divider>Outgoing (you owe)</el-divider>
            <div class="tableTop">
              <el-pagination
                v-model:current-page="connectionsOutgoingPage"
                :page-size="connectionsPageSize"
                :total="selectedConnectionsOutgoing.length"
                small
                background
                layout="prev, pager, next, total"
              />
            </div>
            <el-table
              :data="selectedConnectionsOutgoingPaged"
              size="small"
              border
              table-layout="fixed"
              style="width: 100%"
              class="clickable-table"
              highlight-current-row
              @row-click="onConnectionRowClick"
            >
              <el-table-column
                label="Counterparty"
                min-width="220"
              >
                <template #default="{ row }">
                  <span class="mono pidLink">{{ row.counterparty_pid }}</span>
                  <span
                    v-if="row.counterparty_name"
                    class="muted"
                  > — {{ row.counterparty_name }}</span>
                </template>
              </el-table-column>
              <el-table-column
                prop="equivalent"
                label="Eq"
                width="80"
              />
              <el-table-column
                prop="status"
                label="Status"
                width="90"
              />
              <el-table-column
                label="Available"
                width="120"
              >
                <template #default="{ row }">
                  {{ money(row.available) }}
                </template>
              </el-table-column>
              <el-table-column
                label="Used"
                width="120"
              >
                <template #default="{ row }">
                  {{ money(row.used) }}
                </template>
              </el-table-column>
              <el-table-column
                label="Limit"
                width="120"
              >
                <template #default="{ row }">
                  {{ money(row.limit) }}
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>

        <el-tab-pane
          label="Balance"
          name="balance"
        >
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
            :enabled="Boolean(analyticsEq)"
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
        </el-tab-pane>

        <el-tab-pane
          label="Counterparties"
          name="counterparties"
        >
          <el-alert
            v-if="!analyticsEq"
            title="Pick an equivalent (not ALL) to inspect counterparties."
            type="info"
            show-icon
            class="mb"
          />

          <div v-else>
            <div class="splitGrid">
              <el-card shadow="never">
                <template #header>
                  <TooltipLabel
                    label="Top creditors (you owe)"
                    tooltip-text="Participants who are creditors of this participant (debts where you are the debtor)."
                  />
                </template>
                <el-empty
                  v-if="selectedCounterpartySplit.creditors.length === 0"
                  description="No creditors"
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
                    label="Participant"
                    min-width="220"
                  />
                  <el-table-column
                    prop="amount"
                    label="Amount"
                    min-width="120"
                  >
                    <template #default="{ row }">
                      {{ money(row.amount) }}
                    </template>
                  </el-table-column>
                  <el-table-column
                    prop="share"
                    label="Share"
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
                    label="Top debtors (owed to you)"
                    tooltip-text="Participants who are debtors to this participant (debts where you are the creditor)."
                  />
                </template>
                <el-empty
                  v-if="selectedCounterpartySplit.debtors.length === 0"
                  description="No debtors"
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
                    label="Participant"
                    min-width="220"
                  />
                  <el-table-column
                    prop="amount"
                    label="Amount"
                    min-width="120"
                  >
                    <template #default="{ row }">
                      {{ money(row.amount) }}
                    </template>
                  </el-table-column>
                  <el-table-column
                    prop="share"
                    label="Share"
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
        </el-tab-pane>

        <el-tab-pane
          label="Risk"
          name="risk"
        >
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
            :enabled="Boolean(analyticsEq)"
            :items="riskToggleItems"
          />

          <el-card
            v-if="analyticsEq && analytics.showConcentration"
            shadow="never"
            class="mb"
          >
            <template #header>
              <TooltipLabel
                :label="`Concentration (${analyticsEq})`"
                tooltip-text="Counterparty concentration risk derived from debt shares: top1/top5 and HHI."
              />
            </template>
            <div
              v-if="selectedConcentration.eq"
              class="riskGrid"
            >
              <div class="riskBlock">
                <div class="riskBlock__hdr">
                  <TooltipLabel
                    label="Outgoing concentration"
                    tooltip-text="How concentrated your outgoing debts are (you owe). Higher = dependence on fewer creditors."
                  />
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
              </div>
              <div class="riskBlock">
                <div class="riskBlock__hdr">
                  <TooltipLabel
                    label="Incoming concentration"
                    tooltip-text="How concentrated your incoming credits are (owed to you). Higher = dependence on fewer debtors."
                  />
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
        </el-tab-pane>

        <el-tab-pane
          label="Cycles"
          name="cycles"
        >
          <el-alert
            v-if="!analyticsEq"
            title="Pick an equivalent (not ALL) to inspect clearing cycles."
            type="info"
            show-icon
            class="mb"
          />
          <el-empty
            v-else-if="selectedCycles.length === 0"
            description="No cycles found in fixtures"
          />
          <div
            v-else
            class="cycles"
          >
            <div class="hint">
              <TooltipLabel
                label="Clearing cycles"
                tooltip-text="Each cycle is a directed loop in the debt graph for the selected equivalent. Clearing reduces each edge by the shown amount."
              />
            </div>
            <div
              v-for="(c, idx) in selectedCycles"
              :key="idx"
              class="cycleItem"
              :class="{ 'cycleItem--active': isCycleActive(c) }"
              @click="toggleCycleHighlight(c)"
            >
              <div class="cycleTitle">
                <TooltipLabel
                  :label="`Cycle #${idx + 1}`"
                  tooltip-text="A cycle is a set of debts that can be cleared together while preserving net positions (cycle cancelation)."
                />
              </div>
              <div
                v-for="(e, j) in c"
                :key="j"
                class="cycleEdge"
              >
                <span class="mono">{{ e.debtor }}</span>
                <span class="muted">→</span>
                <span class="mono">{{ e.creditor }}</span>
                <span class="muted">({{ e.equivalent }} {{ money(e.amount) }})</span>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <div v-else-if="selected && selected.kind === 'edge'">
      <el-descriptions
        :column="1"
        border
      >
        <el-descriptions-item label="Equivalent">
          <span>
            {{ selected.equivalent }}
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="From">
          <span class="geoInlineRow">
            {{ selected.from }}
            <CopyIconButton
              :text="selected.from"
              label="From PID"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="To">
          <span class="geoInlineRow">
            {{ selected.to }}
            <CopyIconButton
              :text="selected.to"
              label="To PID"
            />
          </span>
        </el-descriptions-item>
        <el-descriptions-item label="Status">
          {{ selected.status }}
        </el-descriptions-item>
        <el-descriptions-item label="Limit">
          {{ money(selected.limit) }}
        </el-descriptions-item>
        <el-descriptions-item label="Used">
          {{ money(selected.used) }}
        </el-descriptions-item>
        <el-descriptions-item label="Available">
          {{ money(selected.available) }}
        </el-descriptions-item>
        <el-descriptions-item label="Created at">
          {{ selected.created_at }}
        </el-descriptions-item>
      </el-descriptions>
    </div>
  </el-drawer>
</template>

<style scoped>
.mb {
  margin-bottom: 12px;
}

.ctl {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.toolbarLabel {
  font-size: var(--geo-font-size-label);
  font-weight: var(--geo-font-weight-label);
  color: var(--el-text-color-secondary);
}

.ctl__field {
  width: 100%;
}

.drawerControls {
  margin-bottom: 10px;
}

.drawerControls__row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 10px;
  align-items: end;
}

.drawerControls__actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.drawerTabs :deep(.el-tabs__header) {
  margin: 0 0 8px 0;
}

.drawerTabs :deep(.el-tabs__content) {
  padding: 0;
}

.drawerTabs {
  font-size: var(--geo-font-size-label);
  line-height: 1.35;
}

/* Typography roles inside the analytics drawer */
.drawerTabs :deep(.el-card__header) {
  font-size: var(--geo-font-size-title);
  font-weight: var(--geo-font-weight-title);
  color: var(--el-text-color-primary);
}

.drawerTabs :deep(.el-table__header .cell) {
  font-weight: var(--geo-font-weight-table-header);
  color: var(--el-text-color-primary);
}

.hint {
  margin-bottom: 10px;
  font-size: var(--geo-font-size-label);
  color: var(--el-text-color-secondary);
}

.summaryGrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.summaryCard :deep(.el-card__header) {
  padding: 10px 12px;
}

.summaryCard :deep(.el-card__body) {
  padding: 12px;
}

.kpi {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kpi__value {
  font-size: var(--geo-font-size-value);
  font-weight: var(--geo-font-weight-value);
}

.kpi__hint {
  font-size: var(--geo-font-size-sub);
}

.kpi__metric {
  font-size: var(--geo-font-size-label);
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.metricRows {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.metricRow {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.metricRow__label {
  min-width: 0;
  flex: 1;
  color: var(--el-text-color-secondary);
}

.metricRow__value {
  flex: 0 0 auto;
  font-size: var(--geo-font-size-label);
  font-weight: 600;
  color: var(--el-text-color-primary);
  text-align: right;
}

.kpi__row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.splitGrid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

.shareCell {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 8px;
  align-items: center;
}

.riskGrid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 10px;
}

@media (min-width: 780px) {
  .riskGrid {
    grid-template-columns: 1fr 1fr;
  }
}

.riskBlock {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px;
}

.riskBlock__hdr {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: var(--geo-font-size-title);
  font-weight: 600;
  margin-bottom: 6px;
}

.capRow {
  display: grid;
  grid-template-columns: 120px 1fr 56px;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}

.capRow__label {
  font-size: var(--geo-font-size-label);
  color: var(--el-text-color-secondary);
}

.capRow__value {
  font-size: 12px;
  font-weight: 600;
  text-align: right;
  color: var(--el-text-color-primary);
}

.hist {
  display: flex;
  align-items: flex-end;
  gap: 3px;
  height: 54px;
}

.hist__bar {
  width: 8px;
  background: var(--el-color-primary-light-5);
  border-radius: 3px;
}

.hist__labels {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-top: 6px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: 12px;
}

.pidLink {
  color: var(--el-color-primary);
}

.clickable-table :deep(tr) {
  cursor: pointer;
}

.tableTop {
  display: flex;
  justify-content: flex-end;
  margin: 6px 0 8px;
}

.cycles {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.cycleItem {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  padding: 10px;
  cursor: pointer;
}

.cycleItem--active {
  border-color: var(--el-color-warning);
  box-shadow: 0 0 0 1px var(--el-color-warning) inset;
}

.cycleTitle {
  font-size: 12px;
  font-weight: 600;
}

.cycleEdge {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
</style>
