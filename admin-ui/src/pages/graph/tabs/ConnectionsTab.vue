<template>
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
        v-model:current-page="incomingPage"
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
        v-model:current-page="outgoingPage"
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
</template>

<script setup lang="ts">
import { t } from '../../../i18n'

export type ConnectionRow = {
  direction: 'incoming' | 'outgoing'
  counterparty_pid: string
  counterparty_name: string
  equivalent: string
  status: string
  available: string
  used: string
  limit: string
  bottleneck: boolean
}

defineProps<{
  selectedConnectionsIncoming: ConnectionRow[]
  selectedConnectionsOutgoing: ConnectionRow[]
  selectedConnectionsIncomingPaged: ConnectionRow[]
  selectedConnectionsOutgoingPaged: ConnectionRow[]
  connectionsPageSize: number
  onConnectionRowClick: (row: ConnectionRow) => void
  money: (value: string) => string
}>()

const incomingPage = defineModel<number>('incomingPage', { required: true })
const outgoingPage = defineModel<number>('outgoingPage', { required: true })
</script>
