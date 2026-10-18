/**
 * 042 T4205 - the offline effects workbench: the recorded event playlists of the offline snapshot
 * (`public/simulator-fixtures/v1/<EQ>/events/*.json`) are played through THE SAME event path as the server's SSE events,
 * so beams, bursts and clearing pulses can be seen and tuned without a server and a database.
 *
 * "The same path" means: every recorded event is read by the SSE normaliser (`normalizeSimulatorEvent`), applied by
 * `applyAcceptedRealEvent` and its intents executed by `executeRealEventIntents` with the FX functions the live
 * connection is given (`useSimulatorApp.ts`). There is no second renderer and no translation of an old format: a
 * recording the normaliser does not accept is REFUSED as a whole and named, never played in part (AGENTS.md section 9).
 *
 * Only the effect events are allowed in a playlist: `tx.updated`, `tx.failed`, `clearing.done`. A `run_status` belongs to
 * a run (offline there is none) and a `topology.changed` may ask for a snapshot refresh no offline source can answer.
 * Every event must be of the snapshot's equivalent - the folder's own; equivalents are never mixed in what is drawn.
 *
 * What it does NOT do: play on the server source (there the network lives on its own; no playlist is loaded), restore
 * the snapshot after a playlist (the recorded patches stay applied until the scene is loaded again), honour the
 * recorded `ts` (the recordings span days; events are spaced by their own effect length, see `gapAfterMs`).
 */
import { computed, reactive, ref, watch } from 'vue'

