<script setup lang="ts">
import { t } from '../../i18n'

const props = defineProps<{
  open: boolean
}>()
</script>

<template>
  <div
    v-if="props.open"
    class="legend"
  >
    <div class="legend__title">
      {{ t('graph.legend.title') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-person" /> {{ t('graph.legend.personNode') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-business" /> {{ t('graph.legend.businessNode') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-debt" /> {{ t('graph.legend.debtNode') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-suspended" /> {{ t('participant.status.suspended') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-left" /> {{ t('participant.status.left') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-deleted" /> {{ t('participant.status.deleted') }}
    </div>
    <div class="legend__row legend__row--hint">
      <span class="swatch swatch--hint" /> {{ t('graph.legend.sizeByAbsNet') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-active" /> {{ t('trustlines.status.active') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-frozen" /> {{ t('trustlines.status.frozen') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-closed" /> {{ t('trustlines.status.closed') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-bottleneck" /> {{ t('graph.legend.bottleneck') }}
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-incident" /> {{ t('graph.legend.incidentEdge') }}
    </div>
  </div>
</template>

<style scoped>
.legend {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 2;
  background: rgba(17, 19, 23, 0.85);
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: var(--geo-font-size-label);
  color: var(--el-text-color-primary);
  min-width: 240px;
  backdrop-filter: blur(4px);
}

.legend__title {
  font-weight: 600;
  margin-bottom: 8px;
}

.legend__row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 4px 0;
}

.swatch {
  display: inline-block;
  width: 18px;
  height: 10px;
  border-radius: 2px;
  border: 1px solid rgba(255, 255, 255, 0.18);
}

.swatch--node-active {
  display: none;
}

.swatch--node-person {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #3b82f6;
}

.swatch--node-suspended {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #e6a23c;
}

.swatch--node-debt {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #f97316;
}

.swatch--node-left {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #909399;
}

.swatch--node-deleted {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #606266;
}

.swatch--node-business {
  width: 14px;
  height: 12px;
  border-radius: 4px;
  background: #10b981;
}

.swatch--hint {
  width: 14px;
  height: 10px;
  border-radius: 4px;
  background: rgba(148, 163, 184, 0.65);
}

.legend__row--hint {
  margin-top: 8px;
}

.swatch--edge-active {
  background: #409eff;
}

.swatch--edge-frozen {
  background: #909399;
}

.swatch--edge-closed {
  background: #a3a6ad;
}

.swatch--edge-bottleneck {
  background: #f56c6c;
  height: 10px;
}

.swatch--edge-incident {
  background: repeating-linear-gradient(90deg, #606266 0 4px, transparent 4px 7px);
}
</style>
