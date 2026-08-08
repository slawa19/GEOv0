import type { GraphLink, GraphNode } from '../types'
import { ApiError } from './http'
import type { RunError, RunStatus, ScenarioSummary, ScenariosListResponse, SimulatorGraphSnapshot } from './simulatorTypes'

const SIMULATOR_API_VERSION = 'simulator-api/1'

type JsonObject = Record<string, unknown>
type ContractDecoder<T> = (value: unknown, path: string) => T

class ContractViolation extends Error {}

export class SimulatorContractError extends ApiError {
  contract: string
  diagnostic: string

  constructor(contract: string, diagnostic: string) {
    super(`Invalid 2xx Simulator response contract (${contract}): ${diagnostic}`, {
      status: 200,
      bodyText: diagnostic,
    })
    this.name = 'SimulatorContractError'
    this.contract = contract
    this.diagnostic = diagnostic
  }
}

function fail(path: string, expectation: string): never {
  throw new ContractViolation(`${path}: ${expectation}`)
}

function objectAt(value: unknown, path: string): JsonObject {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) fail(path, 'expected object')
  return value as JsonObject
}

function arrayAt(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) fail(path, 'expected array')
  return value
}

function stringAt(value: unknown, path: string): string {
  if (typeof value !== 'string') fail(path, 'expected string')
  return value
}

const CANONICAL_ISO_DATE_TIME =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/

export function isCanonicalIsoDateTime(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const match = CANONICAL_ISO_DATE_TIME.exec(value)
  if (!match) return false

  const [, yearText, monthText, dayText, hourText, minuteText, secondText] = match
  const year = Number(yearText)
  const month = Number(monthText)
  const day = Number(dayText)
  const hour = Number(hourText)
  const minute = Number(minuteText)
  const second = Number(secondText)
  const leapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
  const daysInMonth = [31, leapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

  return (
    month >= 1 &&
    month <= 12 &&
    day >= 1 &&
    day <= (daysInMonth[month - 1] ?? 0) &&
    hour <= 23 &&
    minute <= 59 &&
    second <= 59 &&
    !Number.isNaN(Date.parse(value))
  )
}

function dateTimeAt(value: unknown, path: string): string {
  const text = stringAt(value, path)
  if (!isCanonicalIsoDateTime(text)) fail(path, 'expected canonical ISO date-time string')
  return text
}

function numberAt(value: unknown, path: string, opts?: { integer?: boolean; min?: number; max?: number }): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) fail(path, 'expected finite number')
  if (opts?.integer && !Number.isInteger(value)) fail(path, 'expected integer')
  if (opts?.min != null && value < opts.min) fail(path, `expected number >= ${opts.min}`)
  if (opts?.max != null && value > opts.max) fail(path, `expected number <= ${opts.max}`)
  return value
}

function onlyKeys(value: JsonObject, path: string, allowed: readonly string[]): void {
  const allowedSet = new Set(allowed)
  const extra = Object.keys(value).find((key) => !allowedSet.has(key))
  if (extra) fail(`${path}.${extra}`, 'unexpected field')
}

function optionalString(value: JsonObject, key: string, path: string): string | null | undefined {
  const item = value[key]
  if (item === undefined || item === null) return item
  return stringAt(item, `${path}.${key}`)
}

function optionalDateTime(value: JsonObject, key: string, path: string): string | null | undefined {
  const item = value[key]
  if (item === undefined || item === null) return item
  return dateTimeAt(item, `${path}.${key}`)
}

function optionalNumber(
  value: JsonObject,
  key: string,
  path: string,
  opts?: { integer?: boolean; min?: number; max?: number },
): number | null | undefined {
  const item = value[key]
  if (item === undefined || item === null) return item
  return numberAt(item, `${path}.${key}`, opts)
}

function stringArrayAt(value: unknown, path: string): string[] {
  return arrayAt(value, path).map((item, index) => stringAt(item, `${path}[${index}]`))
}

function apiVersionAt(value: unknown, path: string): string {
  const version = stringAt(value, path)
  if (version !== SIMULATOR_API_VERSION) fail(path, `expected ${SIMULATOR_API_VERSION}`)
  return version
}

function decodeScenarioSummary(value: unknown, path: string): ScenarioSummary {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, [
    'api_version',
    'scenario_id',
    'name',
    'created_at',
    'participants_count',
    'trustlines_count',
    'equivalents',
    'clusters_count',
    'hubs_count',
    'tags',
  ])

  const tagsRaw = raw.tags
  const tags = tagsRaw === undefined || tagsRaw === null ? tagsRaw : stringArrayAt(tagsRaw, `${path}.tags`)
  return {
    api_version: apiVersionAt(raw.api_version, `${path}.api_version`),
    scenario_id: stringAt(raw.scenario_id, `${path}.scenario_id`),
    name: optionalString(raw, 'name', path),
    created_at: optionalDateTime(raw, 'created_at', path),
    participants_count: numberAt(raw.participants_count, `${path}.participants_count`, { integer: true, min: 0 }),
    trustlines_count: numberAt(raw.trustlines_count, `${path}.trustlines_count`, { integer: true, min: 0 }),
    equivalents: stringArrayAt(raw.equivalents, `${path}.equivalents`),
    clusters_count: optionalNumber(raw, 'clusters_count', path, { integer: true }),
    hubs_count: optionalNumber(raw, 'hubs_count', path, { integer: true }),
    tags,
  }
}

