<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'

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

const effectiveDismissMs = computed(() => {
  const len = (props.message ?? '').length
  if (len > 150) return 8000
  if (len > 80) return 6000
  return props.dismissMs
})

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
      timerId = setTimeout(dismiss, effectiveDismissMs.value)
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
    <div
      v-if="visible && message"
      class="error-toast ds-alert ds-alert--err ds-ov-surface ds-ov-toast ds-ov-toast--error"
      role="alert"
      aria-live="assertive"
    >
      <span class="error-toast__icon ds-ov-toast__icon" aria-hidden="true">⚠</span>
      <span class="error-toast__text ds-ov-toast__text">{{ message }}</span>
      <button
        class="error-toast__close ds-ov-toast__close ds-btn ds-btn--ghost ds-btn--icon"
        type="button"
        aria-label="Dismiss"
        @click="dismiss"
      >✕</button>
    </div>
  </Transition>
</template>

<style scoped>
/* Vue Transition */
.error-toast-enter-active,
.error-toast-leave-active {
  transition: opacity var(--ds-toast-transition-dur), transform var(--ds-toast-transition-dur);
}

.error-toast-enter-from,
.error-toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(var(--ds-toast-enter-shift-y));
}
</style>
