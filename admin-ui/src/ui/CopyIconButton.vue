<script setup lang="ts">
import { computed } from 'vue'
import { ElMessage } from 'element-plus'
import { copyToClipboard } from '../utils/copyToClipboard'
import { t } from '../i18n'

type Props = {
  text: string
  label?: string
  tooltip?: string
  successText?: string
  stopPropagation?: boolean
  disabled?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  label: '',
  tooltip: undefined,
  successText: undefined,
  stopPropagation: true,
  disabled: false,
})

const tooltipText = computed(() => props.tooltip ?? t('common.copy'))
const successToastText = computed(() => props.successText ?? t('common.copied'))

const ariaLabel = computed(() => {
  if (props.label) return t('common.copyLabel', { label: props.label })
  return t('common.copyToClipboard')
})

async function onClick(e: MouseEvent) {
  if (props.stopPropagation) {
    e.preventDefault()
    e.stopPropagation()
  }

  const r = await copyToClipboard(props.text)
  if (r.ok) {
    ElMessage.success(successToastText.value)
  } else {
    ElMessage.error(r.error || t('common.copyFailed'))
  }
}
</script>

<template>
  <el-tooltip
    :content="tooltipText"
    placement="top"
    effect="dark"
    :show-after="850"
    popper-class="geoTooltip geoTooltip--label"
  >
    <button
      class="copyBtn"
      type="button"
      :aria-label="ariaLabel"
      :disabled="disabled"
      @click="onClick"
    >
      ⧉
    </button>
  </el-tooltip>
</template>

<style scoped>
.copyBtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  padding: 0;
  margin-left: 0;

  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--el-text-color-secondary);

  cursor: pointer;
  user-select: none;
  font: inherit;
  font-size: 0.8em;
  line-height: 1;
}

.copyBtn:hover {
  background: var(--el-fill-color-light);
  color: var(--el-text-color-primary);
}

.copyBtn:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 2px;
}

.copyBtn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
