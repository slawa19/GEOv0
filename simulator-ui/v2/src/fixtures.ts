import type { DemoEvent, EdgePatch, GraphLink, GraphNode, GraphSnapshot, NodePatch } from './types'
import { VIZ_MAPPING } from './vizMapping'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asString(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new Error(`${label} must be string`)
  return value
}

function asOptionalString(value: unknown): string | undefined {
  if (value === undefined) return undefined
  if (value === null) return undefined
  if (typeof value !== 'string') return undefined
  return value
}

function asOptionalNullableString(value: unknown): string | null | undefined {
  if (value === undefined) return undefined
  if (value === null) return null
  if (typeof value !== 'string') return undefined
  return value
}

function asOptionalNumber(value: unknown): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || Number.isNaN(value)) return undefined
  return value
}

function asOptionalFiniteNumber(value: unknown): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return value
}

function asSnapshotPalette(value: unknown, label: string): GraphSnapshot['palette'] {
  if (value === undefined) return undefined
  if (value === null) return undefined
  if (!isRecord(value)) throw new Error(`${label} must be object`)

  const out: NonNullable<GraphSnapshot['palette']> = {}
  for (const [k, v] of Object.entries(value)) {
    if (!isRecord(v)) throw new Error(`${label}.${k} must be object`)
    const color = asString(v.color, `${label}.${k}.color`)
    const labelText = asOptionalString(v.label)
    out[k] = labelText ? { color, label: labelText } : { color }
  }
  return out
}

function asSnapshotLimits(value: unknown, label: string): GraphSnapshot['limits'] {
  if (value === undefined) return undefined
  if (value === null) return undefined
  if (!isRecord(value)) throw new Error(`${label} must be object`)

  const max_nodes = asOptionalFiniteNumber(value.max_nodes)
  const max_links = asOptionalFiniteNumber(value.max_links)
  const max_particles = asOptionalFiniteNumber(value.max_particles)

  if (max_nodes === undefined && max_links === undefined && max_particles === undefined) return undefined

  return {
    max_nodes,
    max_links,
    max_particles,
  }
}

function asFiniteNumber(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error(`${label} must be a finite number`)
  return value
}

function asEdgeArray(value: unknown, label: string): Array<{ from: string; to: string }> {
  if (value === undefined) return []
  if (!Array.isArray(value)) throw new Error(`${label} must be array`)
  return value.map((e, idx) => {
    if (!isRecord(e)) throw new Error(`${label}[${idx}] must be object`)
    return {
      from: asString(e.from, `${label}[${idx}].from`),
      to: asString(e.to, `${label}[${idx}].to`),
    }
  })
}

function asOptionalAnyNumberOrString(value: unknown): string | number | undefined {
  if (value === undefined) return undefined
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return undefined
}

type NetSign = -1 | 0 | 1 | null | undefined

function normalizeBalanceAtomsMagnitude(
  net_balance_atoms: string | null | undefined,
  net_sign: NetSign,
  context: string,
): string | null | undefined {
  if (net_balance_atoms === undefined) return undefined
  if (net_balance_atoms === null) return null

  const raw = net_balance_atoms
  if (!raw) return raw

  // Contract: net_balance_atoms is magnitude; sign is carried separately in net_sign.
  // Demo fixtures historically included signed atoms; normalize them at load-time.
  if (raw.startsWith('-')) {
    if (net_sign === -1) return raw.slice(1)
    if (net_sign === undefined) {
      throw new Error(`Signed net_balance_atoms without net_sign at ${context}`)
    }
    throw new Error(`Signed net_balance_atoms with net_sign=${String(net_sign)} at ${context}`)
  }

  return raw
}

