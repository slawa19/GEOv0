<script setup lang="ts">
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
      Legend
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-active" /> active participant
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-frozen" /> frozen participant
    </div>
    <div class="legend__row">
      <span class="swatch swatch--node-business" /> business (node shape)
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-active" /> active trustline
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-frozen" /> frozen trustline
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-closed" /> closed trustline
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-bottleneck" /> bottleneck (thick)
    </div>
    <div class="legend__row">
      <span class="swatch swatch--edge-incident" /> incident initiator side (dashed)
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
  font-size: 12px;
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
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #67c23a;
}

.swatch--node-frozen {
  width: 12px;
  height: 12px;
  border-radius: 6px;
  background: #e6a23c;
}

.swatch--node-business {
  width: 14px;
  height: 10px;
  border-radius: 4px;
  background: #1f2d3d;
  border: 2px solid #409eff;
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
