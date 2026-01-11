<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { TOOLTIPS, type TooltipKey, type TooltipLink } from '../content/tooltips'

type Props = {
  label: string
  tooltipKey?: TooltipKey
  tooltipText?: string
}

const props = defineProps<Props>()
const route = useRoute()

const structured = computed(() => {
  if (props.tooltipKey) return TOOLTIPS[props.tooltipKey]
  return null
})

const hasTooltip = computed(() => Boolean(structured.value || props.tooltipText))

function linkToWithScenario(link: TooltipLink) {
  const currentQuery = route.query ?? {}
  const scenario = typeof currentQuery.scenario === 'string' ? currentQuery.scenario : undefined
  const mergedQuery: Record<string, string> = {
    ...(scenario ? { scenario } : {}),
    ...(link.to.query ?? {}),
  }
  return { path: link.to.path, query: mergedQuery }
}
</script>

<template>
  <span class="tl">
    <span>{{ label }}</span>
    <el-popover v-if="hasTooltip" placement="top" trigger="hover" :show-after="250" :width="360">
      <template #reference>
        <span class="tl__icon" aria-label="help">?</span>
      </template>
      <div v-if="structured" class="tl__content">
        <div class="tl__title">{{ structured.title }}</div>
        <div v-for="(line, idx) in structured.body" :key="idx" class="tl__line">{{ line }}</div>
        <div v-if="structured.links?.length" class="tl__links">
          <router-link
            v-for="(lnk, idx) in structured.links"
            :key="idx"
            class="tl__link"
            :to="linkToWithScenario(lnk)"
          >
            {{ lnk.label }}
          </router-link>
        </div>
      </div>
      <div v-else class="tl__content">
        {{ tooltipText }}
      </div>
    </el-popover>
  </span>
</template>

<style scoped>
.tl {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.tl__icon {
  display: inline-flex;
  width: 12px;
  height: 12px;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  font-size: 10px;
  line-height: 12px;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border: 1px solid var(--el-border-color);
  cursor: help;
  user-select: none;
}

.tl__content {
  max-width: 360px;
  line-height: 1.35;
}

.tl__title {
  font-weight: 600;
  margin-bottom: 6px;
}

.tl__line {
  margin: 2px 0;
}

.tl__links {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 8px;
}

.tl__link {
  color: var(--el-color-primary);
  text-decoration: underline;
}
</style>
