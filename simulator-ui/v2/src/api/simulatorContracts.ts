import type { GraphLink, GraphNode } from '../types'
import { ApiError } from './http'
import type {
  BottleneckItem,
  BottleneckReasonCode,
  BottleneckTarget,
  BottlenecksResponse,
  ClearingOnceResponse,
  MetricPoint,
  MetricSeries,
  MetricSeriesKey,
  MetricUnit,
  MetricsResponse,
  RunError,
  RunStatus,
  ScenarioSummary,
  ScenariosListResponse,
  SimulatorActionClearingRealResponse,
  SimulatorActionParticipantsListResponse,
  SimulatorActionPaymentRealResponse,
  SimulatorActionTrustlineCloseResponse,
  SimulatorActionTrustlineCreateResponse,
  SimulatorActionTrustlineUpdateResponse,
  SimulatorActionTrustlinesListResponse,
  SimulatorGraphSnapshot,
  SimulatorPaymentTargetsResponse,
  TxOnceResponse,
} from './simulatorTypes'

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

const CANONICAL_DECIMAL_STRING = /^-?\d+(?:\.(\d+))?$/

function decimalStringAt(value: unknown, path: string): string {
  const text = stringAt(value, path)
  const match = CANONICAL_DECIMAL_STRING.exec(text)
  const digitCount = text.replace(/[-.]/g, '').length
  if (!match || digitCount > 50 || (match[1]?.length ?? 0) > 18) {
    fail(path, 'expected bounded plain decimal string')
  }
  return text
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

function optionalDecimalString(value: JsonObject, key: string, path: string): string | null | undefined {
  const item = value[key]
  if (item === undefined || item === null) return item
  return decimalStringAt(item, `${path}.${key}`)
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

function decodeClearingEdge(value: unknown, path: string): { from: string; to: string } {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['from', 'to'])
  return {
    from: stringAt(raw.from, `${path}.from`),
    to: stringAt(raw.to, `${path}.to`),
  }
}

function clientActionId(value: JsonObject, path: string): { client_action_id?: string | null } {
  const item = optionalString(value, 'client_action_id', path)
  return item !== undefined ? { client_action_id: item } : {}
}

function requireOk(value: JsonObject, path: string): void {
  if (value.ok !== true) fail(`${path}.ok`, 'expected true')
}

function decodeTxOnce(value: unknown, path: string): TxOnceResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['ok', 'emitted_event_id', 'client_action_id'])
  requireOk(raw, path)
  return {
    ok: true,
    emitted_event_id: stringAt(raw.emitted_event_id, `${path}.emitted_event_id`),
    ...clientActionId(raw, path),
  }
}

function decodeClearingOnce(value: unknown, path: string): ClearingOnceResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['ok', 'plan_id', 'done_event_id', 'client_action_id'])
  requireOk(raw, path)
  return {
    ok: true,
    plan_id: stringAt(raw.plan_id, `${path}.plan_id`),
    done_event_id: stringAt(raw.done_event_id, `${path}.done_event_id`),
    ...clientActionId(raw, path),
  }
}

function decodeTrustlineCreate(value: unknown, path: string): SimulatorActionTrustlineCreateResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, [
    'ok',
    'trustline_id',
    'from_pid',
    'to_pid',
    'equivalent',
    'limit',
    'client_action_id',
  ])
  requireOk(raw, path)
  return {
    ok: true,
    trustline_id: stringAt(raw.trustline_id, `${path}.trustline_id`),
    from_pid: stringAt(raw.from_pid, `${path}.from_pid`),
    to_pid: stringAt(raw.to_pid, `${path}.to_pid`),
    equivalent: stringAt(raw.equivalent, `${path}.equivalent`),
    limit: decimalStringAt(raw.limit, `${path}.limit`),
    ...clientActionId(raw, path),
  }
}

function decodeTrustlineUpdate(value: unknown, path: string): SimulatorActionTrustlineUpdateResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['ok', 'trustline_id', 'old_limit', 'new_limit', 'client_action_id'])
  requireOk(raw, path)
  return {
    ok: true,
    trustline_id: stringAt(raw.trustline_id, `${path}.trustline_id`),
    old_limit: decimalStringAt(raw.old_limit, `${path}.old_limit`),
    new_limit: decimalStringAt(raw.new_limit, `${path}.new_limit`),
    ...clientActionId(raw, path),
  }
}

function decodeTrustlineClose(value: unknown, path: string): SimulatorActionTrustlineCloseResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['ok', 'trustline_id', 'client_action_id'])
  requireOk(raw, path)
  return {
    ok: true,
    trustline_id: stringAt(raw.trustline_id, `${path}.trustline_id`),
    ...clientActionId(raw, path),
  }
}

