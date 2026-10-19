<script setup lang="ts">
import { computed } from 'vue'
import type { PlaylistWorkbenchOption } from '../composables/usePlaylistWorkbench'
import HudBar from './common/HudBar.vue'

type PlaylistWorkbenchProps = {
  offered: readonly PlaylistWorkbenchOption[]
  /** The playlist the controls act on; choosing another stops the current playback (the workbench owns it). */
  selectedId: string
  select: (id: string) => void
  playingId: string | null
  cursorId: string | null
  position: number
  total: number
  play: (id: string) => void
  step: (id: string) => void
  stop: () => void
}

type Props = {
  apiMode: 'fixtures' | 'real'
  /**
   * 043 S2 (decision 2): "Источник" - the server (a live run) or the offline snapshot. The root switches it (a reload:
   * the data pipeline is replaced). Optional only so that a mount without it shows no select. 048 S1: only with
   * `debug`.
   */
  setSource?: (source: 'real' | 'fixtures') => void

  /**
   * 042 T4205: the offline effects workbench - the recorded playlists of the offline snapshot, played through the live
   * event path (`usePlaylistWorkbench.ts`). Shown on the offline source only, and only with a playlist it can play.
   * 048 S1: only with `debug`.
   */
  playlists?: PlaylistWorkbenchProps

  /**
   * 028 F-028-47 (C1): the equivalents the scene can be shown in (`equivalentOptions`; the root narrows it to the one
   * the offline demo snapshots carry). 048 S1 (decision 4): one - its code as text; more - a select.
   */
  equivalentOptions: readonly string[]

  showResetView: boolean
  resetView: () => void

  /**
   * 043 S1: the one "Подробнее" toggle (owned by the root). Off: the bottom row is the legend, the total debt (slot
   * `summary`) and "Reset view". On: the row of service controls above it - EQ, Layout, Labels and "Inspect graph"
   * (slot `navigator`). Hidden means not rendered. 048 S1 (decisions 4 and 5): the Scene select, Refresh of the
   * snapshot, Analytics and Artifacts are gone from here (Analytics and Artifacts to "Admin ▾" of the top bar).
   */
  detailsOpen?: boolean

  /**
   * 048 S1 (decision 5): `?debug=1` (read by `useSimulatorApp`). The developer's controls - "Источник", Quality and the
   * playlist workbench - are rendered only with it.
   */
  debug?: boolean
}

const props = withDefaults(defineProps<Props>(), { detailsOpen: false, debug: false })

const eq = defineModel<string>('eq', { required: true })
const layoutMode = defineModel<string>('layoutMode', { required: true })
const quality = defineModel<string>('quality', { required: true })
const labelsLod = defineModel<string>('labelsLod', { required: true })

// 042 T4205: the playlist the workbench acts on is the workbench's (a change of choice stops the current playback).
const offeredPlaylists = computed(() => (props.debug && props.apiMode !== 'real' ? props.playlists?.offered ?? [] : []))
const playlistId = computed({
  get: () => props.playlists?.selectedId ?? '',
  set: (id: string) => props.playlists?.select(id),
})
/** The position in the selected playlist: "0 / N" until it is played. */
const playlistPosition = computed(() => {
  const pl = props.playlists
  return pl && pl.total > 0 ? `${pl.position} / ${pl.total}` : ''
})
</script>