import { normalizeSimulatorEvent, type AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { canonicalEquivalent, sameEquivalent } from '../config/equivalents'
import type { ClearingDoneEvent, TxUpdatedEvent } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import {
  applyAcceptedRealEvent,
  createRealEventReplayOwner,
  executeRealEventIntents,
  type RealEventDraft,
  type RealEventStateDeps,
} from './realEventPipeline'

/** The recorded playlists of an offline snapshot, in the order the workbench offers them. */
export const PLAYLISTS = [
  { id: 'demo-tx', label: 'Платежи' },
  { id: 'demo-clearing', label: 'Клиринг' },
] as const

export type PlaylistEvent = Extract<AcceptedSimulatorEvent, { type: 'tx.updated' | 'tx.failed' | 'clearing.done' }>
export type LoadedPlaylist = { id: string; label: string; equivalent: string; events: PlaylistEvent[] }

export type PlaylistParse =
  | { status: 'ok'; playlist: LoadedPlaylist }
  | { status: 'empty' }
  | { status: 'refused'; reason: string }

const PLAYABLE = new Set(['tx.updated', 'tx.failed', 'clearing.done'])

/** Reads one recorded playlist; refuses it whole on the first event the live path would not accept as such. */
export function parsePlaylist(
  raw: unknown,
  meta: { id: string; label: string },
  equivalent: string,
  sourcePath: string,
): PlaylistParse {
  if (!Array.isArray(raw)) return { status: 'refused', reason: `${sourcePath}: a playlist is an array of events` }
  if (raw.length === 0) return { status: 'empty' }
  const events: PlaylistEvent[] = []
  for (let i = 0; i < raw.length; i += 1) {
    const normalized = normalizeSimulatorEvent(raw[i])
    if (normalized.status === 'ignored') {
      const type = normalized.eventType ? ` '${normalized.eventType}'` : ''
      return { status: 'refused', reason: `${sourcePath} events[${i}]${type}: ${normalized.kind}:${normalized.diagnostic}` }
    }
    const event = normalized.event
    if (!PLAYABLE.has(event.type)) {
      return { status: 'refused', reason: `${sourcePath} events[${i}]: '${event.type}' is not an effect event` }
    }
    const evt = event as PlaylistEvent
    if (!sameEquivalent(evt.equivalent, equivalent)) {
      return {
        status: 'refused',
        reason: `${sourcePath} events[${i}]: equivalent '${evt.equivalent}' is not the snapshot's '${equivalent}'`,
      }
    }
    events.push(evt)
  }
  return { status: 'ok', playlist: { id: meta.id, label: meta.label, equivalent: canonicalEquivalent(equivalent), events } }
}

export type PlaylistsOfEquivalent = {
  equivalent: string
  offered: LoadedPlaylist[]
  /** Read and found empty: nothing to play, so not offered. */
  empty: string[]
  /** Not offered, with the reason (a fetch failure, an event the live path refuses, another equivalent). */
  refused: Array<{ id: string; reason: string }>
}

export async function loadPlaylists(
  equivalent: string,
  fetchJson: (path: string) => Promise<unknown>,
  base: string,
): Promise<PlaylistsOfEquivalent> {
  const eq = canonicalEquivalent(equivalent)
  const out: PlaylistsOfEquivalent = { equivalent: eq, offered: [], empty: [], refused: [] }
  for (const meta of PLAYLISTS) {
    const sourcePath = `${base}/${encodeURIComponent(eq)}/events/${meta.id}.json`
    let raw: unknown
    try {
      raw = await fetchJson(sourcePath)
    } catch (e) {
      out.refused.push({ id: meta.id, reason: `${sourcePath}: ${e instanceof Error ? e.message : String(e)}` })
      continue
    }
    const parsed = parsePlaylist(raw, meta, eq, sourcePath)
    if (parsed.status === 'ok') out.offered.push(parsed.playlist)
    else if (parsed.status === 'empty') out.empty.push(meta.id)
    else out.refused.push({ id: meta.id, reason: parsed.reason })
  }
  return out
}

/** The pause after an event before the next one: the event's own effect length plus a gap, so effects do not overlap. */
export const PLAYLIST_GAP_PAD_MS = 400
/** A clearing's edge pulses last 4200 ms (`useRealClearingFx.ts`). */
export const CLEARING_EFFECT_MS = 4200

export function gapAfterMs(event: PlaylistEvent): number {
  if (event.type === 'tx.updated') return (event.ttl_ms ?? 1200) + PLAYLIST_GAP_PAD_MS
  if (event.type === 'clearing.done') return CLEARING_EFFECT_MS + PLAYLIST_GAP_PAD_MS
  return PLAYLIST_GAP_PAD_MS
}

export type PlaylistPlayerState = {
  /** The playlist being played automatically, or null. */
  playingId: string | null
  /** The playlist of the last delivered event and how many of its events were delivered (1-based), for "Шаг". */
  cursorId: string | null
  position: number
  total: number
}

type Timers = {
  setTimeout: (fn: () => void, ms: number) => unknown
  clearTimeout: (handle: unknown) => void
}

const defaultTimers: Timers = {
  setTimeout: (fn, ms) => globalThis.setTimeout(fn, ms),
  clearTimeout: (h) => globalThis.clearTimeout(h as ReturnType<typeof globalThis.setTimeout>),
}

/**
 * Plays a loaded playlist: all of it (`play`, each event after the previous one's `gapAfterMs`) or one event at a time
 * (`step`, wrapping to the first after the last). A playback has its own id, passed with every event: the live path
 * uses it as the "run" a delayed receiver label must still belong to, so a stopped or replaced playback drops them.
 */
export function createPlaylistPlayer(deps: {
  deliver: (event: PlaylistEvent, playbackId: string) => void
  /** A new playback begins (the effect caches of the previous one are reset, as for a new run). */
  onPlaybackStart?: () => void
  /**
   * A playback ends by Stop, a replaced scene or another playlist chosen: its pending effects (the later legs of a beam,
   * the receiver's burst) must not fire. Not called when a playlist simply plays to its end.
   */
  onPlaybackEnd?: () => void
  timers?: Timers
}) {
  const timers = deps.timers ?? defaultTimers
  const state = reactive<PlaylistPlayerState>({ playingId: null, cursorId: null, position: 0, total: 0 })
  let seq = 0
  let playbackId: string | null = null
  let timer: unknown = null

  function clearTimer() {
    if (timer !== null) timers.clearTimeout(timer)
    timer = null
  }

  function begin(playlist: LoadedPlaylist) {
    seq += 1
    playbackId = `playlist:${playlist.equivalent}:${playlist.id}:${seq}`
    state.cursorId = playlist.id
    state.position = 0
    state.total = playlist.events.length
    deps.onPlaybackStart?.()
  }

  function deliverAt(playlist: LoadedPlaylist, index: number) {
    const event = playlist.events[index]!
    state.position = index + 1
    deps.deliver(event, playbackId!)
    return event
  }

  function stop() {
    clearTimer()
    state.playingId = null
    if (playbackId !== null) deps.onPlaybackEnd?.()
    playbackId = null
  }

  function play(playlist: LoadedPlaylist) {
    stop()
    if (playlist.events.length === 0) return
    begin(playlist)
    state.playingId = playlist.id
    const run = (index: number) => {
      timer = null
      const event = deliverAt(playlist, index)
      if (index + 1 >= playlist.events.length) {
        state.playingId = null
        return
      }
      timer = timers.setTimeout(() => run(index + 1), gapAfterMs(event))
    }
    run(0)
  }

  function step(playlist: LoadedPlaylist) {
    if (playlist.events.length === 0) return
    if (state.playingId !== null) stop()
    // A wrap after the last event is a fresh playback, as Play is: a new id and reset effect caches - otherwise the
    // clearing dedup (by plan id, `useRealClearingFx.ts`) would swallow a replayed clearing.
    if (playbackId === null || state.cursorId !== playlist.id || state.position >= playlist.events.length) begin(playlist)
    deliverAt(playlist, state.position)
  }

  /** Forget the cursor and the playback (the scene was replaced: its playlists are of another snapshot). */
  function reset() {
    stop()
    state.cursorId = null
    state.position = 0
    state.total = 0
  }

  return { state, play, step, stop, reset, activePlaybackId: () => playbackId }
}

/**
 * The delivery of a recorded event: the live connection's own steps (`useSimulatorRealMode.ts`): the replay owner
 * admits the event (a duplicate id within one playback, an event of another equivalent than the snapshot's, or of a
 * playback that is no longer the active one is not applied), then apply, commit, execute - with the same FX functions. The run counters the live path keeps are a private draft here:
 * a playlist is not a run, and the server's counters are not touched. The playback id stands for the run id, so a
 * delayed receiver label of a stopped or replaced playback is dropped by the live path's own guard.
 */
export function createLivePathDelivery(deps: {
  state: SimulatorAppState
  patchApplier: RealEventStateDeps['patchApplier']
  isUserFacingRunError: (code: string) => boolean
  inc: (map: Record<string, number>, key: string) => void
  getActivePlaybackId: () => string | null
  optionalFxEnabled?: () => boolean
  wakeUp: () => void
  runRealTxFx: (event: TxUpdatedEvent) => void
  runRealClearingDoneFx: (event: ClearingDoneEvent) => void
  pushTxAmountLabel: (nodeId: string, amount: string, unit: string, opts: { throttleMs: number }) => void
  clampRealTxTtlMs: (ttl: unknown) => number
  scheduleTimeout: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => void
}): (event: PlaylistEvent, playbackId: string) => void {
  const draftReal: RealEventDraft['real'] = {
    runId: null,
    runStatus: null,
    lastError: '',
    runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
  }
  const replayOwner = createRealEventReplayOwner()
  return (event, playbackId) => {
    const admission = replayOwner.admit({
      event,
      connectionRunId: playbackId,
      activeRunId: deps.getActivePlaybackId(),
      connectionEquivalent: deps.state.snapshot?.equivalent ?? '',
      currentCursor: null,
    })
    if (admission.status !== 'accepted') return
    draftReal.runId = playbackId
    const intents = applyAcceptedRealEvent(event, playbackId, { real: draftReal, state: deps.state }, {
      patchApplier: deps.patchApplier,
      isUserFacingRunError: deps.isUserFacingRunError,
      inc: deps.inc,
    })
    replayOwner.commit(event.event_id, playbackId)
    executeRealEventIntents(intents, {
      getActiveRunId: deps.getActivePlaybackId,
      getSceneEquivalent: () => deps.state.snapshot?.equivalent ?? null,
      optionalFxEnabled: deps.optionalFxEnabled,
      // Asked only by a `topology.changed` without patches, which a playlist cannot hold (`parsePlaylist`).
      refreshSnapshot: () => undefined,
      wakeUp: deps.wakeUp,
      runRealTxFx: deps.runRealTxFx,
      runRealClearingDoneFx: deps.runRealClearingDoneFx,
      pushTxAmountLabel: deps.pushTxAmountLabel,
      clampRealTxTtlMs: deps.clampRealTxTtlMs,
      scheduleTimeout: deps.scheduleTimeout,
    })
  }
}

export type PlaylistWorkbenchOption = { id: string; label: string; count: number }

/**
 * The workbench of the offline source: loads the playlists of the snapshot's equivalent while the source is the offline
 * snapshot, and offers only those it can play (no control that would do nothing).
 */
export function usePlaylistWorkbench(deps: {
  isOfflineSource: () => boolean
  /** The equivalent of the snapshot on screen; null while there is none. */
  getSnapshotEquivalent: () => string | null
  /** The identity of the scene on screen: a new value stops the player (its playlists are of the old scene). */
  getSceneIdentity: () => unknown
  fetchJson: (path: string) => Promise<unknown>
  base: string
  deliver: (event: PlaylistEvent, playbackId: string) => void
  /**
   * The effect caches of the live path: a new playback resets both (as a new run does); an ended one invalidates the
   * pending tx effects (`resetTxFx` bumps the generation the beam legs and the receiver burst check).
   */
  fx: { resetTxFx: () => void; resetClearingDedup: () => void }
  warn?: (message: string, context: Record<string, unknown>) => void
  timers?: Timers
}) {
  const player = createPlaylistPlayer({
    deliver: deps.deliver,
    onPlaybackStart: () => {
      deps.fx.resetClearingDedup()
      deps.fx.resetTxFx()
    },
    onPlaybackEnd: () => deps.fx.resetTxFx(),
    timers: deps.timers,
  })
  const choice = ref('')
  const loaded = reactive<{ equivalent: string | null; playlists: LoadedPlaylist[] }>({ equivalent: null, playlists: [] })
  let loadSeq = 0

  watch(
    () => (deps.isOfflineSource() ? canonicalEquivalent(deps.getSnapshotEquivalent()) : ''),
    async (eq) => {
      const mine = ++loadSeq
      player.reset()
      loaded.equivalent = null
      loaded.playlists = []
      if (!eq) return
      const result = await loadPlaylists(eq, deps.fetchJson, deps.base)
      if (mine !== loadSeq) return
      loaded.equivalent = result.equivalent
      loaded.playlists = result.offered
      for (const r of result.refused) (deps.warn ?? console.warn)('offline playlist not offered', { id: r.id, reason: r.reason })
    },
    { immediate: true },
  )

  watch(deps.getSceneIdentity, () => player.stop())

  const find = (id: string) => loaded.playlists.find((p) => p.id === id) ?? null

  /** Plays only a playlist of the snapshot's own equivalent: the scene may have changed since the load. */
  function playable(id: string): LoadedPlaylist | null {
    const pl = find(id)
    if (!pl || !sameEquivalent(pl.equivalent, deps.getSnapshotEquivalent())) return null
    return pl
  }

  const offered = computed<PlaylistWorkbenchOption[]>(() =>
    loaded.playlists.map((p) => ({ id: p.id, label: p.label, count: p.events.length })),
  )

  /** The playlist the controls act on: the chosen one while it is offered, else the first offered. */
  const selectedId = computed(() => offered.value.find((p) => p.id === choice.value)?.id ?? offered.value[0]?.id ?? '')

  return reactive({
    offered,
    selectedId,
    /** Another playlist chosen: the current playback stops (its pending effects with it). */
    select: (id: string) => {
      if (id === selectedId.value) return
      player.stop()
      choice.value = id
    },
    playingId: computed(() => player.state.playingId),
    cursorId: computed(() => player.state.cursorId),
    /** The position in the SELECTED playlist (0 until it is played), and its length. */
    position: computed(() => (player.state.cursorId === selectedId.value ? player.state.position : 0)),
    total: computed(() => offered.value.find((p) => p.id === selectedId.value)?.count ?? 0),
    play: (id: string) => {
      const pl = playable(id)
      if (pl) player.play(pl)
    },
    step: (id: string) => {
      const pl = playable(id)
      if (pl) player.step(pl)
    },
    stop: () => player.stop(),
    activePlaybackId: () => player.activePlaybackId(),
  })
}

export type PlaylistWorkbench = ReturnType<typeof usePlaylistWorkbench>
