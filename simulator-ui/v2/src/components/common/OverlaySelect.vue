<script setup lang="ts">
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'

import { useOverlayDropdownFocus } from '../../composables/useOverlayDropdownFocus'
import { useWindowContainerEl } from '../../composables/windowManager/windowContainerContext'

type OverlaySelectOption = {
  value: string | null
  label: string
  disabled?: boolean
}

type Props = {
  id: string
  modelValue: string | null
  options: OverlaySelectOption[]
  triggerLabel: string
  labelledBy?: string
  describedBy?: string
  title?: string
  disabled?: boolean
  placeholder?: string
  surfaceLabel?: string
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
  placeholder: '—',
  surfaceLabel: undefined,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
}>()

const open = ref(false)
const triggerRef = ref<HTMLElement | null>(null)
const surfaceRef = ref<HTMLElement | null>(null)
const surfaceStyle = ref<Record<string, string>>({})

const injectedContainerEl = useWindowContainerEl()

const normalizedOptions = computed<OverlaySelectOption[]>(() => [
  { value: null, label: props.placeholder },
  ...props.options,
])

const selectedLabel = computed(() => {
  const selected = normalizedOptions.value.find((option) => option.value === props.modelValue)
  return selected?.label ?? props.placeholder
})

const teleportTarget = computed<HTMLElement | null>(() => {
  if (injectedContainerEl?.value) return injectedContainerEl.value
  if (typeof document === 'undefined') return null
  return document.body
})

const { onTriggerKeydown, onSurfaceKeydown, closeAndRestoreFocus } = useOverlayDropdownFocus(
  open,
  triggerRef,
  surfaceRef,
)

function setOpen(next: boolean): void {
  open.value = next
  if (!next) return
  void nextTick(() => {
    updateSurfacePosition()
  })
}

function onTriggerClick(): void {
  if (props.disabled) return
  setOpen(!open.value)
}

function emitValue(value: string | null): void {
  emit('update:modelValue', value)
}

function onNativeChange(event: Event): void {
  const target = event.target
  if (!(target instanceof HTMLSelectElement)) return
  emitValue(target.value ? target.value : null)
}

function readPositiveCssPx(el: Element | null, name: `--${string}`, fallback: number): number {
  if (!el || typeof window === 'undefined') return fallback
  const value = window.getComputedStyle(el).getPropertyValue(name).trim()
  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback
}

function onOptionSelect(value: string | null): void {
  emitValue(value)
  closeAndRestoreFocus()
}

function updateSurfacePosition(): void {
  const trigger = triggerRef.value
  const surface = surfaceRef.value
  if (!trigger || !surface) return

  const targetEl = teleportTarget.value
  const bounds = targetEl
    ? targetEl.getBoundingClientRect()
    : new DOMRect(0, 0, window.innerWidth, window.innerHeight)
  const triggerRect = trigger.getBoundingClientRect()
  const gap = readPositiveCssPx(trigger, '--ds-space-2', 8)
  const inset = readPositiveCssPx(trigger, '--ds-space-2', 8)
  const maxSurfaceWidth = readPositiveCssPx(trigger, '--ds-controls-interact-select-max-w', triggerRect.width)
  const viewportMaxWidth = Math.max(0, bounds.width - inset * 2)

  const baseStyle: Record<string, string> = {
    position: injectedContainerEl?.value ? 'absolute' : 'fixed',
    minWidth: `${Math.round(triggerRect.width)}px`,
    maxWidth: `${Math.round(Math.min(maxSurfaceWidth, viewportMaxWidth))}px`,
    left: `${Math.round(triggerRect.left - bounds.left)}px`,
    top: `${Math.round(triggerRect.bottom - bounds.top + gap)}px`,
  }

  surfaceStyle.value = baseStyle

  const surfaceRect = surface.getBoundingClientRect()
  const maxLeft = Math.max(inset, Math.round(bounds.width - surfaceRect.width - inset))
  let left = Math.round(triggerRect.left - bounds.left)
  left = Math.min(Math.max(left, inset), maxLeft)

  let top = Math.round(triggerRect.bottom - bounds.top + gap)
  const upTop = Math.round(triggerRect.top - bounds.top - surfaceRect.height - gap)
  const hasMoreRoomAbove = triggerRect.top - bounds.top > bounds.bottom - triggerRect.bottom
  const wouldOverflowBelow = top + surfaceRect.height > bounds.height - inset
  if (wouldOverflowBelow && (upTop >= inset || hasMoreRoomAbove)) {
    top = Math.max(inset, upTop)
  }

  surfaceStyle.value = {
    ...baseStyle,
    left: `${left}px`,
    top: `${top}px`,
  }
}

function onGlobalPointerDown(event: Event): void {
  if (!open.value) return
  const target = event.target as Node | null
  if (!target) return
  if (triggerRef.value?.contains(target)) return
  if (surfaceRef.value?.contains(target)) return
  open.value = false
}

