<script setup lang="ts">
import type { WindowInstance } from '../composables/windowManager/types'
import { computed, onMounted, onUnmounted, ref } from 'vue'

type Props = {
  instance: WindowInstance
  title?: string
  /** MVP: allow migrated windows to disable the generic header to avoid double-headers. */
  showHeader?: boolean
  /** Option B: WM owns geometry only; visuals stay in legacy components. */
  frameless?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  title: undefined,
  showHeader: true,
  frameless: false,
})

defineOptions({ inheritAttrs: false })

const emit = defineEmits<{
  close: []
  focus: []
  measured: [size: { width: number; height: number }]
}>()

const shellRef = ref<HTMLElement | null>(null)

const shellStyle = computed(() => {
  const base = {
    left: props.instance.rect.left + 'px',
    top: props.instance.rect.top + 'px',
    zIndex: String(props.instance.z),
  } as Record<string, string>

  if (props.frameless) {
    // In frameless mode only position is WM-controlled (no explicit width/height).
    // Apply constraint minimums to prevent the shell from rendering as a tiny loading stub
    // before content populates (e.g. interact-panel before participants arrive).
    // This reduces the visual size delta between loading-stub and loaded states,
    // making the transition less jarring without hardcoding panel-specific values.
    const c = props.instance.constraints
    return {
      ...base,
      minWidth: c.minWidth + 'px',
      minHeight: c.minHeight + 'px',
    }
  }

  return {
    ...base,
    width: props.instance.rect.width + 'px',
    height: props.instance.rect.height + 'px',
  }
})

const effectiveShowHeader = computed(() => props.showHeader && !props.frameless)

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
    :class="['ws-shell', { 'ws-shell--framed': !props.frameless }]"
    :data-win-id="String(props.instance.id)"
    :data-win-type="props.instance.type"
    :data-win-active="props.instance.active ? '1' : '0'"
    :style="shellStyle"
    @pointerdown="onPointerDown"
  >
    <div v-if="effectiveShowHeader" class="ws-header" :data-title="props.title ? '1' : '0'">
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
  box-sizing: border-box;
  /*
   * Max-size safety: keep window within the WM layer.
   * NOTE: in frameless mode we rely on intrinsic content sizing.
   */
  max-width: calc(100% - 24px);
  max-height: calc(100% - 24px);
}

.ws-shell--framed {
  display: flex;
  flex-direction: column;
  border-radius: var(--ds-radius-md, 10px);
  overflow: hidden;

  /* DS tokens only (no raw colors/shadows): see designSystem.tokens.css + designSystem.overlays.css */
  background: var(--ds-surface-1);
  border: 1px solid var(--ds-border);
  box-shadow: var(--ds-shadow-soft);
  backdrop-filter: var(--ds-blur);
  -webkit-backdrop-filter: var(--ds-blur);
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
  background: var(--ds-surface-0);
  border-bottom: 1px solid var(--ds-border-subtle);
  user-select: none;
}

.ws-title {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  line-height: 16px;
  color: var(--ds-text-2);
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
  border: 1px solid var(--ds-border-subtle);
  background: var(--ds-surface-0);
  color: var(--ds-text-2);
  cursor: pointer;
}

.ws-close:hover {
  background: var(--ds-surface-hover);
  color: var(--ds-text-1);
}

.ws-close:focus-visible {
  outline: none;
  box-shadow: var(--ds-focus-ring-btn);
}

.ws-body {
  /* Frameless mode: do not impose scrolling/clipping; legacy panels handle it. */
}

.ws-shell--framed .ws-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
</style>

