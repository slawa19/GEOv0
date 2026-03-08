<script setup lang="ts">
import { computed, onUnmounted, ref, unref, watch, type Ref } from 'vue'

const props = defineProps<{
  /**
   * Can be passed as a raw string (typical) or as a Ref (for direct state wiring).
   */
  message?: string | null | Ref<string | null>
}>()

const emit = defineEmits<{
  dismiss: []
}>()

const messageText = computed(() => (props.message === undefined ? null : (unref(props.message) as string | null)))

const effectiveDismissMs = computed(() => {
  const len = (messageText.value ?? '').length
  return len > 50 ? 3500 : 2500
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
  messageText,
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
  <Transition name="success-toast">
    <div
      v-if="visible && messageText"
      class="success-toast ds-alert ds-alert--ok ds-ov-surface ds-ov-toast ds-ov-toast--success"
      role="status"
      aria-live="polite"
    >
      <span class="success-toast__icon ds-ov-toast__icon" aria-hidden="true">✓</span>
      <span class="success-toast__text ds-ov-toast__text">{{ messageText }}</span>
      <button
        class="success-toast__close ds-ov-toast__close ds-btn ds-btn--ghost ds-btn--icon"
        type="button"
        aria-label="Dismiss"
        @click="dismiss"
      >
        ✕
      </button>
    </div>
  </Transition>
</template>

<style scoped>
/* Vue Transition */
.success-toast-enter-active,
.success-toast-leave-active {
  transition: opacity var(--ds-toast-transition-dur), transform var(--ds-toast-transition-dur);
}

.success-toast-enter-from,
.success-toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(var(--ds-toast-enter-shift-y));
}
</style>

