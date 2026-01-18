<script setup lang="ts">
import type { Component } from 'vue'
import { X } from 'lucide-vue-next'

type NodeType = 'business' | 'person'

type GeoNodeUi = {
  id: string
  type: NodeType
  name: string
  trustLimit: number
  balance: number
  trustScore: number
}

const props = defineProps<{ node: GeoNodeUi; icon: Component; x: number; y: number }>()
const emit = defineEmits<{ close: [] }>()

function close() {
  emit('close')
}
</script>

<template>
  <div
    class="card"
    :style="{ left: `${props.x}px`, top: `${props.y}px` }"
  >
    <div class="card__wrap">
      <div class="card__bg"></div>
      <div class="card__glow"></div>
      <div class="card__border"></div>
      <div
        class="card__accent"
        :class="props.node.type === 'business' ? 'card__accent--business' : 'card__accent--person'"
      ></div>

      <div class="card__content">
        <div class="card__header">
          <div>
            <div class="card__kicker">Node Identity</div>
            <div class="card__title">{{ props.node.name }}</div>
          </div>

          <component
            :is="props.icon"
            class="card__typeIcon"
            :class="props.node.type === 'business' ? 'card__typeIcon--business' : 'card__typeIcon--person'"
          />
        </div>

        <div class="card__sep"></div>

        <div class="card__rows">
          <div class="card__row">
            <span class="card__label">Total Trust Limit</span>
            <span class="card__mono">{{ props.node.trustLimit }} GC</span>
          </div>
          <div class="card__row">
            <span class="card__label">Net Position</span>
            <span
              class="card__balance"
              :class="props.node.balance < 0 ? 'card__balance--neg' : 'card__balance--pos'"
            >
              {{ props.node.balance > 0 ? '+' : '' }}{{ props.node.balance }}
            </span>
          </div>

          <div class="card__score">
            <div class="card__scoreTop">
              <span>Trust Score</span>
              <span>{{ props.node.trustScore }}/100</span>
            </div>
            <div class="card__bar">
              <div
                class="card__barFill"
                :class="props.node.type === 'business' ? 'card__barFill--business' : 'card__barFill--person'"
                :style="{ width: `${props.node.trustScore}%` }"
              ></div>
            </div>
          </div>
        </div>

        <button
          class="card__close"
          type="button"
          @click="close"
        >
          <X class="card__closeIcon" />
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card {
  position: absolute;
  z-index: 30;
  width: 240px;
  pointer-events: none;
}

.card__wrap {
  position: relative;
  border-radius: 12px;
  overflow: hidden;
  box-shadow:
    0 30px 80px rgba(0, 0, 0, 0.55),
    0 0 0 1px rgba(148, 163, 184, 0.14);
}

.card__bg {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(120% 120% at 30% 0%, rgba(34, 211, 238, 0.12), transparent 55%),
    radial-gradient(120% 120% at 95% 20%, rgba(167, 139, 250, 0.12), transparent 55%),
    radial-gradient(140% 140% at 20% 100%, rgba(16, 185, 129, 0.10), transparent 60%),
    rgba(2, 6, 23, 0.62);
}

.card__glow {
  position: absolute;
  inset: -2px;
  border-radius: 14px;
  background:
    linear-gradient(135deg, rgba(34, 211, 238, 0.55), rgba(16, 185, 129, 0.40), rgba(167, 139, 250, 0.55));
  opacity: 0.75;
  filter: blur(10px);
  pointer-events: none;
}

.card__border {
  position: absolute;
  inset: 0;
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.card__border::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: 12px;
  padding: 1px;
  background: linear-gradient(135deg, rgba(34, 211, 238, 0.85), rgba(16, 185, 129, 0.65), rgba(167, 139, 250, 0.85));
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  opacity: 0.55;
}

.card__accent {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
}

.card__accent--business {
  background: #10b981;
  box-shadow: 0 0 18px rgba(16, 185, 129, 0.9);
}

.card__accent--person {
  background: #3b82f6;
  box-shadow: 0 0 18px rgba(59, 130, 246, 0.9);
}

.card__content {
  position: relative;
  padding: 14px 14px 12px;
  color: rgba(248, 250, 252, 0.95);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
}

.card__kicker {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.14em;
  font-weight: 800;
  color: rgba(148, 163, 184, 0.9);
  margin-bottom: 2px;
}

.card__title {
  font-size: 13px;
  font-weight: 800;
  letter-spacing: 0.02em;
}

.card__typeIcon {
  width: 18px;
  height: 18px;
}

.card__typeIcon--business {
  color: rgba(16, 185, 129, 0.9);
}

.card__typeIcon--person {
  color: rgba(59, 130, 246, 0.9);
}

.card__sep {
  height: 1px;
  margin: 12px 0;
  background: linear-gradient(90deg, transparent, rgba(148, 163, 184, 0.28), transparent);
}

.card__rows {
  display: grid;
  gap: 10px;
}

.card__row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.card__label {
  font-size: 12px;
  color: rgba(148, 163, 184, 0.92);
}

.card__mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: rgba(226, 232, 240, 0.92);
}

.card__balance {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 18px;
  font-weight: 900;
}

.card__balance--neg {
  color: rgba(248, 113, 113, 0.95);
}

.card__balance--pos {
  color: rgba(16, 185, 129, 0.95);
}

.card__scoreTop {
  display: flex;
  justify-content: space-between;
  color: rgba(100, 116, 139, 0.95);
  font-size: 10px;
  margin-bottom: 6px;
}

.card__bar {
  height: 4px;
  border-radius: 999px;
  background: rgba(51, 65, 85, 0.8);
  overflow: hidden;
}

.card__barFill {
  height: 100%;
}

.card__barFill--business {
  background: #10b981;
}

.card__barFill--person {
  background: #3b82f6;
}

.card__close {
  pointer-events: auto;
  position: absolute;
  top: 10px;
  right: 10px;
  width: 28px;
  height: 28px;
  border-radius: 10px;
  border: 1px solid rgba(148, 163, 184, 0.18);
  background: rgba(2, 6, 23, 0.35);
  color: rgba(148, 163, 184, 0.95);
  cursor: pointer;
}

.card__close:hover {
  border-color: rgba(34, 211, 238, 0.45);
  color: rgba(34, 211, 238, 0.95);
}

.card__closeIcon {
  width: 16px;
  height: 16px;
}
</style>

