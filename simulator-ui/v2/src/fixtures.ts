import type { DemoEvent, GraphLink, GraphNode, GraphSnapshot } from './types'
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

function assertVizKeyKnown(kind: 'viz_color_key' | 'viz_width_key' | 'viz_alpha_key', key: string, context: string) {
  const strict = String(import.meta.env.VITE_STRICT_VIZ_KEYS ?? '1') === '1'
  if (!strict) return

  if (kind === 'viz_color_key') {
    if (!VIZ_MAPPING.node.color[key]) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
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
  const res = await fetch(path, { headers: { Accept: 'application/json' } })
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

    return {
      id,
      name: asOptionalString(n.name),
      type: asOptionalString(n.type),
      status: asOptionalString(n.status),
      links_count: typeof n.links_count === 'number' ? n.links_count : undefined,
      net_balance_atoms: (typeof n.net_balance_atoms === 'string' || n.net_balance_atoms === null) ? n.net_balance_atoms : undefined,
      net_sign: (n.net_sign === -1 || n.net_sign === 0 || n.net_sign === 1 || n.net_sign === null) ? n.net_sign : undefined,
      viz_color_key: vizColorKey,
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
      trust_limit: l.trust_limit as any,
      used: l.used as any,
      available: l.available as any,
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
    palette: isRecord(raw.palette) ? (raw.palette as any) : undefined,
    limits: isRecord(raw.limits) ? (raw.limits as any) : undefined,
  }
}

export function validateEvents(raw: unknown, sourcePath: string): DemoEvent[] {
  if (!Array.isArray(raw)) throw new Error(`Events playlist must be an array (${sourcePath})`)

  return raw.map((evt, idx) => {
    if (!isRecord(evt)) throw new Error(`events[${idx}] must be object (${sourcePath})`)
    const type = asString(evt.type, `events[${idx}].type (${sourcePath})`)

    if (type === 'tx.updated') {
      if (!Array.isArray(evt.edges)) throw new Error(`tx.updated.edges must be array (${sourcePath})`)
      return {
        event_id: asString(evt.event_id, `events[${idx}].event_id (${sourcePath})`),
        ts: asString(evt.ts, `events[${idx}].ts (${sourcePath})`),
        type: 'tx.updated',
        equivalent: asString(evt.equivalent, `events[${idx}].equivalent (${sourcePath})`),
        ttl_ms: Number(evt.ttl_ms),
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
      }
    }

    if (type === 'clearing.plan') {
      if (!Array.isArray(evt.steps)) throw new Error(`clearing.plan.steps must be array (${sourcePath})`)
      return {
        event_id: asString(evt.event_id, `events[${idx}].event_id (${sourcePath})`),
        ts: asString(evt.ts, `events[${idx}].ts (${sourcePath})`),
        type: 'clearing.plan',
        equivalent: asString(evt.equivalent, `events[${idx}].equivalent (${sourcePath})`),
        plan_id: asString(evt.plan_id, `events[${idx}].plan_id (${sourcePath})`),
        steps: (evt.steps as any).map((s: any, sidx: number) => {
          if (!isRecord(s)) throw new Error(`clearing.plan.steps[${sidx}] must be object (${sourcePath})`)
          return {
            at_ms: Number(s.at_ms),
            intensity_key: asOptionalString(s.intensity_key),
            highlight_edges: Array.isArray(s.highlight_edges) ? (s.highlight_edges as any) : undefined,
            particles_edges: Array.isArray(s.particles_edges) ? (s.particles_edges as any) : undefined,
            flash: isRecord(s.flash) ? (s.flash as any) : undefined,
          }
        }),
      }
    }

    if (type === 'clearing.done') {
      return {
        event_id: asString(evt.event_id, `events[${idx}].event_id (${sourcePath})`),
        ts: asString(evt.ts, `events[${idx}].ts (${sourcePath})`),
        type: 'clearing.done',
        equivalent: asString(evt.equivalent, `events[${idx}].equivalent (${sourcePath})`),
        plan_id: asString(evt.plan_id, `events[${idx}].plan_id (${sourcePath})`),
      }
    }

    throw new Error(`Unknown event type '${type}' (${sourcePath})`)
  })
}
