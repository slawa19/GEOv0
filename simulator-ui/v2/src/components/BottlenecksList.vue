<script setup lang="ts">
import { computed } from 'vue'

import type { BottleneckItem, BottleneckTarget } from '../api/simulatorTypes'

const props = defineProps<{
  items: BottleneckItem[]
  /** Resolves a participant id to a display name; falls back to the id when unknown. */
  getNodeName?: (id: string) => string | null
}>()

const emit = defineEmits<{
  (e: 'focus-bottleneck', target: BottleneckTarget): void
}>()

function nameOf(id: string): string {
  const resolved = String(props.getNodeName?.(id) ?? '').trim()
  return resolved || id
}

function targetLabel(target: BottleneckTarget): string {
  return target.kind === 'edge'
    ? `${nameOf(target.from)} → ${nameOf(target.to)}`
    : nameOf(target.id)
}

function targetKey(target: BottleneckTarget): string {
  return target.kind === 'edge' ? `edge:${target.from}->${target.to}` : `node:${target.id}`
}

/**
 * `score` is a plain `number` on the wire (`BottleneckItem.score`), not money — it is a 0..1
 * ranking weight, so ordinary numeric formatting is correct here. The decimal-string discipline
 * applies to `MetricPoint.v`, which this component never touches.
 */
function scorePercent(score: number): string {
  if (!Number.isFinite(score)) return '—'
  return `${Math.round(score * 100)}%`
}

/**
 * Severity thresholds come from the product spec (section 5, `>= 0.6` / `>= 0.3`); the colours it
 * hardcoded there are expressed as DS badge modifiers instead of literals.
 */
function severityModifier(score: number): string {
  if (!Number.isFinite(score)) return 'ds-badge--info'
  if (score >= 0.6) return 'ds-badge--err'
  if (score >= 0.3) return 'ds-badge--warn'
  return 'ds-badge--ok'
}

const list = computed<BottleneckItem[]>(() => props.items ?? [])
</script>

<template>
  <div class="bnl" :data-count="list.length">
    <p v-if="list.length === 0" class="bnl__empty ds-muted" data-state="empty">
      No bottlenecks reported
    </p>
    <ul v-else class="bnl__list">
      <li
        v-for="item in list"
        :key="`${targetKey(item.target)}:${item.reason_code}`"
        class="bnl__item ds-subpanel"
        :data-reason-code="item.reason_code"
        :data-target-kind="item.target.kind"
      >
        <div class="bnl__head">
          <span class="bnl__target ds-mono">{{ targetLabel(item.target) }}</span>
          <span class="bnl__score ds-badge" :class="severityModifier(item.score)">
            {{ scorePercent(item.score) }}
          </span>
        </div>
        <div class="bnl__reason ds-section-label">{{ item.reason_code }}</div>
        <p v-if="item.label" class="bnl__label ds-help">{{ item.label }}</p>
        <p v-if="item.suggested_action" class="bnl__action ds-help">{{ item.suggested_action }}</p>
        <div class="bnl__actions">
          <button
            type="button"
            class="ds-btn ds-btn--ghost ds-btn--sm"
            :aria-label="`Focus ${targetLabel(item.target)}`"
            @click="emit('focus-bottleneck', item.target)"
          >
            Focus
          </button>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
/* Layout only: colours, radii and spacing all come from DS tokens/primitives. */
.bnl__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-2);
}

.bnl__item {
  display: flex;
  flex-direction: column;
  gap: var(--ds-space-1);
  padding: var(--ds-space-2);
}

.bnl__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--ds-space-2);
  min-width: 0;
}

.bnl__target {
  overflow-wrap: anywhere;
  min-width: 0;
}

.bnl__empty,
.bnl__label,
.bnl__action {
  margin: 0;
}

.bnl__actions {
  display: flex;
  justify-content: flex-end;
}
</style>
