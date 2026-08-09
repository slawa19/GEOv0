import type {
  AcceptedSimulatorEvent,
  NormalizedRunStatusEvent,
  NormalizedTxFailedEvent,
} from '../api/normalizeSimulatorEvent'
import type { RunStatus, TopologyChangedEvent } from '../api/simulatorTypes'
import type { ClearingDoneEvent, EdgePatch, GraphLink, GraphNode, NodePatch, TxUpdatedEvent } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { resolveTxDirection } from '../utils/txDirection'
import { toLowerTrim } from '../utils/stringHelpers'

export type RealEventReplayRejection = {
  status: 'rejected'
  kind: 'malformed' | 'context'
  diagnostic: 'frame_id_mismatch' | 'active_run_changed' | 'run_id_mismatch' | 'equivalent_mismatch'
}

export type RealEventReplayAdmission =
  | RealEventReplayRejection
  | { status: 'duplicate'; cursor: string | null }
  | { status: 'accepted'; cursor: string }

export type RealEventReplayOwner = {
  admit: (input: {
    event: AcceptedSimulatorEvent
    frameId?: string
    connectionRunId: string
    activeRunId: string | null
    connectionEquivalent: string
    currentCursor: string | null
  }) => RealEventReplayAdmission
  commit: (eventId: string, runId: string) => void
  reset: () => void
}

function producerSequence(eventId: string | null, runId: string): bigint | null {
  if (!eventId) return null
  const prefix = `evt_${runId}_`
  if (!eventId.startsWith(prefix)) return null
  const raw = eventId.slice(prefix.length)
  if (!/^\d+$/.test(raw)) return null
  try {
    return BigInt(raw)
  } catch {
    return null
  }
}

function monotonicCursor(current: string | null, eventId: string, runId: string): string {
  const currentSeq = producerSequence(current, runId)
  const eventSeq = producerSequence(eventId, runId)
  if (currentSeq !== null && eventSeq !== null && eventSeq < currentSeq) return current!
  return eventId
}

export function createRealEventReplayOwner(opts?: { maxIds?: number; pruneBatch?: number }): RealEventReplayOwner {
  const processedEventIds = new Map<string, true>()
  let processedRunId: string | null = null
  const maxIds = opts?.maxIds ?? 5000
  const pruneBatch = opts?.pruneBatch ?? 500

  function reset() {
    processedRunId = null
    processedEventIds.clear()
  }

  function admit(input: Parameters<RealEventReplayOwner['admit']>[0]): RealEventReplayAdmission {
    const { event, frameId, connectionRunId, activeRunId, connectionEquivalent, currentCursor } = input

    if (frameId !== undefined && frameId !== event.event_id) {
      return { status: 'rejected', kind: 'malformed', diagnostic: 'frame_id_mismatch' }
    }
    if (activeRunId !== connectionRunId) {
      return { status: 'rejected', kind: 'context', diagnostic: 'active_run_changed' }
    }
    if (event.type === 'run_status') {
      if (event.run_id !== connectionRunId) {
        return { status: 'rejected', kind: 'context', diagnostic: 'run_id_mismatch' }
      }
    } else if (event.equivalent.trim().toUpperCase() !== connectionEquivalent.trim().toUpperCase()) {
      return { status: 'rejected', kind: 'context', diagnostic: 'equivalent_mismatch' }
    }

    if (processedRunId !== connectionRunId) {
      processedRunId = connectionRunId
      processedEventIds.clear()
    }
    if (processedEventIds.has(event.event_id)) {
      return { status: 'duplicate', cursor: currentCursor }
    }

    return {
      status: 'accepted',
      cursor: monotonicCursor(currentCursor, event.event_id, connectionRunId),
    }
  }

  function commit(eventId: string, runId: string) {
    if (processedRunId !== runId) {
      processedRunId = runId
      processedEventIds.clear()
    }
    processedEventIds.set(eventId, true)
    if (processedEventIds.size > maxIds) {
      const targetSize = Math.max(0, maxIds - pruneBatch)
      while (processedEventIds.size > targetSize) {
        const firstKey = processedEventIds.keys().next().value as string | undefined
        if (!firstKey) break
        processedEventIds.delete(firstKey)
      }
    }

  }

  return { admit, commit, reset }
}

export type RealEventDraft = {
  real: {
    runId: string | null
    runStatus: RunStatus | null
    lastError: string
    runStats: {
      attempts: number
      committed: number
      rejected: number
      errors: number
      timeouts: number
      rejectedByCode: Record<string, number>
      errorsByCode: Record<string, number>
    }
  }
  state: SimulatorAppState
}