function asNodePatchArray(value: unknown, label: string): NodePatch[] | undefined {
  if (value === undefined) return undefined
  if (!Array.isArray(value)) throw new Error(`${label} must be array`)
  return value.map((p, idx) => {
    if (!isRecord(p)) throw new Error(`${label}[${idx}] must be object`)
    const id = asString(p.id, `${label}[${idx}].id`)

    const vizColorKey = asOptionalNullableString(p.viz_color_key)
    if (vizColorKey) assertVizKeyKnown('viz_color_key', vizColorKey, `${label}[${idx}] node:${id}`)

    const vizShapeKey = asOptionalNullableString(p.viz_shape_key)
    if (vizShapeKey) assertVizKeyKnown('viz_shape_key', vizShapeKey, `${label}[${idx}] node:${id}`)

    let viz_size: NodePatch['viz_size'] = undefined
    if (p.viz_size !== undefined && p.viz_size !== null) {
      if (!isRecord(p.viz_size)) throw new Error(`${label}[${idx}].viz_size must be object`)
      const w = asOptionalNumber(p.viz_size.w)
      const h = asOptionalNumber(p.viz_size.h)
      if (w === undefined || h === undefined) throw new Error(`${label}[${idx}].viz_size.w/h must be numbers`)
      viz_size = { w, h }
    } else if (p.viz_size === null) {
      viz_size = null
    }

    const netSign = (p.net_sign === -1 || p.net_sign === 0 || p.net_sign === 1 || p.net_sign === null) ? p.net_sign : undefined
    const netBal = (typeof p.net_balance_atoms === 'string' || p.net_balance_atoms === null) ? p.net_balance_atoms : undefined
    const normalizedNetBal = normalizeBalanceAtomsMagnitude(netBal, netSign, `${label}[${idx}] node:${id}`)

    return {
      id,
      net_balance_atoms: normalizedNetBal,
      net_sign: netSign,
      viz_color_key: vizColorKey,
      viz_shape_key: vizShapeKey,
      viz_size,
    }
  })
}

function asEdgePatchArray(value: unknown, label: string): EdgePatch[] | undefined {
  if (value === undefined) return undefined
  if (!Array.isArray(value)) throw new Error(`${label} must be array`)
  return value.map((p, idx) => {
    if (!isRecord(p)) throw new Error(`${label}[${idx}] must be object`)
    const source = asString(p.source, `${label}[${idx}].source`)
    const target = asString(p.target, `${label}[${idx}].target`)

    const vw = asOptionalNullableString(p.viz_width_key)
    const va = asOptionalNullableString(p.viz_alpha_key)
    const vc = asOptionalNullableString(p.viz_color_key)
    if (vw) assertVizKeyKnown('viz_width_key', vw, `${label}[${idx}] link:${source}->${target}`)
    if (va) assertVizKeyKnown('viz_alpha_key', va, `${label}[${idx}] link:${source}->${target}`)
    if (vc) assertVizKeyKnown('viz_color_key', vc, `${label}[${idx}] link:${source}->${target}`)

    const used = asOptionalAnyNumberOrString(p.used)
    const available = asOptionalAnyNumberOrString(p.available)

    return {
      source,
      target,
      used,
      available,
      viz_color_key: vc,
      viz_width_key: vw,
      viz_alpha_key: va,
    }
  })
}

function assertVizKeyKnown(kind: 'viz_color_key' | 'viz_shape_key' | 'viz_width_key' | 'viz_alpha_key', key: string, context: string) {
  const strict = String(import.meta.env.VITE_STRICT_VIZ_KEYS ?? '1') === '1'
  if (!strict) return

  if (kind === 'viz_color_key') {
    if (!VIZ_MAPPING.node.color[key]) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
    return
  }

  if (kind === 'viz_shape_key') {
    if (key !== 'circle' && key !== 'rounded-rect') throw new Error(`Unknown ${kind}='${key}' at ${context}`)
    return
  }

  if (kind === 'viz_width_key') {
    if (VIZ_MAPPING.link.width_px[key] === undefined) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
    return
  }

  if (kind === 'viz_alpha_key') {
    if (VIZ_MAPPING.link.alpha[key] === undefined) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
  }
}

export async function loadJson<T>(path: string): Promise<T> {
  const cache: RequestCache | undefined = import.meta.env.DEV ? 'no-store' : undefined
  const res = await fetch(path, { headers: { Accept: 'application/json' }, cache })
  if (!res.ok) throw new Error(`Failed to fetch ${path}: ${res.status} ${res.statusText}`)
  return (await res.json()) as T
}

export async function loadSnapshot(eq: string): Promise<{ snapshot: GraphSnapshot; sourcePath: string }> {
  const base = String(import.meta.env.VITE_FIXTURES_BASE ?? '/simulator-fixtures/v1')
  const sourcePath = `${base}/${encodeURIComponent(eq)}/snapshot.json`
  const raw = await loadJson<unknown>(sourcePath)
  const snapshot = validateSnapshot(raw, sourcePath)
  return { snapshot, sourcePath }
}