<template>
  <div class="ds-ov-bottom">
    <!-- 043 S1: the service row, while Details is on; above the main row, which therefore never moves. -->
    <HudBar v-if="props.detailsOpen" variant="ghost" layout="between" class="bb-details-row" aria-label="Bottom bar" data-testid="bottom-details-row">
      <div class="hud-bar__left" aria-label="View settings">
        <div class="ds-row bb-row" aria-label="Equivalent">
        <span class="ds-label">EQ</span>
        <select v-if="props.equivalentOptions.length > 1" v-model="eq" class="ds-select" aria-label="Equivalent" data-testid="equivalent-select">
          <option v-for="code in props.equivalentOptions" :key="code" :value="code">{{ code }}</option>
        </select>
        <span v-else class="ds-value ds-mono" data-testid="equivalent-text">{{ props.equivalentOptions[0] ?? eq }}</span>
      </div>

      <div class="ds-row bb-row" aria-label="Layout">
        <span class="ds-label">Layout</span>
        <select v-model="layoutMode" class="ds-select" aria-label="Layout" data-testid="layout-select">
          <option value="admin-force">Organic cloud</option>
          <option value="community-clusters">Clusters</option>
          <option value="balance-split">Balance</option>
          <option value="type-split">Type</option>
          <option value="status-split">Status</option>
        </select>
      </div>

      <div v-if="props.debug && props.setSource" class="ds-row bb-row" aria-label="Source">
        <span class="ds-label">Источник</span>
        <select
          class="ds-select"
          aria-label="Источник"
          data-testid="source-select"
          :value="apiMode"
          @change="props.setSource?.(($event.target as HTMLSelectElement).value === 'fixtures' ? 'fixtures' : 'real')"
        >
          <option value="real">Сервер</option>
          <option value="fixtures">Снимок без сервера</option>
        </select>
      </div>

      <div v-if="props.debug" class="ds-row bb-row" aria-label="Quality">
        <span class="ds-label">Quality</span>
        <select v-model="quality" class="ds-select" aria-label="Quality" data-testid="quality-select">
          <option value="low">Low</option>
          <option value="med">Med</option>
          <option value="high">High</option>
        </select>
      </div>

      <div class="ds-row bb-row" aria-label="Labels">
        <span class="ds-label">Labels</span>
        <select v-model="labelsLod" class="ds-select" aria-label="Labels" data-testid="labels-select">
          <option value="off">Off</option>
          <option value="selection">Selection</option>
          <option value="neighbors">Neighbors</option>
        </select>
      </div>
    </div>

    <div class="hud-bar__right bb-tools" aria-label="Tools">
      <div
        v-if="props.playlists && offeredPlaylists.length"
        class="ds-row bb-row"
        aria-label="Playlist workbench"
        data-testid="playlist-workbench"
      >
        <span class="ds-label">Плейлист</span>
        <select v-model="playlistId" class="ds-select" aria-label="Плейлист">
          <option v-for="p in offeredPlaylists" :key="p.id" :value="p.id">{{ p.label }} ({{ p.count }})</option>
        </select>
        <button
          v-if="props.playlists.playingId === null"
          class="ds-btn ds-btn--secondary ds-btn--sm"
          type="button"
          title="Проиграть записанные события по очереди"
          @click="props.playlists.play(playlistId)"
        >
          Проиграть
        </button>
        <button v-else class="ds-btn ds-btn--secondary ds-btn--sm" type="button" @click="props.playlists.stop()">Стоп</button>
        <button
          v-if="props.playlists.playingId === null"
          class="ds-btn ds-btn--ghost ds-btn--sm"
          type="button"
          title="Проиграть следующее записанное событие"
          @click="props.playlists.step(playlistId)"
        >
          Шаг
        </button>
        <span v-if="playlistPosition" class="ds-value ds-mono" aria-label="Позиция плейлиста">{{ playlistPosition }}</span>
      </div>

      <div v-if="$slots.navigator" class="bb-navigator"><slot name="navigator" /></div>
    </div>
    </HudBar>

    <!-- 043 S1: the main row - always on screen. 039 slice B: the legend is part of this measured bar. -->
    <HudBar variant="ghost" layout="between" class="bb-main-row">
      <div v-if="$slots.legend" class="ds-ov-bottom__legend bb-main-legend"><slot name="legend" /></div>
      <div class="hud-bar__right bb-main-right">
        <slot name="summary" />
        <button
          v-if="showResetView"
          class="ds-btn ds-btn--secondary ds-btn--sm"
          type="button"
          @click="resetView"
        >
          Reset view
        </button>
      </div>
    </HudBar>
  </div>
</template>

<style scoped>
/* 043 S1: the rows of this bar are as tight as the top stack's (`--ds-hud-bar-py: 2px` there); nested HudBars inherit
   it. Measured at 1366x768 (layout.qa.ts, R9): the main row with 10 px paddings made the first view 81 % canvas. */
.ds-ov-bottom {
  --ds-hud-bar-py: 2px;
}

.bb-row {
  gap: var(--ds-space-2, 8px);
}

.bb-main-legend {
  margin-bottom: 0;
  min-width: 0;
}

.bb-main-right {
  margin-left: auto;
}

/*
  048 S1 (the owner's frame, 2026-10-19): with `?debug=1` the service row wraps (Источник, Quality and, offline, the
  playlist), and its tools group then started the second line at the LEFT - "Inspect graph", the last item, landed in
  the middle of the screen next to the playlist ("Шаг 0/20"), while without debug it sits at the bottom right. The tools
  group takes the free space before it (`margin-left: auto`), so whatever line it lands on, it ends at the right edge,
  and "Inspect graph", its last item, with it.
*/
.bb-tools {
  margin-left: auto;
}

/*
  "Inspect graph" is a button of the row; its panel opens upwards over the scene and does not grow the measured bar.
  048 S1 (the owner's frame, 2026-10-19): closed, the button is IN the horizontal flow of the row, so its place is as
  wide as the button itself - it was a fixed 8.5rem with the button absolutely positioned over it, and in `hud` the
  button is wider (145 px against 136): it reached over the item to its left (the playlist's "0 / 20"). The place has no
  height and sits at the bottom of its line, the button rising from it: the line - and the measured bar - is no taller
  than before. Open, the navigator floats above the WHOLE service row, at the row's right edge: the row is its
  containing block (§15 Codex r3, 2026-10-19: anchored to its zero-height place, the open panel came down over the
  playlist line and the selects to its left).
*/
.bb-details-row {
  position: relative;
}

.bb-navigator {
  min-width: 8.5rem;
  align-self: flex-end;
  height: 0;
  display: flex;
  align-items: flex-end;
  justify-content: flex-end;
}

.bb-navigator :deep(.graph-navigator) {
  position: relative;
  flex: 0 0 auto;
  width: max-content;
  max-width: min(520px, calc(100vw - 2 * var(--ds-ov-inset, 12px)));
}

/* Open: the panel floats above the service row, so it never covers the row's other controls. */
.bb-navigator :deep(.graph-navigator:has(> details[open])) {
  position: absolute;
  right: var(--ds-hud-bar-px, var(--ds-space-3, 12px));
  bottom: calc(100% + var(--ds-space-2, 8px));
}

/* ── Responsive: prevent horizontal overflow on narrow viewports ─────────── */
@media (max-width: 500px) {
  /* Allow rows inside sections to wrap too */
  .bb-row {
    flex-wrap: wrap;
  }

  /* ds-value: no forced nowrap */
  :deep(.ds-value) {
    white-space: normal;
  }
}
</style>
