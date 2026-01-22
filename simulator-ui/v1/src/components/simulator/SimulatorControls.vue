<script setup lang="ts">
import { Activity, ArrowRightLeft } from 'lucide-vue-next'

const props = defineProps<{ disabledClearing: boolean }>()

const emit = defineEmits<{
  'single-tx': []
  clearing: []
}>()

function onTx() {
  emit('single-tx')
}

function onClearing() {
  emit('clearing')
}
</script>

<template>
  <div class="controls">
    <button
      class="controls__btn"
      type="button"
      @click="onTx"
    >
      <ArrowRightLeft class="controls__icon" />
      <span>Single Tx</span>
    </button>

    <button
      class="controls__btn controls__btn--primary"
      type="button"
      :disabled="props.disabledClearing"
      @click="onClearing"
    >
      <Activity
        class="controls__icon"
        :class="props.disabledClearing ? 'controls__icon--pulse' : ''"
      />
      <span>{{ props.disabledClearing ? 'Clearing…' : 'Run Clearing' }}</span>
    </button>
  </div>
</template>

<style scoped>
.controls {
  position: absolute;
  left: 50%;
  bottom: 22px;
  transform: translateX(-50%);

  display: flex;
  gap: 12px;
  z-index: 25;

  padding: 10px;
  border-radius: 18px;
  background: rgba(2, 6, 23, 0.45);
  border: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  box-shadow:
    0 24px 64px rgba(0, 0, 0, 0.65),
    0 0 0 1px rgba(255, 255, 255, 0.05);
}

.controls__btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;

  padding: 11px 16px;
  border-radius: 12px;

  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.1);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: rgba(226, 232, 240, 0.95);

  font-size: 11px;
  font-weight: 900;
  letter-spacing: 0.1em;
  text-transform: uppercase;

  cursor: pointer;

  transition:
    transform 120ms ease,
    border-color 160ms ease,
    background-color 160ms ease,
    box-shadow 160ms ease,
    color 160ms ease;
}

.controls__btn:hover {
  border-color: rgba(255, 255, 255, 0.3);
  background: rgba(255, 255, 255, 0.08);
  color: #ffffff;
  box-shadow: 0 0 20px rgba(255, 255, 255, 0.05);
}

.controls__btn:active {
  transform: translateY(1px) scale(0.98);
}

.controls__btn--primary {
  background: linear-gradient(135deg, rgba(34, 211, 238, 0.25), rgba(167, 139, 250, 0.25));
  border-color: rgba(255, 255, 255, 0.2);
  color: #ffffff;
}

.controls__btn--primary:hover {
  border-color: rgba(255, 255, 255, 0.4);
  background: linear-gradient(135deg, rgba(34, 211, 238, 0.35), rgba(167, 139, 250, 0.35));
  box-shadow: 0 0 32px rgba(167, 139, 250, 0.25);
}

.controls__btn:disabled {
  cursor: not-allowed;
  opacity: 0.82;
  border-color: rgba(245, 158, 11, 0.45);
  color: rgba(245, 158, 11, 0.92);
  background: rgba(120, 53, 15, 0.28);
}

.controls__icon {
  width: 16px;
  height: 16px;
}

.controls__icon--pulse {
  animation: pulse 0.9s ease-in-out infinite;
}

@keyframes pulse {
  0% {
    transform: scale(1);
    opacity: 0.9;
  }
  50% {
    transform: scale(1.1);
    opacity: 1;
  }
  100% {
    transform: scale(1);
    opacity: 0.9;
  }
}
</style>

