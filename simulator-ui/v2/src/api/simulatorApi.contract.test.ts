import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { SimulatorContractError } from './simulatorContracts'
import type { ScenarioDetail } from './simulatorTypes'
import {
  actionClearingOnce,
  actionClearingReal,
  actionPaymentReal,
  actionTrustlineClose,
  actionTrustlineCreate,
  actionTrustlineUpdate,
  actionTxOnce,
  getBottlenecks,
  getMetrics,
  getParticipantsList,
  getPaymentTargets,
  getRun,
  getScenario,
  getScenarioPreview,
  getSnapshot,
  getTrustlinesList,
  listScenarios,
} from './simulatorApi'

const cfg = { apiBase: 'http://simulator.test/api/v1', accessToken: 'test-token' }

const scenario = {
  api_version: 'simulator-api/1',
  scenario_id: 'scenario-1',
  name: 'Scenario One',
  created_at: '2026-08-08T10:00:00Z',
  participants_count: 2,
  trustlines_count: 1,
  equivalents: ['UAH'],
  clusters_count: null,
  hubs_count: 1,
  tags: ['smoke'],
}

/** The details the backend answers for the scenarios of `api/scenario-detail-conformance.json`. The backend test
 * (`tests/integration/test_p036_a_scenario_detail_conformance.py`) uploads each `scenario` and requires the route's answer
 * to equal `detail`; this file requires the decoder to accept every `detail` unchanged. One file, two readers: the backend
 * and this decoder cannot drift apart unseen. */
const here = dirname(fileURLToPath(import.meta.url))
const CONFORMANCE_PATH = resolve(here, '../../../../api/scenario-detail-conformance.json')
const conformance = JSON.parse(readFileSync(CONFORMANCE_PATH, 'utf8')) as {
  cases: Array<{ name: string; detail: Record<string, unknown> }>
}

/** `ScenarioDetail` of the canon: the summary plus the required `episodes` and the nullable `playback`. */
const scenarioDetail = { ...scenario, episodes: [] as unknown[], playback: null }

const storyEpisodes = [
  {
    index: 1,
    time_ms: 1000,
    caption: { ru: 'Знакомство', en: 'Meeting' },
    pause_after: false,
    kind: 'note',
    focus: null,
    anchor: null,
    expected_cycle: null,
  },
  {
    index: 2,
    time_ms: 5000,
    caption: { ru: 'Первая покупка', en: 'First purchase' },
    pause_after: true,
    kind: 'payment',
    focus: { pids: ['A', 'B'], edges: [{ from: 'B', to: 'A' }] },
    anchor: { event: 'tx.updated', from: 'A', to: 'B', amount: '5.00', equivalent: 'UAH', time_ms: null },
    expected_cycle: null,
  },
  {
    index: 3,
    time_ms: 9000,
    caption: { ru: 'Клиринг', en: 'Clearing' },
    pause_after: false,
    kind: 'clearing',
    focus: null,
    anchor: { event: 'clearing.done', from: null, to: null, amount: null, equivalent: null, time_ms: 9000 },
    expected_cycle: ['A', 'B', 'C'],
  },
]

const storyDetail = {
  ...scenario,
  description: { ru: 'Описание', en: 'Description' },
  episodes: storyEpisodes,
  playback: { tick_seconds: 2.5, intensity_percent: 0, inject_enabled: true },
}

const runStatus = {
  api_version: 'simulator-api/1',
  run_id: 'run-1',
  scenario_id: 'scenario-1',
  mode: 'real',
  state: 'running',
  started_at: '2026-08-08T10:00:00Z',
  stopped_at: null,
  stop_requested_at: null,
  stop_source: null,
  stop_reason: null,
  stop_client: null,
  sim_time_ms: null,
  intensity_percent: null,
  ops_sec: null,
  queue_depth: null,
  errors_total: 0,
  committed_total: 0,
  rejected_total: 0,
  attempts_total: 0,
  timeouts_total: 0,
  errors_last_1m: 0,
  consec_all_rejected_ticks: null,
  last_error: null,
  last_event_type: null,
  current_phase: null,
}

const snapshot = {
  equivalent: 'UAH',
  generated_at: '2026-08-08T10:00:00Z',
  nodes: [
    {
      id: 'A',
      name: 'Alice',
      type: 'person',
      status: 'active',
      links_count: 1,
      net_balance_atoms: '0',
      net_sign: 0,
      net_balance: '0.00',
      viz_color_key: 'person',
      viz_shape_key: 'circle',
      viz_size: { w: 16, h: 16 },
      viz_badge_key: null,
    },
  ],
  links: [
    {
      id: 'A-UAH-B',
      source: 'A',
      target: 'B',
      trust_limit: '100.00',
      used: '5.00',
      available: '95.00',
      status: 'active',
      viz_color_key: null,
      viz_width_key: 'thin',
      viz_alpha_key: 'active',
    },
  ],
  palette: { person: { color: '#fff', label: null } },
  limits: { max_nodes: null, max_links: 100, max_particles: 220 },
}

