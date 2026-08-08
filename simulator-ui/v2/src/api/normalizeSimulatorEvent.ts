import type { ClearingDoneEvent, EdgePatch, NodePatch, TxUpdatedEvent } from '../types'
import type {
  RunStatusEvent,
  TopologyChangedEvent,
  TopologyChangedEdgeRef,
  TopologyChangedNodeRef,
  TxFailedEvent,
} from './simulatorTypes'
import { isCanonicalIsoDateTime } from './simulatorContracts'

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function asString(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function asBoolean(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null
}

function asArray(v: unknown): unknown[] | null {
  return Array.isArray(v) ? v : null
}

function hasOnlyKeys(raw: Record<string, unknown>, allowed: readonly string[]): boolean {
  const allowedSet = new Set(allowed)
  return Object.keys(raw).every((key) => allowedSet.has(key))
}

type ParseResult<T> = { ok: true; value: T } | { ok: false }

const PARSE_FAILED: ParseResult<never> = { ok: false }

function parsed<T>(value: T): ParseResult<T> {
  return { ok: true, value }
}

function optionalString(raw: Record<string, unknown>, key: string): ParseResult<string | undefined> {
  const value = raw[key]
  if (value == null) return parsed(undefined)
  return typeof value === 'string' ? parsed(value) : PARSE_FAILED
}

function optionalNumber(
  raw: Record<string, unknown>,
  key: string,
  opts?: { integer?: boolean; min?: number; max?: number },
): ParseResult<number | null | undefined> {
  const value = raw[key]
  if (value === undefined) return parsed(undefined)
  if (value === null) return parsed(null)
  if (typeof value !== 'number' || !Number.isFinite(value)) return PARSE_FAILED
  if (opts?.integer && !Number.isInteger(value)) return PARSE_FAILED
  if (opts?.min != null && value < opts.min) return PARSE_FAILED
  if (opts?.max != null && value > opts.max) return PARSE_FAILED
  return parsed(value)
}

function normalizeNodePatchArray(v: unknown): ParseResult<NodePatch[] | undefined> {
  if (v == null) return parsed(undefined)
  const arr = asArray(v)
  if (!arr) return PARSE_FAILED

  const out: NodePatch[] = []
  for (const raw of arr) {
    if (!isRecord(raw)) return PARSE_FAILED
    const id = asString(raw.id)
    if (!id) return PARSE_FAILED

    const p: NodePatch = { id }

    if (raw.net_balance_atoms !== undefined) {
      if (typeof raw.net_balance_atoms !== 'string' && raw.net_balance_atoms !== null) return PARSE_FAILED
      p.net_balance_atoms = raw.net_balance_atoms
    }
    if (raw.net_sign !== undefined) {
      if (raw.net_sign !== -1 && raw.net_sign !== 0 && raw.net_sign !== 1 && raw.net_sign !== null) return PARSE_FAILED
      p.net_sign = raw.net_sign
    }
    if (raw.net_balance !== undefined) {
      if (typeof raw.net_balance !== 'string' && raw.net_balance !== null) return PARSE_FAILED
      p.net_balance = raw.net_balance
    }
    if (raw.viz_color_key !== undefined) {
      if (typeof raw.viz_color_key !== 'string' && raw.viz_color_key !== null) return PARSE_FAILED
      p.viz_color_key = raw.viz_color_key
    }
    if (raw.viz_shape_key !== undefined) {
      if (typeof raw.viz_shape_key !== 'string' && raw.viz_shape_key !== null) return PARSE_FAILED
      p.viz_shape_key = raw.viz_shape_key
    }

    if (raw.viz_size !== undefined) {
      if (raw.viz_size === null) p.viz_size = null
      else if (isRecord(raw.viz_size)) {
        const w = asNumber(raw.viz_size.w)
        const h = asNumber(raw.viz_size.h)
        if (w == null || h == null) return PARSE_FAILED
        p.viz_size = { w, h }
      } else return PARSE_FAILED
    }

    out.push(p)
  }
  return parsed(out.length ? out : undefined)
}

function normalizeEdgePatchArray(v: unknown): ParseResult<EdgePatch[] | undefined> {
  if (v == null) return parsed(undefined)
  const arr = asArray(v)
  if (!arr) return PARSE_FAILED

  const out: EdgePatch[] = []
  for (const raw of arr) {
    if (!isRecord(raw)) return PARSE_FAILED
    const source = asString(raw.source)
    const target = asString(raw.target)
    if (!source || !target) return PARSE_FAILED

    const p: EdgePatch = { source, target }
    for (const key of ['trust_limit', 'used', 'available'] as const) {
      const value = raw[key]
      if (value === undefined || value === null) continue
      if (typeof value !== 'string' && (typeof value !== 'number' || !Number.isFinite(value))) return PARSE_FAILED
      p[key] = value
    }
    for (const key of ['viz_color_key', 'viz_width_key', 'viz_alpha_key'] as const) {
      const value = raw[key]
      if (value === undefined) continue
      if (typeof value !== 'string' && value !== null) return PARSE_FAILED
      p[key] = value
    }
    out.push(p)
  }
  return parsed(out.length ? out : undefined)
}

function normalizeTxEdges(v: unknown): ParseResult<TxUpdatedEvent['edges']> {
  if (v == null) return parsed([])
  const arr = asArray(v)
  if (!arr) return PARSE_FAILED

  const edges: TxUpdatedEvent['edges'] = []
  for (const raw of arr) {
    if (!isRecord(raw)) return PARSE_FAILED
    const from = asString(raw.from)
    const to = asString(raw.to)
    if (!from || !to) return PARSE_FAILED

    let style: TxUpdatedEvent['edges'][number]['style']
    if (raw.style != null) {
      if (!isRecord(raw.style)) return PARSE_FAILED
      const width = raw.style.viz_width_key
      const alpha = raw.style.viz_alpha_key
      if (width !== undefined && width !== null && typeof width !== 'string') return PARSE_FAILED
      if (alpha !== undefined && alpha !== null && typeof alpha !== 'string') return PARSE_FAILED
      if (typeof width === 'string' || typeof alpha === 'string') {
        style = {}
        if (typeof width === 'string') style.viz_width_key = width
        if (typeof alpha === 'string') style.viz_alpha_key = alpha
      }
    }

    edges.push(style ? { from, to, style } : { from, to })
  }
  return parsed(edges)
}

function normalizeNodeBadges(v: unknown): ParseResult<TxUpdatedEvent['node_badges']> {
  if (v == null) return parsed([])
  const arr = asArray(v)
  if (!arr) return PARSE_FAILED

  const badges: NonNullable<TxUpdatedEvent['node_badges']> = []
  for (const raw of arr) {
    if (!isRecord(raw)) return PARSE_FAILED
    const id = asString(raw.id)
    if (!id) return PARSE_FAILED
    const badge = raw.viz_badge_key
    if (badge !== undefined && badge !== null && typeof badge !== 'string') return PARSE_FAILED
    badges.push({ id, viz_badge_key: typeof badge === 'string' ? badge : null })
  }
  return parsed(badges)
}

function normalizeEventEdges(v: unknown): ParseResult<Array<{ from: string; to: string }> | undefined> {
  if (v == null) return parsed(undefined)
  const arr = asArray(v)
  if (!arr) return PARSE_FAILED

  const edges: Array<{ from: string; to: string }> = []
  for (const raw of arr) {
    if (!isRecord(raw)) return PARSE_FAILED
    const from = asString(raw.from)
    const to = asString(raw.to)
    if (!from || !to) return PARSE_FAILED
    edges.push({ from, to })
  }
  return parsed(edges)
}

function topologyArray(v: unknown): ParseResult<unknown[]> {
  if (v === undefined) return parsed([])
  return Array.isArray(v) ? parsed(v) : PARSE_FAILED
}

export type NormalizedRunStatusEvent = Omit<
  RunStatusEvent,
  'sim_time_ms' | 'intensity_percent' | 'ops_sec' | 'queue_depth'
> & {
  sim_time_ms?: number | null
  intensity_percent?: number | null
  ops_sec?: number | null
  queue_depth?: number | null
}

export type NormalizedTxFailedEvent = Omit<TxFailedEvent, 'from' | 'to'> & {
  from?: string
  to?: string
}

export type AcceptedSimulatorEvent =
  | NormalizedRunStatusEvent
  | TxUpdatedEvent
  | NormalizedTxFailedEvent
  | ClearingDoneEvent
  | TopologyChangedEvent

export type SimulatorEventNormalization =
  | { status: 'accepted'; event: AcceptedSimulatorEvent }
  | { status: 'ignored'; kind: 'malformed' | 'unknown'; eventType?: string; diagnostic: string }

function accepted(event: AcceptedSimulatorEvent): SimulatorEventNormalization {
  return { status: 'accepted', event }
}

function ignored(
  kind: 'malformed' | 'unknown',
  diagnostic: string,
  eventType?: string,
): SimulatorEventNormalization {
  return { status: 'ignored', kind, diagnostic, eventType }
}

export function normalizeSimulatorEvent(raw: unknown): SimulatorEventNormalization {
  if (!isRecord(raw)) return ignored('malformed', 'event_not_object')

  const type = asString(raw.type)
  const event_id = asString(raw.event_id)
  const ts = asString(raw.ts)

  if (!type || !event_id || !isCanonicalIsoDateTime(ts)) {
    return ignored('malformed', 'invalid_event_envelope', type ?? undefined)
  }

  if (type === 'run_status') {
    const run_id = asString(raw.run_id)
    const scenario_id = asString(raw.scenario_id)
    const state = asString(raw.state)
    const allowedStates = new Set(['idle', 'running', 'paused', 'stopping', 'stopped', 'error'])
    if (!run_id || !scenario_id || !state || !allowedStates.has(state)) {
      return ignored('malformed', 'invalid_run_status_identity', type)
    }

    const simTime = optionalNumber(raw, 'sim_time_ms', { integer: true, min: 0 })
    const intensity = optionalNumber(raw, 'intensity_percent', { integer: true, min: 0, max: 100 })
    const ops = optionalNumber(raw, 'ops_sec', { min: 0 })
    const queue = optionalNumber(raw, 'queue_depth', { integer: true, min: 0 })
    if (!simTime.ok || !intensity.ok || !ops.ok || !queue.ok) {
      return ignored('malformed', 'invalid_run_status_metrics', type)
    }

    let last_error: NormalizedRunStatusEvent['last_error'] = null
    if (raw.last_error != null) {
      if (!isRecord(raw.last_error)) return ignored('malformed', 'invalid_run_status_error', type)
      const code = asString(raw.last_error.code)
      const message = asString(raw.last_error.message)
      const at = asString(raw.last_error.at)
      if (!code || !message || !isCanonicalIsoDateTime(at)) {
        return ignored('malformed', 'invalid_run_status_error', type)
      }
      last_error = { code, message, at }
    }

    const lastEventType = optionalString(raw, 'last_event_type')
    const currentPhase = optionalString(raw, 'current_phase')
    const stopRequestedAt = optionalString(raw, 'stop_requested_at')
    const stopSource = optionalString(raw, 'stop_source')
    const stopReason = optionalString(raw, 'stop_reason')
    const stopClient = optionalString(raw, 'stop_client')
    if (
      !lastEventType.ok ||
      !currentPhase.ok ||
      !stopRequestedAt.ok ||
      !stopSource.ok ||
      !stopReason.ok ||
      !stopClient.ok
    ) {
      return ignored('malformed', 'invalid_run_status_optional_field', type)
    }
    if (stopRequestedAt.value !== undefined && !isCanonicalIsoDateTime(stopRequestedAt.value)) {
      return ignored('malformed', 'invalid_run_status_optional_field', type)
    }

    const evt: NormalizedRunStatusEvent = {
      event_id,
      ts,
      type: 'run_status',
      run_id,
      scenario_id,
      state,
      sim_time_ms: simTime.value,
      intensity_percent: intensity.value,
      ops_sec: ops.value,
      queue_depth: queue.value,
      last_event_type: lastEventType.value ?? null,
      current_phase: currentPhase.value ?? null,
      last_error,
    }

    if (stopRequestedAt.value) evt.stop_requested_at = stopRequestedAt.value
    if (stopSource.value != null) evt.stop_source = stopSource.value
    if (stopReason.value != null) evt.stop_reason = stopReason.value
    if (stopClient.value != null) evt.stop_client = stopClient.value

    for (const key of [
      'attempts_total',
      'committed_total',
      'rejected_total',
      'errors_total',
      'timeouts_total',
      'consec_all_rejected_ticks',
    ] as const) {
      const value = optionalNumber(raw, key, { integer: true, min: 0 })
      if (!value.ok) return ignored('malformed', `invalid_run_status_${key}`, type)
      if (typeof value.value === 'number') evt[key] = value.value
    }

    return accepted(evt)
  }

  if (type === 'tx.updated') {
    const equivalent = asString(raw.equivalent)
    if (!equivalent) return ignored('malformed', 'invalid_tx_updated_equivalent', type)

    const fromResult = optionalString(raw, 'from')
    const toResult = optionalString(raw, 'to')
    const amountResult = optionalString(raw, 'amount')
    const intensityResult = optionalString(raw, 'intensity_key')
    if (!fromResult.ok || !toResult.ok || !amountResult.ok || !intensityResult.ok) {
      return ignored('malformed', 'invalid_tx_updated_optional_field', type)
    }

    const amountFlyout = raw.amount_flyout == null ? undefined : asBoolean(raw.amount_flyout)
    if (raw.amount_flyout != null && amountFlyout == null) {
      return ignored('malformed', 'invalid_tx_updated_amount_flyout', type)
    }
    const ttl = optionalNumber(raw, 'ttl_ms', { integer: true })
    if (!ttl.ok) return ignored('malformed', 'invalid_tx_updated_ttl', type)

    const edges = normalizeTxEdges(raw.edges)
    const badges = normalizeNodeBadges(raw.node_badges)
    const nodePatch = normalizeNodePatchArray(raw.node_patch)
    const edgePatch = normalizeEdgePatchArray(raw.edge_patch)
    if (!edges.ok || !badges.ok || !nodePatch.ok || !edgePatch.ok) {
      return ignored('malformed', 'invalid_tx_updated_collection', type)
    }

    const from = fromResult.value
    const to = toResult.value
    const amount = amountResult.value
    if (amountFlyout === true) {
      const hasEndpoints = Boolean(from && to)
      if (!amount?.trim() || (!hasEndpoints && edges.value.length === 0)) {
        return ignored('malformed', 'invalid_tx_updated_amount_flyout_contract', type)
      }
    }

    return accepted({
      event_id,
      ts,
      type: 'tx.updated',
      equivalent,
      from,
      to,
      amount,
      amount_flyout: amountFlyout ?? undefined,
      ttl_ms: typeof ttl.value === 'number' ? ttl.value : 1200,
      intensity_key: intensityResult.value,
      edges: edges.value,
      node_badges: badges.value,
      node_patch: nodePatch.value,
      edge_patch: edgePatch.value,
    })
  }

  if (type === 'tx.failed') {
    const equivalent = asString(raw.equivalent)
    const from = optionalString(raw, 'from')
    const to = optionalString(raw, 'to')
    if (!equivalent || !from.ok || !to.ok || !isRecord(raw.error)) {
      return ignored('malformed', 'invalid_tx_failed_envelope', type)
    }

    const code = asString(raw.error.code)
    const message = asString(raw.error.message)
    const at = optionalString(raw.error, 'at')
    if (!code || !message || !at.ok || !isCanonicalIsoDateTime(at.value)) {
      return ignored('malformed', 'invalid_tx_failed_error', type)
    }

    const evt: NormalizedTxFailedEvent = {
      event_id,
      ts,
      type: 'tx.failed',
      equivalent,
      from: from.value,
      to: to.value,
      error: { code, message, at: at.value },
    }
    return accepted(evt)
  }

  if (type === 'clearing.done') {
    const equivalent = asString(raw.equivalent)
    const plan_id = asString(raw.plan_id)
    if (!equivalent || !plan_id) return ignored('malformed', 'invalid_clearing_done_identity', type)

    const clearedCycles = optionalNumber(raw, 'cleared_cycles', { integer: true, min: 0 })
    const clearedAmount = optionalString(raw, 'cleared_amount')
    const cycleEdges = normalizeEventEdges(raw.cycle_edges)
    const nodePatch = normalizeNodePatchArray(raw.node_patch)
    const edgePatch = normalizeEdgePatchArray(raw.edge_patch)
    if (!clearedCycles.ok || !clearedAmount.ok || !cycleEdges.ok || !nodePatch.ok || !edgePatch.ok) {
      return ignored('malformed', 'invalid_clearing_done_payload', type)
    }

    return accepted({
      event_id,
      ts,
      type: 'clearing.done',
      equivalent,
      plan_id,
      cleared_cycles: typeof clearedCycles.value === 'number' ? clearedCycles.value : undefined,
      cleared_amount: clearedAmount.value,
      cycle_edges: cycleEdges.value,
      node_patch: nodePatch.value,
      edge_patch: edgePatch.value,
    })
  }

  if (type === 'topology.changed') {
    const equivalent = asString(raw.equivalent)
    if (!equivalent || !isRecord(raw.payload)) {
      return ignored('malformed', 'invalid_topology_changed_envelope', type)
    }

    const payloadRaw = raw.payload
    if (
      !hasOnlyKeys(payloadRaw, [
        'added_nodes',
        'removed_nodes',
        'frozen_nodes',
        'added_edges',
        'removed_edges',
        'frozen_edges',
        'node_patch',
        'edge_patch',
      ])
    ) {
      return ignored('malformed', 'invalid_topology_changed_payload_extra', type)
    }
    const addedNodesRaw = topologyArray(payloadRaw.added_nodes)
    const removedNodesRaw = topologyArray(payloadRaw.removed_nodes)
    const frozenNodesRaw = topologyArray(payloadRaw.frozen_nodes)
    const addedEdgesRaw = topologyArray(payloadRaw.added_edges)
    const removedEdgesRaw = topologyArray(payloadRaw.removed_edges)
    const frozenEdgesRaw = topologyArray(payloadRaw.frozen_edges)
    const nodePatch = normalizeNodePatchArray(payloadRaw.node_patch)
    const edgePatch = normalizeEdgePatchArray(payloadRaw.edge_patch)
    if (
      !addedNodesRaw.ok ||
      !removedNodesRaw.ok ||
      !frozenNodesRaw.ok ||
      !addedEdgesRaw.ok ||
      !removedEdgesRaw.ok ||
      !frozenEdgesRaw.ok ||
      !nodePatch.ok ||
      !edgePatch.ok
    ) {
      return ignored('malformed', 'invalid_topology_changed_collection', type)
    }

    const added_nodes: TopologyChangedNodeRef[] = []
    for (const node of addedNodesRaw.value) {
      if (!isRecord(node)) return ignored('malformed', 'invalid_topology_changed_node', type)
      if (!hasOnlyKeys(node, ['pid', 'name', 'type'])) {
        return ignored('malformed', 'invalid_topology_changed_node', type)
      }
      const pid = asString(node.pid)
      const name = optionalString(node, 'name')
      const nodeType = optionalString(node, 'type')
      if (!pid || !name.ok || !nodeType.ok) return ignored('malformed', 'invalid_topology_changed_node', type)
      added_nodes.push({ pid, name: name.value, type: nodeType.value })
    }

    const normalizeNodeIds = (values: unknown[]): string[] | null => {
      const ids: string[] = []
      for (const value of values) {
        if (typeof value !== 'string' || !value) return null
        ids.push(value)
      }
      return ids
    }
    const removed_nodes = normalizeNodeIds(removedNodesRaw.value)
    const frozen_nodes = normalizeNodeIds(frozenNodesRaw.value)
    if (!removed_nodes || !frozen_nodes) return ignored('malformed', 'invalid_topology_changed_node_id', type)

    const normalizeTopologyEdges = (values: unknown[]): TopologyChangedEdgeRef[] | null => {
      const edges: TopologyChangedEdgeRef[] = []
      for (const edge of values) {
        if (!isRecord(edge)) return null
        if (!hasOnlyKeys(edge, ['from_pid', 'to_pid', 'equivalent_code', 'limit'])) return null
        const from_pid = asString(edge.from_pid)
        const to_pid = asString(edge.to_pid)
        const equivalent_code = asString(edge.equivalent_code)
        const limit = optionalString(edge, 'limit')
        if (!from_pid || !to_pid || !equivalent_code || !limit.ok) return null
        edges.push({ from_pid, to_pid, equivalent_code, limit: limit.value })
      }
      return edges
    }
    const added_edges = normalizeTopologyEdges(addedEdgesRaw.value)
    const removed_edges = normalizeTopologyEdges(removedEdgesRaw.value)
    const frozen_edges = normalizeTopologyEdges(frozenEdgesRaw.value)
    if (!added_edges || !removed_edges || !frozen_edges) {
      return ignored('malformed', 'invalid_topology_changed_edge', type)
    }

    const reason = optionalString(raw, 'reason')
    if (!reason.ok) return ignored('malformed', 'invalid_topology_changed_reason', type)

    return accepted({
      event_id,
      ts,
      type: 'topology.changed',
      equivalent,
      payload: {
        added_nodes,
        removed_nodes,
        frozen_nodes: frozen_nodes.length ? frozen_nodes : undefined,
        added_edges,
        removed_edges,
        frozen_edges: frozen_edges.length ? frozen_edges : undefined,
        node_patch: nodePatch.value,
        edge_patch: edgePatch.value,
      },
      reason: reason.value,
    })
  }

  return ignored('unknown', 'unknown_event_type', type)
}
