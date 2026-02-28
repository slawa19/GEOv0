<script setup lang="ts">
import type { WindowInstance } from '../composables/windowManager/types'
import { onMounted, onUnmounted, ref } from 'vue'

type Props = {
  instance: WindowInstance
  title?: string
  /** MVP: allow migrated windows to disable the generic header to avoid double-headers. */
  showHeader?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  title: undefined,
  showHeader: true,
})

defineOptions({ inheritAttrs: false })

const emit = defineEmits<{
  close: []
  focus: []
  measured: [size: { width: number; height: number }]
}>()

const shellRef = ref<HTMLElement | null>(null)

let ro: ResizeObserver | null = null

function emitMeasuredFrom(el: HTMLElement) {
  const rect = el.getBoundingClientRect()
  // Normative: measure the whole shell (header + body), i.e. the outer box.
  emit('measured', {
    width: Math.round(rect.width),
    height: Math.round(rect.height),
  })
}

onMounted(() => {
  const el = shellRef.value
  if (!el) return

  // Tests / older environments may not have ResizeObserver.
  if (typeof ResizeObserver === 'undefined') {
    emitMeasuredFrom(el)
    return
  }

  ro = new ResizeObserver((entries) => {
    const entry = entries[0]
    if (!entry) return

    // Prefer border-box if available, otherwise fall back to DOM measurement.
    const borderSize = (entry as ResizeObserverEntry & {
      borderBoxSize?: Array<{ inlineSize: number; blockSize: number }> | { inlineSize: number; blockSize: number }
    }).borderBoxSize

    if (borderSize) {
      const s = Array.isArray(borderSize) ? borderSize[0] : borderSize
      emit('measured', {
        width: Math.round(s.inlineSize),
        height: Math.round(s.blockSize),
      })
      return
    }

    emitMeasuredFrom(el)
  })

  ro.observe(el)
})

onUnmounted(() => {
  ro?.disconnect()
  ro = null
})

function onPointerDown() {
  emit('focus')
}

function onCloseClick(ev: MouseEvent) {
  ev.stopPropagation()
  emit('close')
}
</script>

<template>
  <div
    ref="shellRef"
    class="ws-shell"
    :data-win-id="String(props.instance.id)"
    :data-win-type="props.instance.type"
    :data-win-active="props.instance.active ? '1' : '0'"
    :style="{
      left: props.instance.rect.left + 'px',
      top: props.instance.rect.top + 'px',
      width: props.instance.rect.width + 'px',
      height: props.instance.rect.height + 'px',
      zIndex: String(props.instance.z),
    }"
    @pointerdown="onPointerDown"
  >
    <div v-if="props.showHeader" class="ws-header" :data-title="props.title ? '1' : '0'">
      <div class="ws-title">{{ props.title ?? '' }}</div>
      <button class="ws-close" type="button" aria-label="Close window" @click="onCloseClick">×</button>
    </div>

    <div class="ws-body">
      <slot />
    </div>
  </div>
</template>

<style scoped>
.ws-shell {
  position: absolute;
  display: flex;
  flex-direction: column;
  border-radius: 10px;
  overflow: hidden;
  background: rgba(20, 22, 26, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.35);
}

/* R21 / N-1: WM window open/close animation.
 * These classes are applied by Vue <TransitionGroup name="ws">.
 * Keep the transition on the shell itself so scoped styles always match.
 */
.ws-enter-active,
.ws-leave-active {
  transition:
    transform 180ms cubic-bezier(0.2, 0, 0, 1),
    opacity 180ms cubic-bezier(0.2, 0, 0, 1);
  will-change: transform, opacity;
}

.ws-enter-from,
.ws-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.ws-enter-to,
.ws-leave-from {
  opacity: 1;
  transform: translateY(0);
}

@media (prefers-reduced-motion: reduce) {
  .ws-enter-active,
  .ws-leave-active {
    transition-duration: 0ms;
  }
}

.ws-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  background: rgba(0, 0, 0, 0.18);
  user-select: none;
}

.ws-title {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  line-height: 16px;
  opacity: 0.9;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.ws-close {
  width: 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(0, 0, 0, 0.18);
  color: inherit;
  cursor: pointer;
}

.ws-close:hover {
  background: rgba(255, 255, 255, 0.08);
}

.ws-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>