export async function loadEvents(eq: string, playlist: string): Promise<{ events: DemoEvent[]; sourcePath: string }> {
  const base = String(import.meta.env.VITE_FIXTURES_BASE ?? '/simulator-fixtures/v1')
  const sourcePath = `${base}/${encodeURIComponent(eq)}/events/${playlist}.json`
  const raw = await loadJson<unknown>(sourcePath)
  const events = validateEvents(raw, sourcePath)
  return { events, sourcePath }
}

export function validateSnapshot(raw: unknown, sourcePath: string): GraphSnapshot {
  if (!isRecord(raw)) throw new Error(`Snapshot must be an object (${sourcePath})`)

  const equivalent = asString(raw.equivalent, `snapshot.equivalent (${sourcePath})`)
  const generated_at = asString(raw.generated_at, `snapshot.generated_at (${sourcePath})`)

  if (!Array.isArray(raw.nodes)) throw new Error(`snapshot.nodes must be array (${sourcePath})`)
  if (!Array.isArray(raw.links)) throw new Error(`snapshot.links must be array (${sourcePath})`)

  const nodes: GraphNode[] = raw.nodes.map((n, idx) => {
    if (!isRecord(n)) throw new Error(`nodes[${idx}] must be object (${sourcePath})`)
    const id = asString(n.id, `nodes[${idx}].id (${sourcePath})`)

    const vizColorKey = asOptionalNullableString(n.viz_color_key)
    if (vizColorKey) assertVizKeyKnown('viz_color_key', vizColorKey, `node:${id} (${sourcePath})`)

    const vizShapeKey = asOptionalNullableString(n.viz_shape_key)
    if (vizShapeKey) assertVizKeyKnown('viz_shape_key', vizShapeKey, `node:${id} (${sourcePath})`)

    let viz_size: GraphNode['viz_size'] = undefined
    if (n.viz_size !== undefined && n.viz_size !== null) {
      if (!isRecord(n.viz_size)) throw new Error(`nodes[${idx}].viz_size must be object (${sourcePath})`)
      const w = asOptionalNumber(n.viz_size.w)
      const h = asOptionalNumber(n.viz_size.h)
      if (w === undefined || h === undefined) throw new Error(`nodes[${idx}].viz_size.w/h must be numbers (${sourcePath})`)
      viz_size = { w, h }
    } else if (n.viz_size === null) {
      viz_size = null
    }

    const netBal = (typeof n.net_balance_atoms === 'string' || n.net_balance_atoms === null) ? n.net_balance_atoms : undefined
    const netSign = (n.net_sign === -1 || n.net_sign === 0 || n.net_sign === 1 || n.net_sign === null) ? n.net_sign : undefined
    const normalizedNetBal = normalizeBalanceAtomsMagnitude(netBal, netSign, `node:${id} (${sourcePath})`)

    return {
      id,
      name: asOptionalString(n.name),
      type: asOptionalString(n.type),
      status: asOptionalString(n.status),
      links_count: typeof n.links_count === 'number' ? n.links_count : undefined,
      net_balance_atoms: normalizedNetBal,
      net_sign: netSign,
      viz_color_key: vizColorKey,
      viz_shape_key: vizShapeKey,
      viz_size,
      viz_badge_key: asOptionalNullableString(n.viz_badge_key),
    }
  })

  const nodeIds = new Set(nodes.map((n) => n.id))

  const links: GraphLink[] = raw.links.map((l, idx) => {
    if (!isRecord(l)) throw new Error(`links[${idx}] must be object (${sourcePath})`)
    const source = asString(l.source, `links[${idx}].source (${sourcePath})`)
    const target = asString(l.target, `links[${idx}].target (${sourcePath})`)

    if (!nodeIds.has(source)) throw new Error(`Dangling link source '${source}' at links[${idx}] (${sourcePath})`)
    if (!nodeIds.has(target)) throw new Error(`Dangling link target '${target}' at links[${idx}] (${sourcePath})`)

    const vw = asOptionalNullableString(l.viz_width_key)
    const va = asOptionalNullableString(l.viz_alpha_key)

    if (vw) assertVizKeyKnown('viz_width_key', vw, `link:${source}->${target} (${sourcePath})`)
    if (va) assertVizKeyKnown('viz_alpha_key', va, `link:${source}->${target} (${sourcePath})`)

    return {
      id: asOptionalString(l.id),
      source,
      target,
      trust_limit: asOptionalAnyNumberOrString(l.trust_limit),
      used: asOptionalAnyNumberOrString(l.used),
      available: asOptionalAnyNumberOrString(l.available),
      status: asOptionalString(l.status),
      viz_color_key: asOptionalNullableString(l.viz_color_key),
      viz_width_key: vw,
      viz_alpha_key: va,
    }
  })

  return {
    equivalent,
    generated_at,
    nodes,
    links,
    palette: asSnapshotPalette(raw.palette, `palette (${sourcePath})`),
    limits: asSnapshotLimits(raw.limits, `limits (${sourcePath})`),
  }
}

