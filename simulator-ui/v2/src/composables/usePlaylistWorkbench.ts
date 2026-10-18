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
 * A recording is played on ITS OWN start snapshot (039 `T3935`, 2026-10-18): the playlists are recorded from a live run
 * (`scripts/record_workbench_playlists.py`) whose participants are the run's own, so they cannot be played on the
 * offline source's `<EQ>/snapshot.json`. Each playlist `<EQ>/events/<id>.json` carries `<EQ>/events/<id>-snapshot.json`,
 * the snapshot the run had before its first event; a playlist without it is refused. Play (and a Step that begins a
 * playback) first shows that snapshot through the offline source's own loader (`createRecordingSceneSource`), then
 * plays; the end of the playback (Stop, another playlist, a replaced scene, or the last effect done) returns to the
 * offline source's snapshot. Nothing else ever shows a recording's scene.
 *
 * What it does NOT do: play on the server source (there the network lives on its own; no playlist is loaded), honour the
 * recorded `ts` (events are spaced by their own effect length, see `gapAfterMs`), check the money of a recording (that is
 * the consistency guard of the committed recordings in `usePlaylistWorkbench.test.ts`).
 */
import { computed, reactive, ref, watch } from 'vue'

import { normalizeSimulatorEvent, type AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { canonicalEquivalent, sameEquivalent } from '../config/equivalents'
import type { ClearingDoneEvent, GraphSnapshot, TxUpdatedEvent } from '../types'
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
export type LoadedPlaylist = {
  id: string
  label: string
  equivalent: string
  events: PlaylistEvent[]
  /** The published path of the recording's start snapshot (`<EQ>/events/<id>-snapshot.json`), shown while it plays. */
  sceneSourcePath?: string
}

/** The published path of the start snapshot a recorded playlist carries (039 `T3935`). */
export function recordingSnapshotPath(base: string, equivalent: string, playlistId: string): string {
  return `${base}/${encodeURIComponent(canonicalEquivalent(equivalent))}/events/${playlistId}-snapshot.json`
}

/** Every participant a recorded event names: its ends, its route or cycle lines, its patches. */
function participantsNamed(event: PlaylistEvent): string[] {
  const e = event as PlaylistEvent & {
    from?: string
    to?: string
    edges?: Array<{ from: string; to: string }>
    cycle_edges?: Array<{ from: string; to: string }>
    node_patch?: Array<{ id: string }>
    edge_patch?: Array<{ source: string; target: string }>
  }
  return [
    e.from ?? '',
    e.to ?? '',
    ...(e.edges ?? []).flatMap((x) => [x.from, x.to]),
    ...(e.cycle_edges ?? []).flatMap((x) => [x.from, x.to]),
    ...(e.node_patch ?? []).map((x) => x.id),
    ...(e.edge_patch ?? []).flatMap((x) => [x.source, x.target]),
  ].filter((id) => id !== '')
}

/**
 * Why a recording's start snapshot cannot carry its events, or null: it is not a snapshot of the playlist's equivalent
 * with nodes, or an event names a participant that is not a node of it (the events would be applied to nobody).
 */
export function recordingSceneProblem(raw: unknown, playlist: LoadedPlaylist, sourcePath: string): string | null {
  const snap = raw as Partial<GraphSnapshot> | null
  if (!snap || typeof snap !== 'object' || !Array.isArray(snap.nodes) || snap.nodes.length === 0) {
    return `${sourcePath}: the recording's start snapshot has no nodes`
  }
  if (!sameEquivalent(snap.equivalent, playlist.equivalent)) {
    return `${sourcePath}: the start snapshot is of '${String(snap.equivalent)}', the playlist of '${playlist.equivalent}'`
  }
  const nodes = new Set(snap.nodes.map((n) => n.id))
  for (let i = 0; i < playlist.events.length; i += 1) {
    const missing = participantsNamed(playlist.events[i]!).find((id) => !nodes.has(id))
    if (missing !== undefined) return `${sourcePath}: events[${i}] names '${missing}', not a node of the start snapshot`
  }
  return null
}

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
    if (parsed.status === 'empty') {
      out.empty.push(meta.id)
      continue
    }
    if (parsed.status === 'refused') {
      out.refused.push({ id: meta.id, reason: parsed.reason })
      continue
    }
    // 039 T3935: a recording is played on its own start snapshot; without a usable one it is not offered.
    const sceneSourcePath = recordingSnapshotPath(base, eq, meta.id)
    let scene: unknown
    try {
      scene = await fetchJson(sceneSourcePath)
    } catch (e) {
      out.refused.push({ id: meta.id, reason: `${sceneSourcePath}: ${e instanceof Error ? e.message : String(e)}` })
      continue
    }
    const problem = recordingSceneProblem(scene, parsed.playlist, sceneSourcePath)
    if (problem !== null) {
      out.refused.push({ id: meta.id, reason: problem })
      continue
    }
    out.offered.push({ ...parsed.playlist, sceneSourcePath })
  }
  return out
}

