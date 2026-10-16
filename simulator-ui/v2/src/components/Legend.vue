<script setup lang="ts">
/**
 * The legend of the graph (039 slice B, PR 1): what a colour, a sign, a dashed outline, a ring and a shape say.
 *
 * - WHAT IS SHOWN is decided by `legendEntriesFor` - the classifier that paints the nodes - from the nodes of the
 *   SHOWN snapshot: only features that are present, in the fixed order of the vocabulary. No entries, no legend
 *   (an empty scene has nothing to explain). Hover, selection and focus LOD do not change it.
 * - COLOURS are the `palette` prop: the resolved object the renderer paints with, not a second read of the tokens.
 * - THE TITLE names the equivalent of the shown snapshot (`equivalent` prop), not the selector's choice.
 *
 * A swatch repeats a node of the canvas: the body is the data colour dimmed like the node's body, the outline is the
 * colour itself, the sign is the glyph drawn inside the node. Its `background-color` IS the data colour (the dimming
 * is an inset shadow over it), so a test can compare it with the token.
 *
 * The direction of a trust line is not drawn on the canvas and has no entry here: the owner withdrew the direction
 * marker on 2026-10-16 (spec 039, Changelog); the edge tooltip and the edge window name it as "A -> B".
 */
import { computed } from 'vue'

import type { GraphNode } from '../types'
import { LEGEND_HELP, LEGEND_TITLE, LEGEND_VOCABULARY, legendEntriesFor, type DataPalette, type LegendEntryId } from '../vizMapping'

// The file is `Legend.vue` (the name spec 039 gives it); the component's own name is multi-word, as the lint policy asks.
defineOptions({ name: 'GraphLegend' })

const props = defineProps<{
  /** Nodes of the snapshot on screen; `null` / empty when there is no scene. */
  nodes: ReadonlyArray<GraphNode> | null | undefined
  /** The equivalent of that same snapshot; empty when it names none. */
  equivalent: string
  /** The resolved data colours - the object the canvas paints with. */
  palette: DataPalette
}>()

type Swatch = { cls: string; sign: string; color: string | null }

const SWATCH: Record<LegendEntryId, (p: DataPalette) => Swatch> = {
  debtor: (p) => ({ cls: 'ds-data-swatch--role', sign: '−', color: p.debtor }),
  creditor: (p) => ({ cls: 'ds-data-swatch--role', sign: '+', color: p.creditor }),
  neutral: (p) => ({ cls: 'ds-data-swatch--role', sign: '0', color: p.neutral }),
  unknown: (p) => ({ cls: 'ds-data-swatch--role', sign: '?', color: p.neutral }),
  frozen: (p) => ({ cls: 'ds-data-swatch--frozen', sign: '', color: p.frozen }),
  hub: (p) => ({ cls: 'ds-data-swatch--hub', sign: '', color: p.hub }),
  person: () => ({ cls: 'ds-data-swatch--shape', sign: '', color: null }),
  organisation: () => ({ cls: 'ds-data-swatch--shape ds-data-swatch--rect', sign: '', color: null }),
}

const entries = computed(() => {
  const present = new Set(legendEntriesFor(props.nodes ?? []))
  return LEGEND_VOCABULARY.filter((e) => present.has(e.id)).map((e) => ({ ...e, swatch: SWATCH[e.id](props.palette) }))
})

const title = computed(() => (props.equivalent ? `${LEGEND_TITLE} · ${props.equivalent}` : LEGEND_TITLE))

function swatchStyle(s: Swatch): Record<string, string> {
  if (!s.color) return {}
  // A hub is a ring: the colour is its outline only. Everything else is filled with its colour.
  return s.cls.includes('--hub') ? { borderColor: s.color } : { backgroundColor: s.color, borderColor: s.color }
}
</script>

<template>
  <div
    v-if="entries.length"
    class="ds-legend"
    data-testid="legend"
    role="group"
    aria-labelledby="ds-legend-title"
    aria-describedby="ds-legend-help"
  >
    <span id="ds-legend-title" class="ds-legend__title" data-testid="legend-title" :title="LEGEND_HELP">{{ title }}</span>
    <span id="ds-legend-help" class="ds-legend__help">{{ LEGEND_HELP }}</span>
    <ul class="ds-legend__list">
      <li v-for="e in entries" :key="e.id" class="ds-legend__entry" :data-legend-role="e.id">
        <span class="ds-data-swatch" :class="e.swatch.cls" :style="swatchStyle(e.swatch)" data-legend-swatch aria-hidden="true">{{ e.swatch.sign }}</span>
        <span class="ds-legend__text">{{ e.text }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.ds-legend {
  position: relative;
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
  max-width: 100%;
  box-sizing: border-box;
  padding: 3px 10px;
  border: 1px solid var(--ds-border-subtle);
  border-radius: var(--ds-radius-md, 8px);
  background: var(--ds-surface-1);
  color: var(--ds-text-1);
  font-family: var(--ds-font-sans);
  font-size: 12px;
  line-height: 1.3;
}

.ds-legend__title {
  font-weight: 650;
  white-space: nowrap;
}

/* Read by assistive technology (aria-describedby), not shown: the same sentence is the title's tooltip. */
.ds-legend__help {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.ds-legend__list {
  display: contents;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ds-legend__entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}
</style>