function decodePaymentReal(value: unknown, path: string): SimulatorActionPaymentRealResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, [
    'ok',
    'payment_id',
    'from_pid',
    'to_pid',
    'equivalent',
    'amount',
    'status',
    'client_action_id',
  ])
  requireOk(raw, path)
  return {
    ok: true,
    payment_id: stringAt(raw.payment_id, `${path}.payment_id`),
    from_pid: stringAt(raw.from_pid, `${path}.from_pid`),
    to_pid: stringAt(raw.to_pid, `${path}.to_pid`),
    equivalent: stringAt(raw.equivalent, `${path}.equivalent`),
    amount: decimalStringAt(raw.amount, `${path}.amount`),
    status: stringAt(raw.status, `${path}.status`),
    ...clientActionId(raw, path),
  }
}

function decodeParticipantsList(value: unknown, path: string): SimulatorActionParticipantsListResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['items'])
  return {
    items: arrayAt(raw.items, `${path}.items`).map((item, index) => {
      const itemPath = `${path}.items[${index}]`
      const participant = objectAt(item, itemPath)
      onlyKeys(participant, itemPath, ['pid', 'name', 'type', 'status'])
      return {
        pid: stringAt(participant.pid, `${itemPath}.pid`),
        name: stringAt(participant.name, `${itemPath}.name`),
        type: stringAt(participant.type, `${itemPath}.type`),
        status: stringAt(participant.status, `${itemPath}.status`),
      }
    }),
  }
}

function decodeTrustlinesList(value: unknown, path: string): SimulatorActionTrustlinesListResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['items'])
  return {
    items: arrayAt(raw.items, `${path}.items`).map((item, index) => {
      const itemPath = `${path}.items[${index}]`
      const trustline = objectAt(item, itemPath)
      onlyKeys(trustline, itemPath, [
        'from_pid',
        'from_name',
        'to_pid',
        'to_name',
        'equivalent',
        'limit',
        'used',
        'reverse_used',
        'available',
        'status',
      ])
      return {
        from_pid: stringAt(trustline.from_pid, `${itemPath}.from_pid`),
        from_name: stringAt(trustline.from_name, `${itemPath}.from_name`),
        to_pid: stringAt(trustline.to_pid, `${itemPath}.to_pid`),
        to_name: stringAt(trustline.to_name, `${itemPath}.to_name`),
        equivalent: stringAt(trustline.equivalent, `${itemPath}.equivalent`),
        limit: decimalStringAt(trustline.limit, `${itemPath}.limit`),
        used: decimalStringAt(trustline.used, `${itemPath}.used`),
        reverse_used: decimalStringAt(trustline.reverse_used, `${itemPath}.reverse_used`),
        available: decimalStringAt(trustline.available, `${itemPath}.available`),
        status: stringAt(trustline.status, `${itemPath}.status`),
      }
    }),
  }
}

function decodePaymentTargets(value: unknown, path: string): SimulatorPaymentTargetsResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['items'])
  return {
    items: arrayAt(raw.items, `${path}.items`).map((item, index) => {
      const itemPath = `${path}.items[${index}]`
      const target = objectAt(item, itemPath)
      onlyKeys(target, itemPath, ['to_pid', 'hops', 'max_available'])
      return {
        to_pid: stringAt(target.to_pid, `${itemPath}.to_pid`),
        hops: numberAt(target.hops, `${itemPath}.hops`, { integer: true, min: 1 }),
        max_available: optionalDecimalString(target, 'max_available', itemPath),
      }
    }),
  }
}

const METRIC_SERIES_KEYS = new Set<string>([
  'success_rate',
  'avg_route_length',
  'total_debt',
  'clearing_volume',
  'bottlenecks_score',
  'active_participants',
  'active_trustlines',
])

const METRIC_UNITS = new Set<string>(['%', 'count', 'amount'])

function decodeMetricPoint(value: unknown, path: string): MetricPoint {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['t_ms', 'v'])
  // `v` is a REQUIRED key: an absent one is not the same statement as an explicit `null`, and
  // filling it in here would be exactly the silent repair §9 forbids. (Contrast `unit` in
  // `decodeMetricSeries` below, where absence and `null` do mean the same thing and absence is
  // therefore accepted — the two sites differ on purpose.)
  if (!('v' in raw)) fail(`${path}.v`, 'expected decimal string or null')
  // `null` stays `null` — "not measured" must never become a measured zero — and a present value
  // stays the backend's own decimal string: parsing it into a JS number is where money loses
  // exactness (AGENTS.md §8).
  const v = raw.v === null ? null : decimalStringAt(raw.v, `${path}.v`)
  return {
    t_ms: numberAt(raw.t_ms, `${path}.t_ms`, { integer: true, min: 0 }),
    v,
  }
}

