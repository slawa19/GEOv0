import type { ClearingDoneEvent, ClearingPlanEvent, EdgePatch, NodePatch, TxUpdatedEvent } from '../types'
import type { RunStatusEvent, SimulatorEvent, TxFailedEvent } from './simulatorTypes'

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function asString(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function asNumber(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function asArray(v: unknown): unknown[] | null {
  return Array.isArray(v) ? v : null
}

function normalizeNodePatchArray(v: unknown): NodePatch[] | undefined {
  const arr = asArray(v)
  if (!arr) return undefined
  const out: NodePatch[] = []
  for (const raw of arr) {
    if (!isRecord(raw)) continue
    const id = asString(raw.id)
    if (!id) continue

    const p: NodePatch = { id }

    if (typeof raw.net_balance_atoms === 'string' || raw.net_balance_atoms === null) p.net_balance_atoms = raw.net_balance_atoms
    if (raw.net_sign === -1 || raw.net_sign === 0 || raw.net_sign === 1 || raw.net_sign === null) p.net_sign = raw.net_sign
    if (typeof raw.viz_color_key === 'string' || raw.viz_color_key === null) p.viz_color_key = raw.viz_color_key

    if (raw.viz_size === null) p.viz_size = null
    else if (isRecord(raw.viz_size)) {
      const w = asNumber(raw.viz_size.w)
      const h = asNumber(raw.viz_size.h)
      if (w != null && h != null) p.viz_size = { w, h }
    }

    out.push(p)
  }
  return out.length ? out : undefined
}

function normalizeEdgePatchArray(v: unknown): EdgePatch[] | undefined {
  const arr = asArray(v)
  if (!arr) return undefined
  const out: EdgePatch[] = []
  for (const raw of arr) {
    if (!isRecord(raw)) continue
    const source = asString(raw.source)
    const target = asString(raw.target)
    if (!source || !target) continue

    const p: EdgePatch = { source, target }
    if (typeof raw.used === 'string' || typeof raw.used === 'number') p.used = raw.used
    if (typeof raw.available === 'string' || typeof raw.available === 'number') p.available = raw.available
    if (typeof raw.viz_color_key === 'string' || raw.viz_color_key === null) p.viz_color_key = raw.viz_color_key
    if (typeof raw.viz_width_key === 'string' || raw.viz_width_key === null) p.viz_width_key = raw.viz_width_key
    if (typeof raw.viz_alpha_key === 'string' || raw.viz_alpha_key === null) p.viz_alpha_key = raw.viz_alpha_key
    out.push(p)
  }
  return out.length ? out : undefined
}

export function normalizeSimulatorEvent(raw: unknown): SimulatorEvent | null {
  if (!isRecord(raw)) return null

  const type = asString(raw.type)
  const event_id = asString(raw.event_id)
  const ts = asString(raw.ts)

  if (!type || !event_id || !ts) return null

  if (type === 'run_status') {
    const run_id = asString(raw.run_id)
    const scenario_id = asString(raw.scenario_id)
    const state = asString(raw.state)
    const sim_time_ms = asNumber(raw.sim_time_ms)
    const intensity_percent = asNumber(raw.intensity_percent)
    const ops_sec = asNumber(raw.ops_sec)
    const queue_depth = asNumber(raw.queue_depth)

    if (!run_id || !scenario_id || !state || sim_time_ms == null || intensity_percent == null || ops_sec == null || queue_depth == null) {
      return null
    }

    const evt: RunStatusEvent = {
      event_id,
      ts,
      type: 'run_status',
      run_id,
      scenario_id,
      state,
      sim_time_ms,
      intensity_percent,
      ops_sec,
      queue_depth,
      last_event_type: asString(raw.last_event_type),
      current_phase: asString(raw.current_phase),
      last_error: isRecord(raw.last_error)
        ? {
            code: asString(raw.last_error.code) ?? 'UNKNOWN',
            message: asString(raw.last_error.message) ?? 'UNKNOWN',
            at: asString(raw.last_error.at) ?? undefined,
          }
        : null,
    }

    return evt
  }

  if (type === 'tx.updated') {
    const equivalent = asString(raw.equivalent)
    if (!equivalent) return null

    const ttl_ms = asNumber(raw.ttl_ms) ?? 1200
    const intensity_key = asString(raw.intensity_key) ?? undefined

    const edgesRaw = raw.edges == null ? [] : asArray(raw.edges)
    if (!edgesRaw) return null

    const edges: TxUpdatedEvent['edges'] = []
    for (const e of edgesRaw) {
      if (!isRecord(e)) continue
      const from = asString(e.from)
      const to = asString(e.to)
      if (!from || !to) continue

      let style: TxUpdatedEvent['edges'][number]['style'] = undefined
      if (isRecord(e.style)) {
        const vw = asString(e.style.viz_width_key)
        const va = asString(e.style.viz_alpha_key)
        if (vw || va) {
          style = {}
          if (vw) style.viz_width_key = vw
          if (va) style.viz_alpha_key = va
        }
      }

      edges.push(style ? { from, to, style } : { from, to })
    }

    const nodeBadgesRaw = raw.node_badges == null ? [] : asArray(raw.node_badges)
    const node_badges = nodeBadgesRaw
      ? nodeBadgesRaw
          .map((b) => {
            if (!isRecord(b)) return null
            const id = asString(b.id)
            if (!id) return null
            const viz_badge_key = asString(b.viz_badge_key)
            return { id, viz_badge_key }
          })
          .filter((x): x is { id: string; viz_badge_key: string | null } => x != null)
      : undefined

    const evt: TxUpdatedEvent = {
      event_id,
      ts,
      type: 'tx.updated',
      equivalent,
      ttl_ms,
      intensity_key,
      edges,
      node_badges: node_badges ?? undefined,
      node_patch: normalizeNodePatchArray(raw.node_patch),
      edge_patch: normalizeEdgePatchArray(raw.edge_patch),
    }

    return evt as SimulatorEvent
  }

  if (type === 'tx.failed') {
    const equivalent = asString(raw.equivalent)
    const from = asString(raw.from)
    const to = asString(raw.to)
    if (!equivalent || !from || !to) return null

    const error = isRecord(raw.error)
      ? {
          code: asString(raw.error.code) ?? 'UNKNOWN',
          message: asString(raw.error.message) ?? 'UNKNOWN',
          at: asString(raw.error.at) ?? undefined,
        }
      : { code: 'UNKNOWN', message: 'UNKNOWN' }

    const evt: TxFailedEvent = { event_id, ts, type: 'tx.failed', equivalent, from, to, error }
    return evt
  }

  if (type === 'clearing.plan') {
    const equivalent = asString(raw.equivalent)
    const plan_id = asString(raw.plan_id)
    const stepsRaw = asArray(raw.steps)
    if (!equivalent || !plan_id || !stepsRaw) return null

    const steps: ClearingPlanEvent['steps'] = []
    for (const s of stepsRaw) {
      if (!isRecord(s)) continue
      const at_ms = asNumber(s.at_ms)
      if (at_ms == null) continue
      const step: ClearingPlanEvent['steps'][number] = { at_ms }

      const intensity_key = asString(s.intensity_key)
      if (intensity_key) step.intensity_key = intensity_key

      const mapEdges = (arr: unknown[] | null | undefined) => {
        const out: Array<{ from: string; to: string }> = []
        for (const e of arr ?? []) {
          if (!isRecord(e)) continue
          const from = asString(e.from)
          const to = asString(e.to)
          if (!from || !to) continue
          out.push({ from, to })
        }
        return out
      }

      if (asArray(s.highlight_edges)) step.highlight_edges = mapEdges(asArray(s.highlight_edges))
      if (asArray(s.particles_edges)) step.particles_edges = mapEdges(asArray(s.particles_edges))

      steps.push(step)
    }

    const evt: ClearingPlanEvent = { event_id, ts, type: 'clearing.plan', equivalent, plan_id, steps }
    return evt as SimulatorEvent
  }

  if (type === 'clearing.done') {
    const equivalent = asString(raw.equivalent)
    const plan_id = asString(raw.plan_id)
    if (!equivalent || !plan_id) return null

    const evt: ClearingDoneEvent = {
      event_id,
      ts,
      type: 'clearing.done',
      equivalent,
      plan_id,
      node_patch: normalizeNodePatchArray(raw.node_patch),
      edge_patch: normalizeEdgePatchArray(raw.edge_patch),
    }

    return evt as SimulatorEvent
  }

  return raw as any
}
