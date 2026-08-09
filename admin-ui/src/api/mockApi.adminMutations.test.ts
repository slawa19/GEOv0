import { afterEach, describe, expect, it, vi } from 'vitest'

import { assertSuccess } from './envelope'
import { __resetMockApiForTests, mockApi } from './mockApi'

const config = {
  LOG_LEVEL: 'INFO',
  RATE_LIMIT_ENABLED: true,
  ROUTING_MAX_HOPS: 6,
  ROUTING_MAX_PATHS: 3,
  INTEGRITY_CHECKPOINT_ENABLED: true,
  INTEGRITY_CHECKPOINT_INTERVAL_SECONDS: 300,
  RECOVERY_ENABLED: true,
  RECOVERY_INTERVAL_SECONDS: 60,
  PAYMENT_TX_STUCK_TIMEOUT_SECONDS: 120,
  FEATURE_FLAGS_MULTIPATH_ENABLED: true,
  FEATURE_FLAGS_FULL_MULTIPATH_ENABLED: false,
  CLEARING_ENABLED: true,
}

const flags = {
  multipath_enabled: true,
  full_multipath_enabled: false,
  clearing_enabled: true,
}

const incidents = {
  items: [{
    tx_id: 'TX_STUCK_1',
    state: 'PREPARE_IN_PROGRESS',
    initiator_pid: 'PID_A',
    equivalent: 'UAH',
    age_seconds: 300,
    sla_seconds: 120,
  }],
}

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

function installFixtures() {
  vi.stubGlobal('window', { ...window, location: new URL('http://localhost/?scenario=happy') } as unknown as Window)
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/scenarios/happy.json')) return jsonResponse({ name: 'happy', latency_ms: { min: 0, max: 0 } })
    if (url.endsWith('/datasets/config.json')) return jsonResponse(config)
    if (url.endsWith('/datasets/feature-flags.json')) return jsonResponse(flags)
    if (url.endsWith('/datasets/audit-log.json')) return jsonResponse([])
    if (url.endsWith('/datasets/incidents.json')) return jsonResponse(incidents)
    return new Response('Not Found', { status: 404 })
  }) as unknown as typeof fetch)
}

afterEach(() => {
  vi.unstubAllGlobals()
  __resetMockApiForTests()
})

describe('mock Admin mutation state and audit contracts', () => {
  it('records canonical config and feature-flag audit entries', async () => {
    installFixtures()

    assertSuccess(await mockApi.patchConfig({ ROUTING_MAX_PATHS: 4 }))
    assertSuccess(await mockApi.patchFeatureFlags({ clearing_enabled: false }))
    const audit = assertSuccess(await mockApi.listAuditLog({ page: 1, per_page: 10 }))

    expect(audit.items.map((entry) => entry.action)).toEqual([
      'admin.feature_flags.patch',
      'admin.config.patch',
    ])
    expect(audit.items[1]).toMatchObject({
      object_type: 'config',
      object_id: null,
      before_state: { ROUTING_MAX_PATHS: 3 },
      after_state: { ROUTING_MAX_PATHS: 4 },
    })
    expect(audit.items[0]).toMatchObject({
      object_type: 'feature_flags',
      before_state: { clearing_enabled: true },
      after_state: { clearing_enabled: false },
    })
    expect(audit.items.every((entry) => /^[0-9a-f-]{36}$/i.test(entry.id) && Boolean(entry.timestamp))).toBe(true)
  })

  it('removes an aborted terminal incident from subsequent reloads and records the canonical action', async () => {
    installFixtures()

    expect(assertSuccess(await mockApi.listIncidents({})).items.map((item) => item.tx_id)).toEqual(['TX_STUCK_1'])
    expect(assertSuccess(await mockApi.abortTx('TX_STUCK_1', 'operator recovery'))).toEqual({
      tx_id: 'TX_STUCK_1',
      status: 'aborted',
    })
    expect(assertSuccess(await mockApi.listIncidents({})).items).toEqual([])

    const audit = assertSuccess(await mockApi.listAuditLog({ action: 'admin.transactions.abort' }))
    expect(audit.items).toHaveLength(1)
    expect(audit.items[0]).toMatchObject({
      action: 'admin.transactions.abort',
      object_type: 'transaction',
      object_id: 'TX_STUCK_1',
      after_state: { state: 'ABORTED' },
    })
  })
})