function decodeMetricSeries(value: unknown, path: string): MetricSeries {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['key', 'unit', 'points'])

  const key = stringAt(raw.key, `${path}.key`)
  if (!METRIC_SERIES_KEYS.has(key)) fail(`${path}.key`, 'expected canonical metric series key')

  // `unit` is OPTIONAL by both sides of the contract — the canon lists
  // `MetricSeries.required: [key, points]`, pydantic declares `unit: MetricUnit = None` — and an
  // absent key carries exactly the meaning of an explicit `null`: no unit declared. So absence is
  // accepted and read as `null`.
  //
  // Note the deliberate asymmetry with `decodeMetricPoint` above, where an absent `v` is REJECTED:
  // there `null` says "not measured" and a string says "measured", so a missing key is a third,
  // unstated thing and inventing one of the two would be the silent repair §9 forbids. Here the
  // two states are one state. Neither site is a mistake for the other.
  let unit: MetricUnit = null
  if (raw.unit !== undefined && raw.unit !== null) {
    // Optional key, but not an arbitrary value: an unknown unit is still a contract violation.
    const unitText = stringAt(raw.unit, `${path}.unit`)
    if (!METRIC_UNITS.has(unitText)) fail(`${path}.unit`, 'expected %, count or amount')
    unit = unitText as MetricUnit
  }

  return {
    key: key as MetricSeriesKey,
    unit,
    points: arrayAt(raw.points, `${path}.points`).map((point, index) =>
      decodeMetricPoint(point, `${path}.points[${index}]`),
    ),
  }
}

function decodeMetrics(value: unknown, path: string): MetricsResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['api_version', 'run_id', 'equivalent', 'from_ms', 'to_ms', 'step_ms', 'series'])
  return {
    api_version: apiVersionAt(raw.api_version, `${path}.api_version`),
    run_id: stringAt(raw.run_id, `${path}.run_id`),
    equivalent: stringAt(raw.equivalent, `${path}.equivalent`),
    from_ms: numberAt(raw.from_ms, `${path}.from_ms`, { integer: true, min: 0 }),
    to_ms: numberAt(raw.to_ms, `${path}.to_ms`, { integer: true, min: 0 }),
    step_ms: numberAt(raw.step_ms, `${path}.step_ms`, { integer: true, min: 1 }),
    series: arrayAt(raw.series, `${path}.series`).map((series, index) =>
      decodeMetricSeries(series, `${path}.series[${index}]`),
    ),
  }
}

const BOTTLENECK_REASON_CODES = new Set<string>([
  'LOW_AVAILABLE',
  'HIGH_USED',
  'FREQUENT_ABORTS',
  'TOO_MANY_TIMEOUTS',
  'ROUTING_TOO_DEEP',
  'CLEARING_PRESSURE',
])

function decodeBottleneckTarget(value: unknown, path: string): BottleneckTarget {
  const raw = objectAt(value, path)
  const kind = stringAt(raw.kind, `${path}.kind`)
  if (kind === 'edge') {
    // Wire key is `from` (pydantic `Field(alias="from")`), never `from_`.
    onlyKeys(raw, path, ['kind', 'from', 'to'])
    return {
      kind: 'edge',
      from: stringAt(raw.from, `${path}.from`),
      to: stringAt(raw.to, `${path}.to`),
    }
  }
  if (kind === 'node') {
    onlyKeys(raw, path, ['kind', 'id'])
    return { kind: 'node', id: stringAt(raw.id, `${path}.id`) }
  }
  return fail(`${path}.kind`, 'expected edge or node')
}

function decodeBottleneckItem(value: unknown, path: string): BottleneckItem {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['target', 'score', 'reason_code', 'label', 'suggested_action'])

  const reasonCode = stringAt(raw.reason_code, `${path}.reason_code`)
  if (!BOTTLENECK_REASON_CODES.has(reasonCode)) {
    fail(`${path}.reason_code`, 'expected canonical bottleneck reason code')
  }

  return {
    target: decodeBottleneckTarget(raw.target, `${path}.target`),
    // `score` is a ranking weight, not money: the canon types it `number`.
    score: numberAt(raw.score, `${path}.score`),
    reason_code: reasonCode as BottleneckReasonCode,
    label: optionalString(raw, 'label', path),
    suggested_action: optionalString(raw, 'suggested_action', path),
  }
}