export type RealEventStateDeps = {
  patchApplier: {
    applyNodePatches: (patches: NodePatch[] | undefined) => void
    applyEdgePatches: (patches: EdgePatch[] | undefined) => void
  }
  isUserFacingRunError: (code: string) => boolean
  inc: (map: Record<string, number>, key: string) => void
}

export type RealEventIntent =
  | { type: 'accepted-event' }
  | { type: 'refresh-snapshot' }
  | { type: 'wake' }
  | { type: 'tx-fx'; event: TxUpdatedEvent }
  | { type: 'tx-label'; role: 'sender'; nodeId: string; signedAmount: string; unit: string; throttleMs: number }
  | {
      type: 'tx-label-delayed'
      role: 'receiver'
      nodeId: string
      signedAmount: string
      unit: string
      throttleMs: number
      delayMs: number
      runId: string
    }
  | { type: 'clearing-fx'; event: ClearingDoneEvent }
  | { type: 'amount-flyout-suppressed' }
  | { type: 'burst-throttle'; throttleMs: number }

function isRunStatusEvent(evt: AcceptedSimulatorEvent): evt is NormalizedRunStatusEvent {
  return evt.type === 'run_status'
}

function isTxUpdatedEvent(evt: AcceptedSimulatorEvent): evt is TxUpdatedEvent {
  return evt.type === 'tx.updated'
}

function isTxFailedEvent(evt: AcceptedSimulatorEvent): evt is NormalizedTxFailedEvent {
  return evt.type === 'tx.failed'
}

function isTopologyChangedEvent(evt: AcceptedSimulatorEvent): evt is TopologyChangedEvent {
  return evt.type === 'topology.changed'
}

function burstLabelThrottleMs(status: RunStatus | null): number {
  const ops = Number(status?.ops_sec ?? 0)
  const queueDepth = Number(status?.queue_depth ?? 0)
  if (ops >= 40 || queueDepth >= 200) return 240
  if (ops >= 20 || queueDepth >= 100) return 120
  return 0
}

function nodeColorKey(type?: string | null, status?: string | null): string {
  const normalizedStatus = toLowerTrim(status ?? '')
  if (normalizedStatus === 'suspended' || normalizedStatus === 'left' || normalizedStatus === 'deleted') {
    return normalizedStatus
  }
  const normalizedType = toLowerTrim(type ?? '')
  if (normalizedType === 'business' || normalizedType === 'person') return normalizedType
  return 'unknown'
}

function ensureNodeDefaults(node: GraphNode) {
  const isBusiness = toLowerTrim(String(node.type ?? '')) === 'business'
  if (!node.viz_shape_key) node.viz_shape_key = isBusiness ? 'rounded-rect' : 'circle'
  if (!node.viz_size) node.viz_size = isBusiness ? { w: 26, h: 22 } : { w: 16, h: 16 }
  if (!node.viz_color_key) node.viz_color_key = nodeColorKey(node.type, node.status)
}

