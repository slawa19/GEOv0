<script setup lang="ts">
import { X, ShieldCheck, Users, Target, Zap } from 'lucide-vue-next'

type NodeType = 'business' | 'person'

type GeoNodeUi = {
  id: string
  type: NodeType
  name: string
  trustLimit: number
  balance: number
  trustScore: number
}

const props = defineProps<{ node: GeoNodeUi }>()
const emit = defineEmits<{ close: [] }>()

function close() {
  emit('close')
}
</script>

<template>
  <div class="node-card" :class="{ 'node-card--debt': props.node.balance < 0 }">
    <div class="node-card__header">
      <div class="node-card__title">
        <component 
          :is="props.node.type === 'business' ? ShieldCheck : Users" 
          size="14" 
          class="node-card__type-icon" 
        />
        <span class="node-card__name">{{ props.node.name }}</span>
      </div>
      <button class="node-card__close" @click="close">
        <X size="14" />
      </button>
    </div>

    <div class="node-card__body">
      <div class="node-card__metrics">
        <div class="node-card__metric">
          <div class="node-card__label"><Target size="10" /> LIMIT</div>
          <div class="node-card__value mono">{{ props.node.trustLimit.toLocaleString() }}</div>
        </div>
        <div class="node-card__metric">
          <div class="node-card__label"><Zap size="10" /> BALANCE</div>
          <div class="node-card__value mono" :class="props.node.balance < 0 ? 'text-debt' : 'text-ok'">
            {{ props.node.balance > 0 ? '+' : '' }}{{ props.node.balance.toLocaleString() }}
          </div>
        </div>
      </div>

      <div class="node-card__integrity">
        <div class="node-card__integrity-label">INTEGRITY {{ props.node.trustScore }}%</div>
        <div class="node-card__bar">
          <div class="node-card__bar-fill" :style="{ width: `${props.node.trustScore}%` }"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.node-card {
  position: absolute;
  z-index: 30;
  width: 220px;
  background: rgba(10, 15, 28, 0.95);
  border: 1px solid rgba(0, 210, 255, 0.4);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
  color: #c0caf5;
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
  pointer-events: auto;
  user-select: none;
}

.node-card--debt {
  border-color: rgba(244, 63, 94, 0.4);
}

.node-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  background: rgba(255, 255, 255, 0.03);
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.node-card__title {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.node-card__type-icon {
  color: #00d2ff;
  flex-shrink: 0;
}

.node-card--debt .node-card__type-icon {
  color: #f43f5e;
}

.node-card__name {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: #fff;
}

.node-card__close {
  background: transparent;
  border: none;
  color: #c0caf5;
  opacity: 0.5;
  cursor: pointer;
  padding: 2px;
  transition: opacity 0.2s;
  display: flex;
}

.node-card__close:hover {
  opacity: 1;
}

.node-card__body {
  padding: 10px;
}

.node-card__metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 12px;
}

.node-card__label {
  font-size: 9px;
  font-weight: 700;
  color: rgba(192, 202, 245, 0.5);
  margin-bottom: 4px;
  display: flex;
  align-items: center;
  gap: 4px;
}

.node-card__value {
  font-size: 14px;
  font-weight: 700;
  color: #fff;
}

.node-card__integrity {
  margin-top: 8px;
}

.node-card__integrity-label {
  font-size: 9px;
  font-weight: 700;
  color: rgba(192, 202, 245, 0.5);
  margin-bottom: 4px;
}

.node-card__bar {
  height: 4px;
  background: rgba(255, 255, 255, 0.05);
  border-radius: 2px;
  overflow: hidden;
}

.node-card__bar-fill {
  height: 100%;
  background: #00d2ff;
  transition: width 0.3s ease;
}

.node-card--debt .node-card__bar-fill {
  background: #f43f5e;
}

.text-ok { color: #10fb81; }
.text-debt { color: #f43f5e; }

.mono {
  font-family: 'JetBrains Mono', 'Fira Code', monospace;
}
</style>
