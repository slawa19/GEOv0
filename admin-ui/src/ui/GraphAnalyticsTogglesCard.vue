<script setup lang="ts">
import TooltipLabel from './TooltipLabel.vue'

export type ToggleKey = 'showRank' | 'showDistribution' | 'showConcentration' | 'showCapacity' | 'showBottlenecks' | 'showActivity'

type ToggleItem = {
  key: ToggleKey
  label: string
  tooltipText: string
  requires?: ToggleKey
}

type Props = {
  title: string
  titleTooltipText: string
  enabled: boolean
  modelValue: Record<ToggleKey, boolean>
  items: ToggleItem[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<ToggleKey, boolean>): void
}>()

function isDisabled(item: ToggleItem): boolean {
  if (!props.enabled) return true
  if (item.requires) return !props.modelValue[item.requires]
  return false
}

function setToggle(key: ToggleKey, value: boolean) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}
</script>

<template>
  <el-card
    shadow="never"
    class="mb"
  >
    <template #header>
      <TooltipLabel
        :label="title"
        :tooltip-text="titleTooltipText"
      />
    </template>

    <div class="geoToggleGrid">
      <div
        v-for="item in items"
        :key="item.key"
        class="geoToggleLine"
      >
        <el-switch
          :model-value="modelValue[item.key]"
          size="small"
          :disabled="isDisabled(item)"
          @update:model-value="(v: boolean) => setToggle(item.key, v)"
        />
        <TooltipLabel
          :label="item.label"
          :tooltip-text="item.tooltipText"
        />
      </div>
    </div>
  </el-card>
</template>
