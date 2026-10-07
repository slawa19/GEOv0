<script setup lang="ts">
import { t } from '../../i18n'
import type { ConnectionRow } from './useGraphConnections'

// One direction of a participant's connections in the graph drawer: the pager, then the page of rows. The drawer shows
// two of them (incoming, outgoing); they were two copies of this block (032 S7, D-14).
const page = defineModel<number>('page', { required: true })

defineProps<{
  /** All rows of this direction: their number is the pager's total. */
  rows: ConnectionRow[]
  /** The rows of the current page. */
  pagedRows: ConnectionRow[]
  pageSize: number
  money: (value: string, equivalent: unknown) => string
}>()

const emit = defineEmits<{ rowClick: [row: ConnectionRow] }>()
</script>

<template>
  <div>
    <div class="tableTop">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="rows.length"
        size="small"
        background
        layout="prev, pager, next, total"
      />
    </div>
    <el-table
      :data="pagedRows"
      size="small"
      border
      table-layout="fixed"
      style="width: 100%"
      class="clickable-table"
      highlight-current-row
      @row-click="(row: ConnectionRow) => emit('rowClick', row)"
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
</template>

<style scoped>
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