const clearingRealResponse = {
  ok: true as const,
  equivalent: 'UAH',
  cleared_cycles: 0,
  total_cleared_amount: '0',
  cycles: [],
  client_action_id: null,
}

const metricsQuery = { from_ms: 0, to_ms: 10_000, step_ms: 5_000 }

// Shaped after the canonical `MetricsResponse` (`api/openapi.yaml`, `app/schemas/simulator.py`):
// seven declared series keys, `v` a decimal string or `null`, never a number.
const metricsResponse = {
  api_version: 'simulator-api/1',
  run_id: 'run-1',
  equivalent: 'UAH',
  from_ms: 0,
  to_ms: 10_000,
  step_ms: 5_000,
  series: [
    {
      key: 'success_rate',
      unit: '%',
      // `null` = no measurement yet; `"0.00000000"` = a measured zero. Different states.
      points: [
        { t_ms: 0, v: null },
        { t_ms: 5_000, v: '0.00000000' },
        { t_ms: 10_000, v: '99.50000000' },
      ],
    },
    {
      key: 'total_debt',
      unit: 'amount',
      // Beyond Number.MAX_SAFE_INTEGER on purpose: a float round-trip would corrupt this string.
      points: [{ t_ms: 10_000, v: '9007199254740993.00000001' }],
    },
    {
      key: 'active_trustlines',
      unit: 'count',
      points: [{ t_ms: 10_000, v: '12.00000000' }],
    },
    {
      key: 'avg_route_length',
      unit: null,
      points: [],
    },
  ],
}

const bottlenecksResponse = {
  api_version: 'simulator-api/1',
  run_id: 'run-1',
  equivalent: 'UAH',
  items: [
    {
      target: { kind: 'edge', from: 'alice', to: 'bob' },
      score: 0.75,
      reason_code: 'LOW_AVAILABLE',
      label: 'Alice → Bob',
      suggested_action: 'raise the limit',
    },
    {
      target: { kind: 'node', id: 'carol' },
      score: 0.5,
      reason_code: 'CLEARING_PRESSURE',
      label: null,
      suggested_action: null,
    },
  ],
}

const uncheckedActionCases = [
  {
    label: 'tx-once',
    payload: { ok: true, emitted_event_id: 'evt-1', client_action_id: null },
    call: () => actionTxOnce(cfg, 'run-1', { equivalent: 'UAH' }),
  },
  {
    label: 'clearing-once',
    payload: { ok: true, plan_id: 'plan-1', done_event_id: 'evt-2', client_action_id: null },
    call: () => actionClearingOnce(cfg, 'run-1', { equivalent: 'UAH' }),
  },
  {
    label: 'trustline-create',
    payload: {
      ok: true,
      trustline_id: 'tl-1',
      from_pid: 'alice',
      to_pid: 'bob',
      equivalent: 'UAH',
      limit: '0001.2300',
      client_action_id: null,
    },
    call: () =>
      actionTrustlineCreate(cfg, 'run-1', {
        from_pid: 'alice',
        to_pid: 'bob',
        equivalent: 'UAH',
        limit: '100',
      }),
  },
  {
    label: 'trustline-update',
    payload: {
      ok: true,
      trustline_id: 'tl-1',
      old_limit: '100.00',
      new_limit: '125.00',
      client_action_id: null,
    },
    call: () =>
      actionTrustlineUpdate(cfg, 'run-1', {
        from_pid: 'alice',
        to_pid: 'bob',
        equivalent: 'UAH',
        new_limit: '125',
      }),
  },
  {
    label: 'trustline-close',
    // 026 `T2603.2`: the answer says whether the line closed or the close is only requested.
    payload: { ok: true, trustline_id: 'tl-1', status: 'active', close_requested_at: '2026-10-02T08:00:00Z', client_action_id: null },
    call: () =>
      actionTrustlineClose(cfg, 'run-1', {
        from_pid: 'alice',
        to_pid: 'bob',
        equivalent: 'UAH',
      }),
  },
  {
    label: 'payment-real',
    payload: {
      ok: true,
      payment_id: 'payment-1',
      from_pid: 'alice',
      to_pid: 'bob',
      equivalent: 'UAH',
      amount: '12.50000000',
      status: 'committed',
      // 037 A1: two steps of one route, named, with the route's amount on each.
      routes: [
        {
          hops: [
            { from: 'alice', to: 'carol', amount: '12.50000000' },
            { from: 'carol', to: 'bob', amount: '12.50000000' },
          ],
        },
      ],
      client_action_id: null,
    },
    call: () =>
      actionPaymentReal(cfg, 'run-1', {
        from_pid: 'alice',
        to_pid: 'bob',
        equivalent: 'UAH',
        amount: '12.5',
      }),
  },
  {
    label: 'participants-list',
    payload: { items: [{ pid: 'alice', name: 'Alice', type: 'person', status: 'active' }] },
    call: () => getParticipantsList(cfg, 'run-1'),
  },
  {
    label: 'trustlines-list',
    payload: {
      items: [
        {
          from_pid: 'alice',
          from_name: 'Alice',
          to_pid: 'bob',
          to_name: 'Bob',
          equivalent: 'UAH',
          limit: '100.00',
          used: '5.00',
          reverse_used: '0.00',
          available: '95.00',
          status: 'active',
          close_requested_at: '2026-10-02T08:00:00Z',
        },
      ],
    },
    call: () => getTrustlinesList(cfg, 'run-1', 'UAH'),
  },
  {
    label: 'payment-targets',
    payload: { items: [{ to_pid: 'bob', hops: 2, max_available: '95.00' }] },
    call: () => getPaymentTargets(cfg, 'run-1', 'UAH', 'alice'),
  },
] as const