function applyTopologyEvent(
  event: TopologyChangedEvent,
  draft: RealEventDraft,
  deps: RealEventStateDeps,
  intents: RealEventIntent[],
) {
  const payload = event.payload
  const hasPatches = (payload.node_patch?.length ?? 0) > 0 || (payload.edge_patch?.length ?? 0) > 0
  const hasStructuralChanges =
    payload.added_nodes.length > 0 ||
    payload.removed_nodes.length > 0 ||
    (payload.frozen_nodes?.length ?? 0) > 0 ||
    payload.added_edges.length > 0 ||
    payload.removed_edges.length > 0 ||
    (payload.frozen_edges?.length ?? 0) > 0

  if ((!hasStructuralChanges && !hasPatches) || !draft.state.snapshot) {
    intents.push({ type: 'refresh-snapshot' })
    return
  }

  const snapshot = draft.state.snapshot
  if (String(snapshot.equivalent ?? '').toUpperCase() !== event.equivalent.trim().toUpperCase()) return

  if (!hasStructuralChanges) {
    deps.patchApplier.applyNodePatches(payload.node_patch)
    deps.patchApplier.applyEdgePatches(payload.edge_patch)
    snapshot.generated_at = event.ts
    intents.push({ type: 'wake' })
    return
  }

  const nodesById = new Map(snapshot.nodes.map((node) => [node.id, node] as const))
  const removedNodes = new Set(payload.removed_nodes)
  const frozenNodes = new Set(payload.frozen_nodes ?? [])

  for (const ref of payload.added_nodes) {
    const id = String(ref.pid ?? '').trim()
    if (!id || nodesById.has(id)) continue
    const node: GraphNode = { id, name: ref.name ?? undefined, type: ref.type ?? undefined, status: 'active' }
    ensureNodeDefaults(node)
    snapshot.nodes.push(node)
    nodesById.set(id, node)
  }
  for (const id of frozenNodes) {
    const node = nodesById.get(id)
    if (!node) continue
    node.status = 'suspended'
    node.viz_color_key = nodeColorKey(node.type, node.status)
  }
  if (removedNodes.size) {
    snapshot.nodes = snapshot.nodes.filter((node) => !removedNodes.has(node.id))
    snapshot.links = snapshot.links.filter(
      (link) => !removedNodes.has(String(link.source)) && !removedNodes.has(String(link.target)),
    )
  }

  const edgeKey = (source: string, target: string) => `${source}→${target}`
  const linksByKey = new Map(
    snapshot.links.map((link) => [edgeKey(String(link.source), String(link.target)), link] as const),
  )
  for (const ref of payload.added_edges) {
    const source = String(ref.from_pid ?? '').trim()
    const target = String(ref.to_pid ?? '').trim()
    if (!source || !target || linksByKey.has(edgeKey(source, target))) continue
    const limit = ref.limit ?? undefined
    const link: GraphLink = {
      source,
      target,
      trust_limit: limit,
      used: 0,
      available: limit,
      status: 'active',
      viz_width_key: 'thin',
      viz_alpha_key: 'active',
    }
    snapshot.links.push(link)
    linksByKey.set(edgeKey(source, target), link)
  }
  for (const ref of payload.frozen_edges ?? []) {
    const link = linksByKey.get(edgeKey(String(ref.from_pid).trim(), String(ref.to_pid).trim()))
    if (!link) continue
    link.status = 'frozen'
    link.viz_alpha_key = 'muted'
  }
  const removedEdgeKeys = new Set(
    payload.removed_edges.map((ref) => edgeKey(String(ref.from_pid).trim(), String(ref.to_pid).trim())),
  )
  if (removedEdgeKeys.size) {
    snapshot.links = snapshot.links.filter(
      (link) => !removedEdgeKeys.has(edgeKey(String(link.source), String(link.target))),
    )
  }

  const linkCounts: Record<string, number> = {}
  for (const link of snapshot.links) {
    const source = String(link.source)
    const target = String(link.target)
    linkCounts[source] = (linkCounts[source] ?? 0) + 1
    linkCounts[target] = (linkCounts[target] ?? 0) + 1
  }
  for (const node of snapshot.nodes) node.links_count = linkCounts[node.id] ?? 0

  snapshot.generated_at = event.ts
  deps.patchApplier.applyNodePatches(payload.node_patch)
  deps.patchApplier.applyEdgePatches(payload.edge_patch)
  intents.push({ type: 'wake' })
  // Structural mutations also change the renderer-owned layout cardinality. A
  // wake alone only repaints the existing raw layout objects, so reconcile via
  // the authoritative snapshot path after committing the local trusted state.
  intents.push({ type: 'refresh-snapshot' })
}

