/**
 * 049 S2 (decision 7; §15 Codex P2, 2026-10-20): the request id left the screen - it is `data-request-id` AND the console
 * line (AGENTS.md section 12). Two reachable failures kept the attribute and wrote no console line: a run start refused
 * by the global limit of runs (409 `global_active_limit`), and a failed read of the trust lines (the notices of the trust
 * line window and the line popup). Both now write the one line of `logFailureLine` with the id.
 *
 * Red on `4ebb1e99`: the refusal's line had no `log`, so `writeRunErrorLine` wrote nothing; the data cache logged nothing.
 */
import { computed, ref } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '../api/http'
import { startRunErrorLine, writeRunErrorLine, type RunErrorLine } from './runErrorClassification'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

const linesWith = (warn: { mock: { calls: unknown[][] } }, id: string): string[] =>
  warn.mock.calls.map((c) => c.map(String).join(' ')).filter((l: string) => l.includes(`(request id: ${id})`))

describe('049 S2: a failure whose id is kept in data-request-id also writes its console line', () => {
  it('a run start refused by the limit of runs (409 global_active_limit): one console line with the request id', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const body = JSON.stringify({ error: { code: 'E008', message: 'Global active runs limit reached', details: { conflict_kind: 'global_active_limit', max_active_runs: 10 } } })
    const line = startRunErrorLine(new ApiError('HTTP 409 Conflict for /simulator/runs', { status: 409, bodyText: body, requestId: 'req-limit-1' }))
    expect(line, 'counter-check: the refusal is recognised').not.toBeNull()
    const target: { lastError: string; lastErrorLine?: RunErrorLine | null } = { lastError: '' }
    writeRunErrorLine(target, line, 'en')
    expect(linesWith(warn, 'req-limit-1'), 'the console line carries the id').toHaveLength(1)
    expect(linesWith(warn, 'req-limit-1')[0]).toMatch(/^\[run\] error: HTTP 409/)
    // The drawn sentence is unchanged: no id, no HTTP in it.
    expect(target.lastError).not.toMatch(/req-limit-1|HTTP/)
    // A status repeating the same line writes no second line.
    writeRunErrorLine(target, line, 'en')
    expect(linesWith(warn, 'req-limit-1')).toHaveLength(1)
  })

  it('a failed read of the trust lines (500 with X-Request-ID): one console line with the request id', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const { useInteractActions } = await import('../composables/useInteractActions')
    const { useInteractMode } = await import('../composables/useInteractMode')
    vi.stubGlobal('fetch', vi.fn(async (url: string) => String(url).includes('trustlines-list')
      ? new Response(JSON.stringify({ code: 'E000', message: 'down' }), { status: 500, headers: { 'Content-Type': 'application/json', 'X-Request-ID': 'req-tl-9' } })
      : new Response(JSON.stringify({ items: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const mode = useInteractMode({
      actions: useInteractActions({ httpConfig: ref({ apiBase: '/api/v1' }), runId: ref('run-1') }),
      runId: computed(() => 'run-1'), equivalent: computed(() => 'UAH'), snapshot: ref(null),
    })
    mode.selectEdge('alice->bob')
    await vi.waitFor(() => expect(mode.trustlinesFetchState.value.kind).toBe('failed'))
    expect((mode.trustlinesFetchState.value as { ref?: string | null }).ref, 'counter-check: the notice keeps the id').toBe('req-tl-9')
    const lines = linesWith(warn, 'req-tl-9')
    expect(lines, 'the console line carries the id').toHaveLength(1)
    expect(lines[0]).toMatch(/^\[trustlines\] read failed: HTTP 500/)
  })
})