function decodeScenariosList(value: unknown, path: string): ScenariosListResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['api_version', 'items'])
  return {
    api_version: apiVersionAt(raw.api_version, `${path}.api_version`),
    items: arrayAt(raw.items, `${path}.items`).map((item, index) =>
      decodeScenarioSummary(item, `${path}.items[${index}]`),
    ),
  }
}

const RUN_STATES = new Set(['idle', 'running', 'paused', 'stopping', 'stopped', 'error'])

function decodeLastError(value: unknown, path: string): RunError {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['code', 'message', 'at'])
  return {
    code: stringAt(raw.code, `${path}.code`),
    message: stringAt(raw.message, `${path}.message`),
    at: dateTimeAt(raw.at, `${path}.at`),
  }
}

function decodeRunStatus(value: unknown, path: string): RunStatus {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, [
    'api_version',
    'run_id',
    'scenario_id',
    'mode',
    'state',
    'started_at',
    'stopped_at',
    'stop_requested_at',
    'stop_source',
    'stop_reason',
    'stop_client',
    'sim_time_ms',
    'intensity_percent',
    'ops_sec',
    'queue_depth',
    'errors_total',
    'committed_total',
    'rejected_total',
    'attempts_total',
    'timeouts_total',
    'errors_last_1m',
    'consec_all_rejected_ticks',
    'last_error',
    'last_event_type',
    'current_phase',
  ])

  const mode = stringAt(raw.mode, `${path}.mode`)
  if (mode !== 'fixtures' && mode !== 'real') fail(`${path}.mode`, 'expected fixtures or real')
  const state = stringAt(raw.state, `${path}.state`)
  if (!RUN_STATES.has(state)) fail(`${path}.state`, 'expected canonical run state')

  let lastError: RunError | null | undefined
  if (raw.last_error === undefined || raw.last_error === null) lastError = raw.last_error
  else lastError = decodeLastError(raw.last_error, `${path}.last_error`)

  return {
    api_version: apiVersionAt(raw.api_version, `${path}.api_version`),
    run_id: stringAt(raw.run_id, `${path}.run_id`),
    scenario_id: stringAt(raw.scenario_id, `${path}.scenario_id`),
    mode,
    state,
    started_at: optionalDateTime(raw, 'started_at', path),
    stopped_at: optionalDateTime(raw, 'stopped_at', path),
    stop_requested_at: optionalDateTime(raw, 'stop_requested_at', path),
    stop_source: optionalString(raw, 'stop_source', path),
    stop_reason: optionalString(raw, 'stop_reason', path),
    stop_client: optionalString(raw, 'stop_client', path),
    sim_time_ms: optionalNumber(raw, 'sim_time_ms', path, { integer: true, min: 0 }),
    intensity_percent: optionalNumber(raw, 'intensity_percent', path, { integer: true, min: 0, max: 100 }),
    ops_sec: optionalNumber(raw, 'ops_sec', path, { min: 0 }),
    queue_depth: optionalNumber(raw, 'queue_depth', path, { integer: true, min: 0 }),
    errors_total: optionalNumber(raw, 'errors_total', path, { integer: true, min: 0 }),
    committed_total: optionalNumber(raw, 'committed_total', path, { integer: true, min: 0 }),
    rejected_total: optionalNumber(raw, 'rejected_total', path, { integer: true, min: 0 }),
    attempts_total: optionalNumber(raw, 'attempts_total', path, { integer: true, min: 0 }),
    timeouts_total: optionalNumber(raw, 'timeouts_total', path, { integer: true, min: 0 }),
    errors_last_1m: optionalNumber(raw, 'errors_last_1m', path, { integer: true, min: 0 }),
    consec_all_rejected_ticks: optionalNumber(raw, 'consec_all_rejected_ticks', path, { integer: true, min: 0 }),
    last_error: lastError,
    last_event_type: optionalString(raw, 'last_event_type', path),
    current_phase: optionalString(raw, 'current_phase', path),
  }
}

function nullableStringOrNumber(value: unknown, path: string): string | number | undefined {
  if (value === undefined || value === null) return undefined
  if (typeof value === 'string') return value
  return numberAt(value, path)
}