/**
 * The pause after an event before the next one: the event's own effect length plus a gap, so the drawn effects (beams,
 * pulses, bursts) do not overlap. The amount labels may: they live their CSS fade (2.6 s, a clearing's 4.2 s after its
 * loop), longer than the drawn effect - as before 039 slice C (adversarial pass 2026-10-18).
 */
export const PLAYLIST_GAP_PAD_MS = 400

/**
 * How long an event's effect lasts - asked of the effects themselves (039 slice C, P-3: one duration source, not a
 * copy here): a payment's from its route plan (`useRealTxFx.ts`, `routeEffectMs`), a clearing's from
 * `CLEARING_EFFECT_MS` (`useRealClearingFx.ts`). Until then a payment was spaced by its wire `ttl_ms` and a clearing
 * by a literal 4200 here.
 */
export type EffectLengths = {
  tx: (event: TxUpdatedEvent) => number
  clearingMs: number
  /**
   * 039 T3935: how long the recording's scene stays after the last event's effect before the workbench returns to the
   * offline source's snapshot - the life of the amount labels, which outlive the drawn effect. 0 when not given.
   */
  afterLastMs?: number
}

export function gapAfterMs(event: PlaylistEvent, effects: EffectLengths): number {
  if (event.type === 'tx.updated') return effects.tx(event) + PLAYLIST_GAP_PAD_MS
  if (event.type === 'clearing.done') return effects.clearingMs + PLAYLIST_GAP_PAD_MS
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
 *
 * RECORD OF A DELIBERATE CHANGE (039 `T3935`, 2026-10-18): a playback that reaches its last event now COMPLETES only
 * after that event's effect and `afterLastMs` (`onPlaybackComplete`; `playingId` stays set meanwhile, so Stop is still
 * offered); until then `play` cleared `playingId` at the moment the last event was delivered and a playback never
 * completed. The workbench returns to the offline source's snapshot on completion.
 */
export function createPlaylistPlayer(deps: {
  deliver: (event: PlaylistEvent, playbackId: string) => void
  /** The length of each event's effect, for the pause before the next one (`gapAfterMs`). */
  effects: EffectLengths
  /** A new playback begins (the effect caches of the previous one are reset, as for a new run). */
  onPlaybackStart?: () => void
  /**
   * A playback ends by Stop, a replaced scene or another playlist chosen: its pending effects (the later legs of a beam,
   * the receiver's burst) must not fire. Not called when a playlist simply plays to its end.
   */
  onPlaybackEnd?: () => void
  /** A playback delivered its last event and that event's effect is over (by Play or by the last Step). */
  onPlaybackComplete?: () => void
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
    clearTimer()
    seq += 1
    playbackId = `playlist:${playlist.equivalent}:${playlist.id}:${seq}`
    state.cursorId = playlist.id
    state.position = 0
    state.total = playlist.events.length
    deps.onPlaybackStart?.()
  }

  /** After the last event: the playback completes once its effect (and `afterLastMs`) is over. */
  function completeAfter(event: PlaylistEvent) {
    timer = timers.setTimeout(() => {
      timer = null
      state.playingId = null
      playbackId = null
      deps.onPlaybackComplete?.()
    }, gapAfterMs(event, deps.effects) + (deps.effects.afterLastMs ?? 0))
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
        completeAfter(event)
        return
      }
      timer = timers.setTimeout(() => run(index + 1), gapAfterMs(event, deps.effects))
    }
    run(0)
  }

  /** Whether the next `step(playlist)` begins a new playback (and so, in the workbench, shows its start snapshot). */
  function stepBegins(playlist: LoadedPlaylist): boolean {
    return state.playingId !== null || playbackId === null || state.cursorId !== playlist.id ||
      state.position >= playlist.events.length
  }

  function step(playlist: LoadedPlaylist) {
    if (playlist.events.length === 0) return
    if (state.playingId !== null) stop()
    // A wrap after the last event is a fresh playback, as Play is: a new id and reset effect caches - otherwise the
    // clearing dedup (by plan id, `useRealClearingFx.ts`) would swallow a replayed clearing.
    if (playbackId === null || state.cursorId !== playlist.id || state.position >= playlist.events.length) begin(playlist)
    const event = deliverAt(playlist, state.position)
    if (state.position >= playlist.events.length) completeAfter(event)
  }

  /** Forget the cursor and the playback (the scene was replaced: its playlists are of another snapshot). */
  function reset() {
    stop()
    state.cursorId = null
    state.position = 0
    state.total = 0
  }

  return { state, play, step, stepBegins, stop, reset, activePlaybackId: () => playbackId }
}