function decodeBottlenecks(value: unknown, path: string): BottlenecksResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['api_version', 'run_id', 'equivalent', 'items'])
  return {
    api_version: apiVersionAt(raw.api_version, `${path}.api_version`),
    run_id: stringAt(raw.run_id, `${path}.run_id`),
    equivalent: stringAt(raw.equivalent, `${path}.equivalent`),
    items: arrayAt(raw.items, `${path}.items`).map((item, index) =>
      decodeBottleneckItem(item, `${path}.items[${index}]`),
    ),
  }
}

function decodeClearingCycle(
  value: unknown,
  path: string,
): SimulatorActionClearingRealResponse['cycles'][number] {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, ['cleared_amount', 'edges'])
  return {
    cleared_amount: stringAt(raw.cleared_amount, `${path}.cleared_amount`),
    edges: arrayAt(raw.edges, `${path}.edges`).map((edge, index) =>
      decodeClearingEdge(edge, `${path}.edges[${index}]`),
    ),
  }
}

function decodeClearingReal(value: unknown, path: string): SimulatorActionClearingRealResponse {
  const raw = objectAt(value, path)
  onlyKeys(raw, path, [
    'ok',
    'equivalent',
    'cleared_cycles',
    'total_cleared_amount',
    'cycles',
    'client_action_id',
  ])
  if (raw.ok !== true) fail(`${path}.ok`, 'expected true')

  const clientActionId = optionalString(raw, 'client_action_id', path)
  return {
    ok: true,
    equivalent: stringAt(raw.equivalent, `${path}.equivalent`),
    cleared_cycles: numberAt(raw.cleared_cycles, `${path}.cleared_cycles`, { integer: true, min: 0 }),
    total_cleared_amount: stringAt(raw.total_cleared_amount, `${path}.total_cleared_amount`),
    cycles: arrayAt(raw.cycles, `${path}.cycles`).map((cycle, index) =>
      decodeClearingCycle(cycle, `${path}.cycles[${index}]`),
    ),
    ...(clientActionId !== undefined ? { client_action_id: clientActionId } : {}),
  }
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

export function decodeSimulatorActionClearingRealResponse(value: unknown): SimulatorActionClearingRealResponse {
  return decodeSimulatorResponse('action-clearing-real', value, decodeClearingReal)
}

export function decodeTxOnceResponse(value: unknown): TxOnceResponse {
  return decodeSimulatorResponse('action-tx-once', value, decodeTxOnce)
}

export function decodeClearingOnceResponse(value: unknown): ClearingOnceResponse {
  return decodeSimulatorResponse('action-clearing-once', value, decodeClearingOnce)
}

export function decodeSimulatorActionTrustlineCreateResponse(
  value: unknown,
): SimulatorActionTrustlineCreateResponse {
  return decodeSimulatorResponse('action-trustline-create', value, decodeTrustlineCreate)
}

export function decodeSimulatorActionTrustlineUpdateResponse(
  value: unknown,
): SimulatorActionTrustlineUpdateResponse {
  return decodeSimulatorResponse('action-trustline-update', value, decodeTrustlineUpdate)
}

export function decodeSimulatorActionTrustlineCloseResponse(
  value: unknown,
): SimulatorActionTrustlineCloseResponse {
  return decodeSimulatorResponse('action-trustline-close', value, decodeTrustlineClose)
}

export function decodeSimulatorActionPaymentRealResponse(value: unknown): SimulatorActionPaymentRealResponse {
  return decodeSimulatorResponse('action-payment-real', value, decodePaymentReal)
}

export function decodeSimulatorActionParticipantsListResponse(
  value: unknown,
): SimulatorActionParticipantsListResponse {
  return decodeSimulatorResponse('action-participants-list', value, decodeParticipantsList)
}

export function decodeSimulatorActionTrustlinesListResponse(
  value: unknown,
): SimulatorActionTrustlinesListResponse {
  return decodeSimulatorResponse('action-trustlines-list', value, decodeTrustlinesList)
}

export function decodeSimulatorPaymentTargetsResponse(value: unknown): SimulatorPaymentTargetsResponse {
  return decodeSimulatorResponse('payment-targets', value, decodePaymentTargets)
}

export function decodeMetricsResponse(value: unknown): MetricsResponse {
  return decodeSimulatorResponse('metrics', value, decodeMetrics)
}

export function decodeBottlenecksResponse(value: unknown): BottlenecksResponse {
  return decodeSimulatorResponse('bottlenecks', value, decodeBottlenecks)
}
