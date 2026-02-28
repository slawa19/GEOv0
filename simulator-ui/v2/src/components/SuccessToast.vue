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
      class="success-toast ds-alert ds-alert--ok ds-ov-surface"
      role="status"
      aria-live="polite"
    >
      <span class="success-toast__icon" aria-hidden="true">✓</span>
      <span class="success-toast__text">{{ messageText }}</span>
      <button
        class="success-toast__close ds-btn ds-btn--ghost ds-btn--icon"
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
.success-toast {
  position: absolute;
  /* P1-3: offset above ErrorToast (bottom: 68px) to prevent visual overlap when
   * both toasts are briefly visible (rare but possible in degraded flows). */
  bottom: 128px;
  left: 50%;
  transform: translateX(-50%);
  z-index: var(--ds-z-alert, 200);
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 240px;
  max-width: 480px;
  pointer-events: auto;
}

.success-toast__icon {
  flex-shrink: 0;
  opacity: 0.9;
}

.success-toast__text {
  flex: 1 1 auto;
  word-break: break-word;
}

.success-toast__close {
  flex-shrink: 0;
  min-width: 24px;
  min-height: 24px;
  opacity: 0.85;
  transition: opacity 0.15s;
}

.success-toast__close:hover {
  opacity: 1;
}

/* Vue Transition */
.success-toast-enter-active,
.success-toast-leave-active {
  transition: opacity 0.2s, transform 0.2s;
}

.success-toast-enter-from,
.success-toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(8px);
}
</style>