/**
 * 039 T3935: the offline source's snapshot loader with the recording's scene in it. `load(eq)` is what the offline
 * source loads: the offline snapshot of `eq` (`fixtures.ts`, `loadSnapshot`) - or, between `enter` and `leave`, the
 * recording's start snapshot for that same equivalent, by the same loader (`loadSnapshotAt`). `enter` and `leave`
 * reload the scene through the app's own scene load, so the scene is replaced as any other scene is.
 */
export function createRecordingSceneSource(deps: {
  loadOfflineSnapshot: (eq: string) => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>
  loadSnapshotAt: (sourcePath: string) => Promise<{ snapshot: GraphSnapshot; sourcePath: string }>
  /** The app's scene load (it asks `load` for the snapshot of the current equivalent). */
  reloadScene: () => Promise<void>
  /** The source of the scene on screen (`state.sourcePath`). */
  getSceneSourcePath: () => string
}) {
  let recording: { equivalent: string; sourcePath: string } | null = null
  return {
    load: (eq: string) =>
      recording !== null && sameEquivalent(recording.equivalent, eq)
        ? deps.loadSnapshotAt(recording.sourcePath)
        : deps.loadOfflineSnapshot(eq),
    /** Shows the recording's start snapshot; true once it is the scene on screen. */
    enter: async (equivalent: string, sourcePath: string): Promise<boolean> => {
      recording = { equivalent: canonicalEquivalent(equivalent), sourcePath }
      await deps.reloadScene()
      return recording?.sourcePath === sourcePath && deps.getSceneSourcePath() === sourcePath
    },
    /** Back to the offline source's snapshot; nothing when no recording was asked for. */
    leave: () => {
      if (recording === null) return
      recording = null
      void deps.reloadScene()
    },
    /** The recording's start snapshot asked for, or null. */
    shown: () => recording?.sourcePath ?? null,
  }
}

