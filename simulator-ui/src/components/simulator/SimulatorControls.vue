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
  gap: 10px;
  z-index: 25;

  padding: 8px;
  border-radius: 16px;
  background: rgba(2, 6, 23, 0.32);
  border: 1px solid rgba(148, 163, 184, 0.12);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  box-shadow:
    0 18px 60px rgba(0, 0, 0, 0.55),
    0 0 0 1px rgba(34, 211, 238, 0.06);
}

.controls__btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;

  padding: 10px 14px;
  border-radius: 12px;

  background:
    radial-gradient(120% 140% at 20% 0%, rgba(34, 211, 238, 0.14), transparent 55%),
    rgba(15, 23, 42, 0.55);
  border: 1px solid rgba(148, 163, 184, 0.18);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  color: rgba(226, 232, 240, 0.92);

  font-size: 12px;
  font-weight: 900;
  letter-spacing: 0.08em;
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
  border-color: rgba(34, 211, 238, 0.55);
  color: rgba(34, 211, 238, 0.95);
  box-shadow: 0 0 0 1px rgba(34, 211, 238, 0.12), 0 0 28px rgba(34, 211, 238, 0.14);
}

.controls__btn:active {
  transform: translateY(1px);
}

.controls__btn--primary {
  background:
    linear-gradient(135deg, rgba(34, 211, 238, 0.18), rgba(16, 185, 129, 0.10), rgba(167, 139, 250, 0.18)),
    rgba(30, 27, 75, 0.48);
  border-color: rgba(167, 139, 250, 0.38);
  color: rgba(199, 210, 254, 0.92);
}

.controls__btn--primary:hover {
  border-color: rgba(167, 139, 250, 0.72);
  color: rgba(255, 255, 255, 0.96);
  box-shadow: 0 0 36px rgba(167, 139, 250, 0.18);
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