export function applyAcceptedRealEvent(
  event: AcceptedSimulatorEvent,
  connectionRunId: string,
  draft: RealEventDraft,
  deps: RealEventStateDeps,
): RealEventIntent[] {
  const intents: RealEventIntent[] = [{ type: 'accepted-event' }]

  if (isRunStatusEvent(event)) {
    const status: RunStatus = {
      ...event,
      sim_time_ms: event.sim_time_ms ?? draft.real.runStatus?.sim_time_ms ?? 0,
      intensity_percent: event.intensity_percent ?? draft.real.runStatus?.intensity_percent ?? 0,
      ops_sec: event.ops_sec ?? draft.real.runStatus?.ops_sec ?? 0,
      queue_depth: event.queue_depth ?? draft.real.runStatus?.queue_depth ?? 0,
    }
    draft.real.runStatus = status
    const lastError = status.last_error
    draft.real.lastError =
      lastError && deps.isUserFacingRunError(lastError.code) ? `${lastError.code}: ${lastError.message}` : ''
    if (typeof status.attempts_total === 'number') draft.real.runStats.attempts = status.attempts_total
    if (typeof status.committed_total === 'number') draft.real.runStats.committed = status.committed_total
    if (typeof status.rejected_total === 'number') draft.real.runStats.rejected = status.rejected_total
    if (typeof status.errors_total === 'number') draft.real.runStats.errors = status.errors_total
    if (typeof status.timeouts_total === 'number') draft.real.runStats.timeouts = status.timeouts_total
    return intents
  }

  if (isTxUpdatedEvent(event)) {
    draft.real.runStats.attempts += 1
    draft.real.runStats.committed += 1
    deps.patchApplier.applyNodePatches(event.node_patch)
    deps.patchApplier.applyEdgePatches(event.edge_patch)

    intents.push({ type: 'tx-fx', event }, { type: 'wake' })
    const allowAmountFlyout = event.amount_flyout !== false
    if (!allowAmountFlyout) intents.push({ type: 'amount-flyout-suppressed' })
    const amount = String(event.amount ?? '').trim()
    const resolved = resolveTxDirection({ from: event.from, to: event.to, edges: event.edges ?? [] })
    const throttleMs = burstLabelThrottleMs(draft.real.runStatus)
    intents.push({ type: 'burst-throttle', throttleMs })
    if (allowAmountFlyout && amount && resolved.from) {
      intents.push({
        type: 'tx-label',
        role: 'sender',
        nodeId: resolved.from,
        signedAmount: `-${amount}`,
        unit: event.equivalent,
        throttleMs,
      })
    }
    if (allowAmountFlyout && amount && resolved.to && resolved.to !== resolved.from) {
      intents.push({
        type: 'tx-label-delayed',
        role: 'receiver',
        nodeId: resolved.to,
        signedAmount: `+${amount}`,
        unit: event.equivalent,
        throttleMs,
        delayMs: event.ttl_ms ?? 1200,
        runId: connectionRunId,
      })
    }
    return intents
  }

  if (isTxFailedEvent(event)) {
    const code = String(event.error?.code ?? 'TX_FAILED')
    draft.real.runStats.attempts += 1
    if (code.toUpperCase() === 'PAYMENT_TIMEOUT') {
      draft.real.runStats.timeouts += 1
      draft.real.runStats.errors += 1
      deps.inc(draft.real.runStats.errorsByCode, code)
    } else if (deps.isUserFacingRunError(code)) {
      draft.real.runStats.errors += 1
      deps.inc(draft.real.runStats.errorsByCode, code)
    } else {
      draft.real.runStats.rejected += 1
      deps.inc(draft.real.runStats.rejectedByCode, code)
    }
    return intents
  }

  if (event.type === 'clearing.done') {
    deps.patchApplier.applyNodePatches(event.node_patch)
    deps.patchApplier.applyEdgePatches(event.edge_patch)
    intents.push({ type: 'clearing-fx', event }, { type: 'wake' })
    return intents
  }

  if (isTopologyChangedEvent(event)) applyTopologyEvent(event, draft, deps, intents)
  return intents
}

export function executeRealEventIntents(
  intents: readonly RealEventIntent[],
  deps: {
    getActiveRunId: () => string | null
    optionalFxEnabled?: () => boolean
    onAnySseEvent?: () => void
    refreshSnapshot: () => void
    wakeUp?: () => void
    runRealTxFx: (event: TxUpdatedEvent) => void
    runRealClearingDoneFx: (event: ClearingDoneEvent) => void
    pushTxAmountLabel: (nodeId: string, amount: string, unit: string, opts: { throttleMs: number }) => void
    clampRealTxTtlMs: (ttl: unknown) => number
    scheduleTimeout: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => void
    onIntentExecuted?: (intent: RealEventIntent) => void
    onReceiverLabelPushed?: () => void
    onReceiverGuardDropped?: () => void
  },
) {
  const fxEnabled = () => deps.optionalFxEnabled?.() ?? true
  for (const intent of intents) {
    deps.onIntentExecuted?.(intent)
    if (intent.type === 'accepted-event') deps.onAnySseEvent?.()
    else if (intent.type === 'refresh-snapshot') deps.refreshSnapshot()
    else if (intent.type === 'wake') deps.wakeUp?.()
    else if (intent.type === 'tx-fx' && fxEnabled()) deps.runRealTxFx(intent.event)
    else if (intent.type === 'clearing-fx' && fxEnabled()) deps.runRealClearingDoneFx(intent.event)
    else if (intent.type === 'tx-label' && fxEnabled()) {
      deps.pushTxAmountLabel(intent.nodeId, intent.signedAmount, intent.unit, { throttleMs: intent.throttleMs })
    } else if (intent.type === 'tx-label-delayed' && fxEnabled()) {
      deps.scheduleTimeout(
        () => {
          if (deps.getActiveRunId() !== intent.runId || !fxEnabled()) {
            deps.onReceiverGuardDropped?.()
            return
          }
          deps.pushTxAmountLabel(intent.nodeId, intent.signedAmount, intent.unit, { throttleMs: intent.throttleMs })
          deps.onReceiverLabelPushed?.()
        },
        deps.clampRealTxTtlMs(intent.delayMs),
        { critical: true },
      )
    }
  }
}
