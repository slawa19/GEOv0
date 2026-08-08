import { afterEach, describe, expect, it, vi } from 'vitest'

import { SimulatorContractError } from './simulatorContracts'
import { getRun, getScenario, getScenarioPreview, getSnapshot, listScenarios } from './simulatorApi'

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

    respondWith(scenario)
    await expect(getScenario(cfg, 'scenario-1')).resolves.toEqual(scenario)

    const offsetScenario = { ...scenario, created_at: '2026-08-08T12:00:00.123456+02:00' }
    respondWith(offsetScenario)
    await expect(getScenario(cfg, 'scenario-1')).resolves.toEqual(offsetScenario)
  })

  it('accepts nullable run metrics from the canonical RunStatus response', async () => {
    respondWith(runStatus)
    const result = await getRun(cfg, 'run-1')

    expect(result.sim_time_ms).toBeNull()
    expect(result.intensity_percent).toBeNull()
    expect(result.ops_sec).toBeNull()
    expect(result.queue_depth).toBeNull()
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
      payload: { ...scenario, label: 'legacy field is not in backend schema' },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.label',
    },
    {
      label: 'scenario detail non-date-time date',
      payload: { ...scenario, created_at: '2026-08-08' },
      call: () => getScenario(cfg, 'scenario-1'),
      contract: 'scenario-detail',
      diagnostic: '$.created_at',
    },
    {
      label: 'run status metric',
      payload: { ...runStatus, sim_time_ms: '0' },
      call: () => getRun(cfg, 'run-1'),
      contract: 'run-status',
      diagnostic: '$.sim_time_ms',
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
