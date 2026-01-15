<script setup lang="ts">
import { computed } from 'vue'
import { TOOLTIPS, type TooltipKey } from '../content/tooltips'

type Props = {
  label: string
  tooltipKey?: TooltipKey
  tooltipText?: string
}

const props = defineProps<Props>()

const structured = computed(() => {
  if (props.tooltipKey) return TOOLTIPS[props.tooltipKey]
  return null
})

const hasTooltip = computed(() => Boolean(structured.value || props.tooltipText))

const tooltipContent = computed(() => {
  if (props.tooltipText) return props.tooltipText
  if (!structured.value) return ''
  const title = structured.value.title
  const body = (structured.value.body || []).join(' ')
  // Keep it short: two lines max via CSS clamp.
  return title ? `${title}. ${body}`.trim() : body.trim()
})

const ariaLabel = computed(() => {
  const title = structured.value?.title
  if (title) return `Help: ${title}`
  return `Help for ${props.label}`
})

// Note: TooltipLabel is intentionally compact; links are omitted.
// Use dedicated help pages for long-form guidance.
</script>

<template>
  <span class="tl">
    <span class="tl__label">{{ label }}</span>
    <el-tooltip
      v-if="hasTooltip"
      placement="top"
      effect="dark"
      :show-after="850"
      popper-class="geoTooltip geoTooltip--label"
    >
      <template #content>
        <span class="geoTooltipText geoTooltipText--clamp2">{{ tooltipContent }}</span>
      </template>
      <button class="tl__icon" type="button" :aria-label="ariaLabel">?</button>
    </el-tooltip>
  </span>
</template>

<style scoped>
.tl {
  display: inline-flex;
  align-items: baseline;
  flex-wrap: nowrap;
}

.tl__label {
  min-width: 0;
}

.tl__icon {
  display: inline-block;
  color: var(--el-text-color-secondary);
  background: transparent;
  border: 0;
  cursor: help;
  user-select: none;
  padding: 0;
  margin-left: 2px;
  font: inherit;
  font-weight: 600;
  font-size: 10px;
  line-height: 1;
  vertical-align: super;
}

.tl__icon:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}
</style>