function onGlobalResize(): void {
  if (!open.value) return
  updateSurfacePosition()
}

watch(() => props.options, () => {
  if (!open.value) return
  void nextTick(() => {
    updateSurfacePosition()
  })
}, { deep: true })

watch(open, (isOpen) => {
  if (!isOpen) return
  void nextTick(() => {
    updateSurfacePosition()
  })
})

if (typeof window !== 'undefined') {
  window.addEventListener('resize', onGlobalResize)
}

if (typeof document !== 'undefined') {
  document.addEventListener('pointerdown', onGlobalPointerDown, true)
}

onUnmounted(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', onGlobalResize)
  }
  if (typeof document !== 'undefined') {
    document.removeEventListener('pointerdown', onGlobalPointerDown, true)
  }
})
</script>

<template>
  <select
    :id="props.id"
    class="overlay-select__native-mirror"
    :value="props.modelValue ?? ''"
    :disabled="props.disabled"
    :aria-describedby="props.describedBy"
    tabindex="-1"
    aria-hidden="true"
    @change="onNativeChange"
  >
    <option
      v-for="option in normalizedOptions"
      :key="option.value ?? '__empty__'"
      :value="option.value ?? ''"
      :disabled="option.disabled"
    >
      {{ option.label }}
    </option>
  </select>

  <button
    :id="`${props.id}__trigger`"
    ref="triggerRef"
    class="ds-select overlay-select__trigger"
    type="button"
    :disabled="props.disabled"
    :aria-label="props.labelledBy ? undefined : props.triggerLabel"
    :aria-labelledby="props.labelledBy"
    :aria-describedby="props.describedBy"
    :title="props.title"
    aria-haspopup="listbox"
    :aria-expanded="open ? 'true' : 'false'"
    :aria-controls="`${props.id}__surface`"
    @click="onTriggerClick"
    @keydown="onTriggerKeydown"
  >
    <span class="overlay-select__label">{{ selectedLabel }}</span>
  </button>

  <Teleport v-if="open && teleportTarget" :to="teleportTarget">
    <div
      :id="`${props.id}__surface`"
      ref="surfaceRef"
      class="ds-panel ds-ov-surface ds-ov-dropdown overlay-select__surface"
      :style="surfaceStyle"
      role="listbox"
      :aria-label="props.surfaceLabel ?? props.triggerLabel"
      tabindex="-1"
      @keydown="onSurfaceKeydown"
      @pointerdown.stop
      @wheel.stop
    >
      <div class="overlay-select__list">
        <button
          v-for="option in normalizedOptions"
          :key="option.value ?? '__empty__surface__'"
          class="overlay-select__option"
          type="button"
          role="option"
          :disabled="option.disabled"
          :aria-selected="option.value === props.modelValue ? 'true' : 'false'"
          :data-dropdown-selected="option.value === props.modelValue ? '1' : '0'"
          :data-option-value="option.value ?? ''"
          @click="onOptionSelect(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay-select__native-mirror {
  display: none;
}

.overlay-select__trigger {
  display: inline-flex;
  width: min(100%, var(--ds-controls-interact-select-max-w));
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
  justify-self: start;
  align-items: center;
  justify-content: space-between;
  text-align: left;
  appearance: none;
}

[data-theme='hud'] .overlay-select__trigger,
[data-theme='shadcn'] .overlay-select__trigger {
  background-image: none;
  padding-right: var(--ds-control-padding-x);
}

.overlay-select__trigger::after {
  content: '';
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  margin-left: 8px;
  background-repeat: no-repeat;
  background-position: center;
  background-size: 14px 14px;
}

[data-theme='hud'] .overlay-select__trigger::after {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%2300e5ff' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E");
}

[data-theme='shadcn'] .overlay-select__trigger::after {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%2371717a' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E");
}

.overlay-select__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.overlay-select__surface {
  z-index: calc(var(--ds-z-inset, 60) + 1);
  --ds-ov-dropdown-minw: 0px;
  --ds-ov-dropdown-maxw: var(--ds-controls-interact-select-max-w);
  padding: 4px 0;
  font-family: var(--ds-typo-control-font-family);
  font-size: var(--ds-typo-control-font-size);
  line-height: 1.2;
  letter-spacing: var(--ds-typo-control-letter-spacing);
  font-weight: var(--ds-typo-control-font-weight);
}

.overlay-select__list {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.overlay-select__option {
  width: 100%;
  min-width: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  padding: 10px 12px;
  font: inherit;
  cursor: pointer;
}

.overlay-select__option[aria-selected='true'] {
  background: color-mix(in srgb, var(--ds-accent) 18%, transparent);
}

.overlay-select__option:hover,
.overlay-select__option:focus-visible {
  outline: none;
  background: color-mix(in srgb, var(--ds-accent) 24%, transparent);
}

.overlay-select__option:disabled {
  cursor: default;
  opacity: 0.55;
}
</style>