export type RecordingSceneSource = ReturnType<typeof createRecordingSceneSource>

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
  routeArrivalMs: (event: TxUpdatedEvent) => number
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
      routeArrivalMs: deps.routeArrivalMs,
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
   * pending effects of both (`resetTxFx` bumps the generation the beam legs and the receiver burst check,
   * `resetClearingDedup` the one the clearing loop and its after-step check).
   */
  fx: { resetTxFx: () => void; resetClearingDedup: () => void }
  /** The length of each event's effect, from the effects themselves (`gapAfterMs`). */
  effects: EffectLengths
  /**
   * 039 T3935: the recording's own scene (`createRecordingSceneSource`): `enter` shows a recording's start snapshot and
   * resolves true once it is on screen; `leave` returns to the offline source's snapshot.
   */
  recordingScene: { enter: (equivalent: string, sourcePath: string) => Promise<boolean>; leave: () => void }
  warn?: (message: string, context: Record<string, unknown>) => void
  timers?: Timers
}) {
  const warn = (message: string, context: Record<string, unknown>) => (deps.warn ?? console.warn)(message, context)
  /** Bumped by every scene request of the workbench: a scene that finishes loading after a newer request is not played. */
  let sceneSeq = 0
  /** A recording's scene is being loaded: that scene change is the workbench's own and does not stop it. */
  let entering = false
  /** A new playback is replacing the current one: its scene is entered at once, not left first. */
  let replacing = false

  function leaveScene() {
    sceneSeq += 1
    entering = false
    deps.recordingScene.leave()
  }

  const player = createPlaylistPlayer({
    deliver: deps.deliver,
    effects: deps.effects,
    onPlaybackStart: () => {
      deps.fx.resetClearingDedup()
      deps.fx.resetTxFx()
    },
    // 039 slice C: a clearing has pending effects now too (its spark goes round the cycle on timers, then the pulses and
    // the amount): an ended playback cancels them as it cancels the legs of a beam.
    onPlaybackEnd: () => {
      deps.fx.resetTxFx()
      deps.fx.resetClearingDedup()
      if (!replacing) leaveScene()
    },
    onPlaybackComplete: () => leaveScene(),
    timers: deps.timers,
  })

  /** Ends the playback and any scene request of the workbench: the offline source's snapshot comes back. */
  function halt() {
    const wasEntering = entering
    player.stop()
    if (wasEntering) leaveScene()
  }

  /** Shows the recording's start snapshot, then begins its playback with `begin` - unless something newer came first. */
  async function onItsScene(pl: LoadedPlaylist, begin: () => void) {
    const mine = ++sceneSeq
    replacing = true
    player.stop()
    replacing = false
    if (!pl.sceneSourcePath) {
      warn('offline playlist has no start snapshot', { id: pl.id })
      return
    }
    entering = true
    let shown = false
    try {
      shown = await deps.recordingScene.enter(pl.equivalent, pl.sceneSourcePath)
    } finally {
      if (mine === sceneSeq) entering = false
    }
    if (mine !== sceneSeq) return
    if (!shown) {
      warn('offline playlist start snapshot not shown', { id: pl.id, sourcePath: pl.sceneSourcePath })
      leaveScene()
      return
    }
    begin()
  }

  const choice = ref('')
  const loaded = reactive<{ equivalent: string | null; playlists: LoadedPlaylist[] }>({ equivalent: null, playlists: [] })
  let loadSeq = 0

  watch(
    () => (deps.isOfflineSource() ? canonicalEquivalent(deps.getSnapshotEquivalent()) : ''),
    async (eq) => {
      const mine = ++loadSeq
      halt()
      player.reset()
      loaded.equivalent = null
      loaded.playlists = []
      if (!eq) return
      const result = await loadPlaylists(eq, deps.fetchJson, deps.base)
      if (mine !== loadSeq) return
      loaded.equivalent = result.equivalent
      loaded.playlists = result.offered
      for (const r of result.refused) warn('offline playlist not offered', { id: r.id, reason: r.reason })
    },
    { immediate: true },
  )

  // Synchronous, so the workbench's own scene change (`entering`) is told apart from any other one.
  watch(deps.getSceneIdentity, () => {
    if (!entering) halt()
  }, { flush: 'sync' })

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
      halt()
      choice.value = id
    },
    playingId: computed(() => player.state.playingId),
    cursorId: computed(() => player.state.cursorId),
    /** The position in the SELECTED playlist (0 until it is played), and its length. */
    position: computed(() => (player.state.cursorId === selectedId.value ? player.state.position : 0)),
    total: computed(() => offered.value.find((p) => p.id === selectedId.value)?.count ?? 0),
    /** Shows the recording's start snapshot, then plays it (039 T3935). */
    play: async (id: string) => {
      const pl = playable(id)
      if (pl) await onItsScene(pl, () => player.play(pl))
    },
    /** One event; a Step that begins a playback shows the recording's start snapshot first. */
    step: async (id: string) => {
      const pl = playable(id)
      if (!pl) return
      if (player.stepBegins(pl)) await onItsScene(pl, () => player.step(pl))
      else player.step(pl)
    },
    /** Ends the playback; the offline source's snapshot comes back. */
    stop: () => halt(),
    activePlaybackId: () => player.activePlaybackId(),
  })
}

export type PlaylistWorkbench = ReturnType<typeof usePlaylistWorkbench>
