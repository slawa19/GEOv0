<script setup lang="ts">
import TooltipLabel from '../../ui/TooltipLabel.vue'
import CopyIconButton from '../../ui/CopyIconButton.vue'
import { t } from '../../i18n'
import { labelTrustlineStatus } from '../../i18n/labels'

import type { DrawerTab, SelectedInfo } from '../../composables/useGraphVisualization'
import type { BalanceRow } from '../../types/domain'

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

const open = defineModel<boolean>({ required: true })
const tab = defineModel<DrawerTab>('tab', { required: true })
const eq = defineModel<string>('eq', { required: true })
const connectionsIncomingPage = defineModel<number>('connectionsIncomingPage', { required: true })
const connectionsOutgoingPage = defineModel<number>('connectionsOutgoingPage', { required: true })

defineProps<{
  selected: SelectedInfo | null

  availableEquivalents: string[]

  reloadCurrentView: () => void
  money: (v: string, equivalent: unknown) => string

  // 032 S5 (F-1): the balance rows are the server's `balance_rows`. While they load the drawer says
  // so, on failure it shows the error - it never substitutes a figure computed from the graph.
  metricsLoading: boolean
  metricsError: string | null
  selectedBalanceRows: BalanceRow[]

  selectedConnectionsIncoming: ConnectionRow[]
  selectedConnectionsOutgoing: ConnectionRow[]
  selectedConnectionsIncomingPaged: ConnectionRow[]
  selectedConnectionsOutgoingPaged: ConnectionRow[]

  connectionsPageSize: number

  onConnectionRowClick: (row: ConnectionRow) => void
}>()
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
        </el-descriptions>

        <el-divider />

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
                data-testid="refresh-current-graph-view"
                @click="reloadCurrentView"
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
              v-if="metricsError"
              :title="metricsError"
              type="error"
              show-icon
              :closable="false"
              class="mb"
            />
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
              <el-skeleton
                v-if="metricsLoading"
                animated
                :rows="1"
              />
              <!--
                One line per equivalent, never a total: amounts of different equivalents are
                different units and their sum is not a quantity (F-012-8). With an equivalent
                picked, the server answers with that equivalent's row only.
              -->
              <div
                v-else-if="selectedBalanceRows.length"
                class="kpi"
              >
                <div
                  v-for="row in selectedBalanceRows"
                  :key="row.equivalent"
                  class="kpi__value"
                  data-testid="graph-summary-net"
                >
                  {{ money(row.net, row.equivalent) }} {{ row.equivalent }}
                </div>
                <div class="kpi__hint muted">
                  {{ t('graph.analytics.netPosition.hint') }}
                </div>
              </div>
              <div
                v-else-if="!metricsError"
                class="muted"
              >
                {{ t('common.noData') }}
              </div>
            </el-card>
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
                  size="small"
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
                    {{ money(row.available, row.equivalent) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.used')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.used, row.equivalent) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.limit')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.limit, row.equivalent) }}
                  </template>
                </el-table-column>
              </el-table>

              <el-divider>{{ t('graph.common.outgoingYouOwe') }}</el-divider>
              <div class="tableTop">
                <el-pagination
                  v-model:current-page="connectionsOutgoingPage"
                  :page-size="connectionsPageSize"
                  :total="selectedConnectionsOutgoing.length"
                  size="small"
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
                    {{ money(row.available, row.equivalent) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.used')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.used, row.equivalent) }}
                  </template>
                </el-table-column>
                <el-table-column
                  :label="t('trustlines.limit')"
                  width="120"
                >
                  <template #default="{ row }">
                    {{ money(row.limit, row.equivalent) }}
                  </template>
                </el-table-column>
              </el-table>
            </div>
          </el-tab-pane>

          <el-tab-pane
            :label="t('graph.drawer.tabs.balance')"
            name="balance"
          >
            <el-alert
              v-if="metricsError"
              :title="metricsError"
              type="error"
              show-icon
              :closable="false"
              class="mb"
            />
            <el-skeleton
              v-if="metricsLoading"
              animated
              :rows="3"
            />
            <el-empty
              v-else-if="selectedBalanceRows.length === 0 && !metricsError"
              :description="t('common.noData')"
            />
            <el-table
              v-else-if="selectedBalanceRows.length"
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
                  {{ money(row.outgoing_limit, row.equivalent) }}
                </template>
              </el-table-column>
              <el-table-column
                prop="outgoing_used"
                :label="t('graph.analytics.balance.columns.outUsed')"
                min-width="120"
              >
                <template #default="{ row }">
                  {{ money(row.outgoing_used, row.equivalent) }}
                </template>
              </el-table-column>
              <el-table-column
                prop="incoming_limit"
                :label="t('graph.analytics.balance.columns.inLimit')"
                min-width="120"
              >
                <template #default="{ row }">
                  {{ money(row.incoming_limit, row.equivalent) }}
                </template>
              </el-table-column>
              <el-table-column
                prop="incoming_used"
                :label="t('graph.analytics.balance.columns.inUsed')"
                min-width="120"
              >
                <template #default="{ row }">
                  {{ money(row.incoming_used, row.equivalent) }}
                </template>
              </el-table-column>
              <el-table-column
                prop="total_debt"
                :label="t('graph.analytics.balance.columns.debt')"
                min-width="120"
              >
                <template #default="{ row }">
                  {{ money(row.total_debt, row.equivalent) }}
                </template>
              </el-table-column>
              <el-table-column
                prop="total_credit"
                :label="t('graph.analytics.balance.columns.credit')"
                min-width="120"
              >
                <template #default="{ row }">
                  {{ money(row.total_credit, row.equivalent) }}
                </template>
              </el-table-column>
              <el-table-column
                prop="net"
                :label="t('graph.analytics.balance.columns.net')"
                min-width="120"
              >
                <template #default="{ row }">
                  {{ money(row.net, row.equivalent) }}
                </template>
              </el-table-column>
            </el-table>
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
            <el-tag
              v-if="selected.close_requested_at"
              type="warning"
              size="small"
            >
              {{ t('trustlines.closeRequested') }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.limit')">
            {{ money(selected.limit, selected.equivalent) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.used')">
            {{ money(selected.used, selected.equivalent) }}
          </el-descriptions-item>
          <el-descriptions-item :label="t('trustlines.available')">
            {{ money(selected.available, selected.equivalent) }}
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
</style>