function decodeGraphNode(value: unknown, path: string): GraphNode {
  const raw = objectAt(value, path)
  const node: GraphNode = { id: stringAt(raw.id, `${path}.id`) }

  for (const key of ['name', 'type', 'status'] as const) {
    const item = optionalString(raw, key, path)
    if (typeof item === 'string') node[key] = item
  }
  const linksCount = optionalNumber(raw, 'links_count', path, { integer: true })
  if (typeof linksCount === 'number') node.links_count = linksCount

  for (const key of ['net_balance_atoms', 'net_balance', 'viz_color_key', 'viz_shape_key', 'viz_badge_key'] as const) {
    const item = optionalString(raw, key, path)
    if (item !== undefined) node[key] = item
  }
  const netSign = raw.net_sign
  if (netSign !== undefined) {
    if (netSign !== null && netSign !== -1 && netSign !== 0 && netSign !== 1) {
      fail(`${path}.net_sign`, 'expected -1, 0, 1 or null')
    }
    node.net_sign = netSign
  }
  if (raw.viz_size !== undefined) {
    if (raw.viz_size === null) node.viz_size = null
    else {
      const size = objectAt(raw.viz_size, `${path}.viz_size`)
      onlyKeys(size, `${path}.viz_size`, ['w', 'h'])
      node.viz_size = {
        w: numberAt(size.w, `${path}.viz_size.w`),
        h: numberAt(size.h, `${path}.viz_size.h`),
      }
    }
  }
  return node
}

function decodeGraphLink(value: unknown, path: string): GraphLink {
  const raw = objectAt(value, path)
  const link: GraphLink = {
    source: stringAt(raw.source, `${path}.source`),
    target: stringAt(raw.target, `${path}.target`),
  }

  const id = optionalString(raw, 'id', path)
  if (typeof id === 'string') link.id = id
  for (const key of ['trust_limit', 'used', 'available'] as const) {
    const item = nullableStringOrNumber(raw[key], `${path}.${key}`)
    if (item !== undefined) link[key] = item
  }
  const status = optionalString(raw, 'status', path)
  if (typeof status === 'string') link.status = status
  for (const key of ['viz_color_key', 'viz_width_key', 'viz_alpha_key'] as const) {
    const item = optionalString(raw, key, path)
    if (item !== undefined) link[key] = item
  }
  return link
}

function decodeSnapshot(value: unknown, path: string): SimulatorGraphSnapshot {
  const raw = objectAt(value, path)
  const snapshot: SimulatorGraphSnapshot = {
    equivalent: stringAt(raw.equivalent, `${path}.equivalent`),
    generated_at: dateTimeAt(raw.generated_at, `${path}.generated_at`),
    nodes: arrayAt(raw.nodes, `${path}.nodes`).map((item, index) => decodeGraphNode(item, `${path}.nodes[${index}]`)),
    links: arrayAt(raw.links, `${path}.links`).map((item, index) => decodeGraphLink(item, `${path}.links[${index}]`)),
  }

  if (raw.palette !== undefined && raw.palette !== null) {
    const paletteRaw = objectAt(raw.palette, `${path}.palette`)
    const palette: NonNullable<SimulatorGraphSnapshot['palette']> = {}
    for (const [key, value] of Object.entries(paletteRaw)) {
      const entry = objectAt(value, `${path}.palette.${key}`)
      onlyKeys(entry, `${path}.palette.${key}`, ['color', 'label'])
      const label = optionalString(entry, 'label', `${path}.palette.${key}`)
      palette[key] = {
        color: stringAt(entry.color, `${path}.palette.${key}.color`),
        ...(typeof label === 'string' ? { label } : {}),
      }
    }
    snapshot.palette = palette
  }

  if (raw.limits !== undefined && raw.limits !== null) {
    const limitsRaw = objectAt(raw.limits, `${path}.limits`)
    onlyKeys(limitsRaw, `${path}.limits`, ['max_nodes', 'max_links', 'max_particles'])
    const limits: NonNullable<SimulatorGraphSnapshot['limits']> = {}
    for (const key of ['max_nodes', 'max_links', 'max_particles'] as const) {
      const item = optionalNumber(limitsRaw, key, `${path}.limits`, { integer: true })
      if (typeof item === 'number') limits[key] = item
    }
    snapshot.limits = limits
  }
  return snapshot
}

export function decodeSimulatorResponse<T>(contract: string, value: unknown, decoder: ContractDecoder<T>): T {
  try {
    return decoder(value, '$')
  } catch (error) {
    if (error instanceof ContractViolation) throw new SimulatorContractError(contract, error.message)
    throw error
  }
}

export function decodeScenariosListResponse(value: unknown): ScenariosListResponse {
  return decodeSimulatorResponse('scenario-list', value, decodeScenariosList)
}

export function decodeScenarioSummaryResponse(value: unknown): ScenarioSummary {
  return decodeSimulatorResponse('scenario-detail', value, decodeScenarioSummary)
}

export function decodeRunStatusResponse(value: unknown): RunStatus {
  return decodeSimulatorResponse('run-status', value, decodeRunStatus)
}

export function decodeGraphSnapshotResponse(value: unknown): SimulatorGraphSnapshot {
  return decodeSimulatorResponse('graph-snapshot', value, decodeSnapshot)
}
