<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'

const props = withDefaults(
  defineProps<{
    message: string | null
    /** Auto-dismiss delay in milliseconds (default 4000ms). */
    dismissMs?: number
  }>(),
  { dismissMs: 4000 },
)

const emit = defineEmits<{
  dismiss: []
}>()

const visible = ref(false)
let timerId: ReturnType<typeof setTimeout> | null = null

function clearTimer() {
  if (timerId !== null) {
    clearTimeout(timerId)
    timerId = null
  }
}

function dismiss() {
  clearTimer()
  visible.value = false
  emit('dismiss')
}

watch(
  () => props.message,
  (msg) => {
    clearTimer()
    if (msg) {
      visible.value = true
      timerId = setTimeout(dismiss, props.dismissMs)
    } else {
      visible.value = false
    }
  },
  { immediate: true },
)

onUnmounted(() => {
  clearTimer()
})
</script>

<template>
  <Transition name="error-toast">
    <div v-if="visible && message" class="error-toast ds-ov-surface" role="alert" aria-live="assertive">
      <span class="error-toast__icon" aria-hidden="true">⚠</span>
      <span class="error-toast__text">{{ message }}</span>
      <button
        class="error-toast__close"
        type="button"
        aria-label="Dismiss"
        @click="dismiss"
      >✕</button>
    </div>
  </Transition>
</template>

<style scoped>
.error-toast {
  position: absolute;
  bottom: 68px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 200;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 240px;
  max-width: 480px;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(200, 40, 40, 0.88);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  color: #fff;
  font-size: 0.82rem;
  line-height: 1.4;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.45);
  pointer-events: auto;
}

.error-toast__icon {
  flex-shrink: 0;
  font-size: 1rem;
  opacity: 0.9;
}

.error-toast__text {
  flex: 1 1 auto;
  word-break: break-word;
}

.error-toast__close {
  flex-shrink: 0;
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.75);
  cursor: pointer;
  font-size: 0.85rem;
  padding: 2px 4px;
  line-height: 1;
  transition: color 0.15s;
}

.error-toast__close:hover {
  color: #fff;
}

/* Vue Transition */
.error-toast-enter-active,
.error-toast-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.error-toast-enter-from,
.error-toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}
</style>
