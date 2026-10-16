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
 * THE DIRECTION OF A TRUST LINE (PR 2) is the last entry, after the shapes: an arrow and `Trust → potential debtor`.
 * It is shown when the snapshot has trust lines - the parent hands over `trustLineColor`, the colour the canvas draws
 * their bodies with (`linkColorOf`), and nothing when there are none. The shaft is that colour; the HEAD is the
 * colour and the size the canvas draws the marker with (`palette.direction`, `ARROW`; revised 2026-10-16). The arrow
 * points at the words that name the end it stands at on the canvas: the debtor. It is not a participant feature and
 * not part of `LEGEND_VOCABULARY`.
 */
import { computed } from 'vue'

import { ARROW } from '../render/linkArrowhead'
import type { GraphNode } from '../types'
import { LEGEND_HELP, LEGEND_TITLE, LEGEND_VOCABULARY, legendEntriesFor, TRUST_DIRECTION_TEXT, type DataPalette, type LegendEntryId } from '../vizMapping'

// The file is `Legend.vue` (the name spec 039 gives it); the component's own name is multi-word, as the lint policy asks.
defineOptions({ name: 'GraphLegend' })

const props = defineProps<{
  /** Nodes of the snapshot on screen; `null` / empty when there is no scene. */
  nodes: ReadonlyArray<GraphNode> | null | undefined
  /** The equivalent of that same snapshot; empty when it names none. */
  equivalent: string
  /** The resolved data colours - the object the canvas paints with. */
  palette: DataPalette
  /** The colour trust lines and their direction markers are drawn with; `null` / empty when the snapshot has no lines. */
  trustLineColor?: string | null
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

/** The arrow of the direction entry: a 15 px shaft and the canvas marker's own head (length x width), tip at the right. */
const arrowSvg = (() => {
  const shaft = 15
  const h = Math.max(8, Math.ceil(ARROW.widthPx) + 2)
  const y = h / 2
  const base = 1 + shaft
  const tip = base + ARROW.lengthPx
  const half = ARROW.widthPx / 2
  return { w: Math.ceil(tip) + 1, h, y, base, points: `${tip},${y} ${base},${y - half} ${base},${y + half}` }
})()

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
        <span class="ds-data-swatch" :class="e.swatch.cls" :style="swatchStyle(e.swatch)" data-legend-swatch aria-hidden="true">{{ e.swatch.sign }}<svg
          v-if="e.id === 'frozen'"
          class="ds-legend__dash"
          data-legend-dash
          viewBox="0 0 16 16"
          focusable="false"
        ><circle cx="8" cy="8" r="7" pathLength="8" stroke-dasharray="0.56 0.44" /></svg></span>
        <span class="ds-legend__text">{{ e.text }}</span>
      </li>
      <li v-if="trustLineColor" class="ds-legend__entry" data-legend-direction>
        <span class="ds-legend__arrow" data-legend-swatch>
          <!-- The marker of the canvas on a stretch of its line: its head, the tip at the end the words name. -->
          <svg :width="arrowSvg.w" :height="arrowSvg.h" :viewBox="`0 0 ${arrowSvg.w} ${arrowSvg.h}`" aria-hidden="true" focusable="false">
            <line x1="1" :y1="arrowSvg.y" :x2="arrowSvg.base" :y2="arrowSvg.y" :stroke="trustLineColor" stroke-width="1.25" />
            <polygon :points="arrowSvg.points" :fill="palette.direction" />
          </svg>
        </span>
        <span class="ds-legend__text">{{ TRUST_DIRECTION_TEXT }}</span>
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

/*
 * The frozen swatch (PR 2, 2026-10-12). A dashed CSS border on a 16 px circle came out as a plain grey disc: the fill
 * lay under the border, so the gaps between the dashes were the fill itself, and the dashes were a pixel and a half.
 * Here the fill stops inside the outline (the gaps show the legend's surface) and the outline is drawn as eight even
 * dashes of a circle, light like the dashed outline of a frozen node on the canvas. The fill is still the freeze
 * colour handed over in the palette; `border-style` stays dashed for whoever reads the computed style.
 */
.ds-legend .ds-data-swatch--frozen {
  position: relative;
  border-color: transparent !important;
  background-clip: padding-box;
}

.ds-legend__dash {
  position: absolute;
  inset: -1.5px;
  width: 16px;
  height: 16px;
  overflow: visible;
  fill: none;
  stroke: rgba(255, 255, 255, 0.92);
  stroke-width: 1.6;
}

.ds-legend__arrow {
  display: inline-flex;
  align-items: center;
  flex: 0 0 auto;
}
</style>