export function validateEvents(raw: unknown, sourcePath: string): DemoEvent[] {
  if (!Array.isArray(raw)) throw new Error(`Events playlist must be an array (${sourcePath})`)

  return raw.map((evt, idx) => {
    if (!isRecord(evt)) throw new Error(`events[${idx}] must be object (${sourcePath})`)
    const type = asString(evt.type, `events[${idx}].type (${sourcePath})`)

    if (type === 'tx.updated') {
      if (!Array.isArray(evt.edges)) throw new Error(`tx.updated.edges must be array (${sourcePath})`)
      const ttl = asFiniteNumber(evt.ttl_ms, `events[${idx}].ttl_ms (${sourcePath})`)
      return {
        event_id: asString(evt.event_id, `events[${idx}].event_id (${sourcePath})`),
        ts: asString(evt.ts, `events[${idx}].ts (${sourcePath})`),
        type: 'tx.updated',
        equivalent: asString(evt.equivalent, `events[${idx}].equivalent (${sourcePath})`),
        ttl_ms: ttl,
        intensity_key: asOptionalString(evt.intensity_key),
        edges: evt.edges.map((e: any, eidx: number) => {
          if (!isRecord(e)) throw new Error(`tx.updated.edges[${eidx}] must be object (${sourcePath})`)
          const from = asString(e.from, `tx.updated.edges[${eidx}].from (${sourcePath})`)
          const to = asString(e.to, `tx.updated.edges[${eidx}].to (${sourcePath})`)
          const style = isRecord(e.style) ? (e.style as any) : undefined
          const vw = style ? asOptionalString(style.viz_width_key) : undefined
          const va = style ? asOptionalString(style.viz_alpha_key) : undefined
          if (vw) assertVizKeyKnown('viz_width_key', vw, `event tx edge:${from}->${to} (${sourcePath})`)
          if (va) assertVizKeyKnown('viz_alpha_key', va, `event tx edge:${from}->${to} (${sourcePath})`)
          return { from, to, style: style ? { viz_width_key: vw, viz_alpha_key: va } : undefined }
        }),
        node_badges: Array.isArray(evt.node_badges) ? (evt.node_badges as any) : undefined,
        node_patch: asNodePatchArray((evt as any).node_patch, `tx.updated.node_patch (${sourcePath})`),
        edge_patch: asEdgePatchArray((evt as any).edge_patch, `tx.updated.edge_patch (${sourcePath})`),
      }
    }

    if (type === 'clearing.done') {
      return {
        event_id: asString(evt.event_id, `events[${idx}].event_id (${sourcePath})`),
        ts: asString(evt.ts, `events[${idx}].ts (${sourcePath})`),
        type: 'clearing.done',
        equivalent: asString(evt.equivalent, `events[${idx}].equivalent (${sourcePath})`),
        plan_id: asString(evt.plan_id, `events[${idx}].plan_id (${sourcePath})`),
        cleared_cycles: asOptionalNumber(evt.cleared_cycles),
        cleared_amount: asOptionalString(evt.cleared_amount),
        cycle_edges: evt.cycle_edges === undefined ? undefined : asEdgeArray((evt as any).cycle_edges, `clearing.done.cycle_edges (${sourcePath})`),
        node_patch: asNodePatchArray((evt as any).node_patch, `clearing.done.node_patch (${sourcePath})`),
        edge_patch: asEdgePatchArray((evt as any).edge_patch, `clearing.done.edge_patch (${sourcePath})`),
      }
    }

    throw new Error(`Unknown event type '${type}' (${sourcePath})`)
  })
}
