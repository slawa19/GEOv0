<script setup lang="ts">
/**
 * BUG-5: Interact Mode History Log
 * Displays the last N user actions in a compact inline list. 046 wave 3: a line is drawn from its kept key and data in
 * the language of now (BACKLOG 046-3) - a switch re-renders the lines already shown.
 *
 * 049 S2 (decision 5): with `feedOn` («Показывать события») the log is «События сети»: the network's events beside the
 * person's own actions, and an own line is marked «вы» - the one line known for certain to be the person's (a network
 * line is never marked: its event names no author). Off: exactly the own lines, as before.
 *
 * 049 S2 (CI run 39474653429): the log grows upwards from its bottom anchor, and with five lines and «Подробнее» open at
 * 1024x640 its heading reached the top stack (on CI's fonts the stack's rows wrap and end about 50 px lower). The rule:
 * the log shows as many of its newest lines as fit between its anchor and `topLimitPx` - the bottom edge of the top stack
 * as the root measures it - never more than `maxVisible`, never fewer than one. Measured from the rendered lines (their
 * step), again whenever the lines, the limit, the anchor (`layoutKey`) or the window change; the oldest lines are the ones
 * left out, not rendered (never hidden by a style).
 */
import { computed, nextTick, onBeforeUnmount, onMounted, onUpdated, ref, watch } from 'vue'
import { historyEntryParts, historyEntryText, type HistoryLinePart, type InteractHistoryEntry } from '../composables/interact/useInteractHistory'
import { useUiLocale } from '../composables/useUiLocale'

const props = withDefaults(
  defineProps<{
    entries: InteractHistoryEntry[]
    /** Max visible entries (default 8). */
    maxVisible?: number
    /** 049 S2: the network's events are shown (the title, and the «вы» mark of own lines). */
    feedOn?: boolean
    /** 049 S2: the viewport y the heading must stay below (the top stack's bottom edge); null - no limit. */
    topLimitPx?: number | null
    /** 049 S2: changes whenever the log's bottom anchor may have moved (the bottom stack, «Подробнее»). */
    layoutKey?: string
  }>(),
  { maxVisible: 8, feedOn: false, topLimitPx: null, layoutKey: '' },
)
const { t, locale } = useUiLocale()

const rootEl = ref<HTMLElement | null>(null)
/** How many lines fit under the top stack (`Infinity` until measured or without a limit). */
const fitCount = ref(Number.POSITIVE_INFINITY)

const visible = computed(() => {
  // Show most recent entries at the top
  const e = props.entries
  const n = Math.min(props.maxVisible, fitCount.value)
  return e.slice(Math.max(0, e.length - n)).reverse()
})

