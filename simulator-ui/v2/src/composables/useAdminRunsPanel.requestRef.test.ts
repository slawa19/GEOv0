/**
 * 034 S5a fix-delta, item 4: the friendly text of a rejected admin token keeps the request id.
 */
import { effectScope, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { adminGetAllRuns, adminStopAllRuns } from '../api/simulatorApi'
import { useAdminRunsPanel } from './useAdminRunsPanel'

vi.mock('../api/simulatorApi', () => ({ adminGetAllRuns: vi.fn(), adminStopAllRuns: vi.fn() }))

function panel() {
  const scope = effectScope()
  const p = scope.run(() =>
    useAdminRunsPanel({
      isRealMode: ref(true),
      apiBase: ref('http://x'),
      accessToken: ref('admin-token'),
      runId: ref(null),
      runStatus: ref(null),
    }),
  )!
  return { p, scope }
}

afterEach(() => {
  vi.clearAllMocks()
})

describe('useAdminRunsPanel 403', () => {
  it.each([
    ['getRuns', () => vi.mocked(adminGetAllRuns), (p: ReturnType<typeof panel>['p']) => p.getRuns()],
    ['stopRuns', () => vi.mocked(adminStopAllRuns), (p: ReturnType<typeof panel>['p']) => p.stopRuns()],
  ])('%s: the friendly text stays and gets (ref: <id>)', async (_name, mock, run) => {
    mock().mockRejectedValueOnce(new ApiError('HTTP 403  for /x', { status: 403, requestId: 'req-403' }))
    const { p, scope } = panel()

    await run(p)

    expect(p.lastError.value).toBe('Admin token rejected (HTTP 403) (ref: req-403)')
    scope.stop()
  })

  it('anti-vacuum: a 403 without an id keeps the plain friendly text', async () => {
    vi.mocked(adminGetAllRuns).mockRejectedValueOnce(new ApiError('HTTP 403  for /x', { status: 403 }))
    const { p, scope } = panel()

    await p.getRuns()

    expect(p.lastError.value).toBe('Admin token rejected (HTTP 403)')
    scope.stop()
  })
})
