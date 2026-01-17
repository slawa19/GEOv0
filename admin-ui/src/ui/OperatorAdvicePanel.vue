<script setup lang="ts">
import { computed } from 'vue'
import { t } from '../i18n'
import type { AdviceItem } from '../advice/operatorAdvice'

const props = defineProps<{
  items: AdviceItem[]
  titleKey?: string
  showTitle?: boolean
}>()

const title = computed(() => (props.titleKey ? t(props.titleKey) : t('advice.panel.title')))
const showTitle = computed(() => props.showTitle ?? true)

function severityToAlertType(sev: AdviceItem['severity']): 'info' | 'warning' | 'error' {
  if (sev === 'danger') return 'error'
  if (sev === 'warning') return 'warning'
  return 'info'
}
</script>

<template>
  <div v-if="items.length" class="advicePanel">
    <div v-if="showTitle" class="advicePanel__title">{{ title }}</div>

    <div class="advicePanel__items">
      <el-alert
        v-for="it in items"
        :key="it.id"
        :type="severityToAlertType(it.severity)"
        show-icon
        class="advicePanel__item"
        :title="t(it.titleKey)"
      >
        <template #default>
          <div class="advicePanel__body">{{ t(it.bodyKey, it.bodyVars || {}) }}</div>
          <div v-if="it.actions && it.actions.length" class="advicePanel__actions">
            <router-link
              v-for="a in it.actions"
              :key="a.id"
              :to="a.to"
              custom
              v-slot="{ navigate }"
            >
              <el-button
                size="small"
                type="primary"
                plain
                @click="navigate"
              >
                {{ t(a.labelKey) }}
              </el-button>
            </router-link>
          </div>
        </template>
      </el-alert>
    </div>
  </div>
</template>

<style scoped>
.advicePanel {
  margin-bottom: 12px;
}

.advicePanel__title {
  font-size: 12px;
  opacity: 0.85;
  margin-bottom: 8px;
}

.advicePanel__items {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.advicePanel__item {
  margin: 0;
}

.advicePanel__body {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.35;
  opacity: 0.9;
}

.advicePanel__actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