function measureFit(): void {
  const el = rootEl.value
  const limit = props.topLimitPx
  if (!el || limit === null || limit === undefined || !Number.isFinite(limit)) {
    if (fitCount.value !== Number.POSITIVE_INFINITY) fitCount.value = Number.POSITIVE_INFINITY
    return
  }
  const items = el.querySelectorAll<HTMLElement>('.interact-history__item')
  if (items.length === 0) return
  const box = el.getBoundingClientRect()
  const first = items[0]!.getBoundingClientRect()
  const last = items[items.length - 1]!.getBoundingClientRect()
  const list = el.querySelector('.interact-history__list')
  const gap = list ? Number.parseFloat(getComputedStyle(list).rowGap) || 0 : 0
  const step = items.length > 1 ? items[1]!.getBoundingClientRect().top - first.top : first.height + gap
  if (!(step > 0)) return
  // Bottom-anchored: with k lines the heading's top is `box.bottom - tail - first.height - (k - 1) * step - head`; it
  // stays a gap below the limit.
  const head = first.top - box.top
  const tail = box.bottom - last.bottom
  const room = box.bottom - limit - gap - tail - head - first.height
  const k = Math.max(1, Math.floor(room / step) + 1)
  if (k !== fitCount.value) fitCount.value = k
}
const onResize = () => measureFit()
onMounted(() => {
  measureFit()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => window.removeEventListener('resize', onResize))
onUpdated(measureFit)
watch(() => [props.topLimitPx, props.layoutKey, props.maxVisible], () => void nextTick(measureFit))

/** The text of a line: an own line is marked «вы» while the network's events are beside it. */
function lineText(entry: InteractHistoryEntry): string {
  const text = historyEntryText(entry, locale.value)
  return props.feedOn && entry.source === 'own' ? `${t('feed.you')} · ${text}` : text
}

/**
 * The same line as its parts (049 decision 9): the names give way first, then a series' count; the amount with its unit,
 * the reason of a failure and a clearing's figures never do. The «вы» mark is a value: it says whose the line is.
 */
function lineParts(entry: InteractHistoryEntry): HistoryLinePart[] {
  const parts = historyEntryParts(entry, locale.value)
  return props.feedOn && entry.source === 'own' ? [{ text: `${t('feed.you')} · `, role: 'value' }, ...parts] : parts
}
</script>

<template>
  <div
    v-if="entries.length > 0"
    ref="rootEl"
    class="interact-history"
    :aria-label="feedOn ? t('recentActions.feedAria') : t('recentActions.aria')"
    data-testid="recent-actions"
    :data-feed="feedOn ? 'on' : 'off'"
  >
    <div class="interact-history__header ds-label">{{ feedOn ? t('recentActions.feedTitle') : t('recentActions.title') }}</div>
    <ul class="interact-history__list">
      <li
        v-for="(entry, i) in visible"
        :key="entry.id"
        class="interact-history__item"
        :class="{ 'interact-history__item--latest': i === 0 }"
        :data-source="entry.source"
      >
        <span class="interact-history__icon" aria-hidden="true">{{ entry.icon }}</span>
        <span class="interact-history__text" :title="lineText(entry)"
          ><span
            v-for="(part, k) in lineParts(entry)"
            :key="k"
            :class="`interact-history__${part.role}`"
            >{{ part.text }}</span
          ></span
        >
        <span class="interact-history__time ds-mono">{{ entry.timeText }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
/*
  049 S1 (decisions 4 and 9): the history stands at the right edge of the screen (on the control line, placed by
  `SimulatorAppRoot.vue`), so its content is aligned to that side - the heading and every line end on the line, a shorter
  line starts further right. A long line is cut by an ellipsis inside a bounded width, its whole text is its `title`.
  049 decision 9 (owner's review of the final frames, 2026-10-20): the cut falls on the names - one ellipsis per name, the
  names first, then a series' count; the value part (the amount with its unit, a failure's reason, a clearing's figures)
  never (below). One line per entry: the fit rule above counts lines of one height.
*/
.interact-history {
  pointer-events: none;
  user-select: none;
  max-width: min(28rem, 45vw);
}

.interact-history__header {
  font-size: var(--ds-typo-section-label-font-size);
  opacity: var(--ds-ihl-header-opacity);
  text-transform: uppercase;
  letter-spacing: var(--ds-typo-section-label-letter-spacing);
  margin-bottom: var(--ds-ihl-header-margin-bottom);
  text-align: right;
}

.interact-history__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--ds-ihl-list-gap);
}

.interact-history__item {
  display: flex;
  justify-content: flex-end;
  align-items: baseline;
  gap: var(--ds-ihl-item-gap);
  font-size: var(--ds-ihl-item-font-size);
  opacity: var(--ds-ihl-item-opacity);
  transition: opacity var(--ds-dur-slow);
}

.interact-history__item--latest {
  opacity: 1;
}

.interact-history__icon {
  flex-shrink: 0;
  font-size: var(--ds-ihl-icon-font-size);
  min-width: var(--ds-ihl-icon-minw);
  text-align: center;
}

.interact-history__text {
  display: flex;
  flex: 0 1 auto;
  min-width: 0;
  /* The one part of the history a pointer reaches: the `title` of a cut line (decision 9). */
  pointer-events: auto;
  overflow: hidden;
  white-space: pre;
}

/*
  Who gives way: the names - each keeps its first letter and the ellipsis - and, only once they can give no more, a series'
  count (the shrink factor is proportional to it and to the width, so the names' 10000 against the count's 1 is an order
  in practice). A value never shrinks (internal adversarial pass, 2026-10-21: values that shrank by weight were cut together
  with the count). Every line of the dictionaries fits with its names at their least in the hud's monospace at 1024x640 -
  held for the lines the stand draws by `e2e/qa/debt-before-after.qa.ts` and for every key by
  `src/composables/feedLineBudget.p049.test.ts`.
*/
.interact-history__name,
.interact-history__lead {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.interact-history__name {
  flex: 0 10000 auto;
  min-width: 2ch;
}

.interact-history__lead {
  flex: 0 1 auto;
}

.interact-history__value {
  flex: none;
}

.interact-history__time {
  flex-shrink: 0;
  font-size: var(--ds-ihl-time-font-size);
  opacity: var(--ds-ihl-time-opacity);
}
</style>
