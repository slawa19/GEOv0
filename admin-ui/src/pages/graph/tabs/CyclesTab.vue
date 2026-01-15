<template>
  <el-alert
    v-if="!analyticsEq"
    title="Pick an equivalent (not ALL) to inspect clearing cycles."
    type="info"
    show-icon
    class="mb"
  />
  <el-empty
    v-else-if="selectedCycles.length === 0"
    description="No cycles found in fixtures"
  />

  <div
    v-else
    class="cycles"
  >
    <div class="hint">
      <TooltipLabel
        label="Clearing cycles"
        tooltip-text="Each cycle is a directed loop in the debt graph for the selected equivalent. Clearing reduces each edge by the shown amount."
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
          :label="`Cycle #${idx + 1}`"
          tooltip-text="A cycle is a set of debts that can be cleared together while preserving net positions (cycle cancelation)."
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

type CycleEdge = { debtor: string; creditor: string; equivalent: string; amount: string }

defineProps<{
  analyticsEq: boolean
  selectedCycles: CycleEdge[][]
  isCycleActive: (cycle: CycleEdge[]) => boolean
  toggleCycleHighlight: (cycle: CycleEdge[]) => void
  money: (value: string) => string
}>()
</script>
