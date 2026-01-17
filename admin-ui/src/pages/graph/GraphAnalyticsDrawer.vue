<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import TooltipLabel from '../../ui/TooltipLabel.vue'
import CopyIconButton from '../../ui/CopyIconButton.vue'
import GraphAnalyticsTogglesCard from '../../ui/GraphAnalyticsTogglesCard.vue'
import OperatorAdvicePanel from '../../ui/OperatorAdvicePanel.vue'
import { t } from '../../i18n'
import { labelTrustlineStatus } from '../../i18n/labels'
import { buildGraphDrawerAdvice } from '../../advice/operatorAdvice'

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

const props = defineProps<{
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

const route = useRoute()

const adviceItems = computed(() => {
  return buildGraphDrawerAdvice({
    ctx: {
      pid: props.selected && props.selected.kind === 'node' ? props.selected.pid : null,
      eq: props.analyticsEq,
      threshold: props.threshold,
      concentration: {
        outgoing: {
          levelType: props.selectedConcentration.outgoing.level.type,
          top1: props.selectedConcentration.outgoing.top1,
          top5: props.selectedConcentration.outgoing.top5,
          hhi: props.selectedConcentration.outgoing.hhi,
        },
        incoming: {
          levelType: props.selectedConcentration.incoming.level.type,
          top1: props.selectedConcentration.incoming.top1,
          top5: props.selectedConcentration.incoming.top5,
          hhi: props.selectedConcentration.incoming.hhi,
        },
      },
      capacity: props.selectedCapacity
        ? {
            outPct: props.selectedCapacity.out.pct,
            inPct: props.selectedCapacity.inc.pct,
            bottlenecksCount: props.selectedCapacity.bottlenecks.length,
          }
        : null,
    },
    baseQuery: route.query,
  })
})
</script>

<template>
  <el-drawer
    v-model="open"
    :title="t('graph.drawer.detailsTitle')"
    size="40%"
    data-testid="graph-drawer"
  >
    <div data-testid="graph-drawer-content">
      <div v-if="selected && selected.kind === 'node'">
        <el-descriptions
          class="geoDescriptions"
          :column="1"
          border
        >
          <el-descriptions-item :label="t('participant.columns.pid')">
            <span class="geoInlineRow">
              {{ selected.pid }}
              <CopyIconButton
                :text="selected.pid"
                :label="t('participant.columns.pid')"
              />
            </span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('participant.drawer.displayName')">
            {{ selected.display_name || t('common.na') }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('common.status')">
            {{ selected.status || t('common.na') }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('participant.columns.type')">
            {{ selected.type || t('common.na') }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('graph.drawer.degree')">
            {{ selected.degree }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('graph.drawer.inOut')">
            {{ selected.inDegree }} / {{ selected.outDegree }}
          </el-descriptions-item>
          <el-descriptions-item
            v-if="showIncidents"
            :label="t('graph.drawer.incidentRatio')"
          >
            {{ (incidentRatioByPid.get(selected.pid) || 0).toFixed(2) }}
          </el-descriptions-item>
        </el-descriptions>

        <el-divider>{{ t('graph.drawer.analyticsDivider') }}</el-divider>

        <div class="drawerControls">
          <div class="drawerControls__row">
            <div class="ctl">
              <div class="toolbarLabel">
                {{ t('graph.filters.equivalent') }}
              </div>
              <el-select
                v-model="eq"
                size="small"
                filterable
                class="ctl__field"
                :placeholder="t('graph.filters.equivalent')"
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
                {{ t('common.refresh') }}
              </el-button>
            </div>
          </div>
        </div>

        <el-tabs
          v-model="tab"
          class="drawerTabs"
        >
          <el-tab-pane
            :label="t('graph.drawer.tabs.summary')"
            name="summary"
          >
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

            <OperatorAdvicePanel :items="adviceItems" />

            <GraphAnalyticsTogglesCard
              v-if="analyticsEq"
              v-model="analytics"
              :title="t('graph.analytics.summary.widgetsTitle')"
              :title-tooltip-text="t('graph.analytics.summary.widgetsTooltip')"
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
          </el-tab-pane>

          <el-tab-pane
            :label="t('graph.drawer.tabs.connections')"
            name="connections"
          >
            <div class="hint">
              {{ t('graph.hint.connectionsDerivedFromEdges') }}
            </div>

            <el-empty
              v-if="selectedConnectionsIncoming.length + selectedConnectionsOutgoing.length === 0"
              :description="t('graph.analytics.connections.noneInView')"
            />
            <div v-else>
              <el-divider>{{ t('graph.common.incomingOwedToYou') }}</el-divider>
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
                  :label="t('graph.analytics.connections.columns.counterparty')"
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
                  :label="t('graph.analytics.connections.columns.eq')"
                  width="80"
                />
                <el-table-column
                  prop="status"
                  :label="t('common.status')"
                  width="90"
                />
                <el-table-column
                  :label="t('trustlines.available')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.available) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.used')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.used) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.limit')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.limit) }}
                  </template>
                </el-table-column>
              </el-table>

              <el-divider>{{ t('graph.common.outgoingYouOwe') }}</el-divider>
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
                  :label="t('graph.analytics.connections.columns.counterparty')"
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
                  :label="t('graph.analytics.connections.columns.eq')"
                  width="80"
                />
                <el-table-column
                  prop="status"
                  :label="t('common.status')"
                  width="90"
                />
                <el-table-column
                  :label="t('trustlines.available')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.available) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.used')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.used) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.limit')"
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
            :label="t('graph.drawer.tabs.balance')"
            name="balance"
          >
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
          </el-tab-pane>

          <el-tab-pane
            :label="t('graph.drawer.tabs.counterparties')"
            name="counterparties"
          >
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
                      :tooltip-text="t('graph.analytics.counterparties.topCreditorsYouOweTooltip')"
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
                      :tooltip-text="t('graph.analytics.counterparties.topDebtorsOwedToYouTooltip')"
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
          </el-tab-pane>

          <el-tab-pane
            :label="t('graph.drawer.tabs.risk')"
            name="risk"
          >
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
                  :label="t('graph.analytics.risk.counterpartyConcentration.title')"
                  :tooltip-text="t('graph.analytics.risk.counterpartyConcentration.tooltip')"
                />
              </template>
              <div
                v-if="selectedConcentration.eq"
                class="riskGrid"
              >
                <div class="riskBlock">
                  <div class="riskBlock__hdr">
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
                </div>
                <div class="riskBlock">
                  <div class="riskBlock__hdr">
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
          </el-tab-pane>

          <el-tab-pane
            :label="t('graph.drawer.tabs.cycles')"
            name="cycles"
          >
            <el-alert
              v-if="!analyticsEq"
              :title="t('graph.analytics.cycles.pickEquivalentTitle')"
              type="info"
              show-icon
              class="mb"
            />
            <el-empty
              v-else-if="selectedCycles.length === 0"
              :description="t('graph.analytics.cycles.noneInFixtures')"
            />
            <div
              v-else
              class="cycles"
            >
              <div class="hint">
                <TooltipLabel
                  :label="t('graph.analytics.cycles.title')"
                  :tooltip-text="t('graph.analytics.cycles.titleTooltip')"
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
                    :label="t('graph.analytics.cycles.cycleNumber', { n: idx + 1 })"
                    :tooltip-text="t('graph.analytics.cycles.cycleTooltip')"
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

      <div
        v-else-if="selected && selected.kind === 'edge'"
        data-testid="graph-drawer-edge"
      >
        <el-descriptions
          class="geoDescriptions"
          :column="1"
          border
        >
          <el-descriptions-item :label="t('trustlines.equivalent')">
            <span>
              {{ selected.equivalent }}
            </span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.from')">
            <span class="geoInlineRow">
              {{ selected.from }}
              <CopyIconButton
                :text="selected.from"
                :label="t('trustlines.fromPidLabel')"
              />
            </span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.to')">
            <span class="geoInlineRow">
              {{ selected.to }}
              <CopyIconButton
                :text="selected.to"
                :label="t('trustlines.toPidLabel')"
              />
            </span>
          </el-descriptions-item>
          <el-descriptions-item :label="t('common.status')">
            {{ labelTrustlineStatus(selected.status) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.limit')">
            {{ money(selected.limit) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.used')">
            {{ money(selected.used) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.available')">
            {{ money(selected.available) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.createdAt')">
            {{ selected.created_at }}
          </el-descriptions-item>
        </el-descriptions>
      </div>
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
  font-size: var(--geo-font-size-label);
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
  font-size: var(--geo-font-size-sub);
  margin-top: 6px;
}

.muted {
  color: var(--el-text-color-secondary);
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
  font-size: var(--geo-font-size-sub);
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
  font-size: var(--geo-font-size-label);
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
