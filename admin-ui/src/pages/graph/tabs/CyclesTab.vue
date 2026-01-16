<template>
  <el-alert
    v-if="!analyticsEq"
    :title="t('graph.analytics.cycles.pickEquivalentTitle')"
    type="info"
    show-icon
    class="mb"
  />
  <el-empty
    v-else-if="selectedCycles.length === 0"
    :description="t('graph.analytics.cycles.noneInFixtures')"
  />

  <div
    v-else
    class="cycles"
  >
    <div class="hint">
      <TooltipLabel
        :label="t('graph.analytics.cycles.title')"
        :tooltip-text="t('graph.analytics.cycles.titleTooltip')"
      />
    </div>

    <div
      v-for="(c, idx) in selectedCycles"
      :key="idx"
      class="cycleItem"
      :class="{ 'cycleItem--active': isCycleActive(c) }"
      @click="toggleCycleHighlight(c)"
    >
      <div class="cycleTitle">
        <TooltipLabel
          :label="t('graph.analytics.cycles.cycleNumber', { n: idx + 1 })"
          :tooltip-text="t('graph.analytics.cycles.cycleTooltip')"
        />
      </div>
      <div
        v-for="(e, j) in c"
        :key="j"
        class="cycleEdge"
      >
        <span class="mono">{{ e.debtor }}</span>
        <span class="muted">→</span>
        <span class="mono">{{ e.creditor }}</span>
        <span class="muted">({{ e.equivalent }} {{ money(e.amount) }})</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import TooltipLabel from '../../../ui/TooltipLabel.vue'
import { t } from '../../../i18n/en'

type CycleEdge = { debtor: string; creditor: string; equivalent: string; amount: string }

defineProps<{
  analyticsEq: boolean
  selectedCycles: CycleEdge[][]
  isCycleActive: (cycle: CycleEdge[]) => boolean
  toggleCycleHighlight: (cycle: CycleEdge[]) => void
  money: (value: string) => string
}>()
</script>