function respondWith(payload: unknown): void {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('Simulator critical REST response contracts', () => {
  it('accepts canonical scenario list and detail payloads', async () => {
    respondWith({ api_version: 'simulator-api/1', items: [scenario] })
    await expect(listScenarios(cfg)).resolves.toEqual({ api_version: 'simulator-api/1', items: [scenario] })

    respondWith(scenarioDetail)
    await expect(getScenario(cfg, 'scenario-1')).resolves.toEqual(scenarioDetail)

    const offsetScenario = { ...scenarioDetail, created_at: '2026-08-08T12:00:00.123456+02:00' }
    respondWith(offsetScenario)
    await expect(getScenario(cfg, 'scenario-1')).resolves.toEqual(offsetScenario)
  })

  it('decodes the story of a scenario: description pair, episodes and playback (036)', async () => {
    respondWith(storyDetail)
    const detail = (await getScenario(cfg, 'scenario-1')) as typeof storyDetail

    expect(detail).toEqual(storyDetail)
    expect(detail.episodes.map((e) => e.index)).toEqual([1, 2, 3]) // non-vacuity: the episodes really came through
    expect(detail.episodes[1]?.focus?.edges).toEqual([{ from: 'B', to: 'A' }])
  })

  it('decodes every detail the backend answers for the shared conformance scenarios', async () => {
    expect(conformance.cases.length, `no cases in ${CONFORMANCE_PATH}`).toBeGreaterThanOrEqual(4)
    for (const { name, detail } of conformance.cases) {
      respondWith(detail)
      await expect(getScenario(cfg, String(detail.scenario_id)), name).resolves.toEqual(detail)
    }
    // non-vacuity: the set holds the boundary spellings the decoder is accused of mishandling
    const text = JSON.stringify(conformance.cases)
    expect(text).toContain('999999999999.99999999') // 20 digits, 8 of them fraction: the largest storable amount
    expect(text).toContain('1.000000000000000000') // 18 fraction digits (a payment amount)
    expect(text).toContain(`${'0'.repeat(49)}1`) // 50 digits in all (an anchor amount)
    expect(conformance.cases.some((c) => JSON.stringify(c.detail).includes('"tx.failed"'))).toBe(true)
  })

  it.each(['0.123456789012345678', '0'.repeat(50), `${'1'.repeat(32)}.${'1'.repeat(18)}`, '00005.50', '5'])(
    'accepts the anchor amount %s, a spelling the scenario money grammar allows',
    async (amount) => {
      const anchor = { event: 'tx.updated', from: 'A', to: 'B', equivalent: 'UAH', amount }
      respondWith({ ...scenarioDetail, episodes: [{ ...storyEpisodes[1], anchor: { ...anchor, time_ms: null } }] })
      const detail = (await getScenario(cfg, 'scenario-1')) as ScenarioDetail
      expect(detail.episodes[0]?.anchor?.amount).toBe(amount)
    },
  )

  it('accepts a list item with a description pair or a null one, and a detail without the optional keys', async () => {
    const described = { ...scenario, description: { ru: 'Описание', en: 'Description' } }
    respondWith({ api_version: 'simulator-api/1', items: [described, { ...scenario, description: null }, scenario] })
    const list = await listScenarios(cfg)
    expect(list.items.map((s) => s.description ?? null)).toEqual([{ ru: 'Описание', en: 'Description' }, null, null])

    // the canon requires `episodes` on a detail and nothing else of the story: description and playback may be absent
    respondWith({ ...scenario, episodes: [] })
    await expect(getScenario(cfg, 'scenario-1')).resolves.toMatchObject({ scenario_id: 'scenario-1', episodes: [] })
  })

  it('accepts nullable run metrics from the canonical RunStatus response', async () => {
    respondWith(runStatus)
    const result = await getRun(cfg, 'run-1')

    expect(result.sim_time_ms).toBeNull()
    expect(result.intensity_percent).toBeNull()
    expect(result.ops_sec).toBeNull()
    expect(result.queue_depth).toBeNull()
  })

  it('036 B2: a run status carries the true outcome of the story events, with and without them', async () => {
    const progress = [
      {
        index: 1,
        epoch: 0,
        kind: 'clearing',
        status: 'done',
        equivalent: 'UAH',
        attempts: 1,
        cleared_cycles: 1,
        cycles: [{ cleared_amount: '10.00', edges: [{ from: 'A', to: 'B' }, { from: 'B', to: 'A' }] }],
      },
      { index: 2, epoch: 0, kind: 'payment', status: 'incomplete', reason: 'TIMEOUT', attempts: 2, payment: null },
      {
        index: 3,
        epoch: 1,
        kind: 'payment',
        status: 'done',
        payment: { from: 'A', to: 'B', amount: '5.00', equivalent: 'UAH' },
      },
      { index: 4, epoch: 1, kind: 'inject', status: 'refused', reason: 'inject_disabled_by_scenario' },
    ]
    respondWith({ ...runStatus, episode_progress: progress })
    const result = await getRun(cfg, 'run-1')
    expect(result.episode_progress).toEqual(progress)
    expect(result.episode_progress?.[0]?.cycles?.[0]?.edges[1]).toEqual({ from: 'B', to: 'A' })

    respondWith({ ...runStatus, episode_progress: null })
    await expect(getRun(cfg, 'run-1')).resolves.toMatchObject({ episode_progress: null })
    respondWith(runStatus)
    await expect(getRun(cfg, 'run-1')).resolves.not.toHaveProperty('episode_progress', expect.anything())
  })

  it('026 S4: a snapshot link keeps the close request', async () => {
    const asked = '2026-10-02T08:00:00Z'
    respondWith({ ...snapshot, links: [{ ...snapshot.links[0], trust_limit: '0.00', close_requested_at: asked }] })
    const result = await getSnapshot(cfg, 'run-1', 'UAH')
    expect(result.links[0]?.close_requested_at).toBe(asked)
  })

  it('accepts source/target aliases for run snapshot and scenario preview', async () => {
    respondWith(snapshot)
    await expect(getSnapshot(cfg, 'run-1', 'UAH')).resolves.toMatchObject({
      links: [{ source: 'A', target: 'B' }],
    })

    respondWith(snapshot)
    await expect(getScenarioPreview(cfg, 'scenario-1', 'UAH', { mode: 'real' })).resolves.toMatchObject({
      links: [{ source: 'A', target: 'B' }],
    })
  })

  it('accepts the canonical metrics response and keeps every value in its wire form', async () => {
    respondWith(metricsResponse)
    const result = await getMetrics(cfg, 'run-1', 'UAH', metricsQuery)

    expect(result).toEqual(metricsResponse)

    const successRate = result.series[0]
    // "not measured" survives as null and is not compensated into a zero...
    expect(successRate.points[0].v).toBeNull()
    // ...while a measured zero survives as the decimal string it arrived as.
    expect(successRate.points[1].v).toBe('0.00000000')
    expect(typeof successRate.points[1].v).toBe('string')

    // Money keeps every digit: this string is not representable as a JS number.
    const totalDebt = result.series[1]
    expect(totalDebt.points[0].v).toBe('9007199254740993.00000001')
    expect(String(Number(totalDebt.points[0].v))).not.toBe(totalDebt.points[0].v)

    expect(result.series.map((s) => s.key)).toEqual([
      'success_rate',
      'total_debt',
      'active_trustlines',
      'avg_route_length',
    ])
    expect(result.series[3].unit).toBeNull()
  })

  it('accepts a series with no unit key, because the contract makes unit optional', async () => {
    // The canon lists `MetricSeries.required: [key, points]` and pydantic declares
    // `unit: MetricUnit = None`. A response without the key is therefore valid and must not be
    // rejected: rejecting it would put the decoder *stricter* than its own source of truth.
    respondWith({
      ...metricsResponse,
      series: [{ key: 'success_rate', points: [{ t_ms: 0, v: '1.00000000' }] }],
    })
    const result = await getMetrics(cfg, 'run-1', 'UAH', metricsQuery)

    // Absent and explicit `null` are one state for `unit`, so the absent key reads as `null`...
    expect(result.series[0].unit).toBeNull()
    expect(result.series[0].points[0].v).toBe('1.00000000')

    // ...and that is the exact opposite of `v`, where a missing key stays a rejection.
    respondWith({
      ...metricsResponse,
      series: [{ key: 'success_rate', unit: null, points: [{ t_ms: 0 }] }],
    })
    await expect(getMetrics(cfg, 'run-1', 'UAH', metricsQuery)).rejects.toBeInstanceOf(
      SimulatorContractError,
    )
  })

  it('accepts the canonical bottlenecks response with both target kinds', async () => {
    respondWith(bottlenecksResponse)
    const result = await getBottlenecks(cfg, 'run-1', 'UAH', { limit: 20 })

    expect(result).toEqual(bottlenecksResponse)
    expect(result.items[0].target).toEqual({ kind: 'edge', from: 'alice', to: 'bob' })
    expect(result.items[1].target).toEqual({ kind: 'node', id: 'carol' })
  })

  it.each([
    {
      label: 'legacy flat points shape',
      payload: {
        api_version: 'simulator-api/1',
        equivalent: 'UAH',
        points: [{ t_ms: 0, success_rate: 99.5 }],
      },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.points',
    },
    {
      label: 'numeric metric value',
      payload: {
        ...metricsResponse,
        series: [{ key: 'success_rate', unit: '%', points: [{ t_ms: 0, v: 99.5 }] }],
      },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.series[0].points[0].v',
    },
    {
      label: 'exponential metric value',
      payload: {
        ...metricsResponse,
        series: [{ key: 'success_rate', unit: '%', points: [{ t_ms: 0, v: '1e3' }] }],
      },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.series[0].points[0].v',
    },
    {
      label: 'metric point with no value key at all',
      payload: {
        ...metricsResponse,
        series: [{ key: 'success_rate', unit: '%', points: [{ t_ms: 0 }] }],
      },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.series[0].points[0].v',
    },
    {
      label: 'unknown metric series key',
      payload: {
        ...metricsResponse,
        series: [{ key: 'route_depth', unit: 'count', points: [] }],
      },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.series[0].key',
    },
    {
      label: 'unknown metric unit',
      payload: {
        ...metricsResponse,
        series: [{ key: 'success_rate', unit: 'percent', points: [] }],
      },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.series[0].unit',
    },
    {
      label: 'metrics window missing step',
      payload: { ...metricsResponse, step_ms: undefined },
      call: () => getMetrics(cfg, 'run-1', 'UAH', metricsQuery),
      contract: 'metrics',
      diagnostic: '$.step_ms',
    },
    {
      label: 'payment-real answer without routes',
      payload: { ok: true, payment_id: 'p1', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1', status: 'COMMITTED' },
      call: () => actionPaymentReal(cfg, 'run-1', { from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1' }),
      contract: 'action-payment-real',
      diagnostic: '$.routes',
    },
    {
      label: 'payment-real hop with the python-side from_ alias',
      payload: {
        ok: true, payment_id: 'p1', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1', status: 'COMMITTED',
        routes: [{ hops: [{ from_: 'alice', to: 'bob', amount: '1' }] }],
      },
      call: () => actionPaymentReal(cfg, 'run-1', { from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1' }),
      contract: 'action-payment-real',
      diagnostic: '$.routes[0].hops[0].from_',
    },
    {
      label: 'payment-real hop amount that is not a decimal string',
      payload: {
        ok: true, payment_id: 'p1', from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1', status: 'COMMITTED',
        routes: [{ hops: [{ from: 'alice', to: 'bob', amount: 1 }] }],
      },
      call: () => actionPaymentReal(cfg, 'run-1', { from_pid: 'alice', to_pid: 'bob', equivalent: 'UAH', amount: '1' }),
      contract: 'action-payment-real',
      diagnostic: '$.routes[0].hops[0].amount',
    },
    {
      label: 'legacy bottleneck item shape',
      payload: {
        api_version: 'simulator-api/1',
        run_id: 'run-1',
        equivalent: 'UAH',
        items: [{ kind: 'edge', score: 0.75, from: 'alice', to: 'bob' }],
      },
      call: () => getBottlenecks(cfg, 'run-1', 'UAH', {}),
      contract: 'bottlenecks',
      diagnostic: '$.items[0].kind',
    },
    {
      label: 'unknown bottleneck reason code',
      payload: {
        ...bottlenecksResponse,
        items: [{ ...bottlenecksResponse.items[0], reason_code: 'SOMETHING_ELSE' }],
      },
      call: () => getBottlenecks(cfg, 'run-1', 'UAH', {}),
      contract: 'bottlenecks',
      diagnostic: '$.items[0].reason_code',
    },
    {
      label: 'bottleneck edge target with the python-side from_ alias',
      payload: {
        ...bottlenecksResponse,
        items: [{ ...bottlenecksResponse.items[0], target: { kind: 'edge', from_: 'alice', to: 'bob' } }],
      },
      call: () => getBottlenecks(cfg, 'run-1', 'UAH', {}),
      contract: 'bottlenecks',
      diagnostic: '$.items[0].target.from_',
    },
    {
      label: 'bottleneck target of an unknown kind',
      payload: {
        ...bottlenecksResponse,
        items: [{ ...bottlenecksResponse.items[0], target: { kind: 'cluster', id: 'c1' } }],
      },
      call: () => getBottlenecks(cfg, 'run-1', 'UAH', {}),
      contract: 'bottlenecks',
      diagnostic: '$.items[0].target.kind',
    },
    {
      label: 'bottlenecks response without run_id',
      payload: { ...bottlenecksResponse, run_id: undefined },
      call: () => getBottlenecks(cfg, 'run-1', 'UAH', {}),
      contract: 'bottlenecks',
      diagnostic: '$.run_id',
    },
  ])('rejects malformed 2xx $label response', async ({ payload, call, contract, diagnostic }) => {
    respondWith(payload)

    try {
      await call()
      throw new Error('expected contract rejection')
    } catch (error) {
      expect(error).toBeInstanceOf(SimulatorContractError)
      expect(error).toMatchObject({ status: 200, contract })
      expect((error as SimulatorContractError).diagnostic).toContain(diagnostic)
    }
  })

  it('accepts an honest zero-cycle clearing action response', async () => {
    respondWith(clearingRealResponse)

    await expect(actionClearingReal(cfg, 'run-1', { equivalent: 'UAH' })).resolves.toEqual(clearingRealResponse)
  })

  it.each(uncheckedActionCases)('accepts canonical $label response', async ({ payload, call }) => {
    respondWith(payload)
    await expect(call()).resolves.toEqual(payload)
  })

  it.each([
    {
      label: 'tx-once event id',
      payload: { ok: true, emitted_event_id: 1, client_action_id: null },
      call: uncheckedActionCases[0].call,
      contract: 'action-tx-once',
      diagnostic: '$.emitted_event_id',
    },
    {
      label: 'clearing-once plan id',
      payload: { ok: true, plan_id: 1, done_event_id: 'evt-2', client_action_id: null },
      call: uncheckedActionCases[1].call,
      contract: 'action-clearing-once',
      diagnostic: '$.plan_id',
    },
    {
      label: 'trustline-create decimal limit',
      payload: { ...uncheckedActionCases[2].payload, limit: 100 },
      call: uncheckedActionCases[2].call,
      contract: 'action-trustline-create',
      diagnostic: '$.limit',
    },
    {
      label: 'trustline-update non-canonical decimal',
      payload: { ...uncheckedActionCases[3].payload, new_limit: '1e3' },
      call: uncheckedActionCases[3].call,
      contract: 'action-trustline-update',
      diagnostic: '$.new_limit',
    },
    {
      label: 'trustline-close missing id',
      payload: { ok: true, client_action_id: null },
      call: uncheckedActionCases[4].call,
      contract: 'action-trustline-close',
      diagnostic: '$.trustline_id',
    },
    {
      label: 'payment-real decimal amount',
      payload: { ...uncheckedActionCases[5].payload, amount: 12.5 },
      call: uncheckedActionCases[5].call,
      contract: 'action-payment-real',
      diagnostic: '$.amount',
    },
    {
      label: 'participants-list item',
      payload: { items: [{ pid: 1, name: 'Alice', type: 'person', status: 'active' }] },
      call: uncheckedActionCases[6].call,
      contract: 'action-participants-list',
      diagnostic: '$.items[0].pid',
    },
    {
      label: 'trustlines-list alias',
      payload: {
        items: [{ ...uncheckedActionCases[7].payload.items[0], from_pid: undefined, from: 'alice' }],
      },
      call: uncheckedActionCases[7].call,
      contract: 'action-trustlines-list',
      diagnostic: '$.items[0].from',
    },
    {
      label: 'payment-targets hops',
      payload: { items: [{ to_pid: 'bob', hops: 0, max_available: '95' }] },
      call: uncheckedActionCases[8].call,
      contract: 'payment-targets',
      diagnostic: '$.items[0].hops',
    },
  ])('rejects malformed 2xx $label response', async ({ payload, call, contract, diagnostic }) => {
    respondWith(payload)

    try {
      await call()
      throw new Error('expected contract rejection')
    } catch (error) {
      expect(error).toBeInstanceOf(SimulatorContractError)
      expect(error).toMatchObject({ status: 200, contract })
      expect((error as SimulatorContractError).diagnostic).toContain(diagnostic)
    }
  })

  it.each([
    {
      label: 'scenario list item',
      payload: { api_version: 'simulator-api/1', items: [{ ...scenario, participants_count: '2' }] },
      call: () => listScenarios(cfg),
      contract: 'scenario-list',
      diagnostic: '$.items[0].participants_count',
    },
    {
      label: 'scenario detail extra field',
      payload: { ...scenarioDetail, label: 'legacy field is not in backend schema' },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.label',
    },
    {
      label: 'scenario detail non-date-time date',
      payload: { ...scenarioDetail, created_at: '2026-08-08' },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.created_at',
    },
    {
      label: 'scenario detail without the required episodes',
      payload: scenario,
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes',
    },
    {
      label: 'scenario list item carrying the story (the list does not)',
      payload: { api_version: 'simulator-api/1', items: [{ ...scenario, episodes: [] }] },
      call: () => listScenarios(cfg),
      contract: 'scenario-list',
      diagnostic: '$.items[0].episodes',
    },
    {
      label: 'scenario list item description without a language',
      payload: { api_version: 'simulator-api/1', items: [{ ...scenario, description: { ru: 'x' } }] },
      call: () => listScenarios(cfg),
      contract: 'scenario-list',
      diagnostic: '$.items[0].description.en',
    },
    {
      label: 'scenario description as a plain string (the backend always sends the pair)',
      payload: { ...scenarioDetail, description: 'plain' },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.description',
    },
    {
      label: 'a third language in a text pair',
      payload: { ...scenarioDetail, description: { ru: 'x', en: 'y', fr: 'z' } },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.description.fr',
    },
    {
      label: 'episode extra field',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[0], zoom: 2 }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].zoom',
    },
    {
      label: 'episode caption without a language',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[0], caption: { ru: 'x' } }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].caption.en',
    },
    {
      label: 'episode pause_after not boolean',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[0], pause_after: 'yes' }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].pause_after',
    },
    {
      label: 'episode of an unknown kind',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[0], kind: 'teleport' }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].kind',
    },
    {
      label: 'anchor of an unknown event',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[1], anchor: { event: 'run_status' } }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.event',
    },
    {
      label: 'anchor amount not a decimal string',
      payload: {
        ...scenarioDetail,
        episodes: [{ ...storyEpisodes[1], anchor: { event: 'tx.updated', from: 'A', to: 'B', equivalent: 'UAH', amount: 5 } }],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.amount',
    },
    {
      label: 'tx.updated anchor without an amount',
      payload: {
        ...scenarioDetail,
        episodes: [{ ...storyEpisodes[1], anchor: { event: 'tx.updated', from: 'A', to: 'B', equivalent: 'UAH' } }],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.amount',
    },
    {
      label: 'tx.updated anchor with only the event',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[1], anchor: { event: 'tx.updated' } }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.from',
    },
    {
      label: 'tx.failed anchor carrying an amount (a failure has none)',
      payload: {
        ...scenarioDetail,
        episodes: [
          {
            ...storyEpisodes[1],
            anchor: { event: 'tx.failed', from: 'A', to: 'B', equivalent: 'UAH', amount: '5.00' },
          },
        ],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.amount',
    },
    {
      label: 'tx.failed anchor without an equivalent',
      payload: {
        ...scenarioDetail,
        episodes: [{ ...storyEpisodes[1], anchor: { event: 'tx.failed', from: 'A', to: 'B' } }],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.equivalent',
    },
    {
      label: 'anchor amount with a sign (the scenario money grammar has none)',
      payload: {
        ...scenarioDetail,
        episodes: [
          {
            ...storyEpisodes[1],
            anchor: { event: 'tx.updated', from: 'A', to: 'B', equivalent: 'UAH', amount: '-5.00' },
          },
        ],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.amount',
    },
    {
      label: 'anchor amount with 19 fraction digits',
      payload: {
        ...scenarioDetail,
        episodes: [
          {
            ...storyEpisodes[1],
            anchor: { event: 'tx.updated', from: 'A', to: 'B', equivalent: 'UAH', amount: '0.1234567890123456789' },
          },
        ],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.amount',
    },
    {
      label: 'anchor amount with a terminal newline',
      payload: {
        ...scenarioDetail,
        episodes: [
          { ...storyEpisodes[1], anchor: { event: 'tx.updated', from: 'A', to: 'B', equivalent: 'UAH', amount: '5\n' } },
        ],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].anchor.amount',
    },
    {
      label: 'caption with an empty language',
      payload: { ...scenarioDetail, episodes: [{ ...storyEpisodes[0], caption: { ru: 'x', en: '' } }] },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].caption.en',
    },
    {
      label: 'playback tick below the canon floor',
      payload: { ...scenarioDetail, playback: { tick_seconds: 0.1 } },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.playback.tick_seconds',
    },
    {
      label: 'focus edge in the Python spelling from_',
      payload: {
        ...scenarioDetail,
        episodes: [{ ...storyEpisodes[1], focus: { pids: [], edges: [{ from_: 'A', to: 'B' }] } }],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].focus.edges[0].from_',
    },
    {
      label: 'focus edge with an empty end',
      payload: {
        ...scenarioDetail,
        episodes: [{ ...storyEpisodes[1], focus: { pids: [], edges: [{ from: '', to: 'A' }] } }],
      },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.episodes[0].focus.edges[0].from',
    },
    {
      label: 'playback extra field',
      payload: { ...scenarioDetail, playback: { speed: 2 } },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.playback.speed',
    },
    {
      label: 'playback inject_enabled not boolean',
      payload: { ...scenarioDetail, playback: { inject_enabled: 'yes' } },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.playback.inject_enabled',
    },
    {
      label: 'run status metric',
      payload: { ...runStatus, sim_time_ms: '0' },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.sim_time_ms',
    },
    {
      label: 'run status episode progress with an unknown status',
      payload: { ...runStatus, episode_progress: [{ index: 0, epoch: 0, kind: 'payment', status: 'pending' }] },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.episode_progress[0].status',
    },
    {
      label: 'run status episode progress with an unknown kind',
      payload: { ...runStatus, episode_progress: [{ index: 0, epoch: 0, kind: 'note', status: 'done' }] },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.episode_progress[0].kind',
    },
    {
      label: 'run status episode progress with an extra field',
      payload: { ...runStatus, episode_progress: [{ index: 0, epoch: 0, kind: 'payment', status: 'done', tx_id: 'x' }] },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.episode_progress[0].tx_id',
    },
    {
      label: 'run status episode progress with a float amount',
      payload: { ...runStatus, episode_progress: [{ index: 0, epoch: 0, kind: 'clearing', status: 'done', cycles: [{ cleared_amount: 1.5, edges: [] }] }] },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.episode_progress[0].cycles[0].cleared_amount',
    },
    {
      label: 'run status episode progress with aliased-away edge ends',
      payload: { ...runStatus, episode_progress: [{ index: 0, epoch: 0, kind: 'clearing', status: 'done', cycles: [{ cleared_amount: '1.00', edges: [{ source: 'A', target: 'B' }] }] }] },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.episode_progress[0].cycles[0].edges[0].source',
    },
    {
      label: 'run status invalid error date-time',
      payload: { ...runStatus, last_error: { code: 'FAILED', message: 'bad', at: '2026-02-30T10:00:00Z' } },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.last_error.at',
    },
    {
      label: 'snapshot invalid generated date-time',
      payload: { ...snapshot, generated_at: '2026-08-08 10:00:00Z' },
      call: () => getSnapshot(cfg, 'run-1', 'UAH'),
      contract: 'graph-snapshot',
      diagnostic: '$.generated_at',
    },
    {
      label: 'snapshot link aliases',
      payload: { ...snapshot, links: [{ from: 'A', to: 'B' }] },
      call: () => getSnapshot(cfg, 'run-1', 'UAH'),
      contract: 'graph-snapshot',
      diagnostic: '$.links[0].source',
    },
    {
      label: 'clearing action missing cycle count',
      payload: {
        ok: true,
        equivalent: 'UAH',
        total_cleared_amount: '0',
        cycles: [],
        client_action_id: null,
      },
      call: () => actionClearingReal(cfg, 'run-1', { equivalent: 'UAH' }),
      contract: 'action-clearing-real',
      diagnostic: '$.cleared_cycles',
    },
    {
      label: 'clearing action string cycle count',
      payload: { ...clearingRealResponse, cleared_cycles: '0' },
      call: () => actionClearingReal(cfg, 'run-1', { equivalent: 'UAH' }),
      contract: 'action-clearing-real',
      diagnostic: '$.cleared_cycles',
    },
    {
      label: 'clearing action non-canonical edge alias',
      payload: {
        ...clearingRealResponse,
        cleared_cycles: 1,
        total_cleared_amount: '1.00',
        cycles: [{ cleared_amount: '1.00', edges: [{ from_: 'alice', to: 'bob' }] }],
      },
      call: () => actionClearingReal(cfg, 'run-1', { equivalent: 'UAH' }),
      contract: 'action-clearing-real',
      diagnostic: '$.cycles[0].edges[0].from',
    },
  ])('rejects malformed 2xx $label before returning trusted data', async ({ payload, call, contract, diagnostic }) => {
    respondWith(payload)

    try {
      await call()
      throw new Error('expected contract rejection')
    } catch (error) {
      expect(error).toBeInstanceOf(SimulatorContractError)
      expect(error).toMatchObject({ status: 200, contract })
      expect((error as SimulatorContractError).diagnostic).toContain(diagnostic)
    }
  })
})